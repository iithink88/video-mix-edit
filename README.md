# video-mix-edit · 自然语言自动混剪视频技能

用大白话描述，AI 就把多段视频自动剪成一条成片：横竖比例自动统一、转场特效、自动中文字幕、BGM、结尾 CTA、自动封面、横版+竖版双版本一键出。

全程本地 ffmpeg + 本地语音识别，**不上传任何视频，不依赖外部服务**。

---

## 一、三步安装

### 第 1 步：放技能
把整个 `video-mix-edit` 文件夹复制到 WorkBuddy 的技能目录（重启 WorkBuddy 即可看到）：
- Windows：`C:\Users\<你的用户名>\.workbuddy\skills\video-mix-edit`
- macOS / Linux：`~/.workbuddy/skills/video-mix-edit`

### 第 2 步：装 ffmpeg（必需）
- 下载 https://www.gyan.dev/ffmpeg/builds/ 的 essentials 版，解压后把 `bin` 目录加入系统 PATH；
- 或设置环境变量 `WB_FFMPEG_DIR` 指向 ffmpeg 的 `bin` 目录；
- 或设置 `WB_FFMPEG` 指向 ffmpeg.exe 本身。
验证：命令行输入 `ffmpeg -version` 能出信息即可。

### 第 3 步（可选，提升字幕准确率）：装 faster-whisper
默认字幕走 Vosk，中文偶尔有错字。想要更准，在**任意 Python 3.11** 里建干净 venv 并安装：
```bash
python3.11 -m venv asr311
asr311\Scripts\pip install "ctranslate2==4.0.0" faster-whisper
```
- 放到 `<用户目录>/.workbuddy/binaries/python/envs/asr311` 会被自动探测；
- 或设环境变量 `VME_ASR_PYTHON` 指向该 venv 的 `python.exe`。
> 注：ctranslate2 务必用 `4.0.0`，更新版在部分机器原生会崩；VAD 已默认关闭，不影响识别质量。

---

## 二、怎么用

### 方式 A：自然语言（推荐）
在 WorkBuddy 里直接说，例如：
> 把这两段拼成一条，竖屏和横屏各出一版，字幕要，结尾加"关注我"，再截张封面写"夏日穿搭"

### 方式 B：命令行
```bash
# 1) 文件夹批量混剪（三件套：竖屏+横屏+封面）
python auto_mix.py --dir 素材夹 --output 成片.mp4 --cover-text "我的混剪作品" --bgm bgm.mp3 --per-clip 6

# 2) 精确控制（写 plan.json 后）
python build.py --plan plan.json
```

---

## 三、你说什么 → 它做什么（对照表）

| 你说的话 | 技能做的事 |
| --- | --- |
| 把这几段拼成一条 | 按顺序排列，横竖比例自动统一 |
| clip1 只留前 3 秒 / 掐头去尾 | 逐段 trim（start/end） |
| 滑动转场 / 闪黑转场 | transition: slideup / fadeblack 等 |
| 要字幕 | 自动语音识别烧录中文字幕（faster-whisper→Vosk 降级） |
| 配个背景音乐 | BGM 混音，可调音量、自动淡入淡出 |
| 结尾加"关注我" | 尾部叠加 CTA 文字 |
| 竖屏和横屏各出一版 | versions:["9:16","16:9"] 一次出两版 |
| 截张封面写"夏日穿搭" | 截帧叠标题生成 cover.jpg |
| 节奏紧凑点 / 去气口 | strip_silence: true |

---

## 四、功能清单

- **横竖混比统一**：横屏+竖屏素材自动裁切/补齐成同一规格
- **转场特效**：fade / slideup / slidedown / slideleft / slideright / beat / 闪黑(fadeblack)
- **自动中文字幕**：faster-whisper（最准）→ Vosk 自动降级；产出 `.srt` 供校对
- **BGM**：任意 mp3，可调音量、自动淡入淡出
- **结尾 CTA**：自定义文字 + 时长 + 颜色
- **去气口**：可选 strip_silence，节奏更紧凑
- **横竖双版本**：plan 写 `versions:["9:16","16:9"]` 一次出两版
- **自动封面**：`cover:{enabled:true,time:1,text:"标题"}` 截帧叠标题
- **批量混剪**：`auto_mix.py` 扫文件夹按文件名排序自动拼

---

## 五、常见问题

- **提示找不到 ffmpeg**：按第 2 步装好，并确认 `ffmpeg -version` 可用。
- **字幕有错字**：属正常现象。成片旁会落一份同名 `.srt`，改完后用
  `python build.py --plan plan.json --srt 改好的.srt` 几十秒重出，不用重新识别。
- **faster-whisper 装不上 / 识别崩**：不影响使用，会自动回退 Vosk；想更准按第 3 步重试，务必 `ctranslate2==4.0.0`。
- **Vosk 中文模型**：自动字幕兜底需要 `vosk-model-small-cn-0.22`，放在
  `~/.cache/vosk-models/`，或用环境变量 `VOSK_MODEL_PATH` 指定。
