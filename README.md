# vsum

把视频转成带时间戳的逐字稿，跑在本机，默认不上传（只有 `--summary llm` 会把逐字稿发给你指定的模型端点）。产物是一个目录：逐字稿、srt/vtt 字幕、摘要，
以及一份记着输入 sha256 和解码参数的 manifest。

[English](README.en.md) · 中文

```bash
# 需要 Python 3.9+ 和 ffmpeg
uv tool install "vsum @ git+https://github.com/mudwing-chen/vsum"
# 上面装的是 main 的最新提交（不钉版本）；要可复现就 clone 后 git checkout <commit> 再 pip install -e .
# 或者：pipx install "git+https://github.com/mudwing-chen/vsum"

vsum talk.mp4 -o out/     # 转写 + 导出 + 抽取式摘要
vsum find out/ "价格"      # 这句话在第几秒
vsum clip out/ 03:41 -o clip.mp4
```

> ⚠️ **`pip install vsum` 装的不是这个项目。** PyPI 上 `vsum` 已被另一个同名的视频总结工具占用；
> 本项目的发布名是 `vsum-cli`（命令仍是 `vsum`），还没发到 PyPI，在那之前请用上面的 git 安装。

---

## 它能做什么

摘要本身没法核对：你不知道它有没有编。vsum 先产出逐字稿，再让摘要的每一条回到某一段原文；
回不去的删掉，删了几条记在 `result.json`。

| 你要做的事 | vsum 里对应什么 |
|---|---|
| 引用某句话 | 每段一行，行首 `[mm:ss]`（满一小时 `[H:MM:SS]`），直接当锚点 |
| 确认摘要没瞎编 | 摘要每条要点回查原文，回不去的删掉并计数 |
| 找「那句话在第几秒」 | `vsum find out/ "关键词"`，命中项带秒数 |
| 把那段剪出来 | `vsum clip out/ 03:41 -o clip.mp4`，前后各留 1.5 秒 |
| 换台机器重跑 | `manifest.json` 记 sha256 / 模型 / 设备 / 全部解码参数 |

> 摘要是草稿，结论以 `transcript.md` 为准。

## 它会自己发现写错了

专名和产品名语音模型几乎必错，而正确写法往往就印在画面上。`vsum ocr` 抽帧读画面来核对
（下面是一次真实运行，60 秒视频）：

```bash
$ vsum ocr out/
抽 12 帧（低置信 + 含拉丁词 + 采样）
画面与转写对照 12 处，候选修正 2 条 -> out/ocr.json
```

| 转写里的写法 | 画面上的写法 | 相似度 | 出现 |
|---|---|---|---|
| seadance | **Seedance** | 0.88 | 5 帧 → 自动改 |
| wipe | **Vibe** | 0.50 | 1 帧 → 只报不改 |

```bash
$ vsum fix out/ --from-ocr out/ocr.json --apply
文件 1 个，命中 1 处（high 1 / medium 0），写入 1 处
```

`SeaDance → Seedance` 改了；`Wipe → Vibe` 只出现过一帧，只报不改。
这条链路和竞品的「自定义词表」不是一回事：**你不用事先知道正确写法**。

## 看一眼真实产物

示例在 [`docs/example/`](docs/example/)：一条 4 分 01 秒的中文视频，`transcript.md`、`summary.md`、
`segments.json` 都是完整版。**注意它是 fixture 而不是某次真实运行的原始输出**：
文案来自真实转写，`avg_logprob` 为了构造演示场景做过规则化（只有 8 个取值），
所以用它看格式，别用它推断置信度分布。

`transcript.md`：

```markdown
[00:09] ⚠ Cloud的Fibre 5.2花了209美元。
[00:11] ⚠ GPD 602 Astra只花了不到10美元。
[00:48] 两个都是105万token的上下文。
```

- 每段一行，行首是引用锚点
- `⚠` 是全片置信度最低的一批段（按 `avg_logprob` 排名，数量约 10%；同分的要么全标要么全不标）
- 每 5 分钟自动分章

