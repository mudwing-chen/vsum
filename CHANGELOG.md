# Changelog

按「会改变产物」和「只是更方便」两类记。版本号遵循语义化版本。

## 未发布 — 审计修复

一轮外部审计把「按文档用」和「按默认用法用」的差距摊开了：默认路径（`vsum x.mp4 -o out`）
一直是好的，最扎眼的几个问题全藏在文档写着、但测试没覆盖的分支里。

### 新增 · 转写的自动档

- `--model auto`：GPU 上取 `hardware.model_cap`（24 GB → `large-v3`），CPU 上固定
  `small`；默认仍是 `small`，现有行为不变。`vsum doctor --model auto` 会打印会选哪个。
- `--batch-size N|auto`：`N>1` 走 `BatchedInferencePipeline`；`auto` 按显存给 16/8/4，
  CPU 禁用。默认 1 = 原来的串行路径。API 不兼容（旧版 faster-whisper 不接
  `initial_prompt`）时打印 warning 退回串行，`manifest.decode.batch_fallback` 记原因。
- 转写中途 OOM 自动降档：batch 减半 → compute 降级 → 模型降档 → CPU，最多 3 次，每次从头
  解码，不落半截稿；`decode.fallbacks` 与 warnings 记每一次。全阶梯失败才退出 4。
- `vsum batch --jobs N --devices cuda:0,cuda:1`：每条链一个子进程，按 `CUDA_VISIBLE_DEVICES`
  分卡；`batch.json` 增量写盘，Ctrl-C 先写盘再退出。
- VAD 与切块参数：`--vad-threshold`、`--vad-min-speech-ms`、`--vad-min-silence-ms`、
  `--vad-pad-ms`、`--no-vad`、`--chunk-length`；这些进 `config_sha256`，改了就重转写。

### 会改变产物

- **批推理会改变产物**（opt-in）：`--batch-size` > 1 时按窗口独立解码，
  `condition_on_previous_text` 强制关闭，段边界、时间戳与串行不完全一致。默认 1，不受影响。
- **`vsum transcribe` 成功也退出 1**：`main()` 的 SUB 分支写的是 `return rc or 0`，而该命令
  原样返回 `pipeline.run()` 的 result 字典，`sys.exit(dict)` 把字典打到 stderr 并以 1 退出；
  `--json` / `--quiet` 也只在默认分支生效。按 README 写全命令名的脚本全解析失败。现在子命令
  与默认命令走同一个入口，非 int 返回值一律算成功。
- **摘要回查门和被校验的段不是同一批**：prompt 喂原始段、校验用 `merge_short()` 的合并块，
  而合并块只记首段 start。LLM 引用块中后部原句时时间差超 1 秒，真原文被整条删掉，最坏情况
  `## 要点` 整节为空。回查改为「块内任一段命中即可」，并回落到原始段。
- **低置信标记退化**：`k = max(1, int(n*0.1))` 让 1–9 段的短稿恒定标 1 条，并列按段号截断，
  于是示例的 143 段里 14 条最低值并列、标记全落在开头 30 秒。现在先按 `avg_logprob` 绝对下限
  筛，再取排名 cutoff，同分整组进/整组出（超过 25% 就整组不进）。
- **时间两套口径**：`mmss()` 用 `int()`、`srt_time()` 用 `round()`，59.9996s 在 md 是
  `[00:59]`、在 SRT 是 `00:01:00,000`。现在统一四舍五入，满一小时另给 `H:MM:SS`。
- **manifest 与 result 的 warning 语义分裂**：manifest 里是导出告警字典、result 里是字符串
  列表，LLM 退避告警只进 result。现在两边都是同一份字符串列表；`result.json` 的 `sha256`
  从 3 个产物扩到全部产物；manifest 先写、`result.json` 最后写，中途失败不再留下半新半旧。
