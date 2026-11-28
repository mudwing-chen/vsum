# 依赖与故障对照

> 主文件只放最常见的几条，完整表在这里。先跑 `vsum doctor`，它逐项报位置/版本并给安装命令；`warn` 行只是建议（模型未缓存、磁盘空间紧），不影响退出码。

## 依赖

| 依赖 | 用途 | 说明 |
|---|---|---|
| Python 3.9+ | 本体 | `pip install .`；faster-whisper 随之装好 |
| `av`（随 faster-whisper） | 被 faster-whisper import | vsum **不锁** av 版本：`pyproject.toml` 只要求 `faster-whisper>=1.0`，av 由它的依赖自动带入。转写喂的是 ffmpeg 抽好的 wav，不走 av 解码，所以一般不用手动 pin；真遇到 av 相关报错再按症状表钉 |
| ffmpeg | 抽音频、时长校验、抽帧 | **必须在 PATH 上** |
| ffprobe | 时长 | 可选；缺了 `manifest` 的时长为 `null`，转写不受影响 |
| `selenium` + Chrome/Edge | 抓抖音 | `pip install '.[browser]'`；Safari 需 macOS 且显式指定 |
| yt-dlp | B站 / YouTube | `pip install '.[yt]'`；对抖音已失效 |
| NVIDIA GPU | CUDA 转写 | 可选；无 GPU 走 CPU，`device` / `compute_type` 由 `vsum/hardware.py` 自动选 |

模型缓存在 HuggingFace 目录（`HF_HOME` / `HF_HUB_CACHE` 可改），`vsum doctor` 会看 `model:<名字>` 有没有缓存。

## 症状 → 处置

| 症状 | 解法 |
|---|---|
| `TypeError: ... unexpected keyword argument 'metadata_errors'` | `av` 装到了 14+：`pip install 'av<14'` 后重跑 |
| `LocalEntryNotFoundError` / 模型下载卡住 | 换缓存完整的模型（`--model small`），或先手动下一份 |
| `ffmpeg 抽音频失败` | ffmpeg 不在 PATH，或输入不是 ffmpeg 能解的音视频；先 `ffmpeg -i <文件>` 单测 |
| `cublas64_12.dll` 缺失（Windows） | 把 nvidia cublas / cuda_nvrtc 的 bin 加进 PATH |
| `float16` 不支持 / Pascal 显卡 | 设 `WHISPER_COMPUTE=int8`（或 `int8_float32`） |
| 显存不足 / OOM | 先自动降档，最多 3 次：batch 减半 → 换 `compute_type` → 换更小的模型 → 退回 CPU，每次尝试记在 `manifest.decode.fallbacks`；自动降档仍失败再手动降模型或设 `WHISPER_COMPUTE=int8` |
| 转写很久没输出 | 正常；看进程是否还在跑，`transcript.txt` 出现即有结果 |
| 两次转写结果不同 | 解码带温度回退，不是 bug；需要固定结果就保留 `segments.json` 复用 |
| 字幕条目少于 `segments.json` 段数 | 正常：空文本、负时间、零时长被修正，逐条记在 `manifest.export.invalid_cues`（`manifest.warnings` 是给人读的句子列表） |
| 专有名词被转成同音词 | 抽帧核查；画面拼写才算数，见 [terms.md](terms.md) |
| `--summary llm` 失败 | 检查 `--llm-url` 是否是 OpenAI 兼容 base（到 `/v1` 为止），失败会自动退回抽取式并在 `warnings` 里说明 |
| 摘要比原文短很多 | `result.json` 的 `summary.dropped` 是回查被删的条数；删得多说明要点不是原文子串 |
| 链接抓取报浏览器错误 | `vsum doctor` 看 `browser` 行；装 Chrome/Edge，或 `--browser` 指定 |
| 代理干扰下载 | 抓抖音走 urllib（不吃环境代理）；yt-dlp 加 `--proxy` 或 `--proxy ''` |

## Windows

- 控制台默认 cp936（英文系统是 cp1252），打印中文原本会抛 `UnicodeEncodeError`；0.3.0 起进程内改用 `backslashreplace` 兜底，不再崩，但编不出的字会显示成 `\uXXXX`。想正常看中文：用 Windows Terminal，或先 `chcp 65001`
- 输出重定向/接管道时自动写 UTF-8，可直接喂给别的程序；终端里保持本地编码不动
- `vsum doctor` 的 `browser` 行报 Chrome/Edge 路径；Windows 候选在三个安装根下（`ProgramFiles`、`ProgramFiles(x86)`、`LOCALAPPDATA`）

## macOS / Linux

- Safari 需在「设置 → 高级 → 显示开发菜单」里允许远程自动化，否则 `--browser safari` 报权限错误
- macOS arm64 + Python 3.9 只能配旧版依赖（onnxruntime ≤1.16.3、av ≤13.1.0、tokenizers ≤0.21.4）；稳妥做法 `uv venv --python 3.12`，`vsum doctor` 会提示这一条
- 没有 `nvidia-smi` 时自动走 CPU（`int8`）；`vsum bench` 可以量化本机 RTF

## 退出码

| 码 | 含义 |
|---|---|
| 0 | 成功 |
| 1 | `vsum find` 没有命中 |
| 2 | 输入/参数问题（文件不存在、JSON 坏了、`clip` 的时间格式不对或不在任何一段里） |
| 3 | 依赖缺失（ffmpeg / faster-whisper / selenium） |
| 4 | 转写、抓取或剪片失败（含模型加载失败、找不到原视频） |
| 5 | 评测不达标（`eval --min-cer` / `holdout --max-cer`），或 `batch` 有失败项 |
| 130 | Ctrl-C |
