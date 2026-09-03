# -*- coding: utf-8 -*-
"""语音识别（ASR）多引擎封装 —— 生成 SRT 字幕。

引擎优先级：faster-whisper > openai-whisper > vosk。
任一引擎不可用会自动降级；全部不可用时返回 None，由调用方决定跳过字幕。
"""
import os
import sys
import json
import wave
import subprocess

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ffmpeg_tool as ft  # noqa: E402

# faster-whisper 需要一个「干净」的 Python（部分机器默认 venv 的 torch 已坏、
# 或 Python 3.13 下 ctranslate2 会 Segmentation fault）。推荐在独立 Python 3.11 venv 里装：
#   <用户目录>/.workbuddy/binaries/python/envs/asr311
# 可用环境变量 VME_ASR_PYTHON 指定任意已装 faster-whisper 的 python 解释器；
# 下面候选列表不写死盘符/用户名，优先用环境变量，再试常见 WorkBuddy 管理 venv 位置。
ASR_VENV_CANDIDATES = [
    os.path.join(os.path.expanduser("~"), ".workbuddy", "binaries", "python",
                 "envs", "asr311", "Scripts", "python.exe"),
    os.path.join(os.path.expanduser("~"), ".workbuddy", "binaries", "python",
                 "envs", "asr", "Scripts", "python.exe"),
]


# --------------------------------------------------------------------------
# 音频抽取：统一转成 16kHz 单声道 PCM WAV，三个引擎都能吃
# --------------------------------------------------------------------------
def extract_wav(video_path, out_wav):
    ft.require()
    if os.path.exists(out_wav):
        os.remove(out_wav)
    ft.run([
        ft.FFMPEG, "-y", "-v", "error", "-i", video_path,
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", out_wav,
    ])
    return out_wav if os.path.exists(out_wav) else None


def _fw_external_python():
    """返回能 import faster_whisper 的「外部 python」路径；没有则返回 None。

    优先 VME_ASR_PYTHON 环境变量，否则试常见 asr venv 位置（不写死盘符/用户名）。
    """
    cands = []
    if os.environ.get("VME_ASR_PYTHON"):
        cands.append(os.environ["VME_ASR_PYTHON"])
    cands += ASR_VENV_CANDIDATES
    for p in cands:
        if not p or not os.path.isfile(p):
            continue
        try:
            r = subprocess.run([p, "-c", "import faster_whisper"],
                               capture_output=True, text=True, timeout=30)
            if r.returncode == 0:
                return p
        except Exception:
            continue
    return None


def _asr_faster_whisper_external(python_exe, wav, language="zh", model_size="small"):
    """用外部 python（干净 venv）跑 faster-whisper，返回 segments 列表。"""
    runner = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "asr_fw_runner.py")
    out_json = wav + ".fw.json"
    subprocess.run([python_exe, runner, "--wav", wav, "--language", language,
                    "--model", model_size or "small", "--out", out_json],
                   check=True, capture_output=True, text=True)
    with open(out_json, "r", encoding="utf-8") as f:
        segs = json.load(f)
    try:
        os.remove(out_json)
    except OSError:
        pass
    return segs


def detect_engines():
    """返回可用引擎列表，按优先级排序。"""
    avail = []
    # faster-whisper：本进程可 import，或外部干净 venv 可 import
    fw = None
    try:
        from faster_whisper import WhisperModel  # noqa: F401
        fw = sys.executable
    except Exception:
        fw = _fw_external_python()
    if fw:
        avail.append("faster-whisper")
    try:
        import whisper  # noqa: F401
        avail.append("whisper")
    except Exception:
        pass
    try:
        import vosk  # noqa: F401
        avail.append("vosk")
    except Exception:
        pass
    return avail


# --------------------------------------------------------------------------
# 引擎实现：均返回 list[{"start": float, "end": float, "text": str}]
# --------------------------------------------------------------------------
def _asr_faster_whisper(wav, language="zh", model_size="small"):
    from faster_whisper import WhisperModel
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, _info = model.transcribe(wav, language=language, vad_filter=True)
    return [{"start": float(s.start), "end": float(s.end), "text": s.text.strip()}
            for s in segments if s.text.strip()]


def _asr_whisper(wav, language="zh", model_size="base"):
    import whisper
    model = whisper.load_model(model_size)
    result = model.transcribe(wav, language=language)
    return [{"start": float(s["start"]), "end": float(s["end"]), "text": s["text"].strip()}
            for s in result.get("segments", []) if s["text"].strip()]


