# CLI 参考

查表用。SKILL.md 只写决策，参数与码表在这里；两者冲突时以
`vsum help <命令>` 和 [README.md](../README.md) 为准。

## 全部命令

| 命令 | 干什么 |
|---|---|
| `vsum <文件\|链接> -o DIR` | 转写 + 导出 + 摘要（默认命令；写全就是 `vsum transcribe`） |
| `vsum export --segments DIR -o DIR` | 从 `segments.json` 重新导出四件套（`--segments` 可直接给目录） |
| `vsum fetch URL -o DIR` | 只抓取：抖音 / B站 / YouTube → 本地视频 |
| `vsum login douyin` | 扫码登录（会开可见窗口），登录态存进独立 profile |
| `vsum batch LIST -o DIR` | 一串链接或清单文件，默认串行，失败不中断 |
| `vsum find DIR 关键词...` | 在逐字稿里搜，命中项带 `[mm:ss]` |
| `vsum clip DIR mm:ss -o FILE` | 按时间点剪出那一段（前后各留 1.5 秒） |
| `vsum ocr DIR` | 抽帧 OCR，用画面上的拼写核对专名 |
| `vsum fix DIR` | 按词表修正（默认只预演，`--apply` 才写盘） |
| `vsum audit DIR` | 词表命中的体检报告 |
| `vsum mine DIR` | 挖词表外的变体候选 |
| `vsum doctor` | 逐项检查 python / ffmpeg / 模型 / 显卡 / 浏览器 / yt-dlp / OCR / 磁盘 |
| `vsum bench --audio FILE` | 在本机音频上量 RTF，比较 compute_type |
| `vsum eval --manifest FILE` | 按 manifest 算 CER 与术语召回 |
| `vsum holdout --corpus DIR` | 固定语料上的 CER 与回归比较 |

## 默认命令与 `transcribe` 的参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `-o, --out DIR` | 无（必填） | 产物目录，不存在会建 |
| `--model NAME` | `small` | `tiny/base/small/medium/large-v3/auto`，也可给本地模型目录；`auto` 在 GPU 上按显存取上限、CPU 上固定 `small` |
| `--lang CODE` | `zh` | `zh` / `en` / `auto`；默认解码提示词按它选，`auto` 不设提示词并把检测到的语言写进 `segments.detected_language` |
| `--summary MODE` | `extractive` | `extractive` / `llm` / `none` |
| `--llm-url URL` | 无 | OpenAI 兼容 base，如 `http://127.0.0.1:11434/v1` |
| `--llm-model NAME` | 无 | 云服务或本地模型名 |
| `--llm-key KEY` | 无 | 云服务鉴权；也读 `VSUM_LLM_KEY` / `OPENAI_API_KEY`。只发 `Authorization: Bearer`，不写进任何产物 |
| `--batch-size N\|auto` | `1` | >1 走 `BatchedInferencePipeline`，切段与串行不同；不兼容时自动退回串行并记 `warnings` |
| `--vad-threshold F` | `0.5` | 低于它就当静音 |
| `--vad-min-speech-ms` / `--vad-min-silence-ms` / `--vad-pad-ms` | 引擎默认 | 吞字多时降 speech 门槛，杂音多时升 silence 门槛 |
| `--no-vad` | 关 | 完全关掉 VAD（长静音视频会变慢、可能产生幻觉段） |
| `--chunk-length S` | 引擎默认 | 批推理的切块长度 |
| `--words` | 关 | 记录词级时间戳与概率 |
| `--no-context` | 关（即开条件上下文） | 复读严重时用；代价是中文会漂成繁体、丢标点 |
| `--clean` | 关 | 额外写 `transcript.clean.md`（去口水词与重复），`transcript.md` 不变 |
| `--force` | 关 | 忽略已有转写与已下好的视频，重跑 |
| `--json` | 关 | stdout 只打一行 result，日志走 stderr |
| `--quiet` | 关 | stdout 什么都不打 |
| `--browser auto\|chrome\|edge\|safari` | `auto` | 链接抓取用的浏览器 |
| `--headless` | 关 | 无窗口；抖音风控时别用 |
| `--profile DIR` | 平台默认 | 浏览器 profile 目录 |
| `--terms FILE` | 内置词表 | 术语表 JSON |
| `--pack FILE` | 无 | 额外词表包 |

