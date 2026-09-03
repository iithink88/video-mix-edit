# -*- coding: utf-8 -*-
"""video-mix-edit 构建器：把 plan.json 描述的剪辑计划渲染成一条（或多条）成片。

用法:
    python build.py --plan plan.json [--keep-temp] [--srt SRT]

相对路径基于 plan.json 所在目录解析；output 可写绝对路径。
流程：规格化每段 -> 可选去气口 -> xfade/acrossfade 拼接 -> 字幕/BGM/CTA -> 成片。
扩展能力：
  - versions: 一次出多版本（如竖屏 9:16 + 横屏 16:9），各自独立规格化+烧录
  - cover:    从成片截一张封面图，可选叠加标题文字
字幕只在第一个版本上做一次 ASR（时间轴与分辨率无关），其余版本复用同一份 SRT。
"""
import os
import sys
import json
import shutil
import tempfile
import argparse
import subprocess

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ffmpeg_tool as ft  # noqa: E402

# ---------------------------------------------------------------- 常量
RES_PRESETS = {
    "9:16": (1080, 1920),
    "16:9": (1920, 1080),
    "1:1": (1080, 1080),
    "4:3": (1440, 1080),
    "3:4": (1080, 1440),
}
VERSION_TAG = {  # 多版本输出时的文件名后缀
    "9:16": "竖屏", "16:9": "横屏", "1:1": "方形",
    "4:3": "4x3", "3:4": "3x4", "auto": "",
}
VALID_TRANSITIONS = {
    "fade", "beat", "slideleft", "slideright", "slideup", "slidedown",
    "fadeblack", "fadewhite", "circlecrop", "rectcrop", "smoothleft",
    "smoothright", "smoothup", "smoothdown", "wipeleft", "wiperight",
    "wipeup", "wipedown", "dissolve", "pixelize", "radial", "hblur", "none",
}
DEFAULT_CJK_FONT = "Microsoft YaHei"


class BuildError(Exception):
    pass


# ---------------------------------------------------------------- 计划解析
def load_plan(plan_path):
    with open(plan_path, "r", encoding="utf-8-sig") as f:
        plan = json.load(f)
    segs = plan.get("segments") or []
    if len(segs) < 2:
        raise BuildError("segments 至少需要 2 条素材，混剪才有意义。当前 %d 条。" % len(segs))
    return plan


def resolve(base_dir, p):
    if not p:
        return p
    p = os.path.expanduser(p)
    return p if os.path.isabs(p) else os.path.normpath(os.path.join(base_dir, p))


def _resolve_single(res, first_info):
    """把单个分辨率描述解析成 (w, h, label)。"""
    res = str(res)
    if res == "auto":
        return (first_info["width"], first_info["height"], "auto")
    if res in RES_PRESETS:
        w, h = RES_PRESETS[res]
        return (w, h, res)
    if "x" in res:
        w, h = res.lower().split("x")
        return (int(w), int(h), res)
    raise BuildError("不支持的 resolution: %s（支持 auto/9:16/16:9/1:1/4:3/3:4/WxH）" % res)


def resolve_versions(plan, first_info):
    """返回 [(w, h, label), ...]。plan.versions 优先，否则用单 resolution。"""
    vs = plan.get("versions")
    if isinstance(vs, list) and vs:
        out = []
        for v in vs:
            out.append(_resolve_single(v, first_info))
        return out
    return [_resolve_single(plan.get("resolution", "auto"), first_info)]


def output_path_for(base_dir, plan_output, label):
    """多版本时自动加后缀（_竖屏/_横屏...）；单版本原样。"""
    if not label or label == "auto":
        return resolve(base_dir, plan_output)
    stem, ext = os.path.splitext(plan_output)
    if not ext:
        ext = ".mp4"
    tag = VERSION_TAG.get(label, label.replace(":", "x"))
    return resolve(base_dir, "%s_%s%s" % (stem, tag, ext))


# ---------------------------------------------------------------- 规格化
def build_scale_filter(target_w, target_h, crop_mode):
    """统一分辨率。cover=裁切填满；letterbox=补黑边。"""
    if crop_mode == "letterbox":
        return ("scale=%d:%d:force_original_aspect_ratio=decrease,"
                "pad=%d:%d:(ow-iw)/2:(oh-ih)/2:black,setsar=1"
                % (target_w, target_h, target_w, target_h))
    return ("scale=%d:%d:force_original_aspect_ratio=increase,"
            "crop=%d:%d,setsar=1" % (target_w, target_h, target_w, target_h))