- **评测会给出假满分**：`isdigit()` 过滤字幕序号把「2024」这种正文整条丢掉，VTT 的 `cue-1`
  标识行被拼进上一条正文；语料目录只有一个候选文件时回退到它，可能拿假设本身当参考
  （CER 恒 0、`--max-cer` 静默通过）。现在数字正文保留、标识行只在其紧跟时间戳时才跳过、
  挑不到参考就报错。
- **0 段转写报 ok**：VAD 过滤光或空音频时产物全空、退出 0，与 `TranscribeError` 的
  docstring 矛盾。现在算失败（退出 4），并在写盘前抛出。
- 摘要小问题：关键词对拉丁词大小写敏感（`Fable` / `fable` 分开计）、存疑段取「时间最早 5 条」
  再排序（应为全局最差 5 条）、分章可能只剩标题、LLM 的综述未校验直接进 markdown。

### 不改变产物，但会改变你的操作体验

- **`fix --apply` 零命中不再删 `corrections.json`**：它是输入（手写顶层 `corrections` 格式
  同样受支持），把「本轮没命中」当「清单过期」会在正常操作里静默删用户数据。
- **抖音下载不再毁掉上一次的视频**：先写 `.part`、校验 Content-Type / 大小 / 是否 HTML 页面，
  通过才原子替换；失败只删临时文件。同一链接重跑直接复用已下好的视频（`--force` 重下）。
  `detect()` 改成按 host 匹配，`notyoutube.com` 不再算 YouTube；yt-dlp 禁播放列表、转义 `%`。
- **复用转写不再最后一步崩**：输入 sha、模型、解码参数都对上就复用 `segments.json`，但
  manifest 仍要记 `audio.wav` 的 sha256——为省空间删掉 `audio.wav` 后重跑，会在导出、摘要、
  `result.json` 全写完之后才 `FileNotFoundError`。现在缺了就重抽音频。
- **复用条件纳入所有改变输出的解码参数**：`--words`、`WHISPER_HOTWORDS`、`WHISPER_PROMPT`、
  `WHISPER_COND` 任一变都重转写（manifest 里记 `decode.config_sha256`）。
- **文件类错误带文件名**：以前只有 `error: No such file or directory`。
- **OCR / 浏览器错误归类**：OCR 的「没 ffmpeg」「没引擎」改成 `DependencyError`（3）、
  「一帧都没抽出来」改成 `InputError`（2），不再裸 traceback + 1；登录路径 `Ctrl-C` 也会
  关浏览器（`KeyboardInterrupt` 是 `BaseException`，原来的 `except Exception` 接不住），
  页面加载超时包成 `BrowserError`。
- **`WHISPER_DEVICE=cpu` 时 `compute_type` 跟着换**：以前设备取环境变量、精度仍来自 GPU 探测，
  CPU 后端不支持 `float16`，报「模型加载失败（small / cpu / float16）」看不出根因。
- 其它边界：`lookup.parse_time` 拒绝 `1:75` 与负值；`audit` 的型号正则不再把 `I'm 3 分钟`
  当 `m-3`；`doctor` 把「模型未缓存 / 磁盘小」降为警告并补 yt-dlp 检查（缺 yt-dlp 只提示）。

### 文档与测试

- references/troubleshoot.md 删掉「pyproject 已钉 `av>=11,<14`」（实际不锁）；terms.md 的抽帧
  规则按 `ocr.py` 的实际实现重写（原文提到的 `compression_ratio` 抽段并不存在）；退出码表补齐
  1 / 2 / 4 / 5；`docs/example/` 的产物用当前代码重新生成（`transcript.md` 是完整 143 段，
  不再标「前 90 秒节选」）。
- 补测试：`vsum transcribe` 的退出码与 `--json` / `--quiet`、`fix --apply` 零命中保留清单、
  删掉 `audio.wav` 后的复用、OCR 的错误分类、文件类错误的提示内容、`--words` / `WHISPER_PROMPT`
  触发重转写、型号正则的假阳性；新增 `tests/test_audit.py`。

### 第二轮复查

第二轮复查没找到高危项，都是「按文档用会别扭、但不会毁数据」这一类：

