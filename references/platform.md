# 平台链接：抖音 / B站 / YouTube

> `SKILL.md` 只留入口。这里是浏览器的真实行为、失败分诊和降级顺序。所有命令都是 `vsum` 子命令，跨平台一致。

## 0. 最快的路

```bash
vsum "<链接>" -o out/<短名>              # 抓取 + 转写 + 摘要，一条命令
vsum "<链接>" -o out/<短名> --fetch-only  # 只抓视频
vsum fetch "<链接>" -o out/<短名>          # 同上，只下载
```

`<短名>` 用内容相关的英文或拼音，别用中文长标题（路径、日志、跨平台都更省事）。

## 1. 浏览器

`--browser` 的取值与真实顺序：

| 值 | 行为 |
|---|---|
| `auto`（默认） | 先找 Chrome，没有再找 Edge；都不在就交给 selenium-manager 下 driver |
| `chrome` / `edge` | 强制该家族；找不到直接报错 |
| `safari` | 只在 macOS，且必须显式指定；没有无头模式 |

抓抖音默认起**可见**窗口：无头更容易被风控。`--headless` 是可选加速，失败了就去掉。

独立 profile 目录（默认 `~/.cache/vsum/chrome`，Windows 为 `%LOCALAPPDATA%\vsum\chrome`）：

```bash
vsum login douyin                          # 扫码一次，cookie 落进 profile
vsum "https://v.douyin.com/xxx/" -o out/x  # 之后直接抓
vsum login douyin --profile /tmp/p --timeout 120
```

`login` 每 3 秒查一次 `sessionid` / `sessionid_ss`，默认等 300 秒；成功就打印 profile 路径并关闭浏览器（cookie 落盘）。
`VSUM_BROWSER_PROFILE` 可以指定默认 profile，`--profile` 覆盖它。

## 2. 抖音：走页面接口，不走 yt-dlp

yt-dlp 对抖音已经失效（403 / `Fresh cookies needed`），别在它上面花时间。`vsum` 的路径是：

1. 短链解析 → `www.douyin.com/video/<id>`
2. 浏览器打开视频页，读页面数据里的 `play_addr`（带 Referer 与 Chrome UA 下载）
3. 下载完写 `play.json`（解析结果、字节数、ffprobe 时长、warnings）

细节接口和下载各重试一次。接口报的时长与文件实测差超过 1 秒时只记 warning（文件已经落盘，继续用），不中断。

失败时按这个顺序降级：

1. `vsum login douyin` 重新扫码 → 重试
2. 去掉 `--headless`（本来就是默认可见）
3. 换 `--browser edge` / `--browser chrome`
4. 请用户直接给本地文件——**不要连点重试**，风控窗口只会越拉越长

## 3. B站 / YouTube：yt-dlp

```bash
pip install '.[yt]'
vsum "https://www.bilibili.com/video/BV..." -o out/bili
vsum "https://www.youtube.com/watch?v=..." -o out/yt
```

格式固定为「不超过 1080p 的最佳视频 + 最佳音频，合并成 mp4」。需要登录才能看的视频，用 yt-dlp 自己的 `--cookies-from-browser` 先下好，再喂本地文件。

YouTube 从机房/代理 IP 常触发机器人检查（`The page needs to be reloaded`）；换网络或直接用本地文件。

## 4. 批量

```bash
vsum batch urls.txt -o out/batch
vsum batch index.json -o out/batch --json
```

- 清单：`urls.txt` 一行一条（`#` 开头为注释），或 `index.json` 的 `{"items":[{"href":...}]}`
- 目录名：`douyin-<awemeId>` / `bilibili-BV<id>` / `youtube-<id>`；认不出平台时 `other-<sha1(url)[:10]>`
- 已有 `ok: true` 的 `result.json` 且没给 `--force` → 跳过
- 串行；单项失败不中断，最后写 `batch.json`（原子替换），有失败则退出码 5
- 中途 Ctrl-C 或 kill：已完成的目录不会重转（复用条件见 README「已知限制」）

## 5. 失败分诊

| 症状 | 处置 |
|---|---|
| `不认识的链接` | 只支持抖音 / B站 / YouTube；其它平台先手动下好再喂文件 |
| `从 URL 里没认出视频 id` | 短链没跳转成功，换成 `www.douyin.com/video/<id>` |
| `play_addr` 下载 403 | 直链有时效；重跑一次，仍失败就重新解析 |
| `扫码超时` | 窗口里登录后重跑 `vsum login douyin`；或加 `--timeout` |
| Safari 起不来 | Safari → 设置 → 高级 → 显示开发菜单 → 允许远程自动化 |
| Edge/Chrome 找不到 | `vsum doctor` 看 `browser` 行；装浏览器或 `--browser` 指定另一个 |
| 抓完视频但转写失败 | 抓取和转写是两段；先 `--fetch-only` 确认视频在，再查 ffmpeg / av |
| 时长为 null | 缺 ffprobe；不影响转写，只是 manifest 里没时长 |

## 6. macOS 专属

- Safari 需要「允许远程自动化」，否则 `vsum --browser safari` 报权限错误
- Chrome 首次被 selenium 驱动时会弹「允许控制」提示，点允许即可；独立 profile 不会污染日常浏览器
- macOS arm64 + Python 3.9 装依赖会退到旧版 onnxruntime / av / tokenizers；用 `uv venv --python 3.12` 更稳，`vsum doctor` 会提示这一条
