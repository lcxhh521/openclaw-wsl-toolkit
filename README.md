# OpenClaw 养虾指南

<p align="center">
  <img src="assets/openclaw-mascot.png" alt="OpenClaw 红色小助手" width="120">
</p>

这是一份给 Windows 用户准备的 OpenClaw 使用笔记，也是一套可以装进 Codex 的 skill。

我把它叫“养虾指南”，是因为 OpenClaw 真正麻烦的地方往往不是“能不能跑起来”，而是跑起来以后能不能稳定地待在后台：电脑重启以后还在不在，WSL 休眠以后能不能恢复，断网回来以后 Telegram 机器人还回不回消息，代理一切换会不会又把 gateway 搞挂。

这个仓库想解决的就是这些日常问题。它不假装自己是官方文档，也不把所有事包装成一个一键魔法按钮。它更像一份经过踩坑整理出来的路线图：推荐怎么装、先查哪一层、哪些东西要常驻、哪些东西不能乱动、出了问题怎么判断。

我希望它始终坚持三件事：

- **安全**：token、API key、auth profile、日志、私人 prompt 和机器专属配置不进仓库，也不应该被贴进聊天。
- **透明**：每一步都尽量能解释清楚，少一点“重启试试”的玄学。
- **简单**：能自动恢复的就自动恢复，能放进控制中心看的就别让用户反复敲命令。

## 这个项目解决什么问题

很多时候用户看到的只是“机器人不回消息”。但真正的问题可能在 WSL、gateway、Telegram channel、模型认证、代理、断网恢复，甚至只是 OpenClaw 还没冷启动完。

这份指南的处理顺序很朴素：

1. 先确认 OpenClaw 本地能不能稳定运行。
2. 再确认 gateway 和后台常驻是否可靠。
3. 然后检查 Telegram 是否真正连接。
4. 最后才看 bot token、模型回复、上下文和具体任务。

这样做的好处是，遇到问题时不会一上来就重配 token、换模型、重装 OpenClaw，而是先判断到底是哪一层坏了。

它最开始只是为了解决 OpenClaw + Telegram + WSL2 的稳定性问题。后来慢慢补上了 keepalive、断网恢复、本机控制中心、Token/成本流向、市场信息浸泡模块、语音识别辅助工具和一些可选 API 配置，所以现在更像一个完整的 Windows/WSL 工具包。

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
  -> 长时间断网后的 OpenClaw Network Observer / Netwatch（observe-only）
  -> 本机 OpenClaw 控制中心
  -> Telegram Bot
