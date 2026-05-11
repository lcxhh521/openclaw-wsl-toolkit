# OpenClaw P0 Progress Snapshot

Date: 2026-05-12

This snapshot records the confirmed P0 reliability direction discussed around Telegram/main responsiveness, daily delivery recovery, and agent collaboration.

## P0 goal

Telegram/main should remain responsive while long-running or fragile work happens in background lanes. The goal is not prettier failure notices; the goal is high-quality successful delivery, with failures visible and recoverable when they occur.

## Confirmed hot-path rules

- Do not run long polling loops, long model calls, Notion bulk publish, repair loops, or long log scans directly in Telegram/main foreground.
- Foreground should enqueue work, answer short status queries, review artifacts, and ask for approvals.
- Long work should write artifacts/checkpoints and return short status only.
- Official success notifications should happen only after formal publish succeeds.
- Empty/degraded artifacts must not be treated as official delivery.

## Confirmed diagnostic rules

- Default status should be cheap/local/read-only.
- Avoid recreating high-frequency Control Center polling that caused lag.
- Heavy probes such as gateway probe, logs, channel status, or task audits should be explicit/user-triggered or background diagnostic tasks.
- `turn.json` or task manifests/checkpoints are preferred sources of truth over watcher/process-start events.

## Confirmed 2026-05-11 recovery findings

### PeopleDaily

- Do not directly retry publish/notify.
- Local content has many empty `#### 解析` sections despite analyze checkpoints.
- Required next step: repair/regenerate missing analysis content, run quality/completeness gate, then publish/notify only if the gate passes.

### Market night

- Failure root is likely worker/direct-provider env loading for `VOLCANO_ENGINE_API_KEY`, not necessarily missing local secret material.
- Recovery should first prove the key reaches the actual worker lane without printing it.
- A safe recovery command should stop at local render artifacts before publish/notify.
- Model/provider retries require user approval because they can consume quota.

### Market midday

- Current state is `empty_report_blocked`.
- Before any backfill, verify source/window collection cause.
- If backfilled, label as 补发/归档 and never pretend it was an on-time midday report.

## Confirmed agent-collab status

- `agent-collab` is now a standalone optional module.
- It includes mailbox protocol, main watcher, Codex/external watcher, and optional install examples.
- It should remain opt-in and separate from base OpenClaw installation.

## Not yet safe to publicize as default behavior

The following areas may exist as local experiments or discussion artifacts, but should not be treated as default public behavior without separate review:

- model switching/fallback policies;
- content prompt or quality-standard changes;
- direct-provider routing replacing existing model lanes;
- automatic publish/retry after failed reports;
- broad runtime profile/plugin pruning;
- destructive cleanup or mailbox history deletion.
