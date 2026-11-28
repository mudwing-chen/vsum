# 专有名词：抽帧核查与批量校正

> 主文件只留结论。这里是抽帧、词表结构、置信度语义和候选挖掘。

语音转写对**专有名词几乎必错**：插件名、包名、变量名、人名、版本号。而这些词往往就印在画面上——输入框里的字、字幕、信息卡、安装输出。

**凡是转写里出现「听着像某个词但不敢确定」的，不要猜，抽一帧看。**

## 1. 抽哪几帧

`vsum ocr` 自己抽帧，优先抽这几类（`vsum/ocr.py` 的 `pick_times()`）：

- 低置信段：`avg_logprob` ≤ -0.20 里全片最低的一批（约 10%，同分的整组进或整组不进，超过全片四分之一就整组不进；和 `transcript.md` 里带 `⚠` 的段同一口径），取每段起点
- 含拉丁词的段：画面上的产品名常出现在念完之后，所以起点和起点 +2 秒各抽一帧
- `--every N`：从 0 开始每 N 秒均匀抽一帧，用来扫标题卡 / 信息卡 / 对比表
- `--times 12.4,58.0`：自己指定时间点，优先于上面几条

候选时间点相距不到 1 秒会合并，默认最多抽 24 帧（`--limit` 可改）。

`vsum` 转写完会打印 `LOW_CONF n=10 times=12.4,58.0,133.7`（`n` 是低置信段数，最多列 10 个时间点），也可以直接把这些时间点喂给 ffmpeg：

```bash
ffmpeg -ss 64.5 -i out/video.mp4 -frames:v 1 out/frames/t64.5.jpg
ffmpeg -ss 89.5 -i out/video.mp4 -frames:v 1 out/frames/t89.5.jpg
```

- `-ss` 传**念出该词的时间点**（秒）；抽 3–5 帧成本极低
- 想先扫一遍信息卡 / 标题页 / 对比表格：`ffmpeg -i out/video.mp4 -vf fps=1/10 out/frames/f%04d.jpg`（每 10 秒一帧）
- 帧落盘后用 `read_image` 看，**只挑含目标文字的那几张**（每张都吃上下文）

判据：**画面上出现的拼写是权威的**，覆盖转写和你的推测。画面里确实没有（纯口播、无字幕）时，才在总结里写「发音近似，未能核实」。

## 2. 批量修：同一条语料里同一个错写会重复几十次

```bash
vsum fix out/                          # 预演，只报数量
vsum fix out/ --apply                  # 只写 high
vsum fix out/ --apply --apply-medium   # 连人工确认过的 medium 一起写
vsum fix out/ --apply -v               # 逐条打印
```

原 `transcript.txt` 永远不改。`corrections.json` 里每条有行号、时间、原词、替换词、置信度、`applied`（这一处到底写没写进 fixed），抽检几行就够。
同目录有 `segments.json` 时会同时写 `segments.fixed.json`（只换 `text`，时间戳不动）：

```bash
vsum export --segments out/segments.fixed.json -o out2/
vsum eval --manifest out/manifest.json --hyp out/segments.fixed.json --ref refs/
```

`--apply` 时，某个文件这一轮零改动（0 处 high，或只剩 medium 没写）会删掉它过期的 `transcript.fixed.txt` / `segments.fixed.json`，免得下游读到旧词表的产物；`corrections.json` 不删——它也可能是你手写的输入清单，只提示不覆盖。

## 3. 置信度语义

`confidence` 是**人工标签，不是概率**：它只说明「这种写法要不要免检」。

- `high`：写法本身就能确定（`Grock` → `Grok`、`Deepseq` → `DeepSeek`），`--apply` 直接写
- `medium`：和普通词 / 文件名分不开，必须人看过再决定（裸 `cloud`、`cloud.md`、`open cloud`、`克劳得`、`克劳特`、`claudecode`），默认只报不改

早期版本有个 `escalate_if`（同一行出现 code 就把 medium 升 high），fixture 里误改过，代码已删。medium 永远只报不改，要写得加 `--apply-medium`。

`corrections.json` 的记录会带 `segment.avg_logprob` 与布尔 `low_conf`（该段在 `transcript.md` 里带 `⚠`）；顶层只有 `terms` / `total` / `high` / `medium` / `applied` / `files`，低置信条数不写进文件，只在终端打印「其中 N 处落在低置信段」。它只提示「先看哪几条」，不参与改不改的决策。

## 4. 词表

内置表是 `vsum/data/terms.json`，结构：

```json
{
  "schema": 1,
  "note": "术语表。canonical 是正确写法；variants[].surface 一律小写。",
  "terms": [
    {
      "canonical": "Claude Code",
      "variants": [
        {"surface": "cloud code", "confidence": "high"},
        {"surface": "claudecode", "confidence": "medium", "note": "也可能是 CLAUDE.md"}
      ],
      "note": "Anthropic 的 CLI 编程工具"
    }
  ]
}
```

自定义词表用 `--terms PATH`（`vsum fix` / `vsum audit` 都收）：

```bash
vsum fix out/ --terms ~/.config/vsum/terms.d/ai-zh.json --apply
```

别改包里的 `vsum/data/terms.json`——升级即覆盖。

中文 variant 有额外限制：词边界只挡 ASCII 字母数字下划线，中文没有词边界，
所以单字 variant（`云` → `云端`）会在任意合成词里命中，`termrules.load()` 直接拒绝加载，
至少要两个字。中文变体宁可写长一点，也不要指望边界匹配帮你兜。

## 5. 候选挖掘

```bash
vsum mine out/ --top 40     # 编辑距离 + Soundex 两条通道
```

它只把可疑词捞到眼前，**不直接改文本**。音素骨架很粗（`cloud` / `cloudy` 同码），误报是正常的，进词表前必须人审。

> 已知边界：词边界只挡 ASCII 字母数字下划线；`cloud_code`、`cloud9` 不会被改（宁可漏改，不可错改）。
> 中文侧同理但更严：单字 variant 在加载时就报错，中文不做子串匹配（见上一节）。
