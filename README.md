# OpenClaw 养虾指南

<p align="center">
  <img src="assets/openclaw-mascot.png" alt="OpenClaw 红色小助手" width="120">
</p>

这是一份给 Windows 用户准备的 OpenClaw 使用笔记，也是一套可以装进 Codex 的 skill。

我把它叫“养虾指南”，是因为 OpenClaw 真正麻烦的地方往往不是“能不能跑起来”，而是跑起来以后能不能稳定地待在后台：电脑重启以后还在不在，WSL 休眠以后能不能恢复，断网回来以后 Telegram 机器人还回不回消息，代理一切换会不会又把 gateway 搞挂。

这个仓库想解决的就是这些日常问题。它不假装自己是官方文档，也不把所有事包装成一个一键魔法按钮。它更像一份经过踩坑整理出来的路线图：推荐怎么装、先查哪一层、哪些东西要常驻、哪些东西不能乱动、出了问题怎么判断。

我希望它始终坚持三件事：

- **安全**：token、API key、auth profile 和日志不进仓库，也不应该被贴进聊天。
- **透明**：每一步都尽量能解释清楚，少一点“重启试试”的玄学。
- **简单**：能自动恢复的就自动恢复，能放进控制中心看的就别让用户反复敲命令。

它最开始只是为了解决 OpenClaw + Telegram + WSL2 的稳定性问题。后来慢慢补上了 keepalive、断网恢复、本机控制中心、Token/成本流向、市场信息浸泡模块、语音识别辅助工具和一些可选 API 配置，所以现在更像一个完整的 Windows/WSL 工具包。

## 这个项目解决什么问题

很多时候用户看到的只是“机器人不回消息”。但真正的问题可能在 WSL、gateway、Telegram channel、模型认证、代理、断网恢复，甚至只是 OpenClaw 还没冷启动完。

这份指南的处理顺序很朴素：

1. 先确认 OpenClaw 本地能不能稳定运行。
2. 再确认 gateway 和后台常驻是否可靠。
3. 然后检查 Telegram 是否真正连接。
4. 最后才看 bot token、模型回复、上下文和具体任务。

这样做的好处是，遇到问题时不会一上来就重配 token、换模型、重装 OpenClaw，而是先判断到底是哪一层坏了。

## 推荐安装路径

我默认推荐这条路：

```text
Windows
  -> WSL2
  -> Ubuntu
  -> Ubuntu 内安装 OpenClaw
  -> systemd user gateway
  -> 权限范围确认
  -> 模型选择与本地回复验证
  -> Windows 登录后的 keepalive/autostart
  -> 长时间断网后的 network recovery watchdog
  -> 本机 OpenClaw 控制中心
  -> Telegram Bot
```

推荐 Ubuntu on WSL2，不是因为它看起来更酷，而是因为 OpenClaw gateway 更像一个需要长期在线的后台服务。放在 Ubuntu 里，systemd、路径、权限、后台常驻和恢复逻辑都更容易讲清楚，也更容易排查。

## 它能做什么

这套东西主要分成六块。

第一块是安装和修复。它会优先按 Windows + WSL2 + Ubuntu 的路线处理 OpenClaw，先把 gateway、systemd user service、模型回复和权限范围确认好，再接 Telegram。

第二块是后台稳定性。它把 keepalive、开机后恢复、长时间断网后的恢复、stale socket / polling stall 清理都当成基础设施，而不是出了问题以后临时补救。

第三块是本机控制中心。它是一个 Windows 小程序，用来启动/关闭 OpenClaw、查看 gateway 和 Telegram 是否可用、观察后台任务、Token/成本流向、最近日志和本地产物心跳。它也可以待在系统托盘里，不需要每次都开浏览器。

第四块是市场信息浸泡模块。它是一个可选 `openclaw-job-module`，现在包含两条工作流：一条是 7x24 财经快讯的每日快讯简报，一条是《人民日报》电子版/PDF 的长文本深读。前者按时间段抓取财经快讯流，去重后交给 OpenClaw 写成 Notion 简报；后者按天抓取《人民日报》全部版面、PDF 和文章页，并在 Notion 的 `财经政经 / 人民日报 / 日期页` 下生成版面归档和文章深读子页。这个模块不是基础安装必需项，只有用户明确要市场日报、信息浸泡、人民日报深读或 Notion 闭环时才安装。

