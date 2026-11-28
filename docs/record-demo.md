# 录一段 20 秒的演示

README 第一屏现在只有文字。一段 20 秒的 GIF 比再写 500 字有用——但**必须是真的跑出来的**，
所以这里只给脚本，画面你自己录。

## 录之前

1. **先确认环境是好的**（不然录到一半会弹出报错）：

   ```bash
   vsum doctor          # MISSING 行先解决（hf-cache 尤其不能是 MISSING）；warn / extra 是建议，不影响转写
   ```

2. **准备一条短视频**（30 秒到 2 分钟最好，太长的话录屏会等很久）：

   ```bash
   vsum demo.mp4 -o demo/            # 先跑一次，把模型和产物都热起来
   ```

3. **把终端弄干净**：
   - 宽 100 列、高 30 行左右（太宽 GIF 字就糊了）
   - 字号 16–18px，深色主题（浅色主题在 GitHub 白底上对比度差）
   - 提示符别带用户名和主机名，`cd` 到一个临时目录再开录
   - 关掉所有会弹窗的东西

## 三幕，一共 20 秒

| 时间 | 画面 | 命令 |
|---|---|---|
| 0–5 s | 转写跑起来，进度在动 | `vsum demo.mp4 -o demo/` |
| 5–12 s | 逐字稿滚出来，**指着 `[mm:ss]`** | `head -20 demo/transcript.md` |
| 12–20 s | 搜一个词 → 剪出来 → 播放 | `vsum find demo/ 价格`<br>`vsum clip demo/ 00:41 -o clip.mp4` |

第三幕是整个演示的重点：**别人做总结工具做不到这件事**。如果时间不够，砍掉第一幕，
保留「搜 → 剪」两幕。

## 录

- **Windows**：ScreenToGif（免费，直接出 GIF，能删帧）或 Win+G 录 MP4
- **macOS**：Cmd+Shift+5 录屏，再用 `gifski` 转
- **全平台**：`asciinema rec demo.cast` + `agg demo.cast demo.gif`——纯文字，体积小、
  清晰、不会被系统字体影响；缺点是看不到窗口边框

## 压

GIF 控制在 **8 MB 以内**（超过 GitHub 加载会卡）。10 fps、宽 900–1000px 通常够：

```bash
gifski --fps 10 --width 960 -o docs/demo.gif demo.mp4
```

## 放进去

```markdown
![vsum：转写 → 按秒引用 → 搜到那一段剪出来](docs/demo.gif)
```

放在 README 第一段代码块**之前**——第一屏决定别人要不要往下滚。

## 顺带建议

同一段录屏可以裁成竖版发小红书 / B站，那两个平台的人做短视频二创，正是这个工具的用户；
HN 上的人不做短视频。
