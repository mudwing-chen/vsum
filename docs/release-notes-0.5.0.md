## vsum 0.5.0 — 视频 → 按秒可引用的逐字稿

不是又一个"视频总结"。它给你**能核对、能搜索、能剪**的逐字稿：每段一行 `[mm:ss]`，
摘要每条要点必须能在原文里找到，找不到就删掉并计数。

> ⚠️ **别 `pip install vsum`** —— PyPI 上那个 `vsum` 是同名的另一个项目。
> 本仓库还没上 PyPI（计划改名 `vsum-cli`），请用：
>
> ```bash
> uv tool install "vsum @ git+https://github.com/mudwing-chen/vsum"
> ```

### 这一版新增

- **`vsum find`**：在逐字稿里搜索，命中直接给时间戳；支持多词、上下文、`--json`
- **`vsum clip`**：按时间点把原视频那一段剪出来
- **`vsum ocr`**：抽帧 OCR，用**画面上的拼写**核对专名。中英都出候选——
  英文走形状/读音（`Cloud→Claude`、`GPD→GPT`），中文走带上下文的逐字差异
  （`有加奖→有嘉奖`、`罗大→罗那`，单字不用，怕误伤）
- **`vsum fix --from-ocr`**：候选直接接回修正；≥2 帧都成立的才自动改
- **LLM 摘要分两级**：一句话（模型改写，明标未校验）+ 要点（必须是原文片段，照过回查门）

### 这一版修掉

- 回查门在细分段上**误删真引用**（真实 14 分钟视频：概览 3 条删 2 条，删的全是真的）
- 改 `--lang` 却静默复用旧转写，产物里出现三种互相矛盾的语言声明
- 有 NVIDIA 卡却跑不起来（ctranslate2 找不到 cublas）——现在自动挂 CUDA 运行库，起不来就退回 CPU 并记账
- `doctor` 冷启动 34 秒；`batch` 每条视频重建模型；摘要三个小节重复同一句
- 无音轨输入只报 ffmpeg 的英文原文
- 去掉 `av<14` 的版本锁（vsum 自己解码，不碰 av 的解码路径）

### 安装与验证

```bash
uv tool install "vsum @ git+https://github.com/mudwing-chen/vsum"
vsum doctor
vsum your-video.mp4 -o out/
vsum find out/ "关键词"
```

Windows 11 + P106 实测：抖音短链 → 详情接口 → 下载 → 转写 → OCR → find → clip 全链路。
15 个测试脚本不依赖任何第三方库（不下载模型、不起浏览器），CI 在 Ubuntu 和 Windows 上都过。

### 已知限制

- 转写不可复现（温度回退、换设备都会变）；复用有 sha256 + 模型 + 语言 + context 四道校验
- 摘要只保证出处，不保证摘得对
- 没有人工标注的准确率数字
- OCR 候选是候选，改不改由人定