第五块是 IMA 知识库接入。它记录了如何给 OpenClaw 安装官方 `ima-skills`，用 IMA OpenAPI 读取和搜索腾讯 ima 知识库、添加网页/微信文章、上传文件、管理笔记，并通过自然语言触发这些能力。

第六块是可选增强。比如 Jina embeddings、Tavily web search、豆包/火山录音文件识别。这些不是基础安装必需项，只有真的需要语义记忆、联网检索或本地音频处理时再加。

另外，它会特别注意几件容易出事故的事：不要把 token 发到聊天里，不要把 key 写进仓库，不要随便重置配置，不要把“机器人没回”直接等同于“Telegram token 坏了”。

## 项目结构

```text
.
|-- README.md
|-- .gitignore
|-- modules/
|   `-- openclaw-market-immersion/
|       |-- README.md
|       |-- module.json
|       |-- config/
|       |-- scripts/
|       `-- systemd/
`-- openclaw-telegram-wsl-setup/
    |-- SKILL.md
    |-- agents/
    |   `-- openai.yaml
    `-- tools/
        `-- openclaw-local-monitor/
            |-- OpenClawMonitor.cs
            |-- Build-OpenClawMonitor.ps1
            |-- Generate-OpenClawMonitorIcon.ps1
            |-- Install-OpenClawMonitor.ps1
            |-- Install-Autostart.ps1
            |-- Uninstall-Autostart.ps1
            |-- OpenClawMonitor.ico
            `-- README.md
        `-- openclaw-doubao-asr/
            |-- openclaw-doubao-asr
            |-- Install-DoubaoAsrTool.ps1
            `-- README.md
        `-- openclaw-optional-apis/
            |-- Set-JinaApiKey.ps1
            |-- Set-TavilyApiKey.ps1
            |-- Repair-OpenClawMemoryDeepStatus.ps1
            |-- save-openclaw-jina-key.sh
            |-- save-openclaw-tavily-key.sh
            |-- repair-openclaw-memory-deep-status.py
            |-- Verify-JinaKey.py
            `-- Verify-TavilyKey.py
```

真正的 Codex skill 仍然是：

```text
openclaw-telegram-wsl-setup/
```

这个目录名暂时保留是为了兼容已经安装的 Codex skill 和旧链接；公开项目名称以 **OpenClaw 养虾指南** 为准。这个目录应保持干净，只包含 skill 本身需要的文件和可复用工具。不要把本机 OpenClaw 配置、Telegram token、日志、截图、编译产物或机器专属诊断文件放进去。

## 本机 OpenClaw 控制中心

仓库附带一个 Windows 原生控制中心：

```text
openclaw-telegram-wsl-setup/tools/openclaw-local-monitor/
```

它是本机的主入口，不替代 OpenClaw 官方浏览器 Control UI。打开 `OpenClaw Control` 只会显示本机状态，不会自动启动或关闭 OpenClaw。需要运行时点击 `开启 OpenClaw`；运行中按钮会变成 `关闭 OpenClaw`，再次点击才会真正关闭。

面板主要显示：

- gateway 和 Telegram 是否可用。
- 后台是否存在 `queued/running` task、活跃 TaskFlow，或正在持续产出的本地 daemon/工作区产物心跳。
- Token / 上下文使用快照，以及主会话、Telegram、子任务的流向。
- 从当月本地 session 日志里的 `usage.cost` 汇总已记录成本，并按模型列出成本和 token 去向；每个自然月刷新一次，这不是服务商账单替代品。
- 最近会话和 Telegram/error 日志提醒。
- 系统托盘常驻，最小化或关闭窗口时隐藏到托盘。

Telegram 卡片只显示通道是否已连接。OpenClaw 冷启动时，顶部状态框内部会临时显示启动进度条，标出 gateway、Telegram、模型和 sidecar 预热等阶段，进度到 100% 后自动消失。启动未完成时，面板只做轻量探测，先看 gateway 和 Telegram；等就绪后再加载任务、日志、Token、成本、会话和本地产物，避免控制中心反过来拖慢 OpenClaw 启动。

控制中心里的 `打开 Control` 按钮会调用本地 `Start-OpenClaw.ps1`。这个脚本只在本机临时解析 OpenClaw 网关令牌，并生成带 `#token=...` 的浏览器 Control URL；令牌不写进仓库、不打印到聊天、不提交到日志。这样用户不需要每次手动粘贴网关 token。脚本打开 URL 后会尽量把浏览器窗口恢复并拉到前台，让用户能看见这次点击确实生效。

