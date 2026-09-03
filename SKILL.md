---
name: video-mix-edit
agent_created: true
description: "通用视频混剪 skill。用户给 2 条及以上视频素材 + 用大白话说要求（顺序、每段保留前 N 秒 / 掐头去尾、转场特效、去气口、字幕、BGM、结尾 CTA），把它剪成一条视频；也支持直接给一个【素材文件夹】自动批量混剪（按文件名排序、可限每段时长、自动出竖屏+横屏+封面三件套）。支持横竖混比例自动统一、xfade 转场、自动中文字幕（faster-whisper 最准 / Vosk 兜底，多引擎自动降级）、可选 BGM、手工校对 SRT 复用烧录。全程本地 ffmpeg + 本地 ASR，无需外部服务。触发词：把这几段拼成一条 / 合并这几段 / 按我的要求剪这几段 / 混剪 / 把这个文件夹的视频剪成一个 / 批量混剪。"
---

# video-mix-edit — 通用视频混剪

把 2 条及以上视频素材，按用户的自然语言要求，混剪成一条视频。
核心技术：ffmpeg xfade/acrossfade 链式拼接 + 本地 ASR 字幕。**已在本机全流程实测**
（横竖混合比例统一、无声素材补音轨、Vosk 中文识别、字幕/BGM/CTA 烧录、去气口）。

## 工作流（Agent 必须遵循）

用户给素材 + 要求后，**不要直接跑**，按下面四步走：

1. **解析需求** —— 从用户的话里拆出：
   - `segments`：每条素材的文件路径、拼接顺序
   - 每条是否需要 `trim`（保留前 N 秒 → `{"end": N}`；掐头 → `{"start": N}`；取区间 → `{"start": a, "end": b}`）
   - 转场：`transition`（xfade 类型）+ 是否每段不同
   - `resolution`：竖屏 9:16 / 横屏 16:9 / 不指定就用 `auto`（沿用首段）
   - 去气口：`strip_silence` 是否开（默认关）
   - 字幕：默认开；用户说"不要字幕"才 `enabled: false`
   - BGM：用户给了音频文件才加，否则不加
   - CTA：用户要结尾引导文案才加
2. **写 plan.json** —— 按下方字段契约，把需求翻译成结构化计划（放素材同目录）。
3. **先确认再跑** —— 视频重编码成本高，把"剪辑计划"用简洁列表列给用户确认
   （特别确认比例、转场、trim 区间、输出路径）。用户说"直接剪"或确认后才执行。
4. **执行并交付** —— 运行 build.py，交付成片路径 + 同名 `.srt`（若有字幕），
   并提醒：ASR 字幕需逐句人工校对（错字/断句）。

## 调用方式

```bash
python "<技能目录>/scripts/build.py" --plan plan.json
# 人工校对 SRT 后重出片（跳过 ASR，几十秒完成）:
python build.py --plan plan.json --srt 已校对.srt
# 调试时保留中间文件:
python build.py --plan plan.json --keep-temp
```

`plan.json` 与素材放同一目录即可（相对路径基于 plan.json 所在目录解析）。
输出路径 `output` 可写绝对路径。成片旁会自动落一份同名 `.srt` 供校对。

### 批量自动混剪（给一个文件夹即可）

不想手写 plan.json 时，用 `auto_mix.py` 直接扫素材文件夹自动出片：

```bash
# 三件套（竖屏+横屏+封面），每段最多 6 秒，带 BGM 和字幕
python "<技能目录>/scripts/auto_mix.py" --dir 素材夹 --output 成片.mp4 \
    --cover-text "我的混剪作品" --bgm bgm.mp3 --per-clip 6

# 等价简写：
python auto_mix.py --dir 素材夹 --output 成片.mp4 --preset social --bgm bgm.mp3

# 只出单版本、不要字幕：
python auto_mix.py --dir 素材夹 --output 成片.mp4 --versions 9:16 --no-subtitle
```

- 素材按**文件名自然排序**（1/2/10 不乱）；`--per-clip N` 把每段掐到前 N 秒（默认整段）。
- 默认出 `9:16`+`16:9` 双版本；给 `--cover-text` 自动截封面并叠标题（三件套）。
- 字幕默认开（走 faster-whisper），`--no-subtitle` 关闭。
- 生成的 `auto_mix_plan.json` 默认出片后删；要保留微调加 `--keep-plan`。

**多版本输出**：plan 里写 `versions: ["9:16","16:9"]` 时，会一次产出
`final_竖屏.mp4` 和 `final_横屏.mp4`（文件名按分辨率自动加后缀）；字幕只在
第一版本上识别一次，其余版本复用同一份时间轴 SRT（按各自高度换算字号），省时。
**自动封面**：写 `cover` 字段后，从成片截取 `cover.jpg`（可叠加标题文字）。

## plan.json 字段契约

