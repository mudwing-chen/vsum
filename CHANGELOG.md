# Changelog

按「会改变产物」和「只是更方便」两类记。版本号遵循语义化版本。

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