#### 会改变产物

- **默认提示词不看语言**：`--lang en` 一直喂的是「以下是普通话视频内容。」，英文音频容易被
  带出中文偏置。现在按语言选（`en` 用英文提示、`auto` 不给提示、`zh` 不变），
  `WHISPER_PROMPT` 照旧全局生效；提示词进 `config_digest`，换了语言自然重跑。
- **`--lang auto` 的产物头写 `auto`**：md/manifest 的 `language` 现在写检测到的语言
  （`decode.detected_language`），请求值另记，复用判断仍比请求值。
- **导出**：空 `segments.json` 现在退出 2（以前写出四个空文件还退 0）；负起点被夹到 0 之后，
  若会压住下一条 cue，那条改从被夹段的结尾开始（新增 `overlap_after_fix` 记录）。
- **复用时的时长**：上次 `duration` 是 `null` 时，这次音频在手边就补探一次；
  `probe_duration()` 增加 wav 头零依赖回退，缺 ffprobe 也能读出时长。
- **清洗稿不再带「术语修订」附录**：`transcript.clean.md` 不改词表，那节只会写「无」。

#### 只是更方便

- **doctor 不再把 ffprobe 当必需依赖**：`vsum` 自己不用它（时长探测会退回 PyAV / wav 头），
  缺了记为 `warn`，不再拉高退出码。
- `--clean` 的帮助文案改成它实际做的事（生成 `transcript.clean.md`，`transcript.md` 不变）。
- `vsum help help` 打印总览并退出 0（以前报「没有这个子命令」）。
- 不认识的链接不再先建目录：`vsum fetch https://example.com/x -o dir` 不会留下空 `dir`。

#### 给 agent 的手册（SKILL.md）重写

- **版本前置**：原来第 0 步直接 `vsum doctor`，而 PATH 上可能是 0.3.x 的旧安装（没有
  `doctor/find/clip/ocr/fix/batch`，也不认新参数），照着做会连续吃 argparse 报错。现在先查
  `vsum --version`，低于 0.6 就在仓库根目录改用 `python3 -m vsum`（Windows `py -3 -m vsum`）。
- **补成功判据与退出码**：新增「收尾判据」六条（rc0、`result.json.ok`、`counts.segments > 0`、
  `summary.dropped`、`warnings`、`counts.low_conf`）和完整退出码表（0/1/2/3/4/5/130 及处置）。
- **消除与 README 的漂移**：`--lang zh|en|auto` 及提示词行为、`--no-context`、`--words`、
  `--force`、`--json/--quiet`、`--browser/--headless/--profile` 都写清了「什么时候用」。
- **结构与语气**：正文统一成对 agent 的指令，产物清单/参数细节指到 README 与 references，
  SKILL 只留决策所需（跑什么、按什么顺序读、什么算成功、失败怎么处置）。
- **和 0.6.1 对齐**：0.3.x 其实有 `--json`（只是没有子命令）；`doctor` 的 `MISSING`
  还包括 `av` 与 python 版本；`--force` 不会重抽 `audio.wav`（sha256 对得上就复用）；
  `batch` 补上 `--jobs` / `--devices`；显存不足先走自动降档，再谈手动降模型；
  description 补一句英文触发，方便英文请求命中。

### 第三轮复查（转写增强上线后）

#### 会改变产物

- **`--lang auto` 在真实 faster-whisper 下必崩**：`Tokenizer` 会校验语言码，`"auto"` 不在
  `_LANGUAGE_CODES` 里，直接抛 `ValueError: 'auto' is not a valid language code`；假模型不校验，
  所以端到端测试全绿。现在 `infer_kwargs()` 把 `auto` 翻成 `None`（faster-whisper 的自动检测
  写法），manifest 仍记请求值，检测到的语言记在 `decode.detected_language`。
- **批推理永远无法复用**：manifest 记的 `condition_on_previous_text` 是实际生效的 `False`，
  复用检查却拿命令行的 `True` 去比，`--batch-size > 1` 每次重跑都重新转写。现在有
  `config_sha256` 时只信指纹，逐项比较只留给 0.6 之前没有指纹的 manifest。

