# 自然语言 → plan.json 对照示例

用户怎么说，plan 就怎么写。以下均为**实测通过**的用法。

## 例 1：基础拼接 + 掐头去尾

> "把 clip1 和 clip2 拼起来，clip1 只留前 3 秒，clip2 掐掉开头 1 秒，竖屏，滑动转场"

```json
{
  "segments": [
    { "file": "clip1.mp4", "trim": { "end": 3 } },
    { "file": "clip2.mp4", "trim": { "start": 1 }, "transition": "slideup" }
  ],
  "resolution": "9:16",
  "output": "final.mp4"
}
```

## 例 2：口播字幕 + BGM + 结尾引导

> "这两段口播拼成一条竖屏视频，要字幕，配上 bgm.mp3 当背景音乐，最后加 3 秒'关注我'"

```json
{
  "segments": [
    { "file": "talk1.mp4" },
    { "file": "talk2.mp4", "transition": "fadeblack" }
  ],
  "resolution": "9:16",
  "bgm": { "file": "bgm.mp3", "volume": 0.2 },
  "cta": { "text": "关注我 了解更多", "duration": 3 },
  "output": "final.mp4"
}
```

## 例 3：去气口快节奏混剪

> "这两段口播去掉停顿的气口，剪紧凑一点，卡点转场，不要字幕"

```json
{
  "segments": [
    { "file": "a.mp4", "strip_silence": true },
    { "file": "b.mp4", "strip_silence": true, "transition": "beat" }
  ],
  "subtitle": { "enabled": false },
  "output": "final.mp4"
}
```

## 例 4：人工校对字幕后重出片

第一次跑完后成片旁有 `final.srt`。用户改完错字后：

```bash
python build.py --plan plan.json --srt final.srt
```

## 例 5：一次出竖屏+横屏双版本 + 自动封面

> "这两段拼成一条，竖屏和横屏各出一版，顺便截张封面写上标题'夏日穿搭'"

```json
{
  "segments": [
    { "file": "clip1.mp4", "trim": { "end": 3 } },
    { "file": "clip2.mp4", "transition": "slideup" }
  ],
  "versions": ["9:16", "16:9"],
  "cover": { "enabled": true, "time": 1, "text": "夏日穿搭" },
  "output": "final.mp4"
}
```
产出：`final_竖屏.mp4`（1080x1920）、`final_横屏.mp4`（1920x1080）、`cover.jpg`（带标题）。
字幕只识别一次，两版共用。

## 例 6：批量自动混剪（只给一个文件夹）

> "把这个文件夹里的视频剪成一个，竖屏横屏都出，截张封面，每段别超过 6 秒"

不用手写 plan.json，直接：

```bash
python auto_mix.py --dir ./素材 --output 成片.mp4 \
    --cover-text "我的混剪作品" --bgm bgm.mp3 --per-clip 6
# 或等价简写：
python auto_mix.py --dir ./素材 --output 成片.mp4 --preset social --bgm bgm.mp3
```

脚本会：按文件名排序 → 每段掐前 6 秒 → slideup 转场拼接 →
出 `成片_竖屏.mp4` + `成片_横屏.mp4` + `cover.jpg`（叠标题）+ 自动字幕（faster-whisper）。

## 时长心算（向用户预报用）

成片时长 = Σ各段时长 − Σ转场时长。
例：3s + 4s + 6s，两个 0.5s 转场 → 13 − 1 = 12s。