控制中心会自动更新显示内容。界面上的 `重新检测` 按钮不是普通刷新按钮，而是手动触发一次主动检测：唤醒 WSL、轻量尝试启动 gateway，然后重新读取当前状态。它不修改配置、不重置任务、不碰 token；自动定时刷新仍然只读状态，不会偷偷启动或关闭 OpenClaw。

`Clash 安全模式` 只针对一个特定网络场景：用户为了让 OpenClaw、Codex 或其他国外大模型稳定走代理，开启了 Clash Verge 的 TUN 或全局式路由，但同时发现微信、腾讯服务或国内网页不能正常访问。开启后，控制中心会通过 Clash Verge Rev 暴露的本地 Mihomo 管道把核心维持在规则模式，让 OpenClaw/Codex 命中 `GLOBAL` 代理组，国内流量继续按规则直连。换节点时只需要在 Clash Verge 的 `GLOBAL` 组里选择节点；这个功能不绑定某个国家或具体节点。如果没有开全局/TUN，或者国内应用本来就正常，通常不用开启这个选项。

安装命令：

```powershell
cd .\openclaw-telegram-wsl-setup\tools\openclaw-local-monitor
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Install-OpenClawMonitor.ps1
```

安装脚本会把源码复制到 `%LOCALAPPDATA%\OpenClawMonitor`，在本机编译 `OpenClawMonitor.exe`，创建 `OpenClaw Control` 桌面、开始菜单和 Startup-folder 快捷方式，清理旧的 `OpenClaw Monitor` / `OpenClaw 启动` 等旧入口，并启动控制中心。仓库不提交任何真实 token、API key、auth profile 或本机日志。

图标是透明背景的可爱红色 OpenClaw 小助手风格，避免使用带黑色背景的截图作为桌面或托盘图标。

## 市场信息浸泡模块

仓库附带一个可选的市场信息浸泡模块：

```text
modules/openclaw-market-immersion/
```

它不是默认安装内容，也不是普通 Codex skill，而是给 OpenClaw 调用的 `openclaw-job-module`。只有用户明确要“市场信息浸泡”“财经快讯日报”“人民日报深读”“Notion 日报闭环”或类似长期自动化时，才引导用户决定是否安装。

这个模块现在分成两条子流程。

### 每日快讯简报

每日快讯简报对应我们重构后的日报模块，目标是：

- 按 09:05、12:15、15:20、22:10 四个时间点运行。
- 覆盖对应时间段内的 7x24 财经快讯流。
- 对东方财富、财联社电报、金十数据、新浪财经、华尔街见闻等来源做去重、正文相似度判断和窗口完整性检查。
- 对重复快讯使用 richness score 保留信息更丰富的一条，评分会参考正文长度、是否有真正正文、数字/实体细节、链接和信源优先级。
- 把总结任务交给 OpenClaw，而不是在采集脚本里直接做最终判断。
- 生成“每日快讯简报” Notion 页面，标题按阶段显示为晨报、午报、收盘报、晚报。
- 只有采集、OpenClaw 轻整理和用户启用的发布步骤都成功时，才把这一轮视为成功。
- 如果机器关机、WSL 未启动或网络中断，依靠 `systemd --user` timer 的 `Persistent=yes` 和 service retry 尽量在恢复后补跑。

当前默认时间点是：09:05 晨报、12:15 午报、15:20 收盘后、22:10 晚报。Notion 页面总标题使用“每日快讯简报”，正式阶段会先检查同一父页面下是否已经存在同名日期/阶段页面，避免补跑时重复发布。Telegram 默认只推送 Notion 链接，不再强制附带 Markdown 文件；只有用户把 `telegram.send_mode` 改成 `document` 或 `media` 时，才会发送附件。