#### 更安全 / 少误导

- **加载模型时 OOM**：以前 `"CUDA out of memory"` 命中 CUDA 提示词，被误判成「CUDA 起不来」
  并提示装 cublas，小显存选大模型时直接退 CPU。现在先按模型阶梯降档，实在不行才退 CPU，
  `decode.fallbacks` 记 `oom_load`。
- **OOM 后释放旧模型**：失败的那个模型还留在进程缓存里继续占显存，降档重试更容易再 OOM。
  现在 OOM 路径把它从缓存删掉并尽量 `empty_cache()`。
- **`--batch-size 0`** 不再被当成 `auto`（GPU 上会静默变成 4/16）；按「必须 >= 1」报错。
- **`--devices 0,1`** 按 CUDA 序号理解（以前只认 `cuda:` 前缀，裸数字会被塞进
  `WHISPER_DEVICE=0`，加载必失败）。
- **`--no-vad` 时不再记录没生效的 VAD 参数**：`decode.vad_parameters` 只写实际传给引擎的值。
- 文案：`--model` 帮助补上 `auto`；`--chunk-length` 说明在批推理下也生效；
  README 的 batch 示例带上 `--jobs`。

### 第四轮复查（批推理与并行上线后）

#### 会改变产物

- **低置信 `⚠` 与 `counts.low_conf` 同源**：`to_md()` 以前在 `sanitize()` 之后的 cue 上重算
  低置信段，`pipeline` 的 `counts` 却按原始段落算；只要有空文本段被丢掉，cue 号整体前移，
  `⚠` 就标到别的段上、`low_conf_segments` 也和 `result.json` 对不上。现在 cue 带 `src`
  源段号，`to_md()` 直接用 pipeline 传进来的 `meta["low_conf"]`。
- **复用转写不再复读上一轮的降档警告**：复用路径的 `decode` 来自旧 manifest，
  `batch_fallback` / `fallbacks` 是上一轮发生过的事。以前第二次跑（`reused: true`、根本没调
  模型）仍会在 `result.json` 里写「批推理不可用，已退回串行」。现在只在真正跑模型时拼这段
  警告。
- **中文单字 variant 直接拒绝加载**：词边界只挡 ASCII，单字中文变体（`云` → `云端`）会在任意
  合成词里命中，那不是修正而是污染。`termrules.load()` 现在对长度 < 2 的中文 surface 抛
  `ValueError`。当前内置表 18 条全是英文，不受影响。

#### 更安全 / 少误导 / 少浪费

- **LLM 摘要可带鉴权**：新增 `--llm-key`（也读 `VSUM_LLM_KEY` / `OPENAI_API_KEY`），请求带
  `Authorization: Bearer <key>`；key 不进日志、`manifest.json`、`result.json`。以前只发
  `Content-Type`，README 却写「cloud API 可用」，OpenAI / DeepSeek 这类必然 401。
- **`vsum batch` 的 `counts` 从 `items` 重算**：以前只初始化 `ok/skipped/failed`，中断会写
  `interrupted`、并行会留 `pending`，`total` 与 `counts` 之和不等。现在五键齐全，每次写盘
  都按明细重算。
- **并行日志挪到 `<out>/.logs/`**：子进程日志不再散在目标目录里冒充产物；成功即删、失败保留
  并在条目的 `error` 里给出路径，目标目录也不再预先创建（失败时不留空壳）。
- **`--jobs 1` 也认 `--devices`**：以前串行分支根本不看 `devices`，多卡机器上静默用第 0 张。
  现在串行也按第一个设备设 `WHISPER_DEVICE` / `CUDA_VISIBLE_DEVICES`，多卡时提示只用第一个；
  并行没给 `--devices` 时如实报「探测到 N 张卡」而不是「只认出 0 张卡」。