```

推荐 Ubuntu on WSL2，不是因为它看起来更酷，而是因为 OpenClaw gateway 更像一个需要长期在线的后台服务。放在 Ubuntu 里，systemd、路径、权限、后台常驻和恢复逻辑都更容易讲清楚，也更容易排查。

## 它能做什么

这套东西主要分成八块。

第一块是安装和修复。它会优先按 Windows + WSL2 + Ubuntu 的路线处理 OpenClaw，先把 gateway、systemd user service、模型回复和权限范围确认好，再接 Telegram。

第二块是后台稳定性。它把 keepalive、开机后恢复、长时间断网后的恢复、stale socket / polling stall 清理都当成基础设施，而不是出了问题以后临时补救。

第三块是本机控制中心。它是一个 Windows 小程序，用来启动/关闭 OpenClaw、查看 gateway 和 Telegram 是否可用、观察后台任务、Token/成本流向、最近日志和本地产物心跳。它也可以待在系统托盘里，不需要每次都开浏览器。

现在的控制中心默认走轻量路线：主面板只看生命体征，不再周期性展开 `sessions.list`、`models.list`、`logs.tail`、`tasks audit/show` 这类容易挤占 gateway 的重查询。Token/成本卡片来自离线缓存 `~/.openclaw/monitor-cache/usage-summary.json`，由可选 WSL timer 约每 10 分钟扫描本地 session 文件生成；Token 卡片显示今日流量，成本卡片显示当前自然月累计估算，月初自然归零；浏览器版 Control 保留为 `原生 Control` 高级入口，打开前会提示，因为它可能触发较重的会话/模型查询。

第四块是市场信息浸泡模块。它是一个可选 `openclaw-job-module`，现在包含两条工作流：一条是 7x24 财经快讯的每日快讯简报，一条是《人民日报》电子版/PDF 的要闻版深读。前者按时间段抓取财经快讯流，去重后交给 OpenClaw 写成 Notion 简报；后者按天抓取《人民日报》电子版、PDF 和文章页，只保留要闻版面，在 Notion 日期父页生成全日总览、文章整篇深读，并在文章子页生成结构化原文与解析。这个模块不是基础安装必需项，只有用户明确要市场日报、信息浸泡、人民日报深读或 Notion 闭环时才安装。

第五块是 IMA 知识库接入。它记录了如何给 OpenClaw 安装官方 `ima-skills`，用 IMA OpenAPI 读取和搜索腾讯 ima 知识库、添加网页/微信文章、上传文件、管理笔记，并通过自然语言触发这些能力。

第六块是 translation agent 选装模块。它不是基础安装必需项，只在用户明确需要长文翻译、整书翻译、双语 PDF、翻译排版或专门翻译工作流时启用；它强调 main/Telegram 只做指挥、监督和验收，translation agent 作为隔离执行层通过文件交接、artifact gate 和排版 workflow 交付结果。

第七块是 agent 协作选装模块。它把今晚验证过的 main ↔ Codex mailbox 协作方式整理成独立模块：双方通过 `turn.json` 和 Markdown 消息文件交接，watcher 低频提醒对方继续，不把“进程触发过”误当成“对方已经回复”。这个模块不是基础安装必需项，只有用户明确需要 OpenClaw main 与 Codex/Cursor/Claude Code 等外部 agent 异步协作时才安装。

第八块是可选增强。比如 Jina embeddings、Tavily web search、豆包/火山录音文件识别。这些不是基础安装必需项，只有真的需要语义记忆、联网检索或本地音频处理时再加。

另外，它会特别注意几件容易出事故的事：不要把 token 发到聊天里，不要把 key 写进仓库，不要随便重置配置，不要把“机器人没回”直接等同于“Telegram token 坏了”。

## 项目结构

```text
.
|-- README.md
|-- .gitignore
|-- assets/
|   `-- openclaw-mascot.png
|-- agent-collab/
|   |-- README.md
|   |-- OPTIONAL_INSTALL.md
|   |-- examples/
|   `-- scripts/
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
    |-- docs/
    |   |-- translation-agent-contract.md
    |   `-- translation-agent-isolation-protocol.md
    `-- tools/
        |-- openclaw-local-monitor/
        |   |-- OpenClawMonitor.cs
        |   |-- Build-OpenClawMonitor.ps1
        |   |-- Generate-OpenClawMonitorIcon.ps1
        |   |-- Install-OpenClawMonitor.ps1
        |   |-- Install-UsageCache.ps1
        |   |-- Install-ReliabilityObserver.ps1
        |   |-- Install-Autostart.ps1
        |   |-- Uninstall-Autostart.ps1
        |   |-- openclaw-usage-cache.mjs
        |   |-- openclaw-reliability-observer.mjs
        |   |-- OpenClawMonitor.ico
        |   `-- README.md
        |-- openclaw-netwatch/
        |-- wsl-safe/
        |   |-- openclaw-netwatch
        |   |-- openclaw-netwatch.service
        |   |-- openclaw-netwatch.timer
        |   |-- Install-OpenClawNetwatch.ps1
        |   |-- Uninstall-OpenClawNetwatch.ps1
        |   `-- README.md
        |-- openclaw-doubao-asr/
        |   |-- openclaw-doubao-asr
        |   |-- Install-DoubaoAsrTool.ps1
        |   |-- Set-DoubaoAsrCredentials.ps1
        |   `-- README.md
        |-- openclaw-optional-apis/
        |   |-- Set-JinaApiKey.ps1
        |   |-- Set-TavilyApiKey.ps1
        |   |-- Repair-OpenClawMemoryDeepStatus.ps1
        |   |-- save-openclaw-jina-key.sh
        |   |-- save-openclaw-tavily-key.sh
        |   |-- repair-openclaw-memory-deep-status.py
        |   |-- Verify-JinaKey.py
        |   `-- Verify-TavilyKey.py
        `-- translation-agent/
            |-- translation_handoff.py
            `-- translation_artifact_gate.py
```

真正的 Codex skill 仍然是：

```text
openclaw-telegram-wsl-setup/
```

这个目录名暂时保留是为了兼容已经安装的 Codex skill 和旧链接；公开项目名称以 **OpenClaw 养虾指南** 为准。这个目录应保持干净，只包含 skill 本身需要的文件和可复用工具。不要把本机 OpenClaw 配置、Telegram token、日志、截图、编译产物或机器专属诊断文件放进去。

## 快速安装到 Codex

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

如果你已经在中文对话中调用它，skill 会继续使用中文；如果是新安装流程，它会先确认安装语言和关键选择。

## WSL 安全命令入口

`openclaw-telegram-wsl-setup/tools/wsl-safe/Invoke-WslSafe.ps1` 是 Windows 侧的轻量辅助脚本，用来把多行 Bash/Python 任务安全地送进 Ubuntu on WSL。它会把 CRLF/CR 统一成 LF，使用 UTF-8 no BOM 临时文件，避免 PowerShell 引号、换行和编码把 OpenClaw 维护脚本搅坏。

它适合 Codex/OpenClaw 维护、状态检查、仓库同步、只读诊断等场景；不保存 secrets，默认清理临时文件。因为仓库里的 PowerShell 脚本通常未签名，手动运行时建议先用 `Install-InvokeWslSafe.ps1` 安装到 `%LOCALAPPDATA%\OpenClawWslTools\`，再用 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File ...` 只对本次调用放行；不要依赖直接从 `\\wsl.localhost` 执行 ps1。涉及删除、重启、真实发送或配置写入时，仍然需要按任务本身取得用户授权。

