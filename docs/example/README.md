# docs/example

一条 4 分 01 秒的中文视频（AI 新模型测评）的真实输出，用来给第一次用的人看格式。

| 文件 | 说明 |
|---|---|
| `transcript.md` | **节选**（前 90 秒）。完整版每段一行，带 `[mm:ss]`、低置信 `⚠`、每 5 分钟分章 |
| `summary.md` | 完整。抽取式，每条要点带 `[mm:ss]`，回查不过的已被删 |
| `segments.json` | 完整。段级 `avg_logprob` / `no_speech_prob` / `compression_ratio` / `temperature` |

## 这份输出能说明什么

1. **引用是逐段可查的**：`[00:08]` 就是第 8 秒，不是"大概在开头"。
2. **摘要每条都带出处**，且都是原文子串（回查门要求时间差 ≤1 秒）。
3. **它确实会错**：`Cloud` 应该是 `Claude`、`GPD` 应该是 `GPT`、`Sau` 应该是 `Sora`。
   这不是 bug，是 480 MB 小模型对专有名词的常态——`vsum fix` 就是干这个的，
   但词表得你自己加（包里的 `terms.json` 只是示例）。
4. **摘要很短**：4 分钟 143 段，只摘出 9 条。宁可少，不可错。

## 复现

```bash
vsum 你的视频.mp4 -o out/
vsum find out/ "关键词"
```

模型、设备、解码参数都在 `manifest.json` 里；这份示例是 `small` + `cuda` + `int8_float32`。