- **批推理回退串行后不再空跑降档阶梯**：`_run_model` 发现已退回串行时把 state 的 batch 直接
  置 1，OOM 后不会先做 4→2→1 这几次对串行毫无意义的尝试。
- **`vsum doctor --model auto` 不再报「model:auto 未缓存」**：先按显存解析成具体模型名再查
  缓存，输出 `model:auto ok → small（…faster-whisper-small）`。
- **`hf-cache` 缺失记 `warn`**：缓存目录不存在会自己重建、没下过也能自动下载，都不该让
  doctor 退出 3（真加载失败时转写本身报 3）。
- 删掉死代码 `transcribe.pick_device()`（设备判定早已统一走 `hardware.resolve()`）和
  `textnorm.traditional_chars()`。

#### 文档与测试

- `references/platform.md` 的 batch 段改成「默认串行 / `--jobs` 并行 / `--devices` 分卡」，
  补 `.logs/` 与五键 `counts`；`references/troubleshoot.md` 的 OOM 行补自动降档阶梯
  （batch → compute → 模型 → CPU，最多 3 次，记 `decode.fallbacks`）。
- `README.md` / `README.en.md` / `SKILL.md` 补 `--llm-key` 与云服务鉴权说明；
  `references/terms.md` 补中文 variant 的边界限制。
- 新测试：复用不复读旧降档警告（`test_p2`）、低置信 `⚠` 与 `counts` 同源含空段场景
  （`test_exports`）、`Authorization` 头与无 key 时不发（`test_llm`）、中文单字 variant 被拒
  （`test_terms`）、`--jobs` 的真 Popen 路径（`test_batch`：两个坏子进程、五键 counts、
  日志在 `.logs/`、不留空目录）。
- **`config_sha256` 仍按请求的 `--batch-size` 算**：`--batch-size 4` 那次
  `condition_on_previous_text` 被强制关掉、`--batch-size 1` 那次是开的，两者产物本来就不同，
  按实际跑成的 batch 重算反而会让「同一条命令」每次 fallback 之后都重转一遍。

### 第五轮复查（手册）

`SKILL.md` 是给 agent 的执行手册，这轮按「agent 真会怎么用它」重收了一遍：从 11 KB 压到 8 KB，
参数表、环境变量、产物清单、退出码表下沉到 `references/cli.md`，正文只留「默认值 + 什么时候改」。

- **`vsum login douyin` 标成人工步骤**：它会开一个可见的浏览器窗口等扫码，agent 跑到这里必须
  停下来请用户本人扫；扫码失败/风控就改用本地文件，不重试。以前手册把它当普通命令写在主路径里。
- **补产物落盘约定**：10 分钟视频带 `video.mp4` + `audio.wav` 是 200 MB 级，`batch --jobs 2`
  可以到几个 G。现在写明默认落 `out/<短名>/`、别放系统盘或用户主目录、开跑前看 `doctor` 的
  `disk` 行、转写完成后 `video.mp4` / `audio.wav` 可删而 `segments.json` 留着就能复用。
- **开工检查承认撞名**：PyPI 上的 `vsum` 是另一个同名项目（Baire 的 video-summary），装错会得到
  完全不同的 CLI；`doctor` 里没有 `model_cap` / `plan` 行就是装错了。同时写明「等改名 `vsum-cli`
  发到 PyPI」的提醒，以及和已装 video-summary 类 skill 的分工（一件事只用一个 skill）。
- **`summary.md` 不再无条件当第一读物**：概览要点 <3 条、出现 ≤6 字的残句、`dropped` ≥2，
  任一条成立就跳过它直接读 `transcript.md` 自己写。
- **补两条判据**：无声/纯音乐/无人声的素材别跑（VAD 会过滤成 0 段、退出 4），以及来源自检
  `vsum find out/ "<要点文本>"` 必须命中——要点是原文子串，命中失败说明产物或摘要有一边不对。