## 本机 OpenClaw 控制中心

仓库附带一个 Windows 原生控制中心：

```text
openclaw-telegram-wsl-setup/tools/openclaw-local-monitor/
```

它是本机的主入口，不替代 OpenClaw 官方浏览器 Control UI。打开 `OpenClaw Control` 只会显示本机状态，不会自动启动或关闭 OpenClaw。需要运行时点击 `开启 OpenClaw`；运行中按钮会变成 `关闭 OpenClaw`，再次点击才会真正关闭。

面板主要显示：

- gateway 和 Telegram 是否可用。
- Telegram 是否已连接；冷启动细节会在顶部状态框内部用临时进度条显示，而不是塞进 Telegram 卡片。
- 后台是否存在 `queued/running` task、活跃 TaskFlow，或正在持续产出的本地 daemon / 工作区产物心跳。
- 今日 Token / 输入 Token / 输出 Token / 缓存读取 / 已记录成本。这些卡片只读离线缓存，约每 10 分钟更新一次；Token 是今日流量，成本是当前自然月累计估算，金额不等同服务商账单。
- 最近会话和少量状态提醒；主面板不把日志和任务审计当作自动刷新源。
- 系统托盘常驻能力。

### 安装控制中心

```powershell
cd .\openclaw-telegram-wsl-setup\tools\openclaw-local-monitor
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Install-OpenClawMonitor.ps1
```

安装脚本会把源码复制到：

```text
%LOCALAPPDATA%\OpenClawMonitor
```

然后在本机编译 `OpenClawMonitor.exe`，创建 `OpenClaw Control` 桌面、开始菜单和 Startup-folder 快捷方式，清理旧的 `OpenClaw Monitor` / `OpenClaw 启动` 等旧入口，并启动控制中心。

如需让 Token/成本卡片显示离线缓存，在同一目录安装可选 usage cache timer：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Install-UsageCache.ps1
```

它会在 Ubuntu 里安装 `openclaw-usage-cache` 和 systemd user timer，约每 10 分钟写一次 `~/.openclaw/monitor-cache/usage-summary.json`。这个采集器只扫描本地 session 文件，不连接 gateway、不重启 OpenClaw、不改配置、不碰 secrets。

如需让控制中心解释最近的“Telegram 没回但不知道为什么”，在同一目录安装可选 reliability observer：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Install-ReliabilityObserver.ps1
```

它会约每 1 分钟写一次 `~/.openclaw/monitor-cache/reliability-status.json`，数据来自本地日志、`openclaw-gateway.service` 用户 journal 和 stability 文件。它只做观察：不发 Telegram、不自动重试、不重启 gateway、不调用 `tasks audit/show`、不改配置/模型/binding/session，也不碰 secrets。

### 原生 Control

控制中心里的 `原生 Control` 按钮会调用本地 `Start-OpenClaw.ps1`。这个脚本只在本机临时解析 OpenClaw 网关令牌，并生成带 `#token=...` 的浏览器 Control URL；令牌不写进仓库、不打印到聊天、不提交到日志。这样用户不需要每次手动粘贴网关 token。

这个入口只在 gateway 已经运行时打开浏览器 Control。如果 OpenClaw 停止，面板会提示先点 `开启 OpenClaw`，而不是隐式启动。浏览器 Control 可能触发比本地面板更重的 session/model 查询，所以控制中心会先确认；它适合临时高级操作，不适合长期挂着当状态面板。

### 主面板刷新

主面板是轻量生命体征视图，不再保留一个容易误解的“重新检测”按钮，也不做周期性重型刷新。窗口打开、显式开启/关闭 OpenClaw、托盘恢复时可以读取轻量状态；更深的排查放到 `诊断` 弹窗。

`诊断` 是只读排查入口，用于看 Gateway Resilience、Network Stability、Entrance Pressure、sessions 和 task pressure。它不自动重启、不 cleanup、不 maintenance apply、不 kill 进程、不改模型/binding/config/secrets/session。

OpenClaw 冷启动时，面板会先做轻量探测，先看 gateway 和 Telegram；不会为了补齐 Token、成本、会话或日志而反过来拖慢 OpenClaw 启动。

### Clash 安全模式

`Clash 安全模式` 只针对一个特定网络场景：用户为了让 OpenClaw、Codex 或其他国外大模型稳定走代理，开启了 Clash Verge 的 TUN 或全局式路由，但同时发现微信、腾讯服务或国内网页不能正常访问。

