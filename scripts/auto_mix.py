# -*- coding: utf-8 -*-
"""批量视频自动混剪：把一个文件夹里的视频按文件名顺序自动拼成多版本成片。

用法:
    python auto_mix.py --dir 素材夹 --output 成片名
        [--versions 9:16,16:9]          # 默认就出竖屏+横屏双版本
        [--cover-text "标题"]           # 给的话自动截封面并叠标题（三件套）
        [--per-clip 8]                  # 每段最多几秒，默认用整段
        [--transition slideup]          # 段间转场，默认 slideup
        [--bgm bgm.mp3]                 # 背景音乐
        [--no-subtitle]                 # 关字幕
        [--preset social]              # 等价于 versions=9:16,16:9 + cover + subtitle

特性：
- 按文件名「自然排序」自动排顺序（1.mp4, 2.mp4, 10.mp4 不乱）
- 每段可自动掐到 --per-clip 秒（默认整段都用）
- 段间默认 0.5s 滑动转场
- 默认出「竖屏 + 横屏」双版本，加 --cover-text 即成「封面三件套」
- 字幕默认开（faster-whisper 自动识别，无需手动）

生成的 plan.json 落到 --dir 下（方便二次微调），再调用 build.py 出片。
"""
import os
import sys
import json
import re
import argparse
import subprocess

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ffmpeg_tool as ft  # noqa: E402

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".flv", ".ts"}


def _natural_key(name):
    """文件名自然排序：'10.mp4' 排在 '2.mp4' 后面。"""
    return [int(t) if t.isdigit() else t.lower()
            for t in re.split(r"(\d+)", name)]


def collect_clips(folder):
    files = []
    for f in os.listdir(folder):
        ext = os.path.splitext(f)[1].lower()
        if ext in VIDEO_EXTS:
            files.append(f)
    files.sort(key=_natural_key)
    return files


def main():
    ap = argparse.ArgumentParser(description="批量视频自动混剪")
    ap.add_argument("--dir", required=True, help="素材文件夹")
    ap.add_argument("--output", default="成片.mp4", help="成片文件名（含扩展名）")
    ap.add_argument("--versions", default="9:16,16:9",
                    help="逗号分隔的版本列表，如 9:16,16:9,1:1")
    ap.add_argument("--cover-text", default=None, help="封面标题文字（给则自动加封面）")
    ap.add_argument("--per-clip", type=float, default=None,
                    help="每段最多几秒（默认用整段）")
    ap.add_argument("--transition", default="slideup", help="段间转场")
    ap.add_argument("--transition-duration", type=float, default=0.5)
    ap.add_argument("--bgm", default=None, help="背景音乐文件")
    ap.add_argument("--no-subtitle", action="store_true", help="关闭字幕")
    ap.add_argument("--resolution", default=None, help="单版本时的目标分辨率")
    ap.add_argument("--preset", default=None, help="social = 竖屏+横屏+封面")
    ap.add_argument("--keep-plan", action="store_true",
                    help="保留生成的 plan.json（默认出片后删）")
    args = ap.parse_args()

    ft.require()
    folder = os.path.abspath(args.dir)
    if not os.path.isdir(folder):
        print("❌ 素材文件夹不存在: %s" % folder)
        return 1

    clips = collect_clips(folder)
    if len(clips) < 2:
        print("❌ 文件夹里至少需要 2 个视频，当前 %d 个。" % len(clips))
        print("   支持扩展名: %s" % ", ".join(sorted(VIDEO_EXTS)))
        return 1

    # preset 处理
    versions = [v.strip() for v in args.versions.split(",") if v.strip()]
    cover_text = args.cover_text
    if args.preset == "social":
        versions = ["9:16", "16:9"]
        if cover_text is None:
            cover_text = os.path.splitext(os.path.basename(args.output))[0]

    plan = {
        "segments": [],
        "transition": args.transition,
        "transition_duration": args.transition_duration,
        "crop_mode": "cover",
        "subtitle": {"enabled": not args.no_subtitle, "language": "zh",
                      "engine": "auto", "size": 54, "font": "Microsoft YaHei"},
    }
    if args.resolution and len(versions) == 1:
        plan["resolution"] = args.resolution
    else:
        plan["versions"] = versions
    if args.bgm:
        plan["bgm"] = {"file": args.bgm, "volume": 0.18}
    if cover_text:
        plan["cover"] = {"enabled": True, "time": 0, "text": cover_text,
                          "size": 72, "color": "white"}
    plan["output"] = args.output

    print("扫描到 %d 个素材（按文件名排序）:" % len(clips))
    for i, c in enumerate(clips):
        dur = ft.probe(os.path.join(folder, c)).get("duration")
        trim = {}
        if args.per_clip and dur and dur > args.per_clip:
            trim = {"end": args.per_clip}
        seg = {"file": c}
        if trim:
            seg["trim"] = trim
        if i > 0:
            seg["transition"] = args.transition
            seg["transition_duration"] = args.transition_duration
        plan["segments"].append(seg)
        print("  %d. %s  (%.1fs%s)" % (i + 1, c, dur or 0,
                                       ", 取前%ds" % args.per_clip if trim else ""))

    plan_path = os.path.join(folder, "auto_mix_plan.json")
    with open(plan_path, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)
    print("\n📋 计划已生成: %s" % plan_path)

    # 调用 build.py 出片
    build_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "build.py")
    print("🎬 调用 build.py 出片...\n")
    r = subprocess.run([sys.executable, build_py, "--plan", plan_path],
                       cwd=folder)
    if r.returncode != 0:
        print("❌ build.py 失败，请检查 plan.json")
        return r.returncode

    if not args.keep_plan:
        try:
            os.remove(plan_path)
        except OSError:
            pass
    print("\n✅ 自动混剪完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