- 复核并加回归：`LOW_CONF n=` 与 `result.counts.low_conf` 同源（同一批 segments、同一个
  `export.low_conf_indexes()`），`test_transcribe` 断言 25 段时报的条数就是 `counts` 的值。
  之前看到的「`n=10` 而 `low_conf=2`」是 0.6.1 及更早的分位口径，已在第二轮修掉。

### 第六轮复查（第三方安全审计）

一轮 Windows 11 + cp936 环境下的外部审计（0 Critical / 1 High / 10 Medium / 19 Low）。
这轮按「信任边界」重收：谁能影响产物、哪些路径和 URL 算输入、哪里会因为环境编码崩。

#### 安全与信任边界（会改变行为）

- **LLM API key 不再跟随跨主机重定向**（High）：urllib 默认在 302 时保留 `Authorization`，
  一次网关跳转就能把长期 key 交给别人的域名。现在自定义 opener 只放行同源重定向，
  `--llm-url` 只接受 https 或指向本机的 http（明文 http 打到别的机器直接退出 2）。
- **LLM 响应有上限**：单次读入 8 MB 封顶，`Content-Type` 不是 JSON 时记 warning；
  `"sec": 1e999` 这类非有限数当成「定位不到」，不再 `OverflowError` 穿透成 traceback。
- **综述不再只验时间戳**：`--summary llm` 的「一句话」超过 200 字、或时间引用在原文里
  找不到，整段删除并记 warning；`result.json` 的 `summary.overview_verified` 明确标出
  「这句话没逐字校验」。
- **下载地址与 play.json 路径要自证**：直链只允许 https + 白名单域名 + 非内网解析结果，
  只跟随同 host:port 的重定向；`play.json` 里的 `video` 必须落在产物目录内（软链也算），
  否则不复用。以前 `urlopen` 还接受 `file://`。
- **`clip` / `ocr` 只认能自证的输入路径**：`manifest.input.path` 在产物目录内，或 sha256
  与 manifest 一致，否则退出 2——别人给的产物目录里，那个字段可以指向任意文件。
- **产物原子写**：新增 `vsum/atomic.py`（同目录临时文件 + `os.replace` + fsync），
  `manifest.json`、`result.json`、`segments.json`、`transcript.*`、`summary.md`、`ocr.*` 全部改走它。
  另外每轮开跑前先删掉旧的 `result.json`：它是「这一轮跑完了」的唯一凭据，导出中途失败时
  不能留着上一轮的 `ok: true` 让人误判成功。
- **外部二进制不再用 `shutil.which`**：Windows 上它会把当前目录前置，别人给的目录里放个
  `ffmpeg.EXE` 就能抢先执行。新增 `vsum/toolpath.py`，PATH 里剔除空项与 `.`/`..`。
- **浏览器 profile 收成 0700**，并在登录成功后提示「这个目录等同于账号凭据」；
  `bench --synth` 改用 `mkstemp`，不再用可预测的固定临时文件名。
- **`vsum batch --jobs` 的子进程不再继承 LLM key**（batch 只跑抽取式摘要，用不到它）。

#### 不再误导（产物不变，报错/提示变了）

- **`vsum doctor` 不再把 ffprobe 当必需**：vsum 自己不用它（时长退回 PyAV / wav 头），
  缺了只是少一个 `duration`，现在降为 `warn`。
- **`doctor` 加 `dist` 行**：PyPI 上的 `vsum` 是另一个项目，装错时直接报出来；
  `browser` / `yt` / `ocr` 的安装提示改成 `包名[extra] @ git+…` 的写法。
- **`--lang en` 不再喂中文提示词**：默认提示词按语言选（zh / en / 空），`config_sha256`
  跟着变，换语言必然重转写。
- **`--lang auto` 记检测到的语言**：`manifest.decode.detected_language` 记 Whisper 判定的
  语言，`transcript.md` 头部不再写 `language: auto`。
- **`HF_ENDPOINT` 非官方镜像时给出提示**：`decode.hf_endpoint` 进 manifest，warnings
  说明「模型来源不可认证」，需要严格复现就用本地模型目录。
