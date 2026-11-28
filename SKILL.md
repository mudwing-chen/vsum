---
name: vsum
description: 视频/音频 → 可逐秒引用的逐字稿 + 带出处的摘要。用户给出本地视频/音频文件，或抖音/B站/YouTube 链接，要求转写、总结、提炼要点、核对专有名词时使用。
license: MIT
---

# vsum

给 agent 的操作手册：怎么跑、先定什么、按什么顺序读、什么算成功、失败了怎么办。
参数表、产物清单、退出码表在 [references/cli.md](references/cli.md)，单个命令用 `vsum help <命令>`。

范围：转写、字幕、带出处的摘要、专名核对。**不翻译、不做说话人分离、不烧字幕**；
摘要只保证「每条有出处」，不保证「摘得对」——这些别答应用户。

## 0. 开工前：确认是哪个 vsum

```bash
vsum --version      # 要 0.6.x；低于 0.6 或没有这个命令 → 用 python3 -m vsum（Windows: py -3 -m vsum）
vsum doctor         # MISSING 才必须装；warn / extra 是能降级跑的
```

PyPI 上的 `vsum` **是另一个同名项目**（Baire 的 video-summary），装错会得到完全不同的 CLI。
本项目的安装只有两条：`pip install -e .`，或
`uv tool install "vsum @ git+https://github.com/mudwing-chen/vsum"`。
`vsum doctor` 里没有 `model_cap` / `plan` 行就是装错了：改用 `python3 -m vsum`，并提醒用户
「改名 `vsum-cli` 发到 PyPI 之后再 `pip install`」。

同名 skill 也会撞：本机若还装着别的视频总结 skill（video-summary 之类），触发词都是
「抖音/B站/YouTube + 转写/总结」。一件事只用一个 skill——调 `vsum` 命令的活归这里；
另一套的登录与链接脚本不认这里的参数，别混着用。

## 1. 先定产物落哪

vsum 只写 `-o` 给的目录，但**体积不小**：10 分钟视频带 `video.mp4` + `audio.wav` 是 200 MB 级，
`batch --jobs 2` 能到几个 G。

- 默认 `-o out/<短名>/`。**别落系统盘或用户主目录**：Windows 上优先非 C 盘（如 `D:\vsum\…`），
  macOS 上别落 `~/`。用户已有约定时先问一句「放哪个盘」。
- 开跑前看 `vsum doctor` 的 `disk` 行：低于 3 GB 就别跑长视频或批量，先问用户换目录或清理。
- 转写完成后 `video.mp4` / `audio.wav` 可以删，`segments.json` 留着就能复用（重跑只重抽音频）——
  空间紧时主动这么建议。

## 2. 主路径

```bash
vsum <文件> -o out/<名>/        # 本地文件
vsum "<链接>" -o out/<名>/      # 抖音/B站/YouTube，需要 [browser,yt]
```

**无声、纯音乐、全程没人说话的素材不要跑**：VAD 会把整段过滤掉，结果是 0 段 + 退出 4。
先确认有音轨；没有就直接告诉用户这件事做不了（要字幕只能人工听写）。

### 抖音链接必须请用户本人扫码

```bash
vsum login douyin            # 会打开可见的 Chrome/Edge 窗口等扫码，默认 300 秒超时
```

- 这一步 agent 做不了：**停下来请用户扫码**，说清会弹一个窗口、扫完窗口自己关、超时会退出。
- 扫码失败、超时或一直风控：请用户把手上的视频下到本地，然后走 `vsum <文件>`。
  不要连续重试——风控越试越久。

## 3. 收尾判据

1. 退出码 0。非 0 按 [references/cli.md](references/cli.md) 的码表处置，别猜（2 输入错、3 缺依赖、
   4 转写/抓取失败、5 评测或 batch 有失败项、130 中断）。
2. `out/result.json` 存在且 `ok: true`，`counts.segments > 0`。
3. `warnings` 非空就转述给用户：下载复用、导出修正、LLM 退避都会记在这里。
4. `counts.low_conf` 与 `transcript.md` 里的 `⚠` 同源、条数一致；不一致说明产物被改过。
5. 来源自检：从摘要里挑一条要点，`vsum find out/ "<要点文本>"` 必须命中。
   要点是原文子串，命中失败就是产物或摘要有一边不对。

## 4. 读什么、怎么读

先看 `result.json`（判据都在这），再决定读不读 `summary.md`。

**`summary.md` 只有在三条都满足时才当第一读物**：概览要点 ≥3 条、没有 ≤6 字的残句
（如「A.I行不行」）、`summary.dropped` ≤1。任一条不满足就跳过它，直接读 `transcript.md`
自己写（见 §6），并把跳过原因告诉用户。

