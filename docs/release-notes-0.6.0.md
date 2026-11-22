## vsum 0.6.0 — 装上之后不迷路

这一版不碰产物格式，只修「装完了不知道有哪些功能」和几个「只有真机上才会炸」的坑。

> ⚠️ **别 `pip install vsum`** —— PyPI 上那个 `vsum` 是同名的另一个项目。
> 本仓库还没上 PyPI（计划改名 `vsum-cli`），请用：
>
> ```bash
> uv tool install "vsum @ git+https://github.com/mudwing-chen/vsum"
> ```

### 命令终于都看得见了

以前 `vsum --help` 只打主命令的参数，`find` `clip` `ocr` `fix` `audit` `mine` `eval`
`holdout` 八个命令在帮助里一个字都没有——装完根本不知道还能搜、还能剪、还能核对画面。

```
$ vsum --help
vsum 0.6.0 — 视频/音频 → 可逐秒引用的逐字稿与带出处的摘要

用法
  vsum <文件|链接> -o <目录>        转写本地文件或抖音/B站/YouTube 链接
  vsum <子命令> [参数]              见下表；省掉命令名就是转写
  vsum help <子命令>                看这个子命令的全部参数

转写
  transcribe <文件|链接> -o DIR  转写 + 导出 + 摘要（默认命令，可省掉命令名）
  export --segments FILE -o DIR  从 segments.json 重新导出四件套
...
退出码：0 成功 · 2 用法或输入错误 · 3 缺依赖 · 4 转写/抓取失败 · 5 评测不达标 · 130 中断
```

- `vsum help <命令>` 直达那个命令的完整参数
- `vsum transcribe x.mp4 -o out` 与 `vsum x.mp4 -o out` 等价，只是想写全命令名时有个名字
- 每个参数都有说明了（`-o` `--lang` `--pack` `--clean` `--words` `--summary` 之前只有占位符）
- `vsum export --segments` 可以直接给输出目录

### 真机上才会炸的两个

- **`vsum bench` 在任何 av>=16 的环境里都是坏的**：它把音频路径交给 faster-whisper，
  于是走 `av.decode_audio`；av 16 之后去掉了 `metadata_errors` 参数，faster-whisper 1.2.x
  还在传，直接 `TypeError`。主转写因为喂的是数组所以没事——也就是说这个 bug 只在你最想
  知道「该用什么配置」时出现。现在 bench 和主路径一样自己解码。
- **`vsum bench` 在有 NVIDIA 卡的机器上会报 `cublas64_12.dll is not found`**：它绕过了
  主路径的 CUDA 运行库挂载。现在复用同一条加载路径。

### 模型缓存坏掉时不再说「拒绝访问」

换盘或搬家之后 `~/.cache/huggingface` 可能指向一个已经不存在的磁盘。症状很误导：模型明明
下过，却加载不了，报错只有 `[WinError 5] 拒绝访问`。

- `vsum doctor` 多一行 `hf-cache`，直接说清是「软链断了」「没权限」还是「上级目录不可写」
- 模型加载失败时，报错里给出当前缓存目录和改法（`set HF_HOME=D:\hf`）

### 顺带

- `tests/test_browser.py` 的两处断言在任何 Windows 上都必然失败（拿 `expanduser()` 的
  原始串比 `abspath()` 的返回值），CI 的 windows job 一直在跑它
- README 的「环境变量」被之前的编辑搞成了两张重复的表
- pyproject 补全项目链接、维护者、classifiers；`pip install "vsum[all]"` 一次装齐可选依赖
- README 的速度数字分成两组来源：Apple Silicon 的历史数值 + 本机 P106 的复测
  （15:41 音频 103.8 s，RTF 0.11），并给出 `vsum bench` 复现命令

**完整改动**：见 [CHANGELOG.md](../CHANGELOG.md)。
