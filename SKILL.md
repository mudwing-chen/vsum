---
name: vsum
description: 视频/音频 → 可逐秒引用的逐字稿 + 带出处的摘要。用户给出本地视频/音频文件，或抖音/B站/YouTube 链接，要求转写、总结、提炼要点、核对专有名词时使用。Use when the user wants a local video/audio file (or a Douyin/Bilibili/YouTube link) transcribed, summarized, or checked for proper nouns.
license: MIT
---

# vsum

给 agent 的操作手册：怎么跑、按什么顺序读、什么算成功、失败了怎么办。
命令细节以 `vsum help <命令>` 和 [README.md](README.md) 为准，本文件只留决策需要的东西。

范围：转写、字幕、带出处的摘要、专名核对。**不翻译、不做说话人分离、不烧字幕**；
摘要只保证「每条有出处」，不保证「摘得对」——这些别答应用户（详见 README「不做什么」）。

## 0. 开工前：确认用的是 0.6.x

```bash
vsum --version      # 需要 0.6.x
vsum doctor         # 依赖清单：MISSING 才必须装，warn / extra 是降级可用
```

PATH 上可能装着 0.3.x 的旧安装：那是 `vsum <file> -o DIR` 的单入口，
没有 `find` / `clip` / `ocr` / `fix` / `batch` / `doctor` 这些子命令，也没有 `--no-context`；
`--json` / `--quiet` 倒是有，但只覆盖单入口，
照本文件跑会一路吃 argparse 报错（`vsum find …` 会回 `the following arguments are required: -o/--out`）。
版本低于 0.6 时，在仓库根目录改用模块方式，下文所有 `vsum X` 都替换成它：

```bash
python3 -m vsum --version        # Windows: py -3 -m vsum --version
python3 -m vsum doctor
```

`doctor` 判读：

| 状态 | 含义 | 处置 |
|---|---|---|
| `MISSING` | 必需依赖缺失（ffmpeg / faster-whisper / av；python 版本不够也算） | 装好再跑；`doctor` 此时退出 3 |
| `warn` | 可选（ffprobe、模型未缓存、磁盘紧） | 不影响转写，照常跑 |
| `extra` | 只影响链接抓取（selenium/Chrome、yt-dlp）或抽帧 OCR | 本地文件路径不受影响 |

`vsum doctor --model auto` 会多一行 `plan`，说明 auto 会选哪个模型；默认 `small` 时不显示。

安装本体：`pip install -e .`，或 `uv tool install "vsum @ git+https://github.com/mudwing-chen/vsum"`。
vsum 不锁 av 版本；装不上看 [references/troubleshoot.md](references/troubleshoot.md)。

## 1. 主路径

```bash
vsum <文件或链接> -o out/
```

| 输入 | 前置 |
|---|---|
| 本地文件 | 无（mp4 / mkv / mov / m4a / wav / mp3，ffmpeg 能解的都能转） |
| 抖音 / B站 / YouTube 链接 | `pip install '.[browser,yt]'`；抖音先 `vsum login douyin` 扫码一次 |

常用参数（完整列表用 `vsum --help`、`vsum help <命令>`）：

