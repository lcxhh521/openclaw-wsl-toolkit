# Iteration Log Schema

Append-only operational memory for OpenClaw workspace workflows. This tracks functional/process iterations, validation, rollback, and cleanup candidates. It is not source code and is not deletion authority.

## `iteration_log.jsonl`
One JSON object per line.

Required fields:
- `date`
- `trigger`
- `change_summary`
- `affected_flows`
- `files_changed`
- `validation`
- `rollback_plan`
- `artifact_links`
- `user_visible_effect`
- `risk_level`
- `approval_required`
- `approved_by_or_reason_not_required`

## `cleanup_candidates.jsonl`
Candidates only; deletion requires separate approval and evidence.

Required fields:
- `date`
- `path`
- `reason_candidate`
- `last_known_reference_check`
- `runtime_reference_evidence`
- `checkpoint_dependency_evidence`
- `historical_replay_value`
- `audit_value`
- `safe_to_delete_recommendation`
- `required_approval`

## Retention policy
- Keep iteration logs indefinitely unless Alex explicitly asks to archive.
- Never delete artifacts solely by age.
- Cleanup requires evidence from actual runtime references, config references, checkpoint dependencies, replay needs, and audit value.
- Prefer archive/quarantine before deletion for anything used in scheduled flow, publish/notify path, or recovery workflow.
- Public/user-visible report artifacts and delivery checkpoints should be retained.