- **`vsum export` 不再接受空 segments**（退出 2）；`--clean` 的帮助说明改成实际产物
  `transcript.clean.md`，清洗稿不再带「术语修订」附录。
- **负时间被夹到 0 后不会叠出重叠 cue**：新增 `overlap_after_fix` 修正原因。
- **`vsum help help` 打印总览**（退出 0），不再报「没有这个子命令」。
- **抓取先识别链接再建目录**：不认识的链接不再留下空目录。
- **`--devices` 配 `--jobs 1` 也生效**（取第一个），提示区分「没给 --devices」和「没探测到卡」。
- **`counts` 五键**（ok / skipped / failed / interrupted / pending）在 `batch.json` 里自洽；
  失败任务的日志落在 `<out>/.logs/`，失败不再留下空目录。
- **`holdout.json` 默认写到当前目录**，不再往语料目录里塞文件。
- **输出编码可覆盖**：管道输出仍是 UTF-8，但支持 `VSUM_IO_ENCODING=gbk` 让父进程按 locale
  解码也不崩（中文 Windows 的 `subprocess.run(text=True)` 默认按 cp936 解码）。
- **manifest / result 不再写绝对路径**：产物目录内的 `input.path` / `audio.path` 记文件名
  （目录外的输入保留原样，那是定位所必需的），`result.out` 只记目录名——产物发给别人时
  不会带上本机用户名。
- **`--model auto` 在 bench 里也解析**：`doctor` 推荐 `vsum bench --model auto`，以前照做会
  因为模型名 `auto` 加载失败。

#### 文档与测试

- README / README.en / SKILL / references：av 不锁版本的说明、ffprobe 可选、退出码 3 与 4 的
  分界（模型加载失败是 3）、`VSUM_IO_ENCODING`、代理行为（urllib **会**吃 `http_proxy`，
  老文档写反了）、手工投放 driver 的信任边界、`VSUM_BROWSER_ARGS` 的注入面。
- `docs/example/` 明确标成 fixture（`avg_logprob` 是规则化的），并去掉示例里的本机绝对路径。
- 新增 `SECURITY.md`（信任边界、不做什么、私密报告渠道）；`.gitignore` 补 profile / `.env`；
  CI 加最小 `permissions` 与 `timeout-minutes`；`numpy` 显式声明为直接依赖。
- 回归：`test_llm`（同源重定向、非有限 sec、超大响应、Content-Type、综述超长）、
  `test_fetch`（地址白名单、play.json 越界、复用边界）、`test_lookup`（manifest 越界路径）、
  `test_ocr`（二进制查找走 toolpath）、`test_p2`（陈旧 result.json、产物路径不是绝对路径、
  子进程显式 UTF-8）、`test_hardware` / `test_holdout` / `test_exports` / `test_batch` /
  `test_cli`（pyproject 与包版本一致）。

## 0.6.1 — 2026-11-23

`vsum ocr` 上一版**在真视频上基本没用**：60 秒跑下来 4 条候选全是噪音，真正该抓的错一个没抓到。
这一版把它修到能用。

### 修复

- **候选全是标点/大小写差异**：真实输出是「`l。`→`l`」「`源sk`→`源Sk`」「`近,你`→`近，你`」
  「`频。`→`频`」——四条全是噪音，把真候选淹了。现在只差标点/空白/大小写的差异不算错字。
- **一帧里有标题卡 + 字幕时，整块比对谁都不像**：「Vibe 知识大赏」和「这个话题已经几十亿
  播放量了」同屏，整块文本对哪一句相似度都低，于是被判成「画面可能是标题卡」，那一帧的
  信息全丢了。现在**逐行**比对，取最像的那一行。
- **拉丁词候选只在前一句对得上时才生成**：`Wipe → Vibe` 这种恰恰是「画面上写着、台词里
  没有」的情况，被那条 gate 全扔了。现在拉丁词靠**时间窗**锚定（只看这几秒说的话），
  不要求整句相似。
