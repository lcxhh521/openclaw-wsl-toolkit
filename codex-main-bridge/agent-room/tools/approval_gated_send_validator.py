#!/usr/bin/env python3
"""Dry-run validator for approval-gated Telegram send records.

This script is local Agent Room infrastructure only. It never sends Telegram,
never activates production behavior, and never advances canonical tasks.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any


CST = timezone(timedelta(hours=8))
ROOT = Path("/home/lcxhh/.openclaw/workspace/codex-main-bridge/agent-room")
DEFAULT_FIXTURES = ROOT / "dry-run-fixtures" / "approval-gated-send"
DEFAULT_RESULTS = ROOT / "dry-run-results" / "approval-gated-send"

APPROVAL_SCHEMA_VERSION = "approval_gated_send.v0.2"
ALLOWED_SCOPES = {"telegram_reply", "telegram_non_reply"}
REPLY_SCOPE = "telegram_reply"
NON_REPLY_SCOPE = "telegram_non_reply"

APPROVAL_REQUIRED = [
    "schema_version",
    "source_task_id",
    "dedupe_key",
    "target_identity",
    "source_message_identity",
    "draft_path",
    "draft_content_sha256",
    "approval_scope",
    "approved_at",
    "approved_by",
]

SEND_KEY_REQUIRED = [
    "source_task_id",
    "dedupe_key",
    "target_identity",
    "source_message_identity",
    "draft_path",
    "draft_content_sha256",
    "approval_scope",
]

COMPARABLE = [
    "source_task_id",
    "dedupe_key",
    "target_identity",
    "source_message_identity",
    "draft_path",
    "draft_content_sha256",
    "approval_scope",
]


def now_cst() -> str:
    return datetime.now(CST).isoformat(timespec="seconds")


def load_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return None, f"json_parse_error:{type(exc).__name__}"
    if not isinstance(data, dict):
        return None, "json_not_object"
    return data, None


def is_blank(value: str) -> bool:
    return value.strip() == ""


def add_missing_or_type_reasons(
    reasons: list[str], prefix: str, data: dict[str, Any] | None, required: list[str]
) -> None:
    if data is None:
        reasons.append(f"{prefix}_record_unreadable")
        return
    for field in required:
        if field not in data:
            reasons.append(f"missing_{prefix}_{field}")
            continue
        if not isinstance(data[field], str):
            reasons.append(f"non_string_{prefix}_{field}")
            continue
        if is_blank(data[field]):
            reasons.append(f"blank_{prefix}_{field}")


def scope_reasons(prefix: str, record: dict[str, Any] | None) -> list[str]:
    reasons: list[str] = []
    if record is None:
        return reasons
    scope = record.get("approval_scope")
    source = record.get("source_message_identity")
    if not isinstance(scope, str) or is_blank(scope):
        return reasons
    if scope not in ALLOWED_SCOPES:
        reasons.append(f"unknown_{prefix}_approval_scope")
        return reasons
    if not isinstance(source, str) or is_blank(source):
        return reasons
    if scope == REPLY_SCOPE and source == "none":
        reasons.append(f"invalid_{prefix}_reply_source_message_identity")
    if scope == NON_REPLY_SCOPE and source != "none":
        reasons.append(f"invalid_{prefix}_non_reply_source_message_identity")
    return reasons


def optional_metadata_reasons(prefix: str, record: dict[str, Any] | None) -> list[str]:
    if record is None:
        return []
    metadata = record.get("metadata")
    if not isinstance(metadata, dict):
        return []
    claimed = metadata.get("scope_class")
    if claimed is None:
        return []
    scope = record.get("approval_scope")
    if scope == REPLY_SCOPE and claimed != "reply":
        return [f"inconsistent_{prefix}_scope_metadata"]
    if scope == NON_REPLY_SCOPE and claimed != "non_reply":
        return [f"inconsistent_{prefix}_scope_metadata"]
    if scope not in ALLOWED_SCOPES:
        return [f"inconsistent_{prefix}_scope_metadata"]
    return []


def sha256_file(path: Path) -> tuple[str | None, str | None]:
    try:
        if not path.exists():
            return None, "draft_path_unreadable"
        if not path.is_file():
            return None, "draft_path_not_regular_file"
        return hashlib.sha256(path.read_bytes()).hexdigest(), None
    except Exception as exc:
        return None, f"draft_path_unreadable:{type(exc).__name__}"


def validate_fixture(fixture_dir: Path, fixtures_root: Path) -> dict[str, Any]:
    fixture_name = fixture_dir.name
    approval_path = fixture_dir / "approval.json"
    send_key_path = fixture_dir / "send_key.json"
    expected_path = fixture_dir / "expected.json"
    approval, approval_err = load_json(approval_path)
    send_key, send_key_err = load_json(send_key_path)
    expected, expected_err = load_json(expected_path)

    reasons: list[str] = []
    if approval_err:
        reasons.append(f"approval_{approval_err}")
    if send_key_err:
        reasons.append(f"send_key_{send_key_err}")
    if expected_err:
        reasons.append(f"expected_{expected_err}")

    add_missing_or_type_reasons(reasons, "approval", approval, APPROVAL_REQUIRED)
    add_missing_or_type_reasons(reasons, "send_key", send_key, SEND_KEY_REQUIRED)

    if approval is not None and isinstance(approval.get("schema_version"), str):
        if approval["schema_version"] != APPROVAL_SCHEMA_VERSION:
            reasons.append("approval_schema_version_mismatch")

    reasons.extend(scope_reasons("approval", approval))
    reasons.extend(scope_reasons("send_key", send_key))
    reasons.extend(optional_metadata_reasons("approval", approval))
    reasons.extend(optional_metadata_reasons("send_key", send_key))

    if approval is not None and send_key is not None:
        for field in COMPARABLE:
            if field in approval and field in send_key and isinstance(approval[field], str) and isinstance(send_key[field], str):
                if approval[field] != send_key[field]:
                    reasons.append(f"{field}_mismatch")

    computed_hash = None
    expected_hash = None
    draft_path_value = approval.get("draft_path") if isinstance(approval, dict) else None
    if isinstance(draft_path_value, str) and not is_blank(draft_path_value):
        draft_path = Path(draft_path_value)
        try:
            draft_path.resolve().relative_to(fixtures_root.resolve())
        except Exception:
            reasons.append("draft_path_outside_fixture_root")
        else:
            computed_hash, hash_error = sha256_file(draft_path)
            if hash_error:
                reasons.append(hash_error)
            expected_hash = approval.get("draft_content_sha256") if isinstance(approval, dict) else None
            if computed_hash and isinstance(expected_hash, str) and computed_hash != expected_hash:
                reasons.append("draft_bytes_hash_mismatch")

    accepted = len(reasons) == 0
    expected_accepted = expected.get("accepted") if isinstance(expected, dict) else None
    expectation_matched = expected_accepted is None or accepted == expected_accepted
    if not expectation_matched:
        reasons.append("fixture_expectation_mismatch")

    return {
        "fixture_name": fixture_name,
        "accepted": accepted,
        "expected_accepted": expected_accepted,
        "expectation_matched": expectation_matched,
        "reasons": reasons,
        "approval_scope": approval.get("approval_scope") if isinstance(approval, dict) else None,
        "source_message_identity": approval.get("source_message_identity") if isinstance(approval, dict) else None,
        "expected_draft_content_sha256": expected_hash,
        "computed_draft_content_sha256": computed_hash,
        "approval_path": str(approval_path),
        "send_key_path": str(send_key_path),
    }


def render_md(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Approval-Gated Send Validator Dry-Run Results")
    lines.append("")
    lines.append(f"Generated: {payload['created_at']}")
    lines.append("")
    lines.append("This is a local dry-run result only. It did not send Telegram, activate production behavior, edit OpenClaw source, advance canonical tasks, publish, or notify.")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    for key, value in payload["summary"].items():
        lines.append(f"- {key}: {value}")
    lines.append("")
    lines.append("## Fixtures")
    lines.append("")
    for row in payload["results"]:
        mark = "PASS" if row["expectation_matched"] else "MISMATCH"
        accepted = "accepted" if row["accepted"] else "rejected"
        lines.append(f"### {row['fixture_name']} - {mark}")
        lines.append("")
        lines.append(f"- result: `{accepted}`")
        lines.append(f"- expected_accepted: `{row['expected_accepted']}`")
        lines.append(f"- approval_scope: `{row['approval_scope']}`")
        lines.append(f"- source_message_identity: `{row['source_message_identity']}`")
        if row["reasons"]:
            lines.append("- reasons:")
            for reason in row["reasons"]:
                lines.append(f"  - `{reason}`")
        else:
            lines.append("- reasons: none")
        lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run local dry-run approval-gated send validator fixtures.")
    parser.add_argument("--fixtures-dir", default=str(DEFAULT_FIXTURES))
    parser.add_argument("--results-dir", default=str(DEFAULT_RESULTS))
    args = parser.parse_args()

    fixtures_root = Path(args.fixtures_dir)
    run_id = datetime.now(CST).strftime("%Y%m%d-%H%M%S")
    result_dir = Path(args.results_dir) / run_id
    result_dir.mkdir(parents=True, exist_ok=True)

    fixture_dirs = sorted([p for p in fixtures_root.rglob("*") if p.is_dir() and (p / "approval.json").exists()])
    results = [validate_fixture(p, fixtures_root) for p in fixture_dirs]
    accepted_count = sum(1 for row in results if row["accepted"])
    rejected_count = sum(1 for row in results if not row["accepted"])
    expectation_mismatches = [row["fixture_name"] for row in results if not row["expectation_matched"]]

    payload = {
        "schema": "openclaw.agent_room.approval_gated_send_validator_dry_run.v0",
        "created_at": now_cst(),
        "run_id": run_id,
        "mode": "local_dry_run_only_no_send_no_activation",
        "fixtures_dir": str(fixtures_root),
        "result_dir": str(result_dir),
        "non_activation": {
            "telegram_sends": 0,
            "openclaw_source_edits": 0,
            "production_activation": False,
            "canonical_task_advancement": False,
            "publish_notify_behavior": False,
        },
        "summary": {
            "fixtures": len(results),
            "accepted": accepted_count,
            "rejected": rejected_count,
            "expectation_mismatches": len(expectation_mismatches),
            "all_expectations_matched": len(expectation_mismatches) == 0,
        },
        "expectation_mismatches": expectation_mismatches,
        "results": results,
    }
    (result_dir / "results.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (result_dir / "results.md").write_text(render_md(payload), encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    print(f"result_dir={result_dir}")
    return 0 if payload["summary"]["all_expectations_matched"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
