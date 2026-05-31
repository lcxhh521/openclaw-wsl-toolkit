# OpenClaw 架构升级主线重置（2026-05-19）

## 结论

Alex 的判断成立：过去几天有不少局部 artifact / smoke / 脚手架，但“架构升级主线”的用户可见闭环推进不足。

不能再把讨论轮次、审计文件、dry-run、局部脚本堆积等同于主线进展。后续只有改变运行时行为、形成统一协议、接入真实入口、或给出明确 blocker 的工作，才计入主线。

## 已有但只能算脚手架/局部进展

1. Coding lane 本地 MVP
   - `tools/claude_code_ark_runner.py`
   - `tools/coding_dispatcher.py`
   - `tools/coding_task_entry.py`
   - `tools/coding_acceptance_gate.py`
   - `tools/coding_iteration_ledger.py`
   - `tools/coding_telegram_router_dry_run.py`

   状态：本地 smoke/acceptance gate 有进展；但未接 Telegram native 自动入口，不能算日常可用主线完成。

2. Notification dispatcher dry-run
   - `notification-dispatcher/notification_dispatcher_v0.py`
   - `notification-dispatcher/pending/*.json`

   状态：能从 tasks/diagnosis 生成 pending/review 信息；但没有接 main 的真实合并发送路径，不能解决“完成后没推送”。

3. Scheduler/timer 审计
   - `parallel-audits/20260519-timer-recovery-matrix.md`

   状态：知道哪些 timer disabled/masked；但没有完成安全恢复，线上自动化仍会停摆。

4. Codex/main mailbox
   - 讨论轮次过多，当前已到 seq 868 附近。

   状态：协作通道本身存在，但产出/推进比低；后续不再把 mailbox 往返视为进展。

## 真正的架构主线 Definition of Done

### P0-A：统一任务运行时 / 不再静默停摆

完成标准：市场、人民日报、翻译、coding 等任务都走同一套 task record：
- task.json
- manifest/result/error
- checkpoint
- diagnosis
- pending notification
- main review/dispatch

验收：任一任务失败或完成，Telegram 能收到合并后的短反馈；本地可通过 task id 找到完整证据链。

### P0-B：Notification Dispatcher 接入 main

完成标准：main 能读取 pending notifications，去重、合并、压缩，并在合适时机真实发送 Telegram。

验收：不再出现“Notion 已发布但 Telegram 没推送”或“worker 完成但前台无反馈”。

### P0-C：Scheduler / timer 恢复分级

完成标准：明确并执行最小安全恢复：
- 只读 observer 可直接恢复；
- 本地检查类 timer 可恢复；
- 会触发 Notion/Telegram/模型调用的发布类 timer 走确认后恢复；
- masked timers 有单独恢复记录。

验收：`systemctl --user list-timers` 中核心任务有下一次触发时间；失败后有 diagnosis/notification。

### P0-D：Gateway / Telegram 热路径隔离

完成标准：长任务不抢 Telegram/Gateway；模型调用有 lane/timeout；前台消息不被长回复/长任务阻塞。

验收：补跑日报/人民日报时，Telegram 仍可收发；任务状态可见；超时可中断/恢复。

### P1-A：Translation agent 真实入口

完成标准：Telegram/main 接到非小型翻译任务后能自动生成标准 run，保留 Alex 原始请求，生成 file-based handoff，派发 translation agent，main 通过 artifact gate 验收，回传完整 artifact 路径或 delivery-ready 附件。

验收：不是手动 CLI smoke；而是从 Telegram 指令到 `translation-runs/<run-id>/user_request.md`、`handoff_brief.md`、`manifest.json`、翻译 artifact、coverage/layout/delivery gate，再到前台结果回流的一次完整链路。

### P1-B：Coding agent 备用入口

完成标准：自研 coding dispatcher/harness/acceptance gate/ledger 保持可用，但不作为近期主线硬目标；默认工程协作优先使用 Claude Code/Codex 现有能力。

验收：只有当 Claude Code/Codex lane 出现明确覆盖缺口时，才继续推进 Telegram native coding agent 自动入口；否则保留现有 smoke、ledger 和 gate 作为后备证据。

## 立即收束规则

1. Codex/main 协作：下一轮没有 artifact / patch / smoke evidence / blocker，就判定 no-progress，停止继续讨论。
2. 架构主线汇报只按 DoD：完成、阻塞、下一动作，不再汇报泛化“推进”。
3. 任何 dry-run 只能算候选证据；未接入真实运行时，不计为完成。
4. 当前 P0 仍先处理 2026-05-19 人民日报未推送事故；事故处理完成后，回到 P0-A/P0-B，而不是继续扩散新线。

## 下一批实际动作

1. 完成 2026-05-19 人民日报 Notion + Telegram 推送。
2. 把 Notification Dispatcher 接到 main review/dispatch：先支持 dry-run summary -> visible Telegram summary，不直接全量发 pending。
3. 形成 timer 最小恢复执行清单，并只对 observe/local-safe 类执行；发布类列出确认点。
4. 把 PeopleDaily/market 临时补跑也纳入 task record + pending notification，避免再次绕过主线。