def normalize(src, dst, info, target_w, target_h, target_fps, crop_mode,
              trim=None, work_dir=None):
    """把一段素材裁切 + 统一规格，输出中间文件（保证有音轨）。"""
    vf = build_scale_filter(target_w, target_h, crop_mode)
    vf += ",fps=%s,format=yuv420p" % target_fps

    args = [ft.FFMPEG, "-y", "-v", "error"]
    if trim and trim.get("start"):
        args += ["-ss", str(trim["start"])]          # 输入定位，快
    args += ["-i", src]

    if not info["has_audio"]:
        # 无音轨则补一段静音，保证后续 acrossfade 链路可用
        silent = os.path.join(work_dir, "silent.wav")
        if not os.path.exists(silent):
            ft.run([ft.FFMPEG, "-y", "-v", "error", "-f", "lavfi",
                    "-i", "anullsrc=r=48000:cl=stereo", "-t", "600", silent])
        args += ["-stream_loop", "-1", "-i", silent, "-shortest"]

    if trim and trim.get("end") is not None:
        dur = float(trim["end"]) - float(trim.get("start") or 0)
        args += ["-t", "%.3f" % max(0.0, dur)]

    args += ["-vf", vf, "-c:v", "libx264", "-preset", "medium", "-crf", "20",
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
             "-ar", "48000", "-ac", "2", dst]
    ft.run(args)
    return dst


# ---------------------------------------------------------------- 去气口
def detect_silence(path, noise_db=-35, min_dur=0.3):
    """返回 [(start, end), ...] 静音区间列表。"""
    out, err, _ = ft.run([
        ft.FFMPEG, "-v", "info", "-i", path,
        "-af", "silencedetect=noise=%ddB:d=%s" % (noise_db, min_dur),
        "-f", "null", "-",
    ], check=False)
    spans, cur_start = [], None
    for line in (err or "").splitlines():
        line = line.strip()
        if "silence_start:" in line:
            try:
                cur_start = float(line.split("silence_start:")[1].strip().split()[0])
            except (IndexError, ValueError):
                cur_start = None
        elif "silence_end:" in line and cur_start is not None:
            try:
                end = float(line.split("silence_end:")[1].strip().split()[0])
                spans.append((cur_start, end))
            except (IndexError, ValueError):
                pass
            cur_start = None
    return spans


def strip_silence(src, dst, duration, noise_db=-35, min_dur=0.3, pad=0.08):
    """删除静音段（音画同步裁剪，保留 pad 秒余量）。失败时原样返回 src。"""
    spans = detect_silence(src, noise_db, min_dur)
    if not spans:
        return src

    total = duration
    keeps, cursor = [], 0.0
    for s, e in spans:
        s2, e2 = s + pad, e - pad
        if e2 <= s2:
            continue
        if s2 > cursor:
            keeps.append((cursor, s2))
        cursor = max(cursor, e2)
    if cursor < total:
        keeps.append((cursor, total))
    if not keeps or len(keeps) == 1 and abs(keeps[0][1] - total) < 0.05:
        return src

    # 多区间：filter_complex 裁剪后 concat
    parts, labels = [], []
    for i, (a, b) in enumerate(keeps):
        parts.append("[0:v]trim=start=%.3f:end=%.3f,setpts=PTS-STARTPTS[v%d];"
                     "[0:a]atrim=start=%.3f:end=%.3f,asetpts=PTS-STARTPTS[a%d];"
                     % (a, b, i, a, b, i))
        labels += ["[v%d]" % i, "[a%d]" % i]
    fc = "".join(parts)
    fc += "%sconcat=n=%d:v=1:a=1[v][a]" % ("".join(labels), len(keeps))
    try:
        ft.run([ft.FFMPEG, "-y", "-v", "error", "-i", src, "-filter_complex", fc,
                "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "medium",
                "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac",
                "-b:a", "192k", "-ar", "48000", "-ac", "2", dst])
    except Exception as e:
        print("  [去气口] 失败，改用原片: %s" % str(e)[:120])
        return src
    saved = sum(b - a for a, b in spans)
    print("  [去气口] 删除 %d 段静音，约 %.2f 秒" % (len(spans), saved))
    return dst


