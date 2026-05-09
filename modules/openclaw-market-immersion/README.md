# OpenClaw Market Immersion Module

这是一个 OpenClaw 长期任务模块，不是交易建议系统。

它的职责是到点收集市场快讯流，保留完整原始信息，再交给 OpenClaw 生成连贯的信息汇总，最后生成本地归档；如果用户明确启用，也可以发布到 Notion 或推送到 Telegram。

## 模块边界

- 模块类型：OpenClaw job module
- 入口脚本：`scripts/run_market_immersion.sh <phase>`
- 主程序：`scripts/market_immersion.py`
- 配置文件：`config/market_immersion_config.json`
- systemd 定时器：`systemd/*.timer`
- 输出目录：`~/.openclaw/workspace/market-immersion`

## 阶段

- `morning`：09:05 盘前
- `midday`：12:15 午间
- `close`：15:20 收盘后
- `night`：22:10 夜间
- `smoke`：连通性测试，不发布 Notion / Telegram

## 闭环逻辑

1. systemd timer 到点触发对应 phase。
2. 模块按时间窗口拉取多源 7x24 快讯流。
3. 模块会尽量让每个源扫到窗口起点；如果个别源历史窗口不足，会在质量提示里标记 coverage warning，而不是阻塞整份日报发布。
4. OpenClaw 必须生成连贯的“信息汇总”自然段，否则任务失败。
5. 报告下方保留按时间顺序排列的完整“原始消息流”，方便回看和审计。
6. 如果用户在配置里启用 Notion，正式阶段必须成功发布 Notion。
7. 如果用户在配置里启用 Telegram，正式阶段会尝试推送日报链接或文件。
8. 必要的闭环步骤全部成功后才更新 `state.json` 的 `last_success_at`。

## 市场简报写作标准

每日快讯简报服务的是市场洞察，不是财经媒体式新闻摘要。生成流程要先全面吸收阶段性信息流，再过滤噪音，最后输出对市场理解有帮助的「信息汇总」。

核心原则：

- **全面但降噪**：不能只抓最吵的高频新闻；高频重复信息只合并写一次，低频但有订单、审批、政策、监管、价格异动、产业链数据、出海验证的信息要进入候选池。
- **洞察来自证据**：可以做市场洞察，但判断必须贴着原始信息流里的主体、数字、事件和相互关系，不预测涨跌，不给买卖建议。
- **阶段边界**：`morning`、`midday`、`close` 只输出阶段性市场洞察，不能凭单一阶段信息流下全天结论；只有 `night` / 综合复盘可以形成全天判断，且必须基于全天信息流证据。
- **不教条**：覆盖类别只是后台检查工具，不是前台填空模板。没有增量的类别可以不写，低价值信息不能为了“覆盖全面”被硬塞进总览。
- **段落弹性**：信息汇总默认 4-6 个自然段；如果信息密度确实需要，可以超过预设段落数，但不能靠空话扩段。
- **前台表达**：正文不展示后台分类，不写“本次窗口/补发/没有混入”等工程口径，也不写“不该写成/后续简报应”等批改口吻。

后台可用的降噪/覆盖检查包括：主线风险或地缘变量、市场价格（股指/油价/黄金/汇率/利率等）、国内政策与产业、公司/订单/审批/出海、海外宏观与贸易、低频高价值信号、重要分歧和反证。它们用于帮助判断遗漏，不是要求每份简报机械填满所有类别。

## 当前信息源

- 东方财富财经资讯与 7x24 快讯栏目
- 财联社电报
- 金十数据快讯
- 新浪财经 7x24
- 同花顺实时快讯
- 华尔街见闻 7x24

## 运行

```bash
~/.openclaw/workspace/market-immersion-module/scripts/run_market_immersion.sh morning
```

查看定时器：

```bash
systemctl --user list-timers "openclaw-market-immersion*" --all
```

数据源健康、备用接口与禁止降级发布说明：[`docs/source_interface_failover.md`](docs/source_interface_failover.md)。`openclaw-market-feed-snapshot.timer` 不作为默认方案启用；只有在主要接口失效且暂时找不到替代接口时，才临时开启 30min 快照兜底。