重构后的日报页面不再展示运行窗口元信息、冗余信源行和大块调试字段。总结区保留为连贯文段，原始信息流则按“标题 / 正文 / 时间与来源”分层展示，正文材料不会只截标题，也不会把多源重复快讯原样堆进页面。

日报归档仍会保留本地 Markdown 和 manifest，默认逻辑是：

```text
1. 连贯总结
2. 原始信息流
3. 本地 manifest / 调试归档
```

Notion、Telegram 推送和定时器都应由用户明确选择启用；密钥只通过本地终端或提供商页面输入，不进入聊天和仓库。

### 人民日报深读

人民日报深读是后来新增的长文本工作流，也封装在同一个模块里。它参照手工整理 Notion 的流程：

- 从人民网《人民日报》电子版日期页开始抓取当天全部版面，而不是只抓第 01 版。
- 保存每一版 PDF，用 PDF 保留原始版面布局和可对照阅读入口。
- 抓取每篇文章页的标题、正文、版面、原文链接和 PDF 链接，同时过滤本版责编、版式设计、邮箱等非正文条目。
- 在 Notion 里使用独立父页 `财经政经`，其下是 `人民日报`，再按 `YYYY年MM月DD日` 创建每日日期页；后续运行复用已有父页，不重复创建。
- 日期页按版面列出 PDF 和文章；前 4 版文章默认创建文章深读子页，子页放在对应文章条目下面。
- 文章深读子页采用“逐段原文 + 对应解析 + 全文深度解读”的结构，便于审计原文和解析之间的关系。
- 具体解读 prompt 不随仓库发布；启用人民日报深读时，用户应在本机配置自己的私有 prompt 文件。
- 用 `people_daily_publications.json` 和 Notion 端同名页检查共同防重复；默认不重复创建同一天页面，显式 `--force` 时更新已有日期页内容。

入口脚本：

```bash
~/.openclaw/workspace/market-immersion-module/scripts/run_people_daily_deep_read.sh \
  --date 2026-05-03
```

常用参数：

- `--layout-url`：从指定人民网电子版版面页开始抓取。
- `--manifest`：使用已有 manifest 重新发布或 dry-run。
- `--dry-run`：只验证将生成多少 Notion 块和深读子页，不真正发布。
- `--force`：更新已有日期页内容，默认不启用。

## IMA 知识库接入

OpenClaw 可以通过官方 `ima-skills` 调用腾讯 ima 知识库。这个仓库的 skill 已经加入一套 IMA OpenAPI 配置流程，适合这些场景：

- 查看自己加入或创建的 IMA 知识库。
- 在指定知识库里搜索内容。
- 把网页或微信文章链接加入知识库。
- 上传 PDF、Word、PPT、表格等受支持文件到知识库。
- 创建、搜索、读取或追加 IMA 笔记。

推荐安装路径是：

```powershell
wsl -d Ubuntu -- bash -lc 'openclaw skills search ima'
wsl -d Ubuntu -- bash -lc 'openclaw skills install ima-skills'
```

IMA OpenAPI 需要在 `https://ima.qq.com/agent-interface` 获取 **Client ID** 和 **API Key**。不要把它们发到聊天里，也不要提交进仓库；应通过本地终端提示保存到 Ubuntu：

```text
~/.config/ima/client_id
~/.config/ima/api_key
~/.openclaw/secrets/ima.env
```

然后给 `openclaw-gateway.service` 增加一个 systemd user drop-in，让 gateway 启动时读取 `ima.env`：

```text
~/.config/systemd/user/openclaw-gateway.service.d/ima.conf
```

配置成功后，可以用知识库列表接口做自检。成功时会返回 `code=0` 和知识库 `info_list`，只需要展示知识库名称，不要打印凭证文件内容。

自然语言使用示例：

```text
帮我看看 IMA 里有哪些知识库
搜索“长安投研”里关于 AI 服务器的内容
把这个微信文章链接加入“轻舟的知识库”
上传这个 PDF 到指定 IMA 知识库
```