`summary.md`：

```markdown
## 概览
- Cloud的Fibre 5.2花了209美元 [00:09]
- 10个一个多月,GPD终于发新模型了 [00:00]

## 关键数字
- GPD 602 Astra只花了不到10美元 [00:11]
```

每条末尾的 `[mm:ss]` 都能回查到某一段原文；回查不过的要点会被删掉，
数量记在 `result.json` 的 `summary.dropped`。

## 安装

```bash
# 需要 ffmpeg 在 PATH 上
uv tool install "vsum @ git+https://github.com/mudwing-chen/vsum"
# 或 pipx / pip：
#   pipx install "git+https://github.com/mudwing-chen/vsum"
#   pip install "vsum @ git+https://github.com/mudwing-chen/vsum"

vsum doctor          # 逐项检查 python / ffmpeg / 模型 / 显卡 / 浏览器 / yt-dlp / OCR
```

首次运行会下载模型（默认 `small`，约 480 MB）。`doctor` 里 `warn` 行是建议（模型未缓存、磁盘空间小），不影响退出码；只有 `MISSING` 行表示必须装的依赖缺失（`extra` 是可选依赖）。国内网络下 HuggingFace 常常连不上：

```bash
export HF_ENDPOINT=https://hf-mirror.com     # Windows: set HF_ENDPOINT=...
```

也可以手动指定本地模型目录：`vsum talk.mp4 -o out/ --model D:\models\faster-whisper-small`。

## 用法

```bash
vsum talk.mp4 -o out/                       # 本地文件
vsum "https://www.bilibili.com/video/BV..." -o out/link
vsum batch urls.txt -o out/batch --jobs 2   # 每条一个子进程；失败记进 batch.json，不中断
vsum find out/ "美元" --context 1            # 搜索，带上下文（示例里 16 处命中）
vsum clip out/ 01:14 -o clip.mp4            # 剪出那一段，前后各留 1.5 秒
```

常用参数：`--model tiny|base|small|medium|large-v3|auto`（默认 small；auto 在 GPU 上取显存
上限，CPU 上固定 small）、`--lang zh|en|auto`、`--summary extractive|llm|none`、
`--batch-size N|auto`（默认 1；>1 走批推理，切段会与串行不同）、`--vad-threshold 0.5`、
`--vad-min-speech-ms`、`--vad-min-silence-ms`、`--vad-pad-ms`、`--no-vad`、`--chunk-length`、
`--llm-url` / `--llm-model` / `--llm-key`（云服务鉴权，见下）、`--clean`、`--force`、
`--json`（stdout 只打一行）、`--no-context`（见下）。
`vsum batch` 另有 `--jobs N`（默认 1，每条一个子进程）与 `--devices cuda:0,cuda:1`（给并行
进程分卡）。`--clean` 额外生成 `transcript.clean.md`（去口水词与重复），
逐字稿 `transcript.md` 保持原样；默认提示词按语言选——`--lang en` 喂英文提示词，
`auto` 不预设（不猜语言就不该塞偏置）。

### 全部命令

`vsum --help` 打印同一张表，`vsum help <命令>` 看某个命令的全部参数。

| 命令 | 干什么 |
|---|---|
| `vsum <文件\|链接> -o DIR` | 转写 + 导出 + 摘要（默认命令；写全就是 `vsum transcribe`） |
| `vsum export --segments DIR -o DIR` | 从 `segments.json` 重新导出四件套（`--segments` 可以直接给目录） |
| `vsum fetch URL -o DIR` | 只抓取：抖音 / B站 / YouTube → 本地视频 |
| `vsum login douyin` | 扫码登录，登录态存进独立 profile |
| `vsum batch LIST -o DIR` | 一串链接或清单文件，串行跑，失败不中断 |
| `vsum find DIR 关键词...` | 在逐字稿里搜，命中项带 `[mm:ss]` |
| `vsum clip DIR mm:ss -o FILE` | 按时间点剪出那一段（前后各留 1.5 秒） |
| `vsum ocr DIR` | 抽帧 OCR，用画面上的拼写核对专名 |
| `vsum fix DIR` | 按词表修正（默认只预演，`--apply` 才写盘） |
| `vsum audit DIR` | 词表命中的体检报告 |
| `vsum mine DIR` | 挖词表外的变体候选 |
| `vsum doctor` | 逐项检查 python / ffmpeg / 模型 / 显卡 / 浏览器 / yt-dlp / OCR |
| `vsum bench --audio FILE` | 在本机音频上量 RTF，比较 compute_type |
| `vsum eval --manifest FILE` | 按 manifest 算 CER 与术语召回 |
| `vsum holdout --corpus DIR` | 固定语料上的 CER 与回归比较 |