开启后，控制中心会通过 Clash Verge Rev 暴露的本地 Mihomo 管道把核心维持在规则模式，让 OpenClaw/Codex 命中 `GLOBAL` 代理组，国内流量继续按规则直连。换节点时只需要在 Clash Verge 的 `GLOBAL` 组里选择节点；这个功能不绑定某个国家或具体节点。

如果没有开全局/TUN，或者国内应用本来就正常，通常不用开启这个选项。

## 市场信息浸泡模块

仓库附带一个可选的市场信息浸泡模块：

```text
modules/openclaw-market-immersion/
```

这是一个 OpenClaw 长期任务模块，不是交易建议系统。

它的职责是到点收集市场快讯流，保留完整原始信息，再交给 OpenClaw 生成连贯的信息汇总，最后生成本地归档；如果用户明确启用，也可以发布到 Notion 或推送到 Telegram。

### 模块边界

- 模块类型：OpenClaw job module
- 入口脚本：`scripts/run_market_immersion.sh <phase>`
- 主程序：`scripts/market_immersion.py`
- 配置文件：`config/market_immersion_config.json`
- systemd 定时器：`systemd/*.timer`
- 输出目录：`~/.openclaw/workspace/market-immersion`

公开仓库里的配置保持安全默认值：Notion/Telegram 默认关闭，page ID、chat target 留空，用户私有配置只写进安装后的本机文件。

### 每日快讯简报

阶段：

- `morning`：09:05 盘前
- `midday`：12:15 午间
- `close`：15:20 收盘后
- `night`：22:10 夜间
- `smoke`：连通性测试，不发布 Notion / Telegram

闭环逻辑：

1. systemd timer 到点触发对应 phase。
2. 模块按时间窗口拉取多源 7x24 快讯流。
3. 模块会尽量让每个源扫到窗口起点；如果个别源历史窗口不足，会在质量提示里标记 coverage warning，而不是阻塞整份日报发布。
4. OpenClaw 必须生成连贯的“信息汇总”自然段，否则任务失败。
5. 报告下方保留按时间顺序排列的完整“原始消息流”，方便回看和审计。
6. 如果用户在配置里启用 Notion，正式阶段必须成功发布 Notion。
7. 如果用户在配置里启用 Telegram，正式阶段会尝试推送日报链接或文件。
8. 必要的闭环步骤全部成功后才更新 `state.json` 的 `last_success_at`。

当前信息源：

- 东方财富财经资讯与 7x24 快讯栏目
- 财联社电报
- 金十数据快讯
- 新浪财经 7x24
- 同花顺实时快讯
- 华尔街见闻 7x24

日报归档保留本地 Markdown 和 manifest。页面主体不再拆成固定 8 个栏目，而是：

```text
1. 信息汇总：默认 4-6 个自然段，合并同主题消息，保留具体主体、数字和事件细节。
2. 原始消息流：按时间顺序展示标题、正文、时间与来源。
3. 本地 manifest / 调试归档：供排查和复盘。
```

运行示例：

```bash
~/.openclaw/workspace/market-immersion-module/scripts/run_market_immersion.sh morning
~/.openclaw/workspace/market-immersion-module/scripts/run_market_immersion.sh --phase smoke --no-publish
```

查看定时器：

```bash
systemctl --user list-timers "openclaw-market-immersion*" --all
```

查看最近日志：

```bash
journalctl --user -u openclaw-market-immersion-morning.service -n 100 --no-pager
```

### 数据源健康、备用接口与禁止降级发布

模块现在把“数据源失效处理”和“禁止自动降级发布”分开写清楚：

- `allow_degraded_publication` 默认并应保持为 `false`；缺源、跳过失败源或发布不完整日报都需要用户明确批准。
- 每次抓取会生成 `source_health`，列出失败/窗口不足的数据源、错误原因、可用备用接口和建议动作；该信息默认只留在 manifest/operator diagnostics，不进入用户可见日报，除非显式开启调试输出。
- 备用方案不是高频本地快照，而是 `config/source_registry.json` 维护的当前主源接口注册表；公开仓库只发布当前主源，不发布额外备用接口候选。
- `scripts/verify_source_interfaces.py` 会低频验证候选接口是否与官方网站展示一致；只有验证通过且不是当前 primary 的候选才可作为真正的 `backup_ready`。
- `openclaw-source-interface-verification.timer` 默认每月 1 日和 16 日 07:05 CST 运行一次，用于常备验证，不发布日报、不替换主源。
- 实际日报抓取始终主源优先；主源失败或窗口覆盖不足时，应先尝试已验证备用接口补齐/替代，下一轮主源恢复后自动切回主源。
- 若没有已验证备用接口，不要把内部健康检查块混入正文；可以先发布一版**降级日报**，但格式必须与正常日报一致，只在开头用简短提示说明“本版暂未覆盖哪些源/时间窗口”。随后立即启动备用接口搜索/验证；找到可用替代源后，发布一版**缺失源补充简报**。
- **缺失源补充简报**复用正常每日简讯流程和版式，但范围只限此前缺失源/缺失窗口：顶部要有“仅补充 X 版降级日报缺失的 Y 源/Z 时间段”说明；「信息汇总」不是全日/全市场判断，而是只对这部分补回信息做有边界的总结，不能推出超出缺失源范围的判断；「原始消息流」仍按正常信息流形式列出补回的全部原始信息。

