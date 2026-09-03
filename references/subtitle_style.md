# 字幕样式与踩坑（实现者备注，实测结论）

## ASS 字号换算（最重要的坑）

`subtitles` 滤镜读 SRT 时，libass 用默认 PlayResY=288 坐标系。
force_style 里的 `FontSize` 是 288 坐标系的字号，不是像素：

- 实际像素高 = FontSize / 288 × 视频高度
- 反推：`ass_size = 目标像素 × 288 / 视频高`

例：1080x1920 竖屏想要 56px 字高 → ass_size = 56×288/1920 ≈ 8。
**不换算直接写 54，会渲染成 ~360px 的巨字**（已在实测中踩过）。

build.py 已内置换算：plan 里的 `subtitle.size` 直接填**实际像素**。

## 中文字体

- SRT 烧录（subtitles 滤镜）：`force_style='FontName=Microsoft YaHei'`，
  libass 走系统字体名，Windows 直接可用。
- drawtext（CTA）：必须给 `fontfile=` 具体文件路径，且**盘符冒号必须转义**
  `C\:/Windows/Fonts/msyh.ttc`，否则 filter 解析直接报错（实测踩过）。
  build.py 的 `_font_path()` 已处理（msyh.ttc / simhei.ttf 等常见字体自动定位）。

## 路径转义（subtitles 滤镜）

SRT 路径中的 `:` 和 `\` 都要转义：`filename='C\:/path/to/file.srt'`。
临时文件用 `tempfile.gettempdir()` 下的 ASCII 目录，避免中文路径风险。

## ffmpeg 滤镜类型陷阱

- `copy` 是 **V→V 专用**，音频流收尾要用 `anull`（实测踩过：用 copy 报
  "Media type mismatch"）。
- xfade 要求所有输入分辨率/帧率/像素格式一致 → 每段先规格化
  （scale/crop + fps + format=yuv420p + setsar=1）。
- 无音轨素材必须补静音（anullsrc），否则 acrossfade 链断。

## ASR 引擎现状（本机，2026-09-04 实测已打通）

- **faster-whisper（最准，可选装）**：装在独立 Python 3.11 venv
  `<用户目录>/.workbuddy/binaries/python/envs/asr311`（用任意 Python 3.11 建干净 venv，
  避开默认 venv 里可能坏的 torch，也避开 Python 3.13 下 ctranslate2 的 Segfault）。
  asr.py 的 `_fw_external_python()` 自动探测该 venv，经 `asr_fw_runner.py` 子进程跑识别
  回写 JSON。**自动生效，无需改调用、无需设任何环境变量；没装则回退 Vosk。**
  - 必打的三处补丁（已写进脚本，勿删）：
    1. `ctranslate2==4.0.0` —— 4.8.2 在本机 `WhisperModel/transcribe` 时原生 Segfault；
    2. `vad_filter=False` —— VAD 须 `onnxruntime`，而它本机原生 DLL 初始化失败；
    3. runner 入口 `os.environ.setdefault("HF_HUB_DISABLE_XET","1")` —— 否则首次下模型走
       Xet 后端在本机 Segfault；模型现已缓存到 `~/.cache/huggingface/hub/`，离线可用。
  - 实测：同一段"大家好这是第一段视频素材今天我们来聊聊人工智能"faster-whisper 全对，
    Vosk small 会丢字/错字。质量肉眼可见提升。
- **vosk + vosk-model-small-cn-0.22：兜底可用**，中文识别质量一般
  （如把"混剪技能"识别成"婚检技能"），错字/断句需人工校对。模型在
  `~/.cache/vosk-models/`（Windows 即 `C:\Users\<你的用户名>\.cache\vosk-models\`）。
- 默认引擎链 auto：faster-whisper 可用时优先用它（最准），否则落 vosk。
  想指定别的解释器设 `VME_ASR_PYTHON` 环境变量。