| 参数 | 作用 | 什么时候用 |
|---|---|---|
| `--model tiny\|base\|small\|medium\|large-v3\|auto` | 默认 `small`，也可给本地模型目录；`auto` 按显存取上限（CPU 固定 small） | 没显卡别上 medium（13:47 的视频要 30 分钟以上）；先用 `vsum bench --audio <样例>` 量 RTF 再定 |
| `--batch-size N\|auto` | 默认 1 = 串行；>1 走批推理，切段与串行不同 | 长视频想快、且能接受段边界变化时用；API 不兼容会自动退回串行并告警 |
| `--vad-threshold` / `--vad-min-speech-ms` / `--vad-min-silence-ms` / `--vad-pad-ms` / `--no-vad` / `--chunk-length` | VAD 与切块参数，进 `config_sha256` | 默认即可；吞字多时降 threshold，杂音多时升 |
| `--lang zh\|en\|auto` | 默认 `zh` | `en` 喂英文提示词、`auto` 不设提示词并把检测到的语言写进产物头 |
| `--summary extractive\|llm\|none` | 摘要方式，默认抽取式 | `llm` 要配 `--llm-url <OpenAI 兼容 base> --llm-model <名字>`，失败自动退回抽取式 |
| `--llm-key` | 云服务的 API key（也读 `VSUM_LLM_KEY` / `OPENAI_API_KEY`） | 只有云服务要；本地 Ollama 不用。key 不进任何产物 |
| `--no-context` | `condition_on_previous_text=False` | 复读严重时用；代价是中文会漂成繁体、丢标点 |
| `--words` | 记录词级时间戳与概率 | 需要逐词对齐时用，更慢 |
| `--clean` | 额外写 `transcript.clean.md`（去口水词与重复） | `transcript.md` 逐字保留，不被它改写 |
| `--force` | 忽略已有转写、重下视频 | 换了设置但复用条件没变、或怀疑产物坏了；`audio.wav` 与输入 sha256 对得上时仍会复用 |
| `--json` / `--quiet` | stdout 只打一行 result / 什么都不打 | 脚本、CI 里用；日志走 stderr |
| `--browser auto\|chrome\|edge\|safari`、`--headless`、`--profile` | 链接抓取的浏览器选择 | 抖音风控时用可见窗口，别加 `--headless` |

产物在 `out/`；你要读的是 `transcript.md`、`summary.md`、`result.json`、`segments.json`，
其余（`transcript.srt/.vtt/.txt/.plain.txt`、`transcript.clean.md`、`manifest.json`、`export.source.json`）
给用户或做审计用，完整清单见 README。

## 2. 收尾判据：每次跑完逐条对

1. 退出码是 0。非 0 按下面的码表处置，别猜。
2. `out/result.json` 存在且 `ok: true`。
3. `counts.segments > 0`。0 段转写按失败处理（退出 4），不会留下空产物。
4. `summary.dropped` 是回查删掉的条数。连续出现（≥2）说明摘要没守住原文，`summary.md` 只能当草稿。
5. `warnings` 非空就把内容转述给用户：下载复用、导出修正、LLM 退避都会记在这里。
6. `counts.low_conf` / `transcript.md` 里的 `⚠` 是低置信段，引用前先核对。

退出码（与 README 一致）：

| 码 | 含义 | 你要做的 |
|---|---|---|
| 0 | 成功 | 走上面的判据 |
| 1 | `vsum find` 没有命中 | 不是错误，换关键词或换时间点 |
| 2 | 用法或输入错误（文件不存在、参数不对、JSON 坏了、时间格式不对） | 读 stderr 里的具体文件名/参数，改了再跑 |
| 3 | 缺依赖（faster-whisper / selenium / ffmpeg） | 按 `doctor` 的提示装，别重试 |
| 4 | 转写、抓取或剪片失败（含模型加载失败、找不到原视频） | 看 stderr 首行；网络/风控类见失败对照 |
| 5 | 评测不达标（`eval --min-cer` / `holdout --max-cer`），或 `batch` 有失败项 | 结果在 `eval`/`holdout` 输出或 `batch` 目录里 |
| 130 | Ctrl-C | 已写出的产物可复跑续上，不需要清目录 |

## 3. 读稿顺序

1. `summary.md` — 当草稿看。回查只保证每条有出处，不保证摘得对。
2. `transcript.md` — 结论以它为准。每段一行，`[mm:ss]` 是引用锚点（满一小时 `[H:MM:SS]`）；
   带 `⚠` 的是低置信段（全片 `avg_logprob` 明显最低的一批，约 10%；同分整组进或整组不进）。
3. `result.json` — `counts`、`summary.dropped`、`warnings`。

要**定位原文**别翻文件，直接搜：

```bash
vsum find out/ "关键词" --context 1     # 命中项带 [mm:ss] 和上下文；没命中退出 1
vsum clip out/ 03:41 -o clip.mp4        # 把那一秒前后剪出来听
```

`segments.json` 每段有 `temperature`：非 0 说明这段走了温度回退，文本比前一段更不可靠。

引用只写 `[mm:ss]`（满一小时 `[H:MM:SS]`），不要写「第 N 段」——异常 cue 被修正后，
段号与字幕条目号会错开（映射记在 `manifest.export.invalid_cues`）。

## 4. 专有名词与 OCR