`openclaw-market-feed-snapshot.timer` 不作为默认方案启用；只有在主要接口失效且暂时找不到替代接口时，才可临时开启快照兜底。具体设计见 `modules/openclaw-market-immersion/docs/source_interface_failover.md`。

### 大输入传输与 prompt 边界

`model-inputs/` 是大模型输入传输机制的公开契约，不是运行数据目录。仓库只提交 `model-inputs/README.md` 和占位 manifest，用来说明大 prompt 如何以本地文件 + SHA-256 的方式交给模型通道读取。

- 日报的 prompt 构建逻辑属于稳定工作流，可以随 `market_immersion.py` 同步。
- 真实运行生成的 `*.prompt.txt`、原始信息流、模型输出、manifest 和审稿产物不进仓库。
- 人民日报深读的具体解读 prompt 不随仓库发布；仓库只保留 `~/.openclaw/private-prompts/people_daily/` 私有路径约定、流程代码和 JSON 契约。
- 如果需要示例，只提交 placeholder，不提交真实文章、日报信息流、用户对话或模型输入。


### 人民日报深读

人民日报深读是独立于快讯日报的长文本子流程，但封装在同一个模块里。当前流程按“要闻版深读”而不是“全报归档”设计：

1. 抓取当天电子版、PDF 和文章正文，但只保留版面标签为“要闻”的页面与正文文章。
2. 在 Notion 的人民日报日期页下生成父页：全日总览、要闻版 PDF、按版面展开的文章列表。
3. 父页承载每篇文章的“整篇深度解读”，不放长篇逐段原文。
4. 每篇保留文章创建子页，子页承载“结构化原文与解析”：按意义单元分组，不机械逐自然段。
5. 单篇文章分析概念上仍是两个源 prompt：`article_full_analysis_v1.md` 和 `article_structured_groups_v1.md`；生产上可以由脚本动态合并为一次模型调用，返回 `full_analysis + structured_groups`。
6. 脚本质量门只做结构性硬校验：JSON/prompt_id、`full_analysis` 非空、`structured_groups` 覆盖全部输入段落、`paragraph_indices` 合法；风格和内容质量留在 prompt 自检与人工复核。
7. 版务、责编、版式设计等非正文条目自动过滤；用 `people_daily_publications.json` 记录已发布日期，避免重复创建。

入口脚本：

```bash
~/.openclaw/workspace/market-immersion-module/scripts/run_people_daily_deep_read.sh \
  --layout-url "https://paper.people.com.cn/rmrb/pc/layout/202605/03/node_01.html"
```

常用参数：

- `--date YYYY-MM-DD`：按日期从第 01 版开始抓取。
- `--max-pages 1`：只抓前 N 个版面，适合测试。
- `--delay 120`：自动请求间隔，默认尊重人民网 robots 的 crawl-delay。
- `--manifest PATH`：使用已抓取的 manifest 重新发布或测试。
- `--dry-run`：只验证将要生成的 Notion 页面数量，不真正发布。
- `--force`：更新已有日期页内容，默认不会重复创建同一天页面。

输出目录默认是：

```text
~/.openclaw/workspace/people-daily-deep-read/YYYY-MM-DD/
```

包含 `manifest.json`、PDF 原件、Markdown 归档、分析缓存，以及本地 HTML 对照页；这些只是内部审计材料。正式发布时会调用 OpenClaw 为每篇保留文章生成 `full_analysis` 和 `structured_groups`：父页展示“整篇深度解读”，子页用 `paragraph_indices` 回填浅色原文并展示结构组解析。若启用 Telegram 完成提醒，只发送 Notion 链接，不发送本地 Markdown、manifest、缓存或输出目录。

### 配置自己的人民日报深读 prompt

具体解读 prompt 不随仓库发布。仓库内置的只是流程、页面结构和结构性 JSON 契约；如果启用人民日报深读，建议用户在本机配置自己的私有 prompt。

1. 在本机创建不提交到 GitHub 的 prompt 目录，例如：

