# -*- coding: utf-8 -*-
"""ffmpeg / ffprobe 定位与常用操作封装（跨平台，不硬编码用户名路径）。

优先级：环境变量 WB_FFMPEG_DIR > 环境变量 WB_FFMPEG > PATH > 常见安装目录扫描。
"""
import os
import json
import shutil
import subprocess

if hasattr(__import__("sys").stdout, "reconfigure"):
    __import__("sys").stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(__import__("sys").stderr, "reconfigure"):
    __import__("sys").stderr.reconfigure(encoding="utf-8", errors="replace")


def _candidate_dirs():
    dirs = []
    for key in ("WB_FFMPEG_DIR", "FFMPEG_DIR"):
        v = os.environ.get(key)
        if v:
            dirs.append(v)
    home = os.path.expanduser("~")
    dirs += [
        os.path.join(home, "bin", "ffmpeg"),
        os.path.join(home, "ffmpeg"),
        os.path.join(home, "scoop", "apps", "ffmpeg", "current", "bin"),
        os.path.join(home, "AppData", "Local", "Programs", "ffmpeg", "bin"),
        r"C:/ffmpeg/bin",
        r"C:/Program Files/ffmpeg/bin",
        "/usr/bin",
        "/usr/local/bin",
        "/opt/homebrew/bin",
    ]
    return dirs


def _find(name):
    """返回可执行文件的绝对路径，找不到返回 None。"""
    direct = os.environ.get("WB_FFMPEG" if name == "ffmpeg" else "WB_FFPROBE")
    if direct and os.path.isfile(direct):
        return direct
    p = shutil.which(name)
    if p:
        return p
    for d in _candidate_dirs():
        if not os.path.isdir(d):
            continue
        # 版本化子目录，如 ffmpeg-8.1.2-essentials_build/bin
        try:
            subs = [os.path.join(d, s) for s in os.listdir(d)]
        except OSError:
            subs = []
        for cand in [d] + subs:
            for sub in ("", "bin"):
                fp = os.path.join(cand, sub, name + (".exe" if os.name == "nt" else ""))
                if os.path.isfile(fp):
                    return fp
    return None


FFMPEG = _find("ffmpeg")
FFPROBE = _find("ffprobe")


def require():
    """确保 ffmpeg/ffprobe 可用，否则抛出带安装指引的异常。"""
    if not FFMPEG:
        raise SystemExit(
            "未找到 ffmpeg。请任选一种方式：\n"
            "  1) 安装 ffmpeg 并加入 PATH（Windows: https://www.gyan.dev/ffmpeg/builds/）\n"
            "  2) 设置环境变量 WB_FFMPEG_DIR 指向 ffmpeg 的 bin 目录\n"
            "  3) 设置环境变量 WB_FFMPEG 指向 ffmpeg 可执行文件本身"
        )
    if not FFPROBE:
        raise SystemExit("未找到 ffprobe（通常与 ffmpeg 同目录），请一并安装。")
    return FFMPEG, FFPROBE


def run(args, quiet=True, check=True):
    """执行 ffmpeg/ffprobe。返回 (stdout, stderr, returncode)。"""
    p = subprocess.run(args, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if check and p.returncode != 0:
        raise RuntimeError("命令失败(exit %d):\n%s\n%s"
                           % (p.returncode, " ".join(args)[:400], p.stderr[-1500:]))
    return p.stdout, p.stderr, p.returncode


def probe(path):
    """探测媒体信息，返回 dict：duration / width / height / fps / has_audio / has_video。"""
    out, _, _ = run([
        FFPROBE, "-v", "error",
        "-show_entries", "format=duration",
        "-show_entries", "stream=index,codec_type,codec_name,width,height,r_frame_rate,sample_rate,channels",
        "-of", "json", path,
    ])
    d = json.loads(out or "{}")
    info = {"path": path, "duration": 0.0, "width": 0, "height": 0,
            "fps": 0.0, "has_audio": False, "has_video": False}
    try:
        info["duration"] = float(d.get("format", {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        pass
    for s in d.get("streams", []):
        if s.get("codec_type") == "video" and not info["has_video"]:
            info["has_video"] = True
            info["width"] = int(s.get("width") or 0)
            info["height"] = int(s.get("height") or 0)
            fr = s.get("r_frame_rate") or "0/1"
            try:
                num, den = fr.split("/")
                info["fps"] = round(float(num) / float(den), 3) if float(den) else 0.0
            except (ValueError, ZeroDivisionError):
                info["fps"] = 0.0
        elif s.get("codec_type") == "audio":
            info["has_audio"] = True
    return info


def has_libass():
    """检查 ffmpeg 是否编译了 subtitles/ass 滤镜（烧录字幕必需）。"""
    try:
        out, _, _ = run([FFMPEG, "-hide_banner", "-filters"], check=False)
        return " subtitles " in out or "\n .. subtitles " in out or "subtitles" in out
    except Exception:
        return False


if __name__ == "__main__":
    require()
    print("ffmpeg :", FFMPEG)
    print("ffprobe:", FFPROBE)
    print("libass :", has_libass())