转写对插件名、包名、人名几乎必错，换大模型也不解决（实测 small 与 medium 错法一致）。
**画面上出现的拼写是权威的**，所以先抽帧 OCR 再改：

```bash
pip install 'vsum[ocr]'              # 一次性
vsum ocr out/                        # 抽低置信帧 + OCR，出 ocr.md / ocr.json
vsum ocr out/ --every 30             # 扫标题卡/信息卡
vsum fix out/                        # 预演，不写文件
vsum fix out/ --apply                # 只写 high 置信
vsum fix out/ --apply --apply-medium # 连人工确认过的 medium 一起写
vsum export --segments out/segments.fixed.json -o out2/
```

`ocr.md` 两节：**候选修正**（画面上拼写 vs 转写，带相似度和时间）和**逐帧对照**（含逐字差异）。
字幕与语音常差一句，所以对照按「时间窗里最像的那段」配，不是「包含这一秒的那段」。
`medium` 默认只报不改；置信度是人工标签不是概率。`fix` 零命中时不写 `corrections.json`、
也不删已有的那份（它可能是用户的输入）。细节见 [references/terms.md](references/terms.md)。

## 5. 重写摘要

默认已产出抽取式摘要。要重写就覆盖 `summary.md`，**保留每条要点的 `[mm:ss]`**：

1. 一句话摘要
2. 故事线 / 论证结构
3. 最动人的细节 / 关键数据
4. 主题
5. 转写不确定处（标「未能核实」，不要硬编）

用户只要要点时省略第 3 节。

## 6. 批量与只抓取

```bash
vsum batch urls.txt -o out/batch      # 一行一条；也收 index.json（{"items":[{"href":...}]}）
vsum batch urls.txt -o out/batch --jobs 2 --devices cuda:0,cuda:1   # 每条一个子进程，按卡分
vsum fetch "<链接>" -o out/x          # 只下载视频，不转写
```

`batch` 默认串行；`--jobs N` 并行（每条一个子进程），`--devices` 给并行进程分卡（`cuda:0,cuda:1` 或 `0,1`，串行也认第一个）。目录名 `<平台>-<id>`、已有 `ok` 的 `result.json` 就跳过、失败不中断（退出码 5），汇总在 `batch.json`（`counts` 含 `pending`/`interrupted`，和 `items` 对得上）。并行失败时子进程日志在 `<out>/.logs/`，条目 `error` 里给了路径——先看日志再重跑。
同一个链接连续失败两次就停手，不要加大重试——抖音风控越试越久。
`fetch` 重跑会复用已下好的视频（`play.json` 的 `source_url` 对得上且文件够大），要重下加 `--force`。

## 7. 失败对照

| 症状 | 处置 |
|---|---|
| `doctor` 报 ffmpeg `MISSING` | 装 ffmpeg 并放进 PATH；抽音频要用 |
| 链接连续失败 / 风控 | 先 `vsum login douyin` 重登；还不行就请用户改用本地文件 |
| 模型下载失败（HF 连不上） | `set HF_ENDPOINT=https://hf-mirror.com`，或用 `--model <本地模型目录>` |
| 显存不足 | 转写中途 OOM 会自动降档（batch 减半 → compute → 模型 → CPU），最多 3 次，结果记在 `decode.fallbacks` 与 `warnings`；仍然失败再手动降 `--model` 或设 `WHISPER_COMPUTE=int8` |
| 字幕条目少于段数 | 正常：空文本 / 负时间 / 零时长被修正，负时间夹到 0 后若会压住下一条，那条再往后挪（`overlap_after_fix`），逐条记在 `manifest.export.invalid_cues` |
| `ffmpeg 抽不出音频` | 退出 2，不是缺依赖：输入文件损坏或不是音视频 |

更多：[references/troubleshoot.md](references/troubleshoot.md)（依赖与故障）、
[references/platform.md](references/platform.md)（浏览器、登录、风控、批量）。

## 文件地图

| 路径 | 内容 |
|---|---|
| `vsum/` | Python 包：CLI、抓取、转写、导出、摘要、术语、评测 |
| `tests/` | 回归测试，零第三方依赖 |
| `references/platform.md` | 浏览器与平台抓取、风控、批量 |
| `references/terms.md` | 抽帧核查、词表、`vsum fix` |
| `references/troubleshoot.md` | 依赖、故障对照、macOS 注意 |
