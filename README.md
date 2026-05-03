# OpenClaw 养虾指南

<p align="center">
  <img src="assets/openclaw-mascot.png" alt="OpenClaw 红色小助手" width="120">
</p>

这是一个面向 Windows + WSL2 用户的 OpenClaw 工具包，也是一套可安装到 Codex 的 skill。它关注的不是“第一次能不能跑起来”，而是 OpenClaw 能否长期稳定地待在后台：WSL 休眠后能否恢复、断网后 gateway/Telegram 是否还能工作、代理切换后如何排查、本机状态如何可视化。

它不是官方文档，也不是一键安装器；它更像一份经过踩坑整理的路线图和工具集合。

## 核心原则

- **安全**：token、API key、auth profile、日志和机器专属配置不进仓库，也不应该贴进聊天。
- **透明**：遇到问题先分层定位，不靠“重启试试”的玄学。
- **简单**：能自动恢复的放进后台；能在控制中心看的，不让用户反复敲命令。

## 适合谁

适合你正在做这些事：

- 在 Windows 上通过 WSL2 跑 OpenClaw。
- 想让 OpenClaw gateway、Telegram bot、后台任务更稳定。
- 需要一个本机控制中心查看 gateway、Telegram、任务、日志和 token/成本流向。
- 想安装可选的市场信息浸泡模块、人民日报深读、IMA 知识库接入、Jina/Tavily、豆包/火山 ASR 等增强能力。
- 想把这一套流程交给 Codex 或其他 coding agent 执行。

不适合你如果只是想看 OpenClaw 官方 API 或完整配置参考；这类内容应优先查官方文档。

## 推荐路线

默认推荐路径：

```text
Windows
  -> WSL2
  -> Ubuntu
  -> Ubuntu 内安装 OpenClaw
  -> systemd user gateway
  -> 权限范围确认
  -> 模型回复验证
  -> Windows 登录后的 keepalive/autostart
  -> 长时间断网后的 recovery watchdog
  -> 本机 OpenClaw 控制中心
  -> Telegram Bot
```

先确认本地 OpenClaw 和 gateway 稳定，再接 Telegram。不要一看到“机器人不回消息”就直接重配 token 或重装。

## 项目结构

```text
.
|-- README.md
|-- .gitignore
|-- assets/
|-- modules/
|   `-- openclaw-market-immersion/        # 可选市场信息浸泡模块
`-- openclaw-telegram-wsl-setup/          # Codex skill 主目录
    |-- SKILL.md
    |-- agents/
    `-- tools/
        |-- openclaw-local-monitor/       # Windows 本机控制中心
        |-- openclaw-doubao-asr/          # 豆包/火山 ASR 辅助工具
        `-- openclaw-optional-apis/       # Jina / Tavily 配置工具
```

真正的 Codex skill 是：

```text
openclaw-telegram-wsl-setup/
```

目录名保留为 `openclaw-telegram-wsl-setup` 是为了兼容已经安装的 skill 和旧链接；项目名以 **OpenClaw 养虾指南** 为准。

## 快速安装到 Codex

把 skill 文件夹复制到 Codex 的 skills 目录：

```powershell
Copy-Item -Recurse -Force `
  ".\openclaw-telegram-wsl-setup" `
  "$env:USERPROFILE\.codex\skills\openclaw-telegram-wsl-setup"
```

新开 Codex 会话后，可以这样调用：

```text
Use $openclaw-telegram-wsl-setup to follow the OpenClaw 养虾指南 on Windows with WSL2.
```

如果你用中文调用，它会继续使用中文；新安装流程会先确认语言和关键选择。

## 主要能力

### 1. OpenClaw + WSL2 安装/修复流程

Skill 会按层排查：WSL、Ubuntu、OpenClaw、gateway、模型回复、Telegram channel、后台常驻、代理和网络恢复。目标是避免把所有问题都误判成 token 错误。

详细流程在：

```text
openclaw-telegram-wsl-setup/SKILL.md
```

### 2. 本机 OpenClaw 控制中心

位置：

```text
openclaw-telegram-wsl-setup/tools/openclaw-local-monitor/
```

功能包括：

- 查看 gateway 和 Telegram 是否可用。
- 查看后台 queued/running task、TaskFlow、本地产物心跳。
- 查看 token/上下文使用快照和本地 session 成本汇总。
- 托盘常驻、手动重新检测、打开本地 Control UI。
- 可选 Clash 安全模式，帮助在代理/TUN 场景下减少国内应用受影响。

安装：

```powershell
cd .\openclaw-telegram-wsl-setup\tools\openclaw-local-monitor
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Install-OpenClawMonitor.ps1
```

控制中心不会把 gateway token 写进仓库，也不会把真实 token 打印到聊天。

### 3. 市场信息浸泡模块（可选）

位置：

```text
modules/openclaw-market-immersion/
```