## `find` 和 `clip`：搜索与剪辑

```bash
$ vsum find out/ 价格
2 处命中（价格）
> [00:09] Cloud的Fibre 5.2花了209美元。
  [00:11] GPD 602 Astra只花了不到10美元。

剪出来听：vsum clip out/ 00:09 -o clip.mp4
```

`find` 支持多个词（全部命中才显示）、`--context N` 带上下文、`--json` 给程序用。
`clip` 从 `manifest.json` 记的原视频剪，找不到视频就剪 `audio.wav`。

默认模型是 `small`。不同机器的数字不能互相比较，所以下面两组分开写，来源和命令都列出来，
你可以用 `vsum bench` 在自己机器上重新出数。

早期在一台 Apple Silicon（CPU / int8）上跑同一条 13:47 的口播视频：

| 模型 | 耗时 | 段 / 字 | 「考研」 | 繁体字 | 能不能读 |
|---|---|---|---|---|---|
| tiny | 252.6 s | 510 段 / 4827 字 | 0 次命中（写成「烤煙」8、「考验」3、「考庆」1） | 118 | 不能，句子支离 |
| small | 504.5 s（RTF 0.61） | 145 段 / 4910 字 | 11 次全对 | 0 | 能，可当逐字稿用 |

本机（Windows 11 + NVIDIA P106-100 6 GB，CUDA / `int8_float32`）上的一条 15:41 中文口播：

| 模型 | 加载 | 转写 | RTF | 段数 |
|---|---|---|---|---|
| small | 4.2 s | 103.8 s | 0.11 | 437 |

```bash
vsum bench --audio 你的音频.wav --model small    # 在本机复现这张表
```

medium / large-v3 需要下载 1.5 GB / 3 GB，无显卡时按上面比例还要再慢一倍以上。
有 NVIDIA 卡时 `vsum/hardware.py` 会按 `nvidia-smi` 自动选 `compute_type` 并放宽模型上限，可用 `WHISPER_DEVICE` / `WHISPER_COMPUTE` 覆盖。
真实快慢用 `vsum bench --audio <你的音频>` 量，别信估算。

> **Windows + NVIDIA 少一步？** ctranslate2 需要 CUDA 运行库。装了
> `pip install nvidia-cublas-cu12 nvidia-cudnn-cu12` 的话，vsum 在**加载模型时**把
> 包内 `bin/` 临时挂进 PATH 与 DLL 搜索路径，加载完就撤掉（子进程的 PATH 不会被污染）；
> 真起不来会自动退回 CPU，`manifest.json` 的 `decode.device_source` 记成 `fallback`，
> 不会假装用了显卡。
>
> **`vsum doctor` 的 `hf-cache` 行不是 `ok`？** 模型缓存目录不可用——换盘/搬家之后
> `~/.cache/huggingface` 指向已移除磁盘的软链是最常见的一种，症状是模型明明下过却
> 加载不了，报错只有 `[WinError 5] 拒绝访问`。把缓存指到可写目录再试：
> Windows `set HF_HOME=D:\hf`，macOS/Linux `export HF_HOME=~/hf`。

另外两件事默认已开，除非你要做对照实验：

