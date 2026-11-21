# vsum

把一条视频变成**能逐秒引用的逐字稿**和**带出处的摘要**。一条命令，本地跑，不上传。

```bash
vsum talk.mp4 -o out/
# out/transcript.md  out/summary.md  out/result.json  out/segments.json  out/transcript.srt …
```

MIT · Python 3.9+ · macOS / Linux / Windows

## 不只是「转写一下」

- **可审计**：每段一行，行首 `[mm:ss]`；低置信段标 `⚠`；`segments.json` 保留每段的 `avg_logprob`、`no_speech_prob`、`compression_ratio`、`temperature`
- **摘要带出处**：每条要点以 `[mm:ss]` 结尾，并且必须能回查到某段原文；回查不到的要点会被删掉，数量记进 `result.json` 的 `summary.dropped`
- **术语校正可审**：`vsum fix` 默认只预演，`--apply` 才写；`corrections.json` 逐条给行号、原词、替换词、置信度、改没改
- **出处链**：`export.source.json` 记下导出用的 segments 路径、sha256 和 cue 数
- **输入两种**：本地文件；或抖音 / B站 / YouTube 链接（可选依赖）
- **批量**：`vsum batch urls.txt -o out/`，串行、已完成跳过、失败不中断
- **能自测**：`vsum doctor`（缺什么）、`vsum bench`（本机 RTF）、`vsum eval` / `vsum holdout`（CER 与术语召回）

## 安装

前置：Python 3.9+（macOS arm64 建议用 3.12），`ffmpeg` 在 PATH 上。

```bash
pip install .                  # 本地转写、摘要、导出
pip install '.[browser,yt]'    # 额外：抓链接（selenium + yt-dlp）
vsum doctor
```

`pyproject.toml` 把 `av` 钉在 `>=11,<14`：faster-whisper 1.2.1 仍会调 `av.open(..., metadata_errors=...)`，这个参数在 av 14 被删了，装到新版本会在第一次转写就 `TypeError`。

## 快速开始

```bash
vsum doctor                          # 逐项检查：python / ffmpeg / av / faster-whisper / 设备 / 浏览器
vsum talk.mp4 -o out/                # 转写 + 导出 + 抽取式摘要
vsum talk.mp4 -o out/ --summary none # 只要逐字稿
vsum "https://www.bilibili.com/video/BV..." -o out/link
vsum batch urls.txt -o out/batch     # urls.txt 一行一条；也收 index.json
```

常用参数：`--model tiny|small|medium|large-v3`、`--lang zh|en|auto`、`--clean`（额外出清理后的 `.clean.md`）、
`--summary extractive|llm|none`、`--llm-url`、`--force`（不复用旧转写）、`--json`（stdout 只一行 result.json）、
`--quiet`、`--browser auto|chrome|edge|safari`、`--headless`、`--profile`。

默认模型是 `medium`；显卡与 `compute_type` 由 `vsum/hardware.py` 按 `nvidia-smi` 探测后自动选，可用 `WHISPER_DEVICE` / `WHISPER_COMPUTE` 覆盖。真实快慢用 `vsum bench --audio <你的音频>` 量，别信估算。

## 产物

| 文件 | 内容 |
|---|---|
| `transcript.md` | YAML 头（无生成时间，保证重跑字节一致）+ 每段一行 `[mm:ss]`（低置信加 `⚠`）+ 每 5 分钟分章 + 术语修订附录 |
| `summary.md` | 概览 / 分章 / 关键数字 / 关键词 / 存疑段；每条要点以 `[mm:ss]` 结尾 |
| `result.json` | `schema: 1`；`ok` `out` `source` `counts` `reused` `summary{mode,bullets,dropped}` `artifacts` `sha256` `warnings` `elapsed_sec` |
| `segments.json` | 段级：`start` `end` `text` `avg_logprob` `no_speech_prob` `compression_ratio` `temperature` |
| `transcript.txt` / `.plain.txt` | 带时间戳 / 纯文本 |
| `transcript.srt` / `.vtt` | 字幕；不按标点重切句，长 cue 只计数 |
| `manifest.json` | 输入 sha256、解码参数、`fallback_segments`、导出告警、摘要信息 |
| `export.source.json` | 导出所用 segments 的路径、sha256、cue 数、产物清单 |
| `play.json` | 仅链接输入：解析结果与下载信息 |
| `audio.wav` | 16k 单声道中间产物，复用前会用 sha256 对账 |

## 命令

| 命令 | 作用 |
|---|---|
| `vsum <文件或链接> -o DIR` | 主路径：抓取（链接时）→ 转写 → 导出 → 摘要 → `result.json` |
| `vsum batch LIST -o DIR` | 串行批量；清单可为 `urls.txt` 或 `index.json` |
| `vsum fetch URL -o DIR` | 只抓视频，不转写 |
| `vsum login douyin` | 打开可见浏览器扫码，登录态存进独立 profile |
| `vsum export --segments segments.json -o DIR` | 从已有 segments 重新导出（可吃 `segments.fixed.json`） |
| `vsum fix DIR [--apply] [--apply-medium]` | 按术语表改文本；默认预演 |
| `vsum audit DIR` / `vsum mine DIR` | 审计词表命中 / 挖候选变体（只出候选） |
| `vsum eval --manifest out/manifest.json --ref refs/ [--hyp ...]` | CER、术语召回 + Wilson 区间 |
| `vsum holdout --corpus DIR [--baseline holdout.json]` | 固定语料上的 micro/macro CER、配对 bootstrap 区间 |
| `vsum bench [--audio F] [--synth]` | 本机量 RTF，比较 compute_type |
| `vsum doctor` | 依赖与设备自检 |