# ---------------------------------------------------------------- 拼接
def concat_segments(seg_files, seg_durs, transitions, trans_durs, dst, work_dir):
    """xfade（视频）+ acrossfade（音频）链式拼接。transitions[i] 是段 i 与前一段的转场。"""
    n = len(seg_files)
    if n == 1:
        shutil.copy(seg_files[0], dst)
        return dst

    args = [ft.FFMPEG, "-y", "-v", "error"]
    for f in seg_files:
        args += ["-i", f]

    # 计算每个转场的 offset
    offsets, acc = [], seg_durs[0]
    for i in range(1, n):
        td = trans_durs[i]
        offsets.append(round(acc - td, 3))
        acc = acc + seg_durs[i] - td

    vlabels, alabels = [], []
    for i in range(n):
        vlabels.append("[%d:v]" % i)
        alabels.append("[%d:a]" % i)

    fc = []
    cur_v, cur_a = vlabels[0], alabels[0]
    for i in range(1, n):
        tname = transitions[i]
        td = trans_durs[i]
        off = offsets[i - 1]
        out_v = "[v%d]" % i
        out_a = "[a%d]" % i
        if tname == "none":
            fc.append("%s%sconcat=n=2:v=1:a=0%s;" % (cur_v, vlabels[i], out_v))
            fc.append("%s%sconcat=n=2:v=0:a=1%s;" % (cur_a, alabels[i], out_a))
        else:
            fc.append("%s%sxfade=transition=%s:duration=%.3f:offset=%.3f%s;"
                      % (cur_v, vlabels[i], tname, td, off, out_v))
            fc.append("%s%sacrossfade=d=%.3f:c1=tri:c2=tri%s;"
                      % (cur_a, alabels[i], td, out_a))
        cur_v, cur_a = out_v, out_a

    # 最后统一输出标签（注意：copy 是 V->V 专用，音频必须用 anull）
    fc.append("%snull[vf];%sanull[af];" % (cur_v, cur_a))

    args += ["-filter_complex", "".join(fc),
             "-map", "[vf]", "-map", "[af]",
             "-c:v", "libx264", "-preset", "medium", "-crf", "20",
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
             "-ar", "48000", "-ac", "2", "-movflags", "+faststart", dst]
    ft.run(args)
    print("  转场 offsets:", offsets)
    return dst


# ---------------------------------------------------------------- 字幕
def build_subtitles(plan, seg_files, seg_durs, trans_durs, work_dir, base_dir):
    """对每段做 ASR，按拼接后的时间轴平移合并成一个 SRT。返回 SRT 路径或 None。"""
    sub_cfg = plan.get("subtitle") or {}
    if not sub_cfg.get("enabled", True):
        return None
    try:
        import asr
    except Exception as e:
        print("  [字幕] ASR 模块加载失败，跳过:", str(e)[:120])
        return None

    engines = asr.detect_engines()
    if not engines:
        print("  [字幕] 未检测到 ASR 引擎，跳过字幕。安装：pip install vosk 或 pip install faster-whisper")
        return None

    # 段 i 在成片时间轴上的内容起点
    starts, acc = [0.0], seg_durs[0]
    for i in range(1, len(seg_files)):
        starts.append(round(acc - trans_durs[i], 3))
        acc = acc + seg_durs[i] - trans_durs[i]

    merged = []
    for i, f in enumerate(seg_files):
        srt_i = os.path.join(work_dir, "sub_%d.srt" % i)
        got = asr.transcribe(f, srt_i,
                             language=sub_cfg.get("language", "zh"),
                             engine=sub_cfg.get("engine", "auto"),
                             model_size=sub_cfg.get("model"),
                             offset=starts[i])
        if got and os.path.exists(got):
            merged.append(got)

    if not merged:
        return None
    out = os.path.join(work_dir, "merged.srt")
    with open(out, "w", encoding="utf-8") as fo:
        idx = 0
        for p in merged:
            with open(p, "r", encoding="utf-8") as fi:
                for block in fi.read().strip().split("\n\n"):
                    lines = [l for l in block.splitlines() if l.strip()]
                    if len(lines) >= 3:
                        idx += 1
                        fo.write("%d\n%s\n%s\n\n" % (idx, lines[1], lines[2]))
    print("  [字幕] 合并 %d 段识别结果，共 %d 条字幕" % (len(merged), idx))
    return out