```jsonc
{
  "segments": [                 // 必填，≥2 条素材，按数组顺序拼接
    { "file": "clip1.mp4",
      "trim": { "end": 3 },     // 可选：保留前 3 秒（掐头去尾用 start+end）
      "strip_silence": false,   // 该段是否去静音气口（默认跟随全局）
      "transition": "slideup",  // 该段与前一段的转场类型
      "transition_duration": 0.5 // 转场时长（秒）；beat 模式固定 0.2
    },
    { "file": "clip2.mp4" }
  ],
  "resolution": "auto",         // auto 沿用首段；9:16 / 16:9 / 1:1 / 4:3 / 3:4 / "1080x1920"
  "versions": ["9:16", "16:9"], // 可选：一次出多版本（竖屏+横屏）。填了 versions 优先于 resolution
  "crop_mode": "cover",         // cover=填满裁切不留黑边(默认)；letterbox=黑边补齐
  "fps": 30,                    // 可选，默认沿用首段帧率
  "strip_silence": false,       // 全局默认去气口（默认关，节奏紧可开）
  "cover": {                    // 可选：自动截封面图（从成片截取，可叠标题）
    "enabled": true,
    "time": 0,                  // 截帧时间点（秒），默认 0
    "text": "视频标题",          // 叠加标题（留空/不填则不叠加）
    "size": 72, "color": "white",
    "output": "cover.jpg"
  },
  "silence_noise_db": -35,      // 静音判定阈值
  "silence_min_dur": 0.3,       // 静音最短时长（秒）
  "transition": "fade",         // 全局默认转场（段级可覆盖）
  "transition_duration": 0.5,   // 全局转场时长
  "subtitle": {                 // 口播字幕；默认开
    "enabled": true,
    "language": "zh",
    "engine": "auto",           // auto / faster-whisper / whisper / vosk
    "model": null,              // whisper 模型名（base/small...）
    "font": "Microsoft YaHei",
    "size": 56,                 // 实际像素高（脚本内部会做 ASS 坐标换算）
    "margin_v": 120,            // 距底部像素
    "srt": null                 // 可填已有 SRT 路径，跳过 ASR 直接烧录
  },
  "cta": {                      // 可选：结尾引导文案（drawtext 居中叠加）
    "text": "点击下方小黄车 立即抢购",
    "duration": 3.0, "color": "white", "size": 64, "box": true
  },
  "bgm": { "file": "bgm.mp3", "volume": 0.35 },  // 可选：提供了才加，循环铺满
  "output": "final.mp4"
}
```

## 转场类型（transition）

`fade`(淡入淡出) / `beat`(卡点=0.2s 短 fade) / `none`(硬切) /
`slideleft` `slideright` `slideup` `slidedown`(滑动) / `fadeblack` `fadewhite`(闪黑/闪白) /
`circlecrop` `rectcrop` `smoothleft` `smoothright` `wipeleft` `wiperight` `dissolve` 等。
第一段无转场（它是开头）。

## 每段精确裁剪（trim）

- `{"end": 3}` —— 只保留前 3 秒（最常见：开篇钩子）
- `{"start": 2}` —— 掐掉前 2 秒
- `{"start": 1, "end": 8}` —— 只取第 1~8 秒
- 不写 `trim` 则整段使用

## 字幕引擎（自动降级）

优先级：**faster-whisper（最准）→ openai-whisper → vosk（轻量兜底）**。
脚本自动探测，全部缺失时跳过字幕并提示安装命令，**不会因此失败**。

- **faster-whisper（最准，可选装）**：推荐装在独立 Python 3.11 venv
  `<用户目录>/.workbuddy/binaries/python/envs/asr311`（用任意 Python 3.11 建干净 venv，
  避开默认 venv 里可能损坏的 torch / Python 3.13 下 ctranslate2 的 Segfault）。
  asr.py 自动探测该 venv 并经子进程 `asr_fw_runner.py` 调用，**无需手动设环境变量**；
  指定别的解释器用 `VME_ASR_PYTHON` 环境变量。没装则自动回退 Vosk。
  - 关键补丁（已写进脚本，勿删）：`ctranslate2==4.0.0`（4.8.2 在本机原生 Segfault）、
    `vad_filter=False`（onnxruntime 本机原生 DLL 加载失败，VAD 需它）、
    runner 内已 `os.environ.setdefault("HF_HUB_DISABLE_XET","1")`（否则模型首次下载崩）。
  - 模型已缓存到 `~/.cache/huggingface/hub/`，faster-whisper-small/base 均在，离线可用。
- 本机若只要 Vosk 兜底：中文模型放 `~/.cache/vosk-models/vosk-model-small-cn-0.22`
  （或 `vosk-model-cn-0.22` 大模型更准）或用 `VOSK_MODEL_PATH` 指定。
- 即便用 faster-whisper，中文识别**仍可能有个别错字/断句**，交付前建议人工校对 `.srt`。
- 校对后用 `--srt` 重跑，几十秒出新片，不用重新识别。

## 去气口（strip_silence）

音画**同步**裁剪：silencedetect 检测静音段 → trim/atrim 成对裁切 → concat。
保留 0.08s 余量避免字被削。适合口播；不适合音乐/环境音素材。

## 重要提醒

- ⚠️ ASR 中文识别**必有错字/断句问题**（Vosk small 尤甚），交付前必须人工校对 `.srt`。
- ⚠️ 横竖混合素材务必和用户确认目标比例；`cover` 会裁切画面边缘。
- ⚠️ 多段拼接时长 = 各段时长之和 − 各转场时长，向用户报预期时长时按此估算。
- 脚本只负责生成与烧录，不修正语义错误。示例见 `references/examples.md`，
  字幕样式细节见 `references/subtitle_style.md`。
