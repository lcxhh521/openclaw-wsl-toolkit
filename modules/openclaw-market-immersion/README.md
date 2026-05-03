# OpenClaw Market Immersion Module

这是一个 OpenClaw 长期任务模块，不是交易建议系统。

它的职责是到点收集市场快讯流，保留完整原始信息，再交给 OpenClaw 做轻整理，最后生成本地归档；如果用户明确启用，也可以发布到 Notion 或推送到 Telegram。

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
4. OpenClaw 必须完成 1-8 栏轻整理，否则任务失败。
5. 第 9 栏保留按时间顺序排列的完整原始消息流。
6. 如果用户在配置里启用 Notion，正式阶段必须成功发布 Notion。
7. 如果用户在配置里启用 Telegram，正式阶段会尝试推送日报链接或文件。
8. 必要的闭环步骤全部成功后才更新 `state.json` 的 `last_success_at`。

## 当前信息源

- 东方财富财经资讯与 7x24 快讯栏目
- 财联社电报
- 金十数据快讯
- 新浪财经 7x24
- 华尔街见闻 7x24

## 运行

```bash
~/.openclaw/workspace/market-immersion-module/scripts/run_market_immersion.sh morning
```

查看定时器：

```bash
systemctl --user list-timers "openclaw-market-immersion*" --all
```

查看最近日志：

```bash
journalctl --user -u openclaw-market-immersion-morning.service -n 100 --no-pager
```

## 人民日报深读

人民日报深读是独立于快讯日报的长文本子流程，但封装在同一个模块里。它复刻手工整理 Notion 的流程：

1. 抓取当天 8 个版面的电子版、PDF 和文章正文。
2. 在 Notion 的 `财经政经 / 人民日报 / 某年某月某日` 下生成日期页。
3. 日期页按版面列出文章，每篇文章下面创建深读子页。
4. 前 4 版文章默认生成逐段解读和全文深度解读。
5. 版务、责编、版式设计等非正文条目自动过滤。
6. 用 `people_daily_publications.json` 记录已发布日期，避免重复创建。

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

输出目录默认是 `~/.openclaw/workspace/people-daily-deep-read/YYYY-MM-DD/`，包含 `manifest.json`、PDF 原件、Markdown 归档，以及一个本地 HTML 对照页。正式发布时会调用 OpenClaw 为每篇深读文章生成逐段解析，Notion 子页采用“逐段原文 + 对应解析 + 全文深度解读”的结构，便于审计原文和解释之间的关系。

具体解读 prompt 不随仓库发布。仓库内置的只是 JSON 输出契约和最低质量要求；如果启用人民日报深读，建议用户在本机填入自己的私有 prompt。

### 配置自己的人民日报深读 prompt

1. 在本机创建一个不提交到 GitHub 的 prompt 文件，例如：

```bash
mkdir -p ~/.openclaw/private-prompts
nano ~/.openclaw/private-prompts/people_daily_analysis_prompt.md
```

2. prompt 可以写自己的解读方法，但必须要求模型输出下面的 JSON 结构：

```json
{
  "paragraph_notes": [
    {"excerpt": "段首短摘", "analysis": "该段解析"}
  ],
  "signal_analysis": ["可选：信号/语境分析"],
  "policy_chain": ["可选：政策链路或观察点"],
  "follow_up": ["可选：后续跟踪事项"],
  "full_analysis": ["全文深度解读"]
}
```

其中 `paragraph_notes` 的数量应与原文段落数量一致。

3. 在 `config/market_immersion_config.json` 中配置私有 prompt 路径：

```json
"people_daily_deep_read": {
  "analysis": {
    "prompt_template_path": "~/.openclaw/private-prompts/people_daily_analysis_prompt.md"
  }
}
```

4. 确认 prompt 文件没有被放进仓库；如果使用 git 管理自己的配置，请把私有 prompt 路径加入 `.gitignore`。