`ima-skills` 是被动 skill，不会常驻运行，也不会在 gateway 启动时主动访问 IMA。正常情况下，它只是在启动时多读一个环境变量文件，几乎不应影响 OpenClaw 启动速度；如果启动变慢，应优先排查 gateway、插件、sidecar 或代理日志。

## 可选 API 增强：Jina / Tavily

这部分不是基础安装必需项。只有当用户明确需要更强的语义记忆或联网检索时才配置：

- Jina embeddings：给 OpenClaw `memorySearch` 用，负责语义记忆和本地资料检索。
- Tavily web search：给 OpenClaw `web_search` 用，负责当前网页搜索或定期吸收互联网讨论。

本项目提供本地安全输入脚本，不要把 key 发到聊天里：

```powershell
cd .\openclaw-telegram-wsl-setup\tools\openclaw-optional-apis
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Set-JinaApiKey.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Set-TavilyApiKey.ps1
```

脚本会把 key 保存到 `~/.openclaw/secrets/jina.env` 或 `~/.openclaw/secrets/tavily.env`，并把 OpenClaw 配置指向环境变量 SecretRef。这里有一个容易踩坑的点：Jina 的 `memorySearch.remote.apiKey` 不能写成普通字符串 `env:JINA_API_KEY`，而应该用 OpenClaw 的 SecretRef 形式，否则运行时可能把这段字符串当成真正的 API key 发出去，导致看起来像 “Jina 401 Invalid API key”。

如果实际 `memory search` 已经可用，但 `openclaw memory status --deep` 里的 embedding 健康检查仍报 `fetch failed` / TLS socket disconnected，先不要让用户反复换 key。OpenClaw 2026.4.26 的新 CLI 入口可能会在 `memory` 命令启动时提前预热模型上下文窗口缓存，触发模型发现网络请求，并和 Jina embedding 探针同时走代理，造成健康检查误报。可以运行：

```powershell
cd .\openclaw-telegram-wsl-setup\tools\openclaw-optional-apis
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Repair-OpenClawMemoryDeepStatus.ps1
```

然后验证：

```bash
set -a; . ~/.openclaw/secrets/jina.env; set +a
openclaw memory status --deep --json
openclaw memory search --query "OpenClaw" --max-results 3 --json
```

默认不重启 gateway；如果用户希望立刻生效，再选择重启。否则下次 OpenClaw gateway 重启或电脑重启后自然生效。

## 豆包 / 火山录音文件识别

仓库也附带一个很小的 WSL 工具：

```text
openclaw-telegram-wsl-setup/tools/openclaw-doubao-asr/
```

它解决的是“本地音频转文字”这一层，不是把豆包聊天模型伪装成原生音频理解模型。当前结论是：

- 豆包文本模型可以做转写后的风格分析、taxonomy 复核、字幕/转写语气归纳。
- Ark 聊天接口不能直接替代 Gemini 做原生音频理解。
- 极速版录音文件识别默认资源 ID 是 `volc.bigasr.auc_turbo`，适合短音频、本地临时测试。
- 标准版录音文件识别默认资源 ID 是 `volc.seedasr.auc`，适合长音频或已经有公网 URL 的批量任务。
- 脚本只读取本机 `~/.openclaw/secrets/volcengine.env` 里的 key，不把 key 放进仓库。
- 极速版会把本地音频文件上传到火山引擎；标准版会把音频公网 URL 发给火山引擎。处理私人音频前必须得到用户明确同意。

安装命令：

```powershell
cd .\openclaw-telegram-wsl-setup\tools\openclaw-doubao-asr
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Install-DoubaoAsrTool.ps1
```

安装后可以先做本地自检，不上传音频：

```bash
openclaw-doubao-asr --self-check
```

如果火山语音服务页面给的是 `APP ID / Access Token`，用本地终端录入，不要发到聊天里：

```powershell
cd .\openclaw-telegram-wsl-setup\tools\openclaw-doubao-asr
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Set-DoubaoAsrCredentials.ps1
```

如果自检显示 key 存在、资源 ID 是 `volc.bigasr.auc_turbo`，但转写仍失败，优先去火山控制台确认“大模型录音文件识别”资源是否开通、项目是否有权限、套餐或额度是否可用。

实际使用时：