- **`Wipe / Vibe` 两条通道都够不到**：形状相似度 0.5、Soundex 也不同（W100 / V100）。
  新增 near 通道（同长度、编辑距离 ≤2）作为**候选**，标 `applicable: false`——只报不改。
- **抽帧漏掉标题卡**：以前只在「念出这个词」的那一秒抽帧，而标题卡常常晚一两秒才出现。
  现在含拉丁词的段会多抽 `start+2s`。
- **一个从没触发过的崩溃**：候选聚合的 `else` 分支写的是 `item.update(...)`，而 `item` 是
  `None`。之前一条拉丁候选都没产生过，所以一直没暴露。

### 实测

60 秒视频、12 帧，候选从「4 条全噪音」变成「2 条全是真的」：

| 转写里的写法 | 画面上的写法 | 出现 | 处置 |
|---|---|---|---|
| seadance | Seedance | 5 帧 | 自动改 |
| wipe | Vibe | 1 帧 | 只报不改 |

### 文档

- README 第一屏加「它会自己发现写错了」，用上面这次真实运行；英文 README 同步。

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

- **LLM 摘要失败时说清原因**：以前只记「LLM 摘要失败（没网/没模型/返回不合法），已退回抽取式」，
  现在把 HTTP 状态码和服务端原话一起写进 `warnings`。实测值：本机 Ollama 的
  `llama-server` 二进制缺失时，会直接写
  `HTTP 500 {"error":{"message":"error starting llama-server: llama-server binary not found"...`，
  而不是让人对着「已退回抽取式」猜。
- **跑完给下一步**：第一次跑完只报「完成：N 段，N 字」，新人不知道还能搜、还能剪。现在直接
  打印两行可复制的 `vsum find` / `vsum clip`；`batch` 里关掉（每条都打一遍是噪音）。
- **`--help` 里每个参数都有说明了**：`-o` `--lang` `--pack` `--clean` `--words` `--summary`
  `--llm-model`、`find --limit`、`audit` 的四个参数、`mine` 的四个参数、`fix --terms`
  之前要么没说明，要么只有占位符 `--limit LIMIT`。
- **`vsum export --segments` 可以直接给输出目录**，不用记 `segments.json` 在哪一层。
- **`vsum bench` 先跑本机实际会用的 `compute_type`**：Pascal（P106 这类）上 float16 是软件
  模拟的，之前排第一，等于先白等几分钟才拿到有意义的数。
- pyproject 补全元数据：Homepage / Source / Issues / Changelog / Releases 链接、维护者、
  classifiers 全项、Python 3.9–3.13 声明。

### 修复

- **`vsum bench` 在任何 av>=16 的环境里都是坏的**：它把音频**路径**交给 faster-whisper，
  于是走 `av.decode_audio`；而 av 16 之后去掉了 `metadata_errors` 参数，faster-whisper
  1.2.x 还在传，直接 `TypeError: open() got an unexpected keyword argument`。主转写路径
  因为喂的是数组所以没事——也就是说这个 bug 只在 bench 上，而 bench 正是「想知道该用什么
  配置」的人第一个会跑的命令。现在 bench 自己用 ffmpeg 转 16k 单声道 wav、按数组喂模型，
  和主路径一致。
- **`tests/test_browser.py` 在任何 Windows 上都是红的，而 CI 一直是绿的**：两处断言拿
  `expanduser()` 的原始串去比 `abspath()` 的返回值，`C:\a/b` 与 `C:\a\b` 必然不等。
  CI 没发现是因为 windows job 用 pwsh 跑多行脚本——它只在**最后一条命令**后检查
  `$LASTEXITCODE`，中间挂掉不会让 job 变红。现在 windows job 显式 `shell: bash`
  （带 `-e -o pipefail`），期望值也走 `abspath`，两端一致。
- **CI 的测试清单不再手写**：之前同一份清单硬编码在两个 job 里、README 里还有第三份，
  新加测试漏过 `lookup` / `ocr` / `llm` 三次。现在统一 `for t in tests/test_*.py`。
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
