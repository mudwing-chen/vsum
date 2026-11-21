---
name: vsum
description: 视频/音频 → 可逐秒引用的逐字稿 + 带出处的摘要。用户给出本地视频/音频文件，或抖音/B站/YouTube 链接，要求转写、总结、提炼要点、核对专有名词时使用。
license: MIT
---

# vsum

一条命令把视频变成能引用的文字稿：每段一行带 `[mm:ss]`，摘要每条都能回查到原文。

## 0. 先看环境

```bash
vsum doctor        # python / ffmpeg / av / faster-whisper / 设备 / 浏览器，缺什么直接说
```

装了但报 `TypeError: ... 'metadata_errors'` → `pip install 'av<14'`。完整故障表见 [references/troubleshoot.md](references/troubleshoot.md)。

## 1. 主路径

```bash
vsum <文件或链接> -o out/
```

| 输入 | 说明 |
|---|---|
| 本地文件 | mp4 / mkv / mov / m4a / wav / mp3 都行，ffmpeg 能解的都能转 |
| 抖音 / B站 / YouTube 链接 | 需要 `pip install '.[browser,yt]'`；抖音先 `vsum login douyin` 扫码一次 |

产物在 `out/`：`transcript.md`、`summary.md`、`result.json`、`segments.json`、`transcript.srt/.vtt/.txt/.plain.txt`、`manifest.json`、`export.source.json`。

常用参数：`--model tiny|small|medium|large-v3`（默认 medium）、`--lang zh`、`--summary extractive|llm|none`、
`--clean`、`--force`、`--json`（stdout 只一行结果）、`--quiet`、`--browser auto|chrome|edge|safari`、`--headless`、`--profile`。

转写慢：先用 `vsum bench --audio <样例>` 量本机 RTF，再决定模型；长音频放后台跑，别在前台等。

## 2. 读稿顺序

1. `summary.md` — 当草稿看。回查只保证每条有出处，不保证摘得对。
2. `transcript.md` — 结论以它为准。每段一行，`[mm:ss]` 是引用锚点；带 `⚠` 的段是低置信（全片 `avg_logprob` 最低 10%）。
3. `result.json` — `counts`、`summary.dropped`（回查被删的条数）、`warnings`。

`segments.json` 里有每段的 `temperature`：非 0 说明这段走了温度回退，文本比前一段更不可靠。

引用只写 `[mm:ss]`，不要写「第 N 段」——异常 cue 被修正后，段号与字幕条目号会错开（映射记在 `manifest.warnings.invalid_cues`）。

## 3. 专有名词

转写对插件名、包名、人名几乎必错。**画面上出现的拼写是权威的**：`LOW_CONF` 行给的是最该抽帧的时间点。

```bash
vsum fix out/                        # 预演
vsum fix out/ --apply                # 只写 high 置信
vsum fix out/ --apply --apply-medium # 连人工确认过的 medium 一起写
vsum export --segments out/segments.fixed.json -o out2/
```

`medium` 默认只报不改；置信度是人工标签不是概率；词表结构与抽帧细节见 [references/terms.md](references/terms.md)。

## 4. 总结（重写摘要时）

`vsum` 已默认产出抽取式摘要。要重写就覆盖 `summary.md`，并**保留每条要点的 `[mm:ss]`**：

1. 一句话摘要
2. 故事线 / 论证结构
3. 最动人的细节 / 关键数据
4. 主题
5. 转写不确定处（标「未能核实」，不要硬编）

用户只要要点时省略第 3 节。要机器生成走 `--summary llm --llm-url <OpenAI 兼容 base> --llm-model <名字>`，失败会自动退回抽取式。

## 5. 批量与只抓取

```bash
vsum batch urls.txt -o out/batch      # 一行一条；也收 index.json（{"items":[{"href":...}]}）
vsum fetch "<链接>" -o out/x          # 只下载视频，不转写
```

`batch` 串行、目录名 `<平台>-<id>`、已有 `ok` 的 `result.json` 就跳过、失败不中断（退出码 5）。连续失败两次就停手，抖音风控越试越久。

## 6. 失败对照

| 症状 | 处置 |
|---|---|
| `vsum doctor` 报 ffmpeg 缺失 | 装 ffmpeg 并放进 PATH；抽音频要用 |
| 链接连续失败 / 风控 | 先 `vsum login douyin` 重登；还不行就请用户给本地文件 |
| `av` 14+ 的 `TypeError` | `pip install 'av<14'` |
| 显存不足 | 降 `--model` 或设 `WHISPER_COMPUTE=int8` |
| 字幕条目少于段数 | 正常：空文本 / 负时间 / 零时长被修正，`invalid_cues` 有逐条记录 |

更多： [references/troubleshoot.md](references/troubleshoot.md)（依赖与故障）、[references/platform.md](references/platform.md)（浏览器、登录、风控、批量）。

## 文件地图

| 路径 | 内容 |
|---|---|
| `vsum/` | Python 包：CLI、抓取、转写、导出、摘要、术语、评测 |
| `tests/` | 回归测试，零第三方依赖 |
| `references/platform.md` | 浏览器与平台抓取、风控、批量 |
| `references/terms.md` | 抽帧核查、词表、`vsum fix` |
| `references/troubleshoot.md` | 依赖、故障对照、macOS 注意 |
