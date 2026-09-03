# -*- coding: utf-8 -*-
"""faster-whisper 外部运行器：由 asr.py 在「干净 venv」里调用。

本机默认 venv 的 torch 已坏、且 Python 3.13 下 ctranslate2 会 Segfault，
因此 faster-whisper 装在独立 Python 3.11 venv 中，asr.py 通过子进程调用本脚本，
把识别结果以 JSON 写回，父进程再转成 SRT。

用法（被 asr.py 调用，勿手动跑）：
    python asr_fw_runner.py --wav x.wav --language zh --model small --out r.json
"""
import os
import sys
import json
import argparse

# 必须在 import faster_whisper / huggingface_hub 之前设置，否则首次下载模型会走
# Xet 存储后端在本机崩溃（Segmentation fault）。关闭 Xet 走普通 HTTP 下载即可。
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
# 国内镜像加速模型下载（无网/已缓存时不影响）
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", required=True)
    ap.add_argument("--language", default="zh")
    ap.add_argument("--model", default="small")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    from faster_whisper import WhisperModel
    model = WhisperModel(a.model, device="cpu", compute_type="int8")
    # 注意：vad_filter 需要 onnxruntime，而本机 onnxruntime 原生 DLL 加载失败
    # （与 ctranslate2 同源的运行时问题）。关闭 VAD 仍可正常识别，仅不去静音。
    segments, _ = model.transcribe(a.wav, language=a.language, vad_filter=False)
    out = [{"start": float(s.start), "end": float(s.end), "text": s.text.strip()}
           for s in segments if s.text.strip()]
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print("FW_SEGMENTS=%d" % len(out))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        sys.stderr.write("RUNNER_ERROR: %s\n" % e)
        sys.exit(1)