```bash
mkdir -p ~/.openclaw/private-prompts/people_daily
nano ~/.openclaw/private-prompts/people_daily/article_full_analysis_v1.md
nano ~/.openclaw/private-prompts/people_daily/article_structured_groups_v1.md
nano ~/.openclaw/private-prompts/people_daily/issue_overview_v1.md
```

2. 两个文章级源 prompt 分别负责两个任务：

- `article_full_analysis_v1.md`：生成 `full_analysis`。
- `article_structured_groups_v1.md`：生成 `structured_groups`。

生产上可以设置 `combined_call: true`，由脚本运行时动态读取两个源 prompt 并合并成一次模型调用；不需要维护一个单独的 combined prompt 文件。

3. 合并调用的输出 JSON 结构为：

```json
{
  "prompt_id": "people_daily_article_combined_v1_2026-05-06",
  "full_analysis": ["全文深度解读"],
  "signal_analysis": ["可选：信号/语境分析"],
  "policy_chain": ["可选：政策链路或观察点"],
  "follow_up": ["可选：后续跟踪事项"],
  "structured_groups": [
    {
      "title": "结构组标题",
      "paragraph_indices": [1, 2],
      "analysis": "这一组为什么要放在一起读"
    }
  ]
}
```

4. 在安装后的本机配置 `config/market_immersion_config.json` 中填写私有 prompt 路径：

```json
"people_daily_deep_read": {
  "analysis": {
    "combined_call": true,
    "required_prompt_id": "people_daily_article_combined_v1_2026-05-06",
    "full_analysis": {
      "prompt_template_path": "~/.openclaw/private-prompts/people_daily/article_full_analysis_v1.md",
      "required_prompt_id": "people_daily_full_analysis_v1_2026-05-06"
    },
    "structured_groups": {
      "prompt_template_path": "~/.openclaw/private-prompts/people_daily/article_structured_groups_v1.md",
      "required_prompt_id": "people_daily_structured_groups_v1_2026-05-06"
    },
    "overview": {
      "prompt_template_path": "~/.openclaw/private-prompts/people_daily/issue_overview_v1.md",
      "required_prompt_id": "people_daily_overview_v1_2026-05-06"
    }
  }
}
```

5. 确认 prompt 文件没有被放进仓库；如果使用 git 管理自己的配置，请把私有 prompt 路径加入 `.gitignore`。

### 安装市场模块

该模块不是默认安装内容。用户明确选择安装后，可以按下面的本机安装路径复制模块：

```bash
mkdir -p "$HOME/.openclaw/workspace"
cp -a /path/to/modules/openclaw-market-immersion "$HOME/.openclaw/workspace/market-immersion-module"
chmod +x "$HOME/.openclaw/workspace/market-immersion-module/scripts/"*.sh
python3 -m compileall "$HOME/.openclaw/workspace/market-immersion-module/scripts"
python3 -m json.tool "$HOME/.openclaw/workspace/market-immersion-module/config/market_immersion_config.json" >/dev/null
```

启用 Notion 或 Telegram 推送前，只修改安装后的本机配置，不要把 page ID、chat target 或 token 提交回仓库。

安装 timers 也需要用户明确选择：

```bash
mkdir -p "$HOME/.config/systemd/user"
cp "$HOME/.openclaw/workspace/market-immersion-module/systemd/"* "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
systemctl --user enable --now openclaw-market-immersion-morning.timer
systemctl --user enable --now openclaw-market-immersion-midday.timer
systemctl --user enable --now openclaw-market-immersion-close.timer
systemctl --user enable --now openclaw-market-immersion-night.timer
# 如果用户同时选择启用人民日报日更深读，再启用：
systemctl --user enable --now openclaw-people-daily-deep-read.timer
# 如果用户选择启用备用接口低频验证，再启用：
systemctl --user enable --now openclaw-source-interface-verification.timer
```

`openclaw-market-feed-snapshot.timer` 是接口失效且暂无可用备用接口时的临时兜底快照，不默认启用；正常情况下依赖主源优先、已验证备用接口临时 failover、主源恢复后自动 failback。


## 可选模块：Agent collaboration v0

`agent-collab/` 是一个独立选装模块，用来让 OpenClaw main 和外部 agent（例如 Codex、Cursor、Claude Code 或其他本地 coding agent）通过共享 mailbox 异步协作。它不属于基础 OpenClaw/Telegram 安装，也不会默认启用；只有用户明确需要“多个 agent 互相接力、讨论、交付 artifact”时才安装。

核心文件是：

```text
agent-collab/README.md
agent-collab/OPTIONAL_INSTALL.md
agent-collab/examples/turn.example.json
agent-collab/scripts/openclaw-main-mailbox-watch.py
agent-collab/scripts/codex-mailbox-watch.py
```

协作方式很简单：