这是一个可选 `openclaw-job-module`，不是基础安装项。只有用户明确需要“财经快讯日报”“市场信息浸泡”“人民日报深读”“Notion 日报闭环”时才安装。

包含两条工作流：

- **每日快讯简报**：按 09:05、12:15、15:20、22:10 四个时间点收集 7x24 财经快讯，去重、保留原始信息流，再交给 OpenClaw 轻整理。可选发布到 Notion/Telegram。
- **人民日报深读**：抓取人民网《人民日报》电子版、PDF 和文章正文，按日期生成 Notion 归档和文章深读子页。

默认公开配置中：

- Notion 发布关闭；
- Telegram 推送关闭；
- page ID / chat target 留空；
- 用户需要在自己本机安装后的配置里填入私有信息再启用。

模块说明见：

```text
modules/openclaw-market-immersion/README.md
```

#### 自定义人民日报深读 prompt

仓库不会内置具体的人民日报/政策文本解读 prompt。启用人民日报深读时，用户应在本机创建自己的私有 prompt 文件，并在模块配置中填写：

```json
"people_daily_deep_read": {
  "analysis": {
    "prompt_template_path": "~/.openclaw/private-prompts/people_daily_analysis_prompt.md"
  }
}
```

私有 prompt 不要提交到 GitHub。完整操作步骤见：

```text
modules/openclaw-market-immersion/README.md
```

### 4. IMA 知识库接入（可选）

Skill 中包含腾讯 ima OpenAPI 的配置流程，可用于：

- 查看 IMA 知识库列表；
- 搜索知识库内容；
- 添加网页/微信文章；
- 上传 PDF、Word、PPT、表格等文件；
- 创建、搜索、读取或追加 IMA 笔记。

IMA Client ID / API Key 必须通过本地终端或服务商页面输入，不要贴进聊天或提交到仓库。

### 5. Jina / Tavily 可选 API

位置：

```text
openclaw-telegram-wsl-setup/tools/openclaw-optional-apis/
```

用途：

- Jina embeddings：增强 OpenClaw 语义记忆和本地资料检索。
- Tavily web search：增强联网搜索和当前网页检索。

配置脚本：

```powershell
cd .\openclaw-telegram-wsl-setup\tools\openclaw-optional-apis
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Set-JinaApiKey.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Set-TavilyApiKey.ps1
```

### 6. 豆包 / 火山 ASR 辅助工具（可选）

位置：

```text
openclaw-telegram-wsl-setup/tools/openclaw-doubao-asr/
```

用于本地音频转写。注意：极速版会上传本地音频到火山引擎，标准版会把公网音频 URL 发送给火山引擎。处理私人音频前必须得到用户明确同意。

安装：

```powershell
cd .\openclaw-telegram-wsl-setup\tools\openclaw-doubao-asr
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Install-DoubaoAsrTool.ps1
```

自检：

```bash
openclaw-doubao-asr --self-check
```

## 安全边界

不要提交或公开：

- `~/.openclaw`；
- Telegram bot token、API key、模型凭据、auth profile；
- Notion page ID、Telegram chat target、用户账号 ID；
- 私有 prompt、聊天记录、长期记忆、运行日志、截图；
- 机器专属配置、生成报告、本地诊断产物。

公开仓库只保留可复用工具、流程和脱敏示例。具体用户配置应只写入安装后的本机文件。

## 发布前检查

建议每次提交前至少跑：

```bash
git status
python3 -m py_compile $(git ls-files '*.py')
for f in $(git ls-files '*.sh' 'openclaw-telegram-wsl-setup/tools/openclaw-doubao-asr/openclaw-doubao-asr'); do bash -n "$f"; done
git grep -n -I -E 'gho_|github_pat_|telegram:[0-9]+|NOTION_TOKEN=|BOT_TOKEN=' || true
```

如果关键词扫描命中安全说明或变量名，需要人工确认；不应命中真实密钥或私人 ID。

## 给其他 Agent 使用

这个项目按 Codex skill 格式整理，但核心流程在 Markdown 中。Claude Code、Gemini CLI 或其他 coding agent 也可以读取 `openclaw-telegram-wsl-setup/SKILL.md` 后执行。不同 agent 的工具权限不同，实际命令可能需要调整，但诊断顺序和安全原则应保持一致。

## 当前状态

当前仓库覆盖：

- Windows + WSL2 + Ubuntu 的 OpenClaw 推荐路径；
- gateway/systemd/keepalive/network recovery；
- Telegram bot 接入与常见排障；
- 本机 OpenClaw 控制中心；
- 可选市场信息浸泡与人民日报深读模块；
- IMA、Jina、Tavily、豆包/火山 ASR 等可选增强。

后续适合继续拆分：

- 把过长的 `SKILL.md` 拆出 references；
- 增加英文 README；
- 增加更正式的仓库自检脚本。
