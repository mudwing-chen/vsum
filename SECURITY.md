# 安全策略

## 这个程序信任什么、不信任什么

vsum 处理的是**别人给的媒体和链接**，所以边界画在这里：

- **不可信**：视频内容、逐字稿文本、`play_addr` 等接口返回体、`manifest.json` /
  `play.json` 里的路径、LLM 端点的回应。这些都会被当数据，不会被当指令：
  路径要能自证（在产物目录内或 sha256 对得上），下载地址只允许 https + 白名单域名 +
  非内网解析结果，LLM 回应有大小上限且要过 JSON 解析与回查门。
- **可信**：你自己的命令行参数、仓库里的代码与词表。

## 明确的边界

- 不联网就无法转写：首次运行会从 HuggingFace（或 `HF_ENDPOINT` 指定的镜像）下载模型。
  vsum 不校验权重摘要，`manifest.json` 里的 `decode.hf_endpoint` 只说明「当时用的是哪个源」。
- `--summary llm` 会把逐字稿发给**你指定的**端点。默认拒绝明文 http 打到别的机器，
  也只跟随同源重定向——但端点本身是否可信由你判断。
- `--llm-key` / `VSUM_LLM_KEY` / `OPENAI_API_KEY` 走 `Authorization: Bearer` 头，
  不进日志、不进 manifest / result.json。命令行传参会被同机其它用户从进程表看到，
  能用环境变量就别用参数。
- 浏览器 profile 目录里有登录 cookie，等于账号凭据：vsum 建目录时收成 0700，
  但不要提交、不要共享。
- 子进程（`vsum batch --jobs`）只继承环境变量，不含任何 vsum 自己的状态；
  LLM 相关变量会被剔除。

## 不做什么

不提供在线服务、不上传产物、不执行模型输出的内容、不自动安装或更新二进制
（ffmpeg / yt-dlp / 浏览器要你自己装，`vsum doctor` 只检查并给出提示）。

## 报告问题

发现安全问题时请**不要**先开公开 issue：用 GitHub 的私密漏洞报告
（Security → Report a vulnerability），或直接邮件联系维护者。
请附上能复现的最小输入与 `vsum --version`、`vsum doctor` 的输出（隐去路径与 key）。