- **条件上下文**：默认 `condition_on_previous_text=True`，切句更细（实测同段句数 11→17、句均 33.8→23.4 字、逗号 16→40），摘要才挑得出整句。要退回旧行为设 `WHISPER_COND=0`。
- **繁简归一**：Whisper 对中文不约束正字法，实测 13:47 的稿子里 4827 字有 121 字（2.5%）落成繁体（讀/優/討厭）。写盘前按 Unicode Unihan 的单字简化映射统一成简体，映射表在 `vsum/data/t2s.json`（2623 对，剔除乾/藉/著/覆/於 这类在简体文本里也合法的字）。改不动的是**词级**差异和罕见的合体字：映射表逐字替换，遇到没收录的字原样保留。

## 产物

| 文件 | 内容 |
|---|---|
| `transcript.md` | YAML 头 + 每段一行 `[mm:ss]`（满一小时 `[H:MM:SS]`）+ 低置信 `⚠` + 每 5 分钟分章 |
| `transcript.txt` / `.srt` / `.vtt` / `.plain.txt` | 带时间戳 / 字幕 / 纯文本 |
| `summary.md` | 概览 / 分章 / 关键数字 / 关键词 / 存疑段，每条带 `[mm:ss]` |
| `segments.json` | 段级数据：`start` `end` `text` `avg_logprob` `no_speech_prob` `compression_ratio` `temperature` |
| `manifest.json` | 输入 sha256、解码参数（含 `config_sha256`、`hf_endpoint`）、模型、设备、产物清单、告警（`warnings` 是句子列表，逐条明细在 `export`）；`input.path` / `audio.path` 在产物目录内时只记文件名（目录外的输入保留原样，定位要用） |
| `export.source.json` | 导出所用的 segments 路径 + sha256 + cue 数（字幕自己证明不了出处） |
| `result.json` | 机器可读结论：`ok` `counts` `reused` `summary{mode,bullets,dropped,overview_verified}` `sha256` `warnings`；`out` 只记目录名。**最后写入**，进程中途失败时目录里不会有它 |

## 摘要怎么保证有出处

默认**抽取式**：候选句按「含数字 / 含拉丁词 / 含词表术语 / 含提示词 / 长度」打分，
三个小节依次取还没用过的句子（全局去重），然后过一道硬门：要点必须是**某段原文（或合并后的短段块）**
的子串，且时间戳落在该块的起止范围内（前后各留 1 秒），否则删掉并记进 `dropped`。
短句会先按 `merge_short()` 合并成块再校验，所以一条要点可以跨两三段原话。

它保证每条要点都有出处，不保证摘得对。

要真正"写出来"的摘要，用 LLM：

```bash
vsum talk.mp4 -o out/ --summary llm \
  --llm-url http://127.0.0.1:11434/v1 --llm-model qwen2.5:7b
```

LLM 模式分两级：

- **一句话**（模型改写，不保证逐字准确，会明确标注）
- **要点**（必须是原文片段，照样过回查门）

本地 Ollama / LM Studio 不用鉴权；OpenAI、DeepSeek 这类云服务要带 key：`--llm-key sk-...`，
也可以只设环境变量 `VSUM_LLM_KEY` 或 `OPENAI_API_KEY`（环境变量优先于参数：参数会出现在
同机其它用户的进程表里）。vsum 只发 `Authorization: Bearer <key>`，不把 key 写进日志、
`manifest.json` 或 `result.json`。

发送目标是 `--llm-url` 的 origin：只接受 **https**，或指向本机的 http（`127.0.0.1` /
`localhost` / `::1`）。重定向只跟随**同源**跳转——换域名、换协议、换端口一律拒绝，
否则一次网关跳转就能把 key 交给别人的域名。

长视频会按字符预算分块请求，部分块失败不影响其他块，失败情况记进 `warnings`。
LLM 不可用时自动退回抽取式，并把原因一起记进 `warnings`——包括 HTTP 状态码和服务端原话
（比如 Ollama 装坏了会直接写 `error starting llama-server: ... not found`），
不然你只会看到「已退回抽取式」，不知道该修什么。

## 中文转写的一个关键开关