人民日报深读日更流程说明：[`docs/people_daily_deep_read_workflow.md`](docs/people_daily_deep_read_workflow.md)。

查看最近日志：

```bash
journalctl --user -u openclaw-market-immersion-morning.service -n 100 --no-pager
```

## 数据源健康、备用接口与禁止降级发布

每日快讯简报不应因为个别数据源失败就自动发布降级版。模块当前规则：

- `pipeline.allow_degraded_publication` 默认 `false`；降级发布、跳过失败源或补发不完整版本都需要用户明确批准。
- 每次抓取会在 manifest 中写入 `source_health`，列出失败/窗口不足的数据源、错误原因、恢复/备用接口建议。
- 当前主源接口可以写在公开 `config/source_registry.json`；额外备用接口候选只放安装后的本机私有 registry，不随公开仓库发布。`scripts/verify_source_interfaces.py` 负责只读验证。
- `openclaw-source-interface-verification.timer` 默认每月 1 日和 16 日 07:05 CST 运行一次，低频验证候选接口，不发布日报、不替换主源。
- 日报运行时主源优先；只有主源当前失败时，才使用最近验证为 `backup_ready` 的候选接口；下一轮主源恢复后自动 fail back。

具体设计见 [`docs/source_interface_failover.md`](docs/source_interface_failover.md)。

## 人民日报深读

人民日报深读是独立于快讯日报的长文本子流程，但封装在同一个模块里。当前流程按“要闻版深读”而不是“全报归档”设计：

1. 抓取当天电子版、PDF 和文章正文，但只保留版面标签为“要闻”的页面与正文文章。
2. 在 Notion 的 `财经政经 / 人民日报 / 某年某月某日` 下生成日期父页。
3. 日期父页包含全日总览、要闻版 PDF、按版面展开的文章列表，以及每篇文章的“整篇深度解读”。
4. 每篇保留文章创建子页，子页承载“结构化原文与解析”：按意义单元分组，不机械逐自然段。
5. 单篇文章分析概念上仍是两个源 prompt：`article_full_analysis_v1.md` 和 `article_structured_groups_v1.md`；生产上可以由脚本动态合并为一次模型调用，返回 `full_analysis + structured_groups`。
6. 脚本质量门只做结构性硬校验：JSON/prompt_id、`full_analysis` 非空、`structured_groups` 覆盖全部输入段落、`paragraph_indices` 合法；风格和内容质量留在 prompt 自检与人工复核。
7. 版务、责编、版式设计等非正文条目自动过滤；用 `people_daily_publications.json` 记录已发布日期，避免重复创建。
8. 如果启用 Telegram，完成提醒只发送 Notion 链接，不发送本地 Markdown、manifest、缓存或输出目录。

```bash
~/.openclaw/workspace/market-immersion-module/scripts/run_people_daily_deep_read.sh \
  --layout-url "https://paper.people.com.cn/rmrb/pc/layout/202605/03/node_01.html"
```

常用参数：

- `--date YYYY-MM-DD`：按日期从第01版开始抓取。
- `--max-pages 1`：只抓前 N 个版面，适合测试。
- `--delay 120`：自动请求间隔，默认尊重人民网 robots 的 crawl-delay。
- `--manifest PATH`：使用已抓取的 manifest 重新发布或测试。
- `--dry-run`：只验证将要生成的 Notion 页面数量，不真正发布。
- `--force`：更新已有日期页内容，默认不会重复创建同一天页面。

输出目录默认是 `~/.openclaw/workspace/people-daily-deep-read/YYYY-MM-DD/`，包含 `manifest.json`、PDF 原件、Markdown 归档、分析缓存，以及一个本地 HTML 对照页。正式发布时会调用 OpenClaw 为每篇保留文章生成 `full_analysis` 和 `structured_groups`：父页展示“整篇深度解读”，子页用 `paragraph_indices` 回填浅色原文并展示结构组解析。

具体解读 prompt 不随仓库发布。仓库内置的只是流程、页面结构和结构性 JSON 契约；如果启用人民日报深读，建议用户在本机配置自己的私有 prompt。

### 配置自己的人民日报深读 prompt

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