# ---------------------------------------------------------------- 封面
def make_cover(concat_out, cover_cfg, base_dir, font=DEFAULT_CJK_FONT):
    """从成片截一张封面图，可选叠加标题文字。返回封面路径。"""
    out = resolve(base_dir, cover_cfg.get("output", "cover.jpg"))
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    t = float(cover_cfg.get("time", 0))
    vf = []
    text = cover_cfg.get("text")
    if text:
        text = str(text).replace(":", "\\:").replace("'", "\\'")
        size = int(cover_cfg.get("size", 72))
        color = cover_cfg.get("color", "white")
        ypos = cover_cfg.get("y", "h-text_h-80")
        vf.append("drawtext=fontfile='%s':text='%s':fontsize=%d:fontcolor=%s"
                  ":x=(w-text_w)/2:y=%s:box=1:boxcolor=black@0.45:boxborderw=14"
                  % (_font_path(font), text, size, color, ypos))
    cmd = [ft.FFMPEG, "-y", "-v", "error", "-ss", "%.3f" % t, "-i", concat_out]
    if vf:
        cmd += ["-vf", ",".join(vf)]
    cmd += ["-frames:v", "1", "-q:v", "2", out]
    ft.run(cmd)
    return out


# ---------------------------------------------------------------- 合成单版本
def composite(plan, concat_out, srt, target_w, target_h, final, base_dir):
    """把字幕/BGM/CTA 烧录到 concat_out，产出 final。"""
    vf, af = [], []
    inputs = [ft.FFMPEG, "-y", "-v", "error", "-i", concat_out]
    v_out, a_out = "0:v", "0:a"   # 最终 -map 使用的标签

    if srt and os.path.exists(srt):
        style = (plan.get("subtitle") or {})
        font = style.get("font", DEFAULT_CJK_FONT)
        # size 视为实际像素高。subtitles/libass 对 SRT 用默认 PlayResY=288
        # 坐标系，需换算：ass_size = px * 288 / 视频高，否则字号被放大数倍
        size_px = int(style.get("size", 56))
        ass_size = max(6, round(size_px * 288.0 / float(target_h)))
        margin_v = int(style.get("margin_v", 120))
        force = ("FontName=%s,FontSize=%d,PrimaryColour=&H00FFFFFF,"
                 "OutlineColour=&H00000000,Outline=2,Shadow=1,MarginV=%d,Alignment=2"
                 % (font, ass_size, margin_v))
        # SRT 路径需转义 libass 特殊字符
        sp = srt.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
        vf.append("subtitles=filename='%s':force_style='%s'" % (sp, force))

    cta = plan.get("cta")
    if cta and cta.get("text"):
        text = str(cta["text"]).replace(":", "\\:").replace("'", "\\'")
        dur = float(cta.get("duration", 3.0))
        total = ft.probe(concat_out)["duration"]
        size = int(cta.get("size", 60))
        color = cta.get("color", "red")
        font = cta.get("font", DEFAULT_CJK_FONT)
        box = ":box=1:boxcolor=black@0.35:boxborderw=12" if cta.get("box", True) else ""
        vf.append("drawtext=fontfile='%s':text='%s':fontsize=%d:fontcolor=%s"
                  ":x=(w-text_w)/2:y=(h-text_h)/2:enable='gte(t\\,%.2f)'%s"
                  % (_font_path(font), text, size, color, max(0.0, total - dur), box))

    bgm = plan.get("bgm")
    if bgm and bgm.get("file"):
        bp = resolve(base_dir, bgm["file"])
        if os.path.isfile(bp):
            inputs += ["-stream_loop", "-1", "-i", bp]
            vol = float(bgm.get("volume", 0.35))
            af.append("[1:a]volume=%.3f,apad[bg];" % vol)
            af.append("[bg][0:a]amix=inputs=2:duration=first:"
                      "dropout_transition=2[aout];")
            a_out = "[aout]"
        else:
            print("  [BGM] 文件不存在，跳过:", bp)

    if not vf and not af:
        shutil.copy(concat_out, final)
    else:
        fc = ""
        if vf:
            fc += "[0:v]%s[vout];" % ",".join(vf)
            v_out = "[vout]"
        fc += "".join(af)
        cmd = inputs + ["-filter_complex", fc]
        cmd += ["-map", v_out, "-map", a_out,
                "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                "-ar", "48000", "-ac", "2", "-shortest",
                "-movflags", "+faststart", final]
        ft.run(cmd)


# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser(description="video-mix-edit 构建器")
    ap.add_argument("--plan", required=True, help="剪辑计划 plan.json 路径")
    ap.add_argument("--keep-temp", action="store_true", help="保留临时文件")
    ap.add_argument("--srt", help="使用已有 SRT 烧录（跳过 ASR，适合人工校对后重出片）")
    args = ap.parse_args()

    ft.require()
    plan_path = os.path.abspath(args.plan)
    base_dir = os.path.dirname(plan_path)
    plan = load_plan(plan_path)

    work_dir = tempfile.mkdtemp(prefix="vme_", dir=tempfile.gettempdir())
    print("工作目录:", work_dir)

    try:
        # ---- 1. 探测素材
        segs = plan["segments"]
        srcs, trims = [], []
        for i, s in enumerate(segs):
            f = s.get("file")
            if not f:
                raise BuildError("第 %d 段缺少 file 字段" % (i + 1))
            p = resolve(base_dir, f)
            if not os.path.isfile(p):
                raise BuildError("素材不存在: %s" % p)
            srcs.append(p)
            trims.append(s.get("trim"))

        infos = [ft.probe(p) for p in srcs]
        for i, (p, inf) in enumerate(zip(srcs, infos)):
            if not inf["has_video"]:
                raise BuildError("第 %d 段不是视频文件: %s" % (i + 1, os.path.basename(p)))
            print("  [%d] %s  %.2fs  %dx%d  %.2ffps  %s"
                  % (i + 1, os.path.basename(p), inf["duration"], inf["width"],
                     inf["height"], inf["fps"], "有声" if inf["has_audio"] else "无声"))

        # ---- 2. 目标版本（可多个）
        versions = resolve_versions(plan, infos[0])
        if len(versions) > 1:
            print("多版本输出: " + " / ".join("%dx%d" % (w, h) for w, h, _ in versions))
        global_trans = plan.get("transition", "fade")
        global_tdur = float(plan.get("transition_duration", 0.5))

        # ---- 3. 逐版本渲染
        merged_srt = None
        finals = []
        for vi, (tw, th, label) in enumerate(versions):
            tfps = plan.get("fps") or (infos[0]["fps"] or 30.0)
            crop_mode = plan.get("crop_mode", "cover")
            print("\n=== 版本 %s (%dx%d) ===" % (label or "auto", tw, th))
            if tw % 2 or th % 2:
                tw, th = tw - tw % 2, th - th % 2

            # 3a. 规格化 + 可选去气口
            seg_files, seg_durs = [], []
            for i, (src, info, trim) in enumerate(zip(srcs, infos, trims)):
                norm = os.path.join(work_dir, "norm_%d_%d.mp4" % (vi, i))
                normalize(src, norm, info, tw, th, tfps, crop_mode, trim, work_dir)
                cur = norm
                do_strip = bool(segs[i].get("strip_silence", plan.get("strip_silence", False)))
                if do_strip:
                    cut = os.path.join(work_dir, "cut_%d_%d.mp4" % (vi, i))
                    cur = strip_silence(norm, cut, ft.probe(norm)["duration"],
                                        float(plan.get("silence_noise_db", -35)),
                                        float(plan.get("silence_min_dur", 0.3)))
                seg_files.append(cur)
                seg_durs.append(ft.probe(cur)["duration"])

            # 3b. 转场参数
            transitions, trans_durs = ["none"], [0.0]
            for i in range(1, len(segs)):
                t = str(segs[i].get("transition", global_trans))
                if t not in VALID_TRANSITIONS and t != "none":
                    raise BuildError("不支持的转场: %s（第 %d 段）" % (t, i + 1))
                td = float(segs[i].get("transition_duration", global_tdur))
                if t == "beat":
                    td = 0.2
                if t == "none":
                    td = 0.0
                transitions.append(t)
                trans_durs.append(td)
            print("转场:", " / ".join("%s(%.2fs)" % (t, d)
                                    for t, d in zip(transitions[1:], trans_durs[1:])) or "无")

            # 3c. 拼接
            concat_out = os.path.join(work_dir, "concat_%d.mp4" % vi)
            concat_segments(seg_files, seg_durs, transitions, trans_durs, concat_out, work_dir)
            print("拼接完成:", "%.2fs" % ft.probe(concat_out)["duration"])

            # 3d. 字幕（只在第一版本上做一次 ASR，时间轴与分辨率无关）
            if merged_srt is None:
                preset = args.srt or (plan.get("subtitle") or {}).get("srt")
                if preset:
                    pp = resolve(base_dir, preset)
                    if os.path.isfile(pp):
                        merged_srt = pp
                        print("  [字幕] 复用已有 SRT:", pp)
                    else:
                        print("  [字幕] 指定的 SRT 不存在，改为自动识别:", pp)
                if merged_srt is None:
                    merged_srt = build_subtitles(plan, seg_files, seg_durs,
                                                 trans_durs, work_dir, base_dir)

            # 3e. 合成（字幕复用同一份 SRT，按本版本高度换算字号）
            final = output_path_for(base_dir, plan.get("output", "final.mp4"), label)
            os.makedirs(os.path.dirname(final) or ".", exist_ok=True)
            composite(plan, concat_out, merged_srt, tw, th, final, base_dir)
            finals.append(final)

            # 3f. 封面（仅第一版本）
            if vi == 0:
                cover = plan.get("cover")
                if cover and cover.get("enabled", True):
                    cp = make_cover(concat_out, cover, base_dir,
                                    (plan.get("subtitle") or {}).get("font", DEFAULT_CJK_FONT))
                    print("封面:", cp)

        # ---- 4. 交付
        for f in finals:
            dur = ft.probe(f)["duration"]
            size_mb = os.path.getsize(f) / 1024.0 / 1024.0
            print("\n✅ 成片: %s" % f)
            print("   时长 %.2fs  |  %.1f MB" % (dur, size_mb))
            if merged_srt and os.path.exists(merged_srt):
                # 临时目录会被清理，务必把 SRT 留在成片旁边供人工校对
                stem = os.path.splitext(f)[0]
                srt_out = stem + ".srt"
                shutil.copy(merged_srt, srt_out)
                print("⚠️  ASR 字幕可能有错别字/繁体，交付前请人工校一遍：")
                print("    %s" % srt_out)
        return 0

    finally:
        if args.keep_temp:
            print("临时文件保留于:", work_dir)
        else:
            shutil.rmtree(work_dir, ignore_errors=True)