- Main 提醒 Codex：main 写 `main_to_codex.md`，把 `turn.json` 更新为 `needs_reply=codex`，Codex 侧 watcher 看到后执行本机 `CODEX_WAKE_COMMAND`。
- Codex 提醒 Main：Codex 写 `codex_to_main.md`，把 `turn.json` 更新为 `needs_reply=main`，OpenClaw 侧 watcher 看到后调用 `openclaw agent --session-id ...` 唤醒 main。
- 判断是否闭环只看 `turn.json` 是否被预期一方推进；不能只看 watcher 是否启动过进程。

安装时应按 `agent-collab/OPTIONAL_INSTALL.md` 走：创建 mailbox，配置 main-side watcher，再配置 Codex/external-agent watcher。watcher 必须低频运行，带 lock、重试间隔和最大尝试次数；不要做高频状态轮询，不要自动发布 Telegram/Notion，不要读取 secrets，不要删除用户文件。

## 可选模块：Translation agent 契约与隔离

Translation agent 是选装/可选模块，不是 OpenClaw Telegram WSL 基础安装必需项。只有用户明确需要长文翻译、整书翻译、双语 PDF、翻译排版或专门翻译工作流时，才需要安装或启用。仓库现在同步了 translation agent 的公开契约与主从隔离协议：

```text
openclaw-telegram-wsl-setup/docs/translation-agent-contract.md
openclaw-telegram-wsl-setup/docs/translation-agent-isolation-protocol.md
openclaw-telegram-wsl-setup/tools/translation-agent/
```

启用该可选模块后，核心边界是：main/Telegram 是指挥、监督和验收层；translation agent 是隔离执行层。非小型翻译任务应通过 file-based handoff 运行：保留 Alex 原始请求，生成 `handoff_brief.md`、`task_ledger.json` 和 `acceptance_plan.json`，translation agent 只回小型 JSON envelope，main 再独立验收 artifact。

整书/长文翻译固定要求包括：按章节/自然边界拆分，worker 只写文件并回 `DONE <artifact_file> <byte_count>`，artifact gate 通过前不接受完成状态；先 coverage audit/repair，再 audited content freeze，再排版/PDF，最后做覆盖、页数/词数、字体、表格、乱码、空白页和双语节奏验证。

排版方案也已固化：整书双语 PDF 不直接套 raw Markdown/CSS，必须先构建 normalized IR；默认采用干净书籍式排版，英文段落在上、中文段落在下；不用卡片底纹、重边框、左侧竖线或低对比英文；每个新章节另起一页，但不对每个小节/段落滥用分页；复杂 OCR 表格默认用等宽 `pre` 保留，不假装成错列 HTML table。

重要排版决策应在 translation/layout workflow 内部完成：GLM 和 MiniMax 分别产出详细方案；GLM、MiniMax、GPT 三方都参与排版评价与讨论，分别判断哪些设计应保留、哪些应改进，最后在 workflow 内收敛出最优 `layout_final_brief.md`。GPT 必须由 translation agent/layout workflow 调用并写入 artifact，不能由 main 主脑直接调用 GPT 并综合方案。

## IMA 知识库接入

OpenClaw 可以通过官方 `ima-skills` 调用腾讯 ima 知识库。这个仓库的 skill 加入了一套 IMA OpenAPI 配置流程，适合这些场景：

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

脚本会把 key 保存到 `~/.openclaw/secrets/jina.env` 或 `~/.openclaw/secrets/tavily.env`，并把 OpenClaw 配置指向环境变量 SecretRef。

这里有一个容易踩坑的点：Jina 的 `memorySearch.remote.apiKey` 不能写成普通字符串 `env:JINA_API_KEY`，而应该用 OpenClaw 的 SecretRef 形式，否则运行时可能把这段字符串当成真正的 API key 发出去，导致看起来像 “Jina 401 Invalid API key”。

如果实际 `memory search` 已经可用，但 `openclaw memory status --deep` 里的 embedding 健康检查仍报 `fetch failed` / TLS socket disconnected，先不要让用户反复换 key。可以运行修复脚本：

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

## 豆包 / 火山录音文件识别

仓库附带一个小型 WSL 工具：

```text
openclaw-telegram-wsl-setup/tools/openclaw-doubao-asr/
```

它解决的是“本地音频转文字”这一层，不是把豆包聊天模型伪装成原生音频理解模型。

当前定位：

- 豆包文本模型可以做转写后的风格分析、taxonomy 复核、字幕/转写语气归纳。
- Ark/Doubao 聊天接口不能直接替代原生音频理解模型。
- Flash ASR 默认资源 ID 是 `volc.bigasr.auc_turbo`，适合短音频、本地临时测试。
- Standard ASR 默认资源 ID 是 `volc.seedasr.auc`，适合长音频或已经有公网 URL 的批量任务。
- 脚本只读取本机 `~/.openclaw/secrets/volcengine.env` 里的 key，不把 key 放进仓库。
- Flash 模式会把本地音频文件上传到火山引擎；Standard 模式会把音频公网 URL 发给火山引擎。处理私人音频前必须得到用户明确同意。

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

