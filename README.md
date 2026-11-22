# vsum

把一段视频变成**每一句都能定位到秒**的逐字稿。本地跑，不上传，产物可核对、可搜索、可剪。

[English](README.en.md) · 中文

```bash
# 需要 Python 3.9+ 和 ffmpeg（PyPI 上的 `vsum` 是同名的另一个项目，别装错）
uv tool install "vsum @ git+https://github.com/mudwing-chen/vsum"
# 或者：pipx install "git+https://github.com/mudwing-chen/vsum"

vsum talk.mp4 -o out/     # 转写 + 导出 + 抽取式摘要
vsum find out/ "价格"      # 这句话在第几秒
vsum clip out/ 03:41 -o clip.mp4
```

> ⚠️ **`pip install vsum` 装的不是这个项目。** PyPI 上 `vsum` 已被另一个同名的视频总结工具占用；
> 本仓库还没上 PyPI（计划改名 `vsum-cli` 后发布）。在那之前请用上面的 git 安装。

---

## 它和「视频总结工具」不是一类东西

大多数工具给你一段生成的摘要，你没法核对。vsum 给你**能核对的原始材料**：

| | 普通总结工具 | vsum |
|---|---|---|
| 输出 | 一段摘要 | 逐字稿 + 字幕 + 出处链 |
| 引用 | 说不出第几秒 | 每段一行 `[mm:ss]` |
| 核对 | 无 | 摘要每条要点必须能在原文找到，找不到就删并计数 |
| 搜索 | 无 | `vsum find out/ "关键词"` → 时间戳 |
| 剪片 | 无 | `vsum clip out/ 03:41 -o clip.mp4` |
| 复现 | 无 | `manifest.json` 记 sha256 / 模型 / 设备 / 全部解码参数 |

> 摘要是**草稿**，结论以 `transcript.md` 为准。这是设计选择，不是缺陷——
> 机器能保证「有出处」，保证不了「摘得对」。

## 看一眼真实产物

完整文件在 [`docs/example/`](docs/example/)（一条 4 分 01 秒的中文视频）。

`transcript.md`：

```markdown
[00:08] ⚠ Cloud的Fibre 5.2花了209美元。
[00:11] ⚠ GPD 602 Astra只花了不到10美元。
[00:48] 两个都是105万token的上下文。
```

- 每段一行，行首是引用锚点
- `⚠` 是全片置信度最低的 10%（按排名取，恰好 10%）
- 每 5 分钟自动分章

`summary.md`：

```markdown
## 概览
- Cloud的Fibre 5.2花了209美元 [00:08]
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

vsum doctor          # 逐项检查 python / ffmpeg / 模型 / 显卡 / 浏览器 / OCR
```

首次运行会下载模型（默认 `small`，约 480 MB）。国内网络下 HuggingFace 常常连不上：

```bash
export HF_ENDPOINT=https://hf-mirror.com     # Windows: set HF_ENDPOINT=...
```

也可以手动指定本地模型目录：`vsum talk.mp4 -o out/ --model D:\models\faster-whisper-small`。

## 用法

```bash
vsum talk.mp4 -o out/                       # 本地文件
vsum "https://www.bilibili.com/video/BV..." -o out/link
vsum batch urls.txt -o out/batch            # 一串链接，串行，失败不中断
vsum find out/ "价格" --context 1            # 搜索，带上下文
vsum clip out/ 01:14 -o clip.mp4            # 剪出那一段，前后各留 1.5 秒
```

常用参数：`--model tiny|base|small|medium|large-v3`（默认 small）、`--lang zh|en|auto`、
`--summary extractive|llm|none`、`--clean`、`--force`、`--json`（stdout 只打一行）、
`--no-context`（见下）。

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
| `vsum doctor` | 逐项检查 python / ffmpeg / 模型 / 显卡 / 浏览器 / OCR |
| `vsum bench --audio FILE` | 在本机音频上量 RTF，比较 compute_type |
| `vsum eval --manifest FILE` | 按 manifest 算 CER 与术语召回 |
| `vsum holdout --corpus DIR` | 固定语料上的 CER 与回归比较 |

## `find` 和 `clip`：这才是有用的部分