`condition_on_previous_text` 默认 **开**：模型会沿用上文，输出简体、带标点、句子完整。
关掉（`--no-context`）更不容易在长音频上复读，但实测会漂成繁体、丢句末标点，
摘要是按标点切句的，跟着一起变差。

## 术语修正（要自己维护词表）

转写对专有名词（产品名、人名、版本号）几乎必错，而答案通常印在画面上。
换更大的模型不解决这个问题：实测 `small` 与 `medium`、CPU 与 GPU，错法完全一致。
所以 vsum 的做法是把画面取回来对照：

```bash
pip install 'vsum-cli[ocr] @ git+https://github.com/mudwing-chen/vsum'   # 可选：抽帧 OCR（rapidocr + onnxruntime）
vsum ocr out/                    # 抽最值得看的帧，OCR，和转写并排列出来
vsum ocr out/ --every 30         # 也可以按间隔扫一遍，找标题卡/信息卡
```

产物：

- `ocr.md`：逐帧对照（`转写: Cloud 的 Fibre 5.2` / `画面: Claude 的 Fable 5.2`），
  以及「可能该改的拼写」候选表（画面上的写法 + 相似度 + 出现次数 + 时间）
- `ocr.json`：同样内容的机器可读版本
- `frames/`：抽出来的 jpg，可以直接看

**中英文都出候选**，两套机制：

| 语言 | 依据 | 例子 |
|---|---|---|
| 英文 | 形状相似度 ≥0.8，或读音骨架相同（Soundex） | `Cloud`→`Claude`、`GPD`→`GPT` |
| 中文 | 带一个上下文的逐字差异 | `有加奖`→`有嘉奖`、`罗大`→`罗那` |

中文为什么不给单字：`大`→`那` 这种替换会命中别的句子。带上前后各一个字，
就变成只在那个词里生效的替换；单字差异只在 `ocr.md` 里展示，不进候选。

候选只是候选，但可以直接接回修正（不用手抄词表）：

```bash
vsum fix out/ --from-ocr out/ocr.json           # 预演：把 OCR 候选并进本次修正
vsum fix out/ --from-ocr out/ocr.json --apply   # 写盘：>=2 帧都看到的才自动改
vsum fix out/                    # 只用词表，预演
vsum fix out/ --apply            # 只写 high 置信
vsum fix out/ --terms 我的词表.json --apply
vsum audit out/                  # 词表命中的体检报告
vsum mine out/                   # 挖词表外的变体候选（需人工确认）
```

`data/terms.json` 是**示例词表**（AI 工具名），不是通用词表。`confidence` 是人工标签
不是概率：`high` 才自动改，`medium` 只报不改（要改加 `--apply-medium`）。

## 不做什么

- **不做会议纪要**。没有说话人分离，多人抢话的场景请用飞书妙记或通义听悟：第三方实测
  它们在那件事上是 94% / 93%，vsum 现在连「谁在说」都标不出来。
- **不做在线服务**。没有账号、没有上传、没有额度限制，也没有网页版。
- **不保证摘要「对」**。只保证每条要点都能回到原文的某一秒。

## 平台

| 平台 | 状态 |
|---|---|
| macOS (arm64) | 转写、导出、摘要、fix 闭环实测通过 |
| Linux | CI 跑全部测试 |
| Windows | P106 + Win11 实测：转写 / 导出 / find / clip 通过；抖音短链 → detail 接口 → 下载 → 转写 全链路通过（Edge 158） |

链接抓取：抖音走 Chrome / Edge 的页面接口（先 `vsum login douyin` 扫码一次），
B站 / YouTube 走 yt-dlp（`pip install 'vsum-cli[yt] @ git+https://github.com/mudwing-chen/vsum'`）。
注意发行名是 `vsum-cli`：装 extras 要用 `包名[extra] @ git+…` 的写法，别裸装 `vsum[yt]`（那是别人的项目）。

## 已知限制