def _asr_vosk(wav, language="zh", model_path=None):
    import vosk

    model_path = model_path or os.environ.get("VOSK_MODEL_PATH", "")
    if not model_path:
        home = os.path.expanduser("~")
        cands = [
            os.path.join(home, ".cache", "vosk-models", "vosk-model-small-cn-0.22"),
            os.path.join(home, ".cache", "vosk-models", "vosk-model-cn-0.22"),
            os.path.join(home, "vosk-model-small-cn-0.22"),
        ]
        for c in cands:
            if os.path.isdir(c):
                model_path = c
                break
    if not model_path or not os.path.isdir(model_path):
        raise RuntimeError("未找到 Vosk 中文模型，请设置环境变量 VOSK_MODEL_PATH")
    if not os.path.isdir(model_path):
        raise RuntimeError("Vosk 模型路径无效: %s" % model_path)

    model = vosk.Model(model_path)
    rec = vosk.KaldiRecognizer(model, 16000)
    rec.SetWords(True)

    wf = wave.open(wav, "rb")
    out = []
    try:
        while True:
            data = wf.readframes(4000)
            if len(data) == 0:
                break
            if rec.AcceptWaveform(data):
                r = json.loads(rec.Result())
                if r.get("text"):
                    out.append(r)
        final = json.loads(rec.FinalResult())
        if final.get("text"):
            out.append(final)
    finally:
        wf.close()

    # Vosk 不直接给时间戳；按 result 到达顺序用音频进度近似切分
    # 先取总时长，再按各 result 文本长度比例分配时间
    total = _wav_duration(wav)
    chunks = []
    for r in out:
        words = r.get("result") or []
        text = (r.get("text") or "").strip()
        if not text:
            continue
        if words:
            s = float(words[0]["start"])
            e = float(words[-1]["end"])
        else:
            s = e = 0.0
        chunks.append({"start": s, "end": e, "text": _vosk_text(text)})
    # 若 Vosk 未开启词级时间戳（全 0），退化为整段一条
    if chunks and all(c["end"] <= 0 for c in chunks):
        chunks = [{"start": 0.0, "end": total,
                   "text": _vosk_text(" ".join(c["text"] for c in chunks))}]
    return chunks


def _vosk_text(s):
    """Vosk 中文输出是空格分隔的单字，去掉空格。"""
    return "".join(s.split())


def _wav_duration(wav):
    try:
        wf = wave.open(wav, "rb")
        d = wf.getnframes() / float(wf.getframerate())
        wf.close()
        return d
    except Exception:
        return 0.0


# --------------------------------------------------------------------------
# SRT 输出
# --------------------------------------------------------------------------
def _fmt_ts(t):
    if t < 0:
        t = 0.0
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    ms = int(round((t - int(t)) * 1000))
    if ms == 1000:
        ms = 999
    return "%02d:%02d:%02d,%03d" % (h, m, s, ms)


def write_srt(segments, out_path, offset=0.0, max_chars=18, min_dur=0.6):
    """把识别结果写成 SRT。offset 用于按拼接后的时间轴平移。"""
    lines = []
    idx = 0
    for seg in segments:
        text = seg["text"].strip()
        if not text:
            continue
        start = seg["start"] + offset
        end = seg["end"] + offset
        if end <= start:
            end = start + min_dur
        # 长句按标点/长度拆行
        for piece in _split_text(text, max_chars):
            dur = (end - start) / max(1, len(_split_text(text, max_chars)))
            idx += 1
            lines.append(str(idx))
            lines.append("%s --> %s" % (_fmt_ts(start), _fmt_ts(start + max(dur, min_dur))))
            lines.append(piece)
            lines.append("")
            start += max(dur, min_dur)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return out_path


def _split_text(text, n):
    """按标点切分，再按最大字数硬切。"""
    import re
    parts, buf = [], ""
    for ch in text:
        buf += ch
        if ch in "。！？，、；：,.!?;: ":
            parts.append(buf.strip())
            buf = ""
        elif len(buf) >= n:
            parts.append(buf.strip())
            buf = ""
    if buf.strip():
        parts.append(buf.strip())
    out = []
    for p in parts:
        while len(p) > n:
            out.append(p[:n])
            p = p[n:]
        if p:
            out.append(p)
    return [p for p in out if p]


def transcribe(video_path, out_srt, language="zh", engine="auto",
               model_size=None, offset=0.0, vosk_model=None):
    """对视频做 ASR 并写 SRT。成功返回 SRT 路径，失败返回 None。"""
    ft.require()
    wav = out_srt + ".wav"
    if not extract_wav(video_path, wav):
        print("  [ASR] 抽取音轨失败（可能无音轨）:", os.path.basename(video_path))
        return None

    avail = detect_engines()
    if engine != "auto":
        avail = [engine] if engine in avail else []
    if not avail:
        print("  [ASR] 无可用引擎，跳过字幕。安装其一：")
        print("        pip install vosk          (轻量，需另下中文模型)")
        print("        pip install faster-whisper (更准，需 torch)")
        return None

    segs = None
    for eng in avail:
        try:
            if eng == "faster-whisper":
                # 本进程能 import 就直接跑；否则走外部干净 venv
                try:
                    from faster_whisper import WhisperModel  # noqa: F401
                    segs = _asr_faster_whisper(wav, language, model_size or "small")
                except Exception:
                    ext = _fw_external_python()
                    if ext:
                        segs = _asr_faster_whisper_external(
                            ext, wav, language, model_size or "small")
                    else:
                        raise RuntimeError("faster-whisper 不可用")
            elif eng == "whisper":
                segs = _asr_whisper(wav, language, model_size or "base")
            else:
                segs = _asr_vosk(wav, language, vosk_model)
            if segs:
                print("  [ASR] 引擎=%s 识别出 %d 段" % (eng, len(segs)))
                break
        except Exception as e:
            print("  [ASR] 引擎 %s 失败: %s" % (eng, str(e)[:160]))
            segs = None
    try:
        os.remove(wav)
    except OSError:
        pass

    if not segs:
        return None
    return write_srt(segs, out_srt, offset=offset)