```bash
$ vsum find out/ 价格
2 处命中（价格）
> [00:08] Cloud的Fibre 5.2花了209美元。
  [00:11] GPD 602 Astra只花了不到10美元。

剪出来听：vsum clip out/ 00:08 -o clip.mp4
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
> `pip install nvidia-cublas-cu12 nvidia-cudnn-cu12` 的话，vsum 启动时会自动把
> 包内 `bin/` 挂进 DLL 搜索路径，不用再手动改 PATH；真起不来会自动退回 CPU 并在
> `manifest.json` 的 `decode.device_source` 记成 `fallback`，不会假装用了显卡。
>
> **`vsum doctor` 报 `hf-cache` MISSING？** 模型缓存目录不可用——换盘/搬家之后
> `~/.cache/huggingface` 指向已移除磁盘的软链是最常见的一种，症状是模型明明下过却
> 加载不了，报错只有 `[WinError 5] 拒绝访问`。把缓存指到可写目录再试：
> Windows `set HF_HOME=D:\hf`，macOS/Linux `export HF_HOME=~/hf`。

另外两件事默认已开，除非你要做对照实验：

- **条件上下文**：默认 `condition_on_previous_text=True`，切句更细（实测同段句数 11→17、句均 33.8→23.4 字、逗号 16→40），摘要才挑得出整句。要退回旧行为设 `WHISPER_COND=0`。
- **繁简归一**：Whisper 对中文不约束正字法，实测 13:47 的稿子里 4827 字有 121 字（2.5%）落成繁体（讀/優/討厭）。写盘前按 Unicode Unihan 的单字简化映射统一成简体，映射表在 `vsum/data/t2s.json`（2623 对，剔除乾/藉/著/覆/於 这类在简体文本里也合法的字）。改不动的是**词级**差异和罕见的合体字：映射表逐字替换，遇到没收录的字原样保留。

## 产物

| 文件 | 内容 |
|---|---|
| `transcript.md` | YAML 头 + 每段一行 `[mm:ss]` + 低置信 `⚠` + 每 5 分钟分章 |
| `transcript.txt` / `.srt` / `.vtt` / `.plain.txt` | 带时间戳 / 字幕 / 纯文本 |
| `summary.md` | 概览 / 分章 / 关键数字 / 关键词 / 存疑段，每条带 `[mm:ss]` |
| `segments.json` | 段级数据：`start` `end` `text` `avg_logprob` `no_speech_prob` `compression_ratio` `temperature` |
| `manifest.json` | 输入 sha256、解码参数、模型、设备、产物清单、告警 |
| `export.source.json` | 导出所用的 segments 路径 + sha256 + cue 数（字幕自己证明不了出处） |
| `result.json` | 机器可读结论：`ok` `counts` `reused` `summary{mode,bullets,dropped}` `sha256` `warnings` |

## 摘要的真相

默认**抽取式**：候选句按「含数字 / 含拉丁词 / 含词表术语 / 含提示词 / 长度」打分，
三个小节依次取还没用过的句子（全局去重），然后过一道硬门——要点必须是某段原文的子串、
且时间戳与段起点相差 ≤1 秒，否则删掉并记进 `dropped`。

它保证**每条要点都有出处**，不保证**摘得对**。

要真正"写出来"的摘要，用 LLM：

```bash
vsum talk.mp4 -o out/ --summary llm \
  --llm-url http://127.0.0.1:11434/v1 --llm-model qwen2.5:7b
```

LLM 模式分两级：

- **一句话**（模型改写，不保证逐字准确，会明确标注）
- **要点**（必须是原文片段，照样过回查门）

长视频会按字符预算分块请求，部分块失败不影响其他块，失败情况记进 `warnings`。
LLM 不可用时自动退回抽取式，不报错。

## 中文转写的一个关键开关

`condition_on_previous_text` 默认 **开**：模型会沿用上文，输出简体、带标点、句子完整。
关掉（`--no-context`）更不容易在长音频上复读，但实测会漂成繁体、丢句末标点，
摘要是按标点切句的，跟着一起变差。

## 术语修正（要自己维护词表）

转写对专有名词（产品名、人名、版本号）几乎必错，而答案通常印在画面上。
**换更大的模型不解决这个问题**——实测 `small` 与 `medium`、CPU 与 GPU，错法完全一致。
所以 vsum 的做法是把画面取回来对照：

```bash
pip install 'vsum[ocr]'          # 可选：抽帧 OCR（rapidocr + onnxruntime）
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

## 平台

| 平台 | 状态 |
|---|---|
| macOS (arm64) | 转写、导出、摘要、fix 闭环实测通过 |
| Linux | CI 跑全部测试 |
| Windows | P106 + Win11 实测：转写 / 导出 / find / clip 通过；抖音短链 → detail 接口 → 下载 → 转写 全链路通过（Edge 158） |

链接抓取：抖音走 Chrome / Edge 的页面接口（先 `vsum login douyin` 扫码一次），
B站 / YouTube 走 yt-dlp（`pip install 'vsum[yt]'`）。

## 已知限制

- **转写不可复现**：解码带温度回退，同一段音频 `--force` 再跑，段数与文本都可能变；
  换显卡/换精度也会变。要复用就保留 `segments.json`，复用前会校验输入 sha256、
  模型、语言、context 设置。
- **摘要只保证出处**，不保证摘得对。
- **术语指标没有真人标注集**：仓库里的漏报是回归断言，不是准确率。
- **抽帧 OCR 只是候选**：它给的是「画面上写的是什么」，要不要改仍由人判断。
- 中文繁简混排无法完全避免（取决于模型与上下文设置）。
- 抖音风控阈值未知：连续失败别硬试，`batch` 串行无退避。

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

## 退出码

| 码 | 含义 |
|---|---|
| 0 | 成功 |
| 2 | 用法或输入错误（文件不存在、参数不对、JSON 坏了） |
| 3 | 缺依赖（没装 faster-whisper / selenium / ffmpeg） |
| 4 | 转写或抓取失败（含模型加载失败） |
| 5 | 评测不达标（`eval --min-cer` / `holdout --max-cer`） |
| 130 | Ctrl-C 中断 |

## 开发

```bash
python -m py_compile vsum/*.py tests/*.py
for t in terms exports eval cli loop transcribe fetch hardware holdout browser summary p2 batch lookup ocr llm; do
  python "tests/test_$t.py" || exit 1
done
```

测试零第三方依赖（不下载模型、不起浏览器），CI 在 ubuntu 和 windows 各跑一遍。

## License

MIT
