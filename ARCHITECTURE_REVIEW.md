
# OpenClaw Architecture Review: Telegram/Main Experience Improvements

Date: 2026-05-11

## 1. Root Cause Classification

### 1.1 Architecture Issues

| Issue | Root Cause |
|-------|------------|
| Foreground blocking | No clear foreground/background separation for checkpoint polling loops; main/telegram executing blocking waits instead of delegating to supervisor. |
| Notion/Telegram notifications not sent despite artifacts existing | No checkpoint supervisor that monitors `pending_notifications` in task records and delivers to Telegram only after gates pass; workers trying to send Telegram directly from hot path. |
| Environment/Secrets not loaded consistently for direct-provider lane | No centralized secrets loading mechanism across systemd/supervisor/workers; secrets loaded per-script with inconsistent env handling. |
| Failure visibility without Telegram blocking | No user-facing status dashboard that reads `tasks/*/task.json` and surfaces status/next-actions without executing workflows in hot path. |

### 1.2 Operator/Discipline Issues

| Issue | Root Cause |
|-------|------------|
| Midday market empty report blocking | Worker tried to push degraded/empty report instead of respecting quality gate; no automated check for "empty summary" before attempting publish. |
| Night digest failure due to missing API key | Secrets configured in one context but not another (e.g., user shell vs systemd service); no pre-flight secrets check before worker starts. |

---

## 2. Recommended Hard Boundaries and Invariants

### 2.1 Invariants (Enforced Everywhere)

1. **Telegram/Main Hot Path**: Never execute multi-step workflows, polling loops, long model calls, or external publish steps directly in the Telegram/main conversation.
2. **Quality Gate**: Never publish empty/degraded summaries to Notion or send them as official user-facing output; degraded artifacts are stored locally only.
3. **Checkpoint First**: All workflows must write checkpoints for every stage (collect → analyze → digest → publish → notify) before attempting the next stage; no "roll your own" state in memory.
4. **Secrets Pre-Flight**: Every worker must run a secrets pre-flight check before starting any heavy work; fail fast with clear error if any required secrets are missing.
5. **Notifications Only After Gate**: Notion link notifications to Telegram are allowed only after `publish` checkpoint is verified as formal success; pending notifications are stored in `task.json` and delivered by a supervisor, not by workers.

### 2.2 Boundaries

| Component | Allowed | Disallowed |
|-----------|---------|------------|
| Telegram/Main | Define task contracts, review artifacts, approve/reject tasks, query status dashboard, send short status updates. | Multi-step workflows, polling loops, long model calls, direct Notion/Telegram publish, modifying config/secrets without explicit approval. |
| Workers | Execute task contracts, write checkpoints/artifacts, use direct-provider or gateway-model-lane, fail fast on missing prerequisites. | Sending Telegram notifications directly, modifying OpenClaw config/secrets, executing multi-step without checkpoint gates. |
| Supervisor | Monitor task records, deliver pending notifications, run pre-flight checks, retry with backoff, trigger checkpoint supervisor. | Executing heavy model calls, modifying config/secrets without approval, sending user-facing messages without task-record backing. |

---

## 3. Upgrade Proposal (P0/P1/P2 Prioritization)

### 3.1 P0 (Must Do Immediately)

1. **Checkpoint Supervisor**: Add `scripts/checkpoint_supervisor.py` that monitors tasks, validates checkpoints, triggers pending notification delivery, and enforces quality gates.
2. **Non-blocking Foreground Status**: Modify `task_record.py` and add a lightweight status dashboard that reads `tasks/*/task.json` and surfaces status without executing workflows in hot path.
3. **Centralized Secrets Loading**: Add `scripts/secrets_loader.py` that loads secrets from a single source (e.g., `.env.secrets`) with pre-flight checks and exposes them consistently to all workers/supervisors.
4. **Failure Taxonomy Standardization**: Expand `failure_diagnoser.py` to cover all known failure kinds with standardized root cause/evidence/solution/owner/next-action fields.

### 3.2 P1 (Should Do Soon)

1. **Notification Delivery Supervisor**: Add `scripts/notification_delivery.py` that reads `pending_notifications` from task records and delivers them to Telegram only after publish gates pass; handles retries for delivery failures.
2. **Pre-flight Check Framework**: Add `scripts/preflight_checks.py` that workers must call before starting any work (checks secrets, disk space, network connectivity, checkpoint paths exist, etc.).
3. **Quality Gate Validator**: Add a reusable `quality_gate.py` module that workers must call to validate summaries before attempting publish (checks for empty content, degraded placeholders, etc.).
4. **Task Dashboard UI**: Build a simple terminal UI or web dashboard that shows all tasks, status, next actions, and pending notifications without executing workflows.

### 3.3 P2 (Could Do Later)

1. **Task Queue**: Move from polling task records to a proper task queue (e.g., Redis Queue, Celery) for better scalability and reliability.
2. **Distributed Checkpoint Store**: Move checkpoints from local files to a distributed store (e.g., SQLite, PostgreSQL) for better durability and queryability.
3. **Automatic Secrets Rotation**: Add support for automatic secrets rotation with zero-downtime reloads for workers.
4. **Advanced Failure Recovery**: Add support for more advanced failure recovery scenarios (e.g., rollback to previous checkpoint, partial reruns, etc.).

---

## 4. Specific Implementation Ideas

### 4.1 Non-blocking Foreground Status