`vsum batch` 另有 `--jobs N`（默认 1，每条一个子进程）与 `--devices cuda:0,cuda:1`
（给并行进程分卡，串行也认第一个）。

`vsum ocr` 另有 `--every S`（均匀抽帧）、`--times 00:10,00:30`（指定时间点，优先级最高）、
`--limit N`（默认 24）。`vsum find` 另有 `--context N`、`--limit N`、`--json`。
`vsum fix` 另有 `--apply`、`--apply-medium`、`--from-ocr`、`--json`。

## 环境变量

| 变量 | 作用 | 默认 |
|---|---|---|
| `WHISPER_DEVICE` | `cpu` / `cuda` | 自动探测 |
| `WHISPER_COMPUTE` | `int8` / `int8_float32` / `float16` / `int8_float16` | 按显卡选（CPU 上会自动换成 `int8`） |
| `WHISPER_COND` | 设 `0` 关掉条件上下文 | 开 |
| `WHISPER_PROMPT` | 覆盖按语言选的内置提示词 | 内置 |
| `WHISPER_HOTWORDS` | 设 `1` 把词表拼进解码提示词（实验性） | 关 |
| `WHISPER_WORDS` | 设 `1` 记录词级时间戳 | 关 |
| `HF_ENDPOINT` | HuggingFace 镜像，如 `https://hf-mirror.com` | 官方 |
| `HF_HOME` / `HF_HUB_CACHE` | 模型缓存位置（`vsum doctor` 会查） | HuggingFace 默认 |
| `VSUM_BROWSER_PROFILE` | 抓链接用的浏览器 profile | `~/.cache/vsum/chrome` |
| `VSUM_BROWSER_ARGS` | 追加浏览器参数，逗号分隔（受限环境用 `--no-sandbox,--disable-gpu`） | 空 |
| `SE_CACHE_PATH` | selenium 驱动缓存 | `~/.cache/selenium` |
| `VSUM_LLM_KEY` / `OPENAI_API_KEY` | `--llm-key` 的替代 | 无 |

## 产物

| 文件 | 内容 |
|---|---|
| `transcript.md` | YAML 头 + 每段一行 `[mm:ss]`（满一小时 `[H:MM:SS]`）+ 低置信 `⚠` + 每 5 分钟分章 |
| `transcript.txt` / `.srt` / `.vtt` / `.plain.txt` | 带时间戳 / 字幕 / 纯文本 |
| `transcript.clean.md` | `--clean` 才有；去口水词与重复，不带术语附录 |
| `summary.md` | 概览 / 分章 / 关键数字 / 关键词 / 存疑段，每条带 `[mm:ss]` |
| `segments.json` | 段级数据：`start` `end` `text` `avg_logprob` `no_speech_prob` `compression_ratio` `temperature` |
| `manifest.json` | 输入 sha256、解码参数、模型、设备、产物清单、告警（`warnings` 是句子列表，逐条明细在 `export`） |
| `export.source.json` | 导出所用 segments 的路径 + sha256 + cue 数 |
| `result.json` | 机器可读结论：`ok` `counts` `reused` `summary{mode,bullets,dropped}` `sha256` `warnings` |
| `play.json` | 链接抓取才有：`source_url`、平台、id |
| `ocr.md` / `ocr.json` | `vsum ocr` 的候选修正与逐帧对照 |
| `avg_logprob` 低的一批 | `result.counts.low_conf` 与 md 里的 `⚠` 同一批段（约 10%，同分整组进或整组不进） |

## 退出码

| 码 | 含义 |
|---|---|
| 0 | 成功 |
| 1 | `vsum find` 没有命中（不算错误） |
| 2 | 用法或输入错误（文件不存在、参数不对、JSON 坏了、`clip` 的时间格式不对或不在任何一段里、`segments.json` 为空） |
| 3 | 缺依赖（没装 faster-whisper / selenium / ffmpeg） |
| 4 | 转写、抓取或剪片失败（含模型加载失败、找不到原视频、0 段转写） |
| 5 | 评测不达标（`eval --min-cer` / `holdout --max-cer`），或 `batch` 有失败项 |
| 130 | Ctrl-C 中断 |