## 退出码

| 码 | 含义 |
|---|---|
| 0 | 成功 |
| 2 | 输入或参数问题（文件不存在、JSON 坏了、URL 不认识） |
| 3 | 依赖缺失（ffmpeg、faster-whisper、av、浏览器、selenium） |
| 4 | 抓取或转写失败（风控、下载失败、解码错误） |
| 5 | `batch` 结束但有失败项 |
| 130 | Ctrl-C |

## 摘要的「可回查」到底保证什么

默认抽取式：候选句按「含数字 / 含拉丁词 / 含词表术语 / 含提示词 / 长度 10–40」打分，每节取前几名。
然后过一道硬门：要点必须是某段原文的子串，且时间戳与该段起点相差 ≤1 秒；不满足就删掉。

它保证的是**每条要点都有出处**，不保证摘得对——摘要是草稿，结论以 `transcript.md` 为准。
`--summary llm --llm-url http://127.0.0.1:11434/v1 --llm-model <名字>` 会走 OpenAI 兼容的 `/chat/completions`，同样要过这道回查；失败自动退回抽取式并记 warning。

## 术语校正

```bash
vsum fix out/                                  # 预演，只报数量
vsum fix out/ --apply                          # 只写 high
vsum fix out/ --apply --apply-medium           # 连人工确认过的 medium 一起写
vsum export --segments out/segments.fixed.json -o out2/
```

原文件永远不改。`--apply` 有命中时写 `transcript.fixed.txt` 与 `segments.fixed.json`；零命中时删掉上一轮留下的过期 fixed 和 `corrections.json`，所以「fixed 存在」就代表它比 raw 新。

`confidence` 是人工标签，不是概率：`high` 的写法本身就能确定；`medium` 和普通词/文件名分不开，默认只报不改。自定义词表用 `--terms PATH`，别改包里的 `vsum/data/terms.json`（升级即覆盖）。
`vsum mine` 只把可疑词捞到眼前（编辑距离 + Soundex 两条通道），进词表前必须人审。

## 平台支持

| 平台 | 状态 |
|---|---|
| macOS (arm64) | 本机实测：转写、导出、摘要、fix 闭环、batch 本地文件 |
| Linux | CI 跑全部 13 个测试脚本 |
| Windows | CI 跑全部测试；真机链路（含浏览器抓取）没有跑过，别当已验证 |

链接抓取：抖音走 Chrome / Edge 的页面接口，独立 profile（默认 `~/.cache/vsum/chrome`，Windows 为 `%LOCALAPPDATA%\vsum\chrome`），`vsum login douyin` 扫码一次；B站 / YouTube 走 yt-dlp（限 1080p、合并 mp4）。Safari 只在显式 `--browser safari` 时使用，且要先在 Safari 开发菜单里允许远程自动化。

## 已知限制

- **转写不可复现**：解码带温度回退，同一段音频 `--force` 再跑，段数与文本都可能变。要复用就保留 `segments.json`；复用条件是输入 sha256、模型、segments 非空都对得上。
- **术语指标没有真人标注集**：仓库里的 6 处漏报是回归断言，不是准确率；precision/F1 需要逐处人工标「该不该改」，本项目没有。
- **摘要回查只保证出处**，不保证摘得对。
- **抖音风控阈值未知**：连续失败别硬试，越试越久；`batch` 目前串行、无退避。
- **实验性**：`WHISPER_HOTWORDS=1`（把词表拼进解码提示词）、术语 OCR 候选（未实现）、`--pack` 之外的 L2 能力。
- 字幕条目数可能少于 `segments.json` 段数：空文本、负时间、零时长会被修正并逐条记进 `manifest.warnings.invalid_cues`。

## 环境变量

| 变量 | 作用 | 默认 |
|---|---|---|
| `WHISPER_DEVICE` | `cpu` / `cuda`，覆盖自动探测 | 自动 |
| `WHISPER_COMPUTE` | `int8` / `int8_float16` / `float16` / `int8_float32` | 按显卡选 |
| `WHISPER_HOTWORDS` | 设 1 时把词表拼进解码提示词（实验性） | 关 |
| `WHISPER_WORDS` | 设 1 时记录词级时间戳与概率 | 关 |
| `WHISPER_PROMPT` | 覆盖默认解码提示词 | 内置简中提示 |
| `VSUM_BROWSER_PROFILE` | 抓链接用的浏览器 profile 目录 | `~/.cache/vsum/chrome` |
| `HF_HOME` / `HF_HUB_CACHE` | 模型缓存位置（`vsum doctor` 会查） | HuggingFace 默认 |

## 开发

```bash
python3 -m py_compile vsum/*.py tests/*.py
for t in terms exports eval cli loop transcribe fetch hardware holdout browser summary p2 batch; do
  python3 "tests/test_$t.py" || exit 1
done
```

测试零第三方依赖（不下载模型、不起浏览器），CI 在 ubuntu（3.9 / 3.12）与 windows（3.12）各跑一遍。

## License

MIT
