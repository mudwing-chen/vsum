# Changelog

按「会改变产物」和「只是更方便」两类记。版本号遵循语义化版本。

## 0.6.0 — 2026-11-22

这一版不碰产物格式，全是「装上之后不迷路」的修补。

### 新增

- **`vsum --help` 现在给出全部 15 条命令**，按转写 / 抓取 / 查询与剪辑 / 核对与修正 /
  诊断与评测分组。之前只打主命令的 argparse usage，`find` `clip` `ocr` `fix` `audit`
  `mine` `eval` `holdout` 八个命令在帮助里一个字都没有——装完根本不知道有这些功能。
- **`vsum help <命令>`** 直达某个命令的完整参数；`vsum` 不带参数也打印这张表（退出码 0，
  以前是 usage 报错 + 退出码 2）。
- **`vsum doctor` 多一行 `hf-cache`**：模型缓存目录能不能用单独报。搬家/换盘之后
  `~/.cache/huggingface` 指向已移除磁盘是最常见的一种坏法，以前只表现为「模型明明下过
  却加载不了」，原始报错是 `[WinError 5] 拒绝访问`，看不出跟缓存有关。
- **模型加载失败时给缓存提示**：报错里直接写出当前缓存目录和改法（`set HF_HOME=...`）。
- **`vsum transcribe`**：默认命令的显式名字，`vsum transcribe x.mp4 -o out` 与
  `vsum x.mp4 -o out` 等价。写全命令名后 `vsum help transcribe` 才有东西可看。
- **`pip install "vsum[all]"`**：一次装齐 selenium / yt-dlp / rapidocr。
- README 补「全部命令」「退出码」两张表，英文 README 同步。
- README 的速度表分成两组来源（Apple Silicon 的历史数值 + 本机 P106 的复测：
  15:41 音频 103.8 s、RTF 0.11），并给出 `vsum bench` 复现命令；英文 README 补同款
  「How fast is it?」一节——之前的数字没写清是哪台机器上的。

### 改变

- **`--help` 里每个参数都有说明了**：`-o` `--lang` `--pack` `--clean` `--words` `--summary`
  `--llm-model`、`find --limit`、`audit` 的四个参数、`mine` 的四个参数、`fix --terms`
  之前要么没说明，要么只有占位符 `--limit LIMIT`。
- **`vsum export --segments` 可以直接给输出目录**，不用记 `segments.json` 在哪一层。
- **`vsum bench` 先跑本机实际会用的 `compute_type`**：Pascal（P106 这类）上 float16 是软件
  模拟的，之前排第一，等于先白等几分钟才拿到有意义的数。
- pyproject 补全元数据：Homepage / Source / Issues / Changelog / Releases 链接、维护者、
  15 个 classifiers、Python 3.9–3.13 声明。

### 修复

- **`vsum bench` 在任何 av>=16 的环境里都是坏的**：它把音频**路径**交给 faster-whisper，
  于是走 `av.decode_audio`；而 av 16 之后去掉了 `metadata_errors` 参数，faster-whisper
  1.2.x 还在传，直接 `TypeError: open() got an unexpected keyword argument`。主转写路径
  因为喂的是数组所以没事——也就是说这个 bug 只在 bench 上，而 bench 正是「想知道该用什么
  配置」的人第一个会跑的命令。现在 bench 自己用 ffmpeg 转 16k 单声道 wav、按数组喂模型，
  和主路径一致。
- **`tests/test_browser.py` 在任何 Windows 上都是红的**：两处断言拿 `expanduser()` 的原始串
  去比 `abspath()` 的返回值，`C:\a/b` 与 `C:\a\b` 必然不等。CI 的 windows job 一直在跑这个
  文件。现在期望值也走 `abspath`，两端一致。
- README 的「环境变量」被之前的编辑搞成了两张表：`WHISPER_PROMPT` 和 `VSUM_BROWSER_PROFILE`
  各出现两次，`WHISPER_COND` / `HF_HOME` 掉在第二张里。合并成一张。
- README「开发」段的测试清单漏了 `ocr` 和 `llm`（CI 是跑全的，照文档跑会少两个）。

## 0.5.0 — 2026-11-22

### 新增