实际使用时：

```bash
openclaw-doubao-asr --mode flash --text-only /path/to/audio.wav
openclaw-doubao-asr --mode standard --url "https://example.com/audio.wav" --wait
```

其中第一条是 Flash 模式，直接处理本地短音频；第二条是 Standard 模式，提交火山服务器可访问的音频 URL。

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
- 不要提交 Notion page ID、Telegram chat target、用户账号 ID。
- 不要提交原始日志、包含 token 的截图、本机启动脚本、机器专属配置或本地运行状态。
- 不要提交私有 prompt、聊天记录、长期记忆、生成日报、OCR 全文或第三方截图内容。
- 不要在聊天里粘贴 bot token、模型 API key 或 auth profile；这些内容应通过本地终端提示或服务商 UI 输入。
- 在最终验证 Telegram 前，必须让用户确认 OpenClaw 的文件可见范围、工具权限和执行权限。
- 不要为了让 Telegram 跑通而放宽文件系统边界或执行策略。
- 不要默认开启模型 fallback，除非用户明确选择。
- keepalive 是基础设施，应该安静可靠地存在，但不要留下不必要的可见命令行窗口。
- keepalive 只能负责保活和 `systemctl --user start openclaw-gateway.service`，不要用 `restart`。重复触发 keepalive 不应该打断已经运行的 gateway。
- OpenClaw Network Observer / Netwatch 是网络与 gateway 恢复信号观测基础设施，只应记录状态和恢复建议，不应自动重启 gateway；它必须带防抖、冷却和 gateway 启动宽限期，不应因为一次短暂探测失败或启动期间依赖补装制造重启链路，也不应提交本机日志或机器专属状态文件。

## 发布前检查

提交或发布前，建议至少检查一次：

```bash
git status
python3 -m py_compile $(git ls-files '*.py')
for f in $(git ls-files '*.sh' 'openclaw-telegram-wsl-setup/tools/openclaw-doubao-asr/openclaw-doubao-asr'); do bash -n "$f"; done
git grep -n -I -E 'gho_|github_pat_|telegram:[0-9]+|NOTION_TOKEN=|BOT_TOKEN=' || true
```

关键词扫描可能会命中安全说明、示例变量名或 token-file 示例，这是正常的；但不应该暴露真实密钥、私人 ID 或本机路径。

### GitHub 同步硬规则：系统整体对齐

当把本地最新进度同步到 GitHub 时，不能只补单个脚本或单段说明。必须把 skill、README、模块文件、程序入口、systemd/timer、配置模板、工具脚本和安全边界视为一个系统整体来检查和更新。

每次同步前至少要做一次整体对齐：

1. 对照本地真实运行状态和最新决策，确认 GitHub 里的 `README.md`、`openclaw-telegram-wsl-setup/SKILL.md`、`modules/`、`market-immersion-module/`、`tools/`、`systemd/`、配置模板与实际程序行为一致。
2. 如果程序行为、安装步骤、默认启用项、定时器、可选模块、交付口径或安全边界变了，相关 README / SKILL / 模块文档必须同步更新，不能只推代码。
3. 反过来，如果文档承诺了某个能力，也要核对程序和配置模板确实支持；不支持就改文档或补程序，不能让公开仓库形成虚假的系统说明。
4. 只发布可公开复用的抽象和模板；本机路径、私有接口细节、token、账号 ID、Notion/Telegram 目标、日志、生成内容和运行状态仍必须留在本地。
5. 推送说明要写清楚“本次系统对齐覆盖了哪些层”：skill、README、程序、配置模板、systemd/timer、工具脚本、验证结果；如果有未覆盖项，要明确列为待办或本地专属不发布。

## 当前状态

这个 skill 已经覆盖从新机安装到常见故障修复的完整路径，尤其强调这些事：

1. **Ubuntu on WSL2 是推荐默认路径。**
2. **keepalive/autostart 是 OpenClaw Telegram 稳定运行的基础设施。**
3. **长时间断网恢复要作为基础设施处理，避免网络恢复后 stale socket / polling stall 影响行动；Network Observer / Netwatch 必须保持 observe-only、防抖并尊重 gateway 启动宽限期，避免误重启造成 Telegram 延迟。**
4. **接入 Telegram 前要先确认 OpenClaw 本地模型可以正常回复。**
5. **OpenClaw 的可见范围和权限范围必须由用户确认，且可以用自然语言表达。**

后续可以继续改进的方向包括：

- 拆分过长的 `SKILL.md`，把详细故障案例放进 `references/`。
- 增加更正式的英文 README。
- 增加一个最小化验证脚本，检查 skill frontmatter 和敏感信息。