def _font_path(font_name):
    """把字体名解析成 fontfile 路径（drawtext 需要具体文件）。

    filter 表达式里冒号是分隔符，盘符 C: 必须转义成 C\\:，否则解析失败。
    """
    def esc(p):
        return p.replace("\\", "/").replace(":", "\\:")

    if os.path.isfile(font_name):
        return esc(font_name)
    windirs = [r"C:/Windows/Fonts", "/usr/share/fonts",
               os.path.expanduser("~/.fonts")]
    cands = {
        "Microsoft YaHei": "msyh.ttc",
        "Microsoft YaHei Bold": "msyhbd.ttc",
        "SimHei": "simhei.ttf",
        "SimSun": "simsun.ttc",
        "KaiTi": "simkai.ttf",
        "Arial": "arial.ttf",
    }
    fn = cands.get(font_name)
    if fn:
        for d in windirs:
            p = os.path.join(d, fn)
            if os.path.isfile(p):
                return esc(p)
    key = font_name.lower().replace(" ", "")
    for d in windirs:
        if os.path.isdir(d):
            try:
                for f in os.listdir(d):
                    if f.lower().split(".")[0] == key:
                        return esc(os.path.join(d, f))
            except OSError:
                continue
    return font_name


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except BuildError as e:
        print("❌ %s" % e)
        sys.exit(1)
    except Exception as e:
        print("❌ 未预期错误: %s" % e)
        sys.exit(1)