- **`vsum find`**：在 `segments.json` 里全文搜索，命中项直接给 `[mm:ss]`；支持多词 AND、
  `--context N`、`--json`。
- **`vsum clip`**：按时间点从原视频剪出那一段（前后各留 1.5 秒），找不到视频就剪音频。
- **`vsum ocr`**：抽低置信帧 + OCR，输出「画面上的拼写 vs 转写」的候选与逐帧对照，
  含逐字差异（`加奖→嘉奖`、`巴塞罗大→巴塞罗那`）。可选依赖 `pip install 'vsum[ocr]'`。
  **中英都出候选**：英文走形状/读音两条通道，中文走带上下文的逐字差异
  （「有加奖」→「有嘉奖」而不是单字「加」→「嘉」，单字改文本太容易误伤）。
- **`vsum fix --from-ocr out/ocr.json`**：把 OCR 候选直接并进本次修正，不用手工抄词表。
  同一处「画面写法」在 ≥2 帧里成立、且形状/读音/逐字差异对得上 → `high`（自动写），
  只出现过一次 → `medium`（仍要人过目）。
- LLM 摘要分两级：**一句话**（模型改写，明确标注未逐字校验）+ **要点**（必须是原文片段，
  照样过回查门）。长视频按字符预算分块请求，部分块失败保留成功块。

### 改变

- **默认模型 `medium` → `small`**：首次下载 1.5 GB → 480 MB。
- **不再锁 `av<14`**：vsum 自己用 ffmpeg 抽 16k wav、用标准库 `wave` 读成数组喂模型，
  不走 faster-whisper 的解码路径，所以 av 的版本变化影响不到转写（实测 av 19 正常）。
- **转写复用 gate** 现在校验 `model + language + condition_on_previous_text`。
  以前只比模型名，`--lang en` 会静默复用中文转写、再把 md 头写成 `language: en`。
- **低置信 `⚠` 改为按排名取**：以前取阈值再 `<= cut`，logprob 并列时会把并列项全标上
  （143 段标了 26 段），现在是恰好 10%（14 段）。

### 修复

- **有 NVIDIA 卡却跑不起来**：`pip install nvidia-cublas-cu12 nvidia-cudnn-cu12` 把 DLL 放在
  包内的 `bin/`，Windows 不去那儿找，于是 `cublas64_12.dll is not found`——`doctor` 说有卡、
  转写却崩。现在启动时自动把这些目录挂进 DLL 搜索路径；真起不来会**自动退回 CPU** 并说明。
- **`vsum eval` 的 cue 配对区间以前永远跳过**：要求「参考 cue 数 == segments 段数」，
  真实字幕几乎不满足，那个能力一次数都没出过。改成按**时间重叠**配对，cue 数不必等于段数。
- **回查门在细分段上误删真引用**：分段 1.3 秒时，前一段起点也落在 1 秒内，会取错段、
  把真引用当成编造删掉（真实 14 分钟视频：概览 3 条被删 2 条）。现在先按「秒数取整相等」
  再按文本匹配。
- **>99:59 的视频引用门静默失效**：`[100:00]` 三位分钟匹配不上两位数字的正则。
- **`doctor` 冷启动 34 秒**：`import ctranslate2` 要 20–30 秒；有 `nvidia-smi` 时不再问它。
- **无音轨输入**只报 ffmpeg 的 `does not contain any stream`，现在直接说「输入没有音频流」。
- **`batch` 每条视频重建模型**：现在按 `(模型, 设备, 精度)` 缓存，N 条省 42s×(N-1)。
- **摘要三个小节重复同一句**：取候选时共享 `used` 集合，全局去重；不足两章不再输出「分章」。
- Windows 抖音抓取：`VSUM_BROWSER_ARGS` 可用 `--no-sandbox,--disable-gpu` 救 `tab crashed`。

### 文档

- README 重写：第一屏换成真实输入输出，加「和总结工具的分界」。
- `docs/example/`：一条 4 分钟视频的真实产物（连错词一起放）。
- `references/platform.md` 补「Windows 真机实测」：`SE_CACHE_PATH`、手动放驱动、
  `--profile`、`TEMP` 不可写、`tab crashed` 五个坑。

## 0.4.0 及更早

见 git log。