- Add `task_record.py dashboard` command that outputs a human-readable summary of all tasks, status, next actions, and pending notifications.
- Add `task_record.py status <task_id>` command that outputs a short summary of a single task.
- Ensure both commands read only from `tasks/*/task.json` and do not execute any workflows or modify state.
- Example output:
  ```
  === OpenClaw Task Dashboard ===
  Total tasks: 5
  Running: 2 (market_immersion_night-20260511_night, people_daily_deep_read-2026-05-11)
  Failed: 1 (market_backfill-failed-empty-20260511-192736)
  Awaiting review: 0
  Pending notifications: 2

  === market_immersion_night-20260511_night ===
  Status: running
  Next action: worker_rerun_digest_after_cooldown
  Error kind: summary_failed
  Last updated: 2026-05-11T23:31:19+08:00

  === people_daily_deep_read-2026-05-11 ===
  Status: running
  Next action: worker_classify_notion_failure
  Error kind: notion_publish_failed
  Last updated: 2026-05-11T23:31:54+08:00
  ```

### 4.2 Checkpoint Supervisor

- Add `scripts/checkpoint_supervisor.py` that:
  1. Periodically scans `tasks/*/task.json`.
  2. For each task with status `running`, validates that checkpoints are being written on schedule.
  3. For each task with pending notifications, checks if publish gate has passed and triggers notification delivery if so.
  4. Enforces quality gates by checking that degraded/empty summaries are not published.
  5. Writes its own checkpoints to `state/checkpoint_supervisor.json`.
- Example invocation:
  ```bash
  python3 scripts/checkpoint_supervisor.py --interval 60 --dry-run
  python3 scripts/checkpoint_supervisor.py --interval 60
  ```

### 4.3 Failure Taxonomy

- Expand `failure_diagnoser.py` to cover the following standardized failure kinds:
  | Failure Kind | Description | Owner | Next Action |
  |--------------|-------------|-------|-------------|
  | missing_api_key | Missing provider API key | operator | Add missing API key to secrets |
  | empty_summary | Summary is empty or only has placeholders | worker | Rerun summary stage with valid input |
  | notion_publish_failed | Notion publish failed | worker | Classify failure and retry or send to main review |
  | telegram_delivery_failed | Telegram notification failed | supervisor | Retry delivery with backoff |
  | gateway_unavailable | OpenClaw gateway is unavailable | worker | Wait and retry or use direct-provider lane |
  | provider_timeout | Provider call timed out | worker | Retry with backoff or smaller chunks |
  | quality_gate_failed | Summary did not pass quality gate | worker | Rerun summary stage with better input |
  | checkpoint_missing | Required checkpoint is missing | worker | Rerun from last valid checkpoint |

### 4.4 Environment/Secrets Loading

- Add `scripts/secrets_loader.py` that:
  1. Reads secrets from a single source (e.g., `.env.secrets` in workspace root).
  2. Validates that all required secrets are present.
  3. Exposes secrets as environment variables or a Python dictionary.
  4. Writes a pre-flight check result to `state/secrets_preflight.json`.
- Example `.env.secrets`:
  ```bash
  # OpenAI-compatible providers
  OPENAI_API_KEY=sk-...
  VOLCANO_ENGINE_API_KEY=...
  ARK_API_KEY=...

  # Notion
  NOTION_API_KEY=secret_...
  NOTION_DATABASE_ID=...

  # Telegram
  TELEGRAM_BOT_TOKEN=...
  TELEGRAM_CHAT_ID=...
  ```
- Example usage in Python:
  ```python
  from scripts.secrets_loader import load_secrets, SecretsPreflightError

  try:
      secrets = load_secrets()
      # Use secrets: secrets["OPENAI_API_KEY"], etc.
  except SecretsPreflightError as e:
      print(f"Secrets pre-flight check failed: {e}")
      exit(1)
  ```

### 4.5 User-visible Status Dashboard

- Option 1: Terminal UI using `rich` library.
- Option 2: Simple web dashboard using `flask` or `fastapi` that reads from `tasks/*/task.json` and serves a static page.
- Both options should be read-only and not execute any workflows or modify state.

---

## 5. Risks and Tradeoffs

### 5.1 Risks

| Risk | Mitigation |
|------|------------|
| Over-engineering: Adding too much complexity too quickly | Follow P0/P1/P2 prioritization strictly; implement only what is needed to solve current pain points first. |
| Regressions: Breaking existing working workflows | Add comprehensive tests for all new components; use feature flags and dry-run modes extensively. |
| Increased latency: More checks and supervisors adding overhead | Keep checks lightweight and asynchronous; use caching where appropriate. |

### 5.2 Tradeoffs

| Tradeoff | Decision | Rationale |
|----------|----------|-----------|
| Local files vs distributed store | Local files for now; move to distributed store later (P2) | Local files are simple, reliable, and sufficient for current scale; distributed store adds complexity that is not needed yet. |
| Polling vs event-driven | Polling for now; move to event-driven later (P2) | Polling is simple and easy to implement correctly; event-driven adds complexity that is not needed yet. |
| Terminal UI vs web dashboard | Both; terminal UI first (P0), web dashboard later (P1) | Terminal UI is simple, fast, and works well for power users; web dashboard is more accessible for casual users. |

---

## 6. Next Steps

1. Implement P0 items in order: Checkpoint Supervisor → Non-blocking Foreground Status → Centralized Secrets Loading → Failure Taxonomy Standardization.
2. Test each component extensively with dry-run modes before enabling in production.
3. Roll out changes incrementally to minimize risk of regressions.
4. Monitor task success rates and user feedback after each rollout.