- `transcript.md`：结论以它为准。每段一行，`[mm:ss]` 是引用锚点（满一小时 `[H:MM:SS]`）；
  `⚠` 是低置信段（全片 `avg_logprob` 最低的一批，约 10%，同分整组进或整组不进），引用前先核对。
- `segments.json` 的 `temperature` 非 0 表示这段走了温度回退，比前一段更不可靠。
- 定位原文不用翻文件：`vsum find out/ "关键词" --context 1`（没命中退出 1），
  `vsum clip out/ 03:41 -o clip.mp4` 把那一秒前后剪出来听。
- 引用只写 `[mm:ss]`，不要写「第 N 段」——异常 cue 被修正后段号与字幕条目号会错开。

## 5. 专有名词与 OCR

转写对插件名、包名、人名几乎必错，换大模型也不解决。**画面上出现的拼写是权威的**：

```bash
pip install 'vsum[ocr]'              # 一次性
vsum ocr out/                        # 抽低置信帧 + OCR，出 ocr.md / ocr.json
vsum ocr out/ --every 30             # 扫标题卡/信息卡
vsum fix out/                        # 预演，不写文件
vsum fix out/ --apply                # 只写 high 置信
vsum fix out/ --apply --apply-medium # 连人工确认过的 medium 一起写
vsum export --segments out/segments.fixed.json -o out2/
```

`ocr.md` 两节：**候选修正**（画面上拼写 vs 转写，带相似度与时间）和**逐帧对照**。
字幕与语音常差一句，所以对照按「时间窗里最像的那段」配，不是「包含这一秒的那段」。
`medium` 默认只报不改，置信度是人工标签不是概率。`fix` 零命中时既不写也不删
`corrections.json`（它可能是用户的输入）。细节见 [references/terms.md](references/terms.md)。

## 6. 重写摘要

要重写就覆盖 `summary.md`，**保留每条的 `[mm:ss]`**：

1. 一句话摘要
2. 故事线 / 论证结构
3. 最动人的细节 / 关键数据
4. 主题
5. 转写不确定处（标「未能核实」，不要硬编）

用户只要要点时省略第 3 节。

## 7. 批量与只抓取

```bash
vsum batch urls.txt -o out/batch     # 一行一条；也收 index.json
vsum batch urls.txt -o out/batch --jobs 2 --devices cuda:0,cuda:1
vsum fetch "<链接>" -o out/x         # 只下载，不转写
```

`batch` 默认串行；`--jobs N` 每条一个子进程，`--devices` 分卡（串行也认第一个）。
已有 `ok` 的 `result.json` 就跳过，失败不中断（退出码 5），汇总在 `batch.json`
（`counts` 的五项与 `items` 对得上）。并行失败的子进程日志在 `<out>/.logs/`，
条目 `error` 里给了路径——先看日志再重跑；同一链接连续失败两次就停手。
`fetch` 重跑会复用已下好的视频（`play.json` 的 `source_url` 对得上且文件够大），要重下加 `--force`。

## 8. 失败对照

| 症状 | 处置 |
|---|---|
| `doctor` 报 ffmpeg `MISSING` | 装 ffmpeg 并放进 PATH；抽音频要用 |
| 链接连续失败 / 风控 | 请用户重新扫码登录；还不行改用本地文件 |
| 模型下载失败 | `set HF_ENDPOINT=https://hf-mirror.com`，或 `--model <本地模型目录>` |
| 显存不足 | 自动降档（batch 减半 → compute → 模型 → CPU，最多 3 次，记在 `decode.fallbacks`）；仍失败再手动降 `--model` 或设 `WHISPER_COMPUTE=int8` |
| 字幕条目少于段数 | 正常：空文本 / 负时间 / 零时长被修正，逐条记在 `manifest.export.invalid_cues` |
| `ffmpeg 抽不出音频` | 退出 2，不是缺依赖：输入文件损坏或不是音视频 |

更多：[references/troubleshoot.md](references/troubleshoot.md)（依赖与故障）、
[references/platform.md](references/platform.md)（浏览器、登录、风控、批量）、
[references/cli.md](references/cli.md)（参数、产物、退出码）。

## 文件地图

| 路径 | 内容 |
|---|---|
| `vsum/` | Python 包：CLI、抓取、转写、导出、摘要、术语、评测 |
| `tests/` | 回归测试，零第三方依赖 |
| `references/cli.md` | 参数表、环境变量、产物、退出码 |
| `references/platform.md` | 浏览器与平台抓取、风控、批量 |
| `references/terms.md` | 抽帧核查、词表、`vsum fix` |
| `references/troubleshoot.md` | 依赖、故障对照、macOS 注意 |