```bash
# 极速版：直接处理本地短音频
openclaw-doubao-asr --mode flash --text-only /path/to/audio.wav

# 标准版：提交火山服务器可访问的音频 URL
openclaw-doubao-asr --mode standard --url "https://example.com/audio.wav" --wait
```

## 本地安装到 Codex

把 skill 文件夹复制到 Codex 的 skills 目录：

```powershell
Copy-Item -Recurse -Force `
  ".\openclaw-telegram-wsl-setup" `
  "$env:USERPROFILE\.codex\skills\openclaw-telegram-wsl-setup"
```

然后开启新的 Codex 会话，使用：

```text
Use $openclaw-telegram-wsl-setup to follow the OpenClaw 养虾指南 on Windows with WSL2.
```

如果你已经在中文对话中调用它，skill 会继续使用中文；如果是新安装流程，它会先确认安装语言。

## 给其他 Agent 使用

虽然这个项目是为 Codex skill 格式整理的，但核心流程都写在 `SKILL.md` 里。理论上，Claude Code 或其他能读取 Markdown 指令的 coding agent 也可以理解并执行其中的大部分流程。

需要注意：

- Codex 会根据 skill metadata 自动触发；其他 agent 可能需要你手动把 `SKILL.md` 作为上下文提供给它。
- 涉及本机命令、WSL、Windows 启动项、GitHub、Telegram token 的步骤，仍然需要用户授权或在本机安全输入。
- 不同 agent 的工具权限不同，实际执行方式可能会调整，但诊断顺序和安全原则是一致的。

## 安全原则

使用或维护这个项目时，请遵守以下规则：

- 不要提交 `~/.openclaw`。
- 不要提交 Telegram bot token、API key、模型凭据、auth profile。
- 不要提交原始日志、包含 token 的截图、本机启动脚本或机器专属配置。
- 不要在聊天里粘贴 bot token、模型 API key 或 auth profile；这些内容应通过本地终端提示或服务商 UI 输入。
- 在最终验证 Telegram 前，必须让用户确认 OpenClaw 的文件可见范围、工具权限和执行权限。
- 不要为了让 Telegram 跑通而放宽文件系统边界或执行策略。
- 不要默认开启模型 fallback，除非用户明确选择。
- keepalive 是基础设施，应该安静可靠地存在，但不要留下不必要的可见命令行窗口。
- network recovery watchdog 是断网恢复基础设施，只应记录状态并在确认网络恢复时重启 gateway 一次；它必须带防抖、冷却和 gateway 启动宽限期，不应因为一次短暂探测失败或启动期间依赖补装反复重启，也不应提交本机日志或机器专属状态文件。

## 维护与发布检查

提交或发布前，建议至少检查一次：

```powershell
git status
Select-String -Path .\openclaw-telegram-wsl-setup\SKILL.md -Pattern '\d{8,12}:[A-Za-z0-9_-]{25,}'
Select-String -Path .\openclaw-telegram-wsl-setup\SKILL.md -Pattern 'token|api_key|secret|password'
```

第一条 token 正则不应该命中任何真实 Telegram token。

第二条关键词扫描可能会命中安全说明、示例变量名或 token-file 示例，这是正常的；但不应该暴露真实密钥。

## 当前状态

这个 skill 已经覆盖从新机安装到常见故障修复的完整路径，尤其强调这些事：

1. **Ubuntu on WSL2 是推荐默认路径。**
2. **keepalive/autostart 是 OpenClaw Telegram 稳定运行的基础设施。**
3. **长时间断网恢复要作为基础设施处理，避免网络恢复后 stale socket / polling stall 影响行动；watchdog 必须防抖并尊重 gateway 启动宽限期，避免误重启造成 Telegram 延迟。**
4. **接入 Telegram 前要先确认 OpenClaw 本地模型可以正常回复。**
5. **OpenClaw 的可见范围和权限范围必须由用户确认，且可以用自然语言表达。**

后续可以继续改进的方向包括：

- 拆分过长的 `SKILL.md`，把详细故障案例放进 `references/`。
- 增加更正式的英文 README。
- 增加用于公开发布的示例 prompt。
- 增加一个最小化验证脚本，检查 skill frontmatter 和敏感信息。