- **转写不可复现**：解码带温度回退，同一段音频 `--force` 再跑，段数与文本都可能变；
  换显卡/换精度也会变。要复用就保留 `segments.json`，复用前会校验输入 sha256、
  模型、语言、context 设置。
- **LLM 摘要只用协议级假服务验证过**（起一个 OpenAI 兼容的本地服务，验分块、解析、回查、
  两级渲染），没在真实模型上做过质量评估——它写得好不好取决于你接的模型。
- **术语指标没有真人标注集**：仓库里的漏报是回归断言，不是准确率。
- **抽帧 OCR 只是候选**：它给的是「画面上写的是什么」，要不要改仍由人判断。
- 中文繁简混排无法完全避免（取决于模型与上下文设置）。
- 抖音风控阈值未知：连续失败别硬试，`batch` 串行无退避。
- **`--model` 接受任意 HuggingFace 仓库名**（含镜像上的第三方微调）：这是有意的逃生口，
  但 vsum 不校验权重摘要、也不记 revision。要可复现就先下到本地再用 `--model <目录>`。
- **`batch --jobs` 的子进程继承环境变量**（除 `VSUM_LLM_KEY` / `OPENAI_API_KEY`）；
  每条链的完整 stdout/stderr 落在 `<out>/.logs/`，失败时会把日志路径写进 `batch.json`。

## 环境变量

| 变量 | 作用 | 默认 |
|---|---|---|
| `WHISPER_DEVICE` | `cpu` / `cuda`，覆盖自动探测 | 自动 |
| `WHISPER_COMPUTE` | `int8` / `int8_float32` / `float16` / `int8_float16` | 按显卡选 |
| `WHISPER_COND` | 设 `0` 关掉条件上下文（旧行为，便于对照） | 开 |
| `WHISPER_PROMPT` | 覆盖默认解码提示词 | 内置简中提示 |
| `WHISPER_HOTWORDS` | 设 1 时把词表拼进解码提示词（实验性） | 关 |
| `WHISPER_WORDS` | 设 1 时记录词级时间戳与概率 | 关 |
| `HF_ENDPOINT` | HuggingFace 镜像 | 官方 |
| `HF_HOME` / `HF_HUB_CACHE` | 模型缓存位置（`vsum doctor` 会查） | HuggingFace 默认 |
| `VSUM_BROWSER_PROFILE` | 抓链接用的浏览器 profile | `~/.cache/vsum/chrome` |
| `VSUM_BROWSER_ARGS` | 追加浏览器启动参数，逗号分隔；受限环境里 `--no-sandbox,--disable-gpu` 能救 `tab crashed` | 空 |
| `SE_CACHE_PATH` | selenium 驱动缓存目录（默认目录不可写时改这里） | `~/.cache/selenium` |
| `VSUM_IO_ENCODING` | 输出被重定向/接管道时的编码；父进程按 locale 解码会 `UnicodeDecodeError`，那时设成 `gbk` 等本机编码 | `utf-8` |

## 退出码

| 码 | 含义 |
|---|---|
| 0 | 成功 |
| 1 | `vsum find` 没有命中（便于脚本判断，不算错误） |
| 2 | 用法或输入错误（文件不存在、参数不对、JSON 坏了、`clip` 的时间格式不对或不在任何一段里） |
| 3 | 缺依赖或依赖起不来（没装 faster-whisper / selenium / ffmpeg、CUDA 库缺 DLL、模型加载失败） |
| 4 | 转写、抓取或剪片失败（VAD 全滤掉、OOM 降档后仍失败、下载失败、找不到原视频） |
| 5 | 评测不达标（`eval --min-cer` / `holdout --max-cer`），或 `batch` 有失败项 |
| 130 | Ctrl-C 中断 |

## 开发

```bash
python -m py_compile vsum/*.py tests/*.py
for t in tests/test_*.py; do python "$t" || exit 1; done
```

测试零第三方依赖（不下载模型、不起浏览器），CI 在 ubuntu 和 windows 各跑一遍。
清单是 glob 出来的，加测试文件不用再改 CI。

## License

MIT
