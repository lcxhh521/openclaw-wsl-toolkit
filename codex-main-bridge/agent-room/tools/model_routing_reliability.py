#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
ROOM = ROOT / "agent-room"
COMMENT_ROOT = ROOT / "agent-comments"
ACTIVE_RUNNERS = ROOM / "active-runners"
POLICY_FILE = ROOM / "config" / "claude-code-model-policy.json"
COLLAB_STATUS_FILE = ROOM / "collaboration-status" / "latest.json"


def now_dt() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def now_iso() -> str:
    return now_dt().isoformat(timespec="seconds")


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def read_json(path: Path, default: Any = None) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default
    return default


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except Exception:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def model_tail(model: Any) -> str:
    value = str(model or "").strip()
    return value.rsplit("/", 1)[-1].lower()


def is_doubao_model(model: Any) -> bool:
    tail = model_tail(model)
    return "doubao" in tail or "豆包" in tail


def attempt_ok(attempt: dict[str, Any]) -> bool:
    if "ok" in attempt:
        return bool(attempt.get("ok"))
    return str(attempt.get("status") or "").lower() in {"completed", "success", "succeeded", "ok"}


def attempt_status(attempt: dict[str, Any]) -> str:
    status = str(attempt.get("status") or "").strip().lower()
    if status:
        return status
    return "completed" if attempt_ok(attempt) else "failed"


def safe_rate(success: int, total: int) -> float | None:
    if total <= 0:
        return None
    return round(success / total, 4)


def enabled_permission_keys(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return []
    keys = (
        "source_edit",
        "telegram_send",
        "notion_publish",
        "github_push",
        "secrets_access",
        "global_state_change",
        "quality_surface_change",
    )
    return [key for key in keys if value.get(key) is True]


def comment_created_at(comment: dict[str, Any]) -> datetime | None:
    return parse_dt(comment.get("created_at") or comment.get("ts"))


def collect_comments(since: datetime | None) -> list[dict[str, Any]]:
    comments: list[dict[str, Any]] = []
    for path in sorted(COMMENT_ROOT.glob("*.jsonl")):
        for row in read_jsonl(path):
            created = comment_created_at(row)
            if since and created and created < since:
                continue
            agent_id = str(row.get("agent_id") or row.get("agent") or path.stem)
            item = dict(row)
            item["_comment_file"] = str(path)
            item["_agent_id"] = agent_id
            item["_created_at"] = created.isoformat(timespec="seconds") if created else None
            comments.append(item)
    return comments


def lane_key(agent_id: str, backend: str, model: str) -> str:
    return f"{agent_id}|{backend or 'unknown-backend'}|{model or 'unknown-model'}"


def summarize_model_attempts(comments: list[dict[str, Any]]) -> dict[str, Any]:
    lanes: dict[str, dict[str, Any]] = {}
    model_totals: dict[str, Counter[str]] = defaultdict(Counter)
    fallback_paths: Counter[str] = Counter()
    fallback_success: Counter[str] = Counter()
    fallback_chain_attempts: Counter[str] = Counter()
    fallback_chain_success: Counter[str] = Counter()
    doubao_attempts: list[dict[str, Any]] = []
    external_fallback_records: list[dict[str, Any]] = []
    records_with_attempts = 0

    for comment in comments:
        agent_id = str(comment.get("_agent_id") or "")
        backend = str(comment.get("backend") or "")
        attempts = comment.get("model_attempts")
        if not isinstance(attempts, list) or not attempts:
            selected = str(comment.get("model") or "").strip()
            if selected:
                attempts = [{"model": selected, "status": "completed", "ok": not bool(comment.get("blockers"))}]
            else:
                attempts = []
        if not attempts:
            continue
        records_with_attempts += 1
        attempt_rows: list[dict[str, Any]] = []
        chain: list[str] = []
        chain_ok = False
        external_deepseek_fallback = (
            isinstance(comment.get("external_deepseek_fallback"), dict)
            or (
                backend == "external-deepseek-openai-compatible-worker"
                and any(
                    isinstance(item, dict)
                    and str(item.get("backend") or "").strip() == "external-deepseek-openai-compatible-worker"
                    for item in attempts
                )
            )
        )
        external_record = comment.get("external_deepseek_fallback") if isinstance(comment.get("external_deepseek_fallback"), dict) else {}
        if external_deepseek_fallback:
            capability = str(external_record.get("capability") or "")
            no_tool_boundary = "no_tools" in capability or "no-tools" in capability or "no tools" in capability
            external_fallback_records.append({
                "agent_id": agent_id,
                "run_id": comment.get("run_id"),
                "model": comment.get("model"),
                "status_model": external_record.get("status_model"),
                "model_source": external_record.get("model_source"),
                "capability": capability or "unknown",
                "no_tool_boundary": no_tool_boundary,
                "permitted_tool_surface": enabled_permission_keys(comment.get("effective_permissions")),
                "created_at": comment.get("_created_at"),
            })
        for raw in attempts:
            if not isinstance(raw, dict):
                continue
            model = str(raw.get("model") or "unknown-model")
            raw_backend = str(raw.get("backend") or "").strip()
            if raw_backend:
                backend_for_attempt = raw_backend
            elif external_deepseek_fallback and backend == "external-deepseek-openai-compatible-worker":
                # External fallback comments include the failed/skipped Ark
                # candidate chain before the final no-tools fallback attempt.
                # Keep those pre-fallback attempts on the Ark lane instead of
                # attributing them to the external DeepSeek worker.
                backend_for_attempt = "ark-coding-plan-official-claude-endpoint"
            else:
                backend_for_attempt = backend or "unknown-backend"
            key = lane_key(agent_id, backend_for_attempt, model)
            ok = attempt_ok(raw)
            status = attempt_status(raw)
            lane = lanes.setdefault(key, {
                "agent_id": agent_id,
                "backend": backend_for_attempt,
                "model": model,
                "attempts": 0,
                "successes": 0,
                "failures": 0,
                "skipped": 0,
                "reasons": Counter(),
                "latest_run_id": None,
                "latest_at": None,
            })
            lane["attempts"] += 1
            if status.startswith("skipped"):
                lane["skipped"] += 1
            elif ok:
                lane["successes"] += 1
                chain_ok = True
            else:
                lane["failures"] += 1
            reason = str(raw.get("reason") or status or "unknown")
            lane["reasons"][reason] += 1
            lane["latest_run_id"] = comment.get("run_id")
            lane["latest_at"] = comment.get("_created_at")
            model_totals[model][status if status.startswith("skipped") else ("success" if ok else "failure")] += 1
            if is_doubao_model(model):
                doubao_attempts.append({
                    "agent_id": agent_id,
                    "run_id": comment.get("run_id"),
                    "model": model,
                    "backend": backend_for_attempt,
                    "status": status,
                    "created_at": comment.get("_created_at"),
                })
            if model not in chain:
                chain.append(model)
            attempt_rows.append({"ok": ok, "status": status, "reason": reason})
        model_fallback = comment.get("model_fallback") if isinstance(comment.get("model_fallback"), dict) else {}
        fallback_path = None
        if model_fallback:
            source = str(model_fallback.get("from") or (chain[0] if chain else "unknown"))
            dest = str(model_fallback.get("to") or (chain[-1] if chain else "none"))
            path = str(model_fallback.get("path") or f"{source}->{dest}")
            fallback_paths[path] += 1
            if chain_ok and not comment.get("blockers"):
                fallback_success[path] += 1
            fallback_path = path
        elif len(chain) > 1:
            path = "->".join(chain)
            fallback_paths[path] += 1
            if chain_ok and not comment.get("blockers"):
                fallback_success[path] += 1
            fallback_path = path
        fallback_chain_initiated = (
            len(attempt_rows) > 1
            and any(not row.get("ok") for row in attempt_rows[:-1])
        )
        if fallback_path and fallback_chain_initiated:
            fallback_chain_attempts[fallback_path] += 1
            if attempt_rows and attempt_rows[-1].get("ok") and not comment.get("blockers"):
                fallback_chain_success[fallback_path] += 1
        if fallback_path and not fallback_chain_initiated:
            # Path changed but no earlier failed/skipped attempt; keep observable as
            # non-fallback/degenerate chain for transparency but exclude from
            # effectiveness metric.
            pass

    lane_rows = []
    for key, lane in sorted(lanes.items()):
        attempts = int(lane["attempts"])
        successes = int(lane["successes"])
        failures = int(lane["failures"])
        skipped = int(lane["skipped"])
        reasons = lane.pop("reasons")
        lane_rows.append({
            **lane,
            "attempts": attempts,
            "successes": successes,
            "failures": failures,
            "skipped": skipped,
            "success_rate": safe_rate(successes, attempts - skipped),
            "top_reasons": dict(reasons.most_common(5)),
        })

    fallback_rows = []
    for path, total in fallback_paths.most_common():
        successes = fallback_success.get(path, 0)
        fallback_rows.append({
            "path": path,
            "attempts": total,
            "successes": successes,
            "success_rate": safe_rate(successes, total),
        })
    fallback_chain_rows = []
    fallback_chain_total_attempts = 0
    fallback_chain_total_successes = 0
    for path, total in fallback_chain_attempts.most_common():
        successes = fallback_chain_success.get(path, 0)
        fallback_chain_rows.append({
            "path": path,
            "attempts": total,
            "successes": successes,
            "success_rate": safe_rate(successes, total),
        })
        fallback_chain_total_attempts += total
        fallback_chain_total_successes += successes

    external_fallback_records.sort(key=lambda item: str(item.get("created_at") or ""))
    no_tool_records = [item for item in external_fallback_records if item.get("no_tool_boundary")]
    no_tool_with_permitted_tool_surface = [
        item for item in no_tool_records if item.get("permitted_tool_surface")
    ]
    capability_counts = Counter(str(item.get("capability") or "unknown") for item in external_fallback_records)

    return {
        "records_with_model_attempts": records_with_attempts,
        "lanes": lane_rows,
        "fallback_paths": fallback_rows,
        "fallback_chain_effectiveness": {
            "total_attempts": fallback_chain_total_attempts,
            "successes": fallback_chain_total_successes,
            "success_rate": safe_rate(fallback_chain_total_successes, fallback_chain_total_attempts),
            "paths": fallback_chain_rows,
        },
        "fallback_capability_boundary": {
            "external_deepseek_fallback_records": len(external_fallback_records),
            "external_no_tool_fallback_records": len(no_tool_records),
            "external_no_tool_fallbacks_with_permitted_tool_surface": len(no_tool_with_permitted_tool_surface),
            "capability_counts": dict(capability_counts.most_common()),
            "latest_records": external_fallback_records[-20:],
        },
        "doubao_attempts": doubao_attempts,
        "model_totals": {model: dict(counter) for model, counter in sorted(model_totals.items())},
    }


def summarize_policy() -> dict[str, Any]:
    policy = read_json(POLICY_FILE, {}) or {}
    doubao = policy.get("doubao_family") if isinstance(policy.get("doubao_family"), dict) else {}
    allowed_tails = [str(x) for x in (doubao.get("allowed_tails") or [])]
    allowed_route_keys = [str(x) for x in (doubao.get("allowed_route_keys") or [])]
    routes = policy.get("routes") if isinstance(policy.get("routes"), dict) else {}
    route_doubao_candidates: list[dict[str, Any]] = []
    for route_key, route in sorted(routes.items()):
        candidates = route.get("candidates") if isinstance(route, dict) else []
        for candidate in candidates if isinstance(candidates, list) else []:
            if is_doubao_model(candidate):
                route_doubao_candidates.append({"route_key": route_key, "model": str(candidate)})
    policy_mtime_at = None
    if POLICY_FILE.exists():
        try:
            policy_mtime_at = datetime.fromtimestamp(POLICY_FILE.stat().st_mtime, tz=timezone.utc).astimezone().isoformat(timespec="seconds")
        except Exception:
            policy_mtime_at = None
    doubao_fully_disabled = not allowed_tails and not allowed_route_keys and not route_doubao_candidates
    return {
        "policy_file": str(POLICY_FILE),
        "policy_exists": POLICY_FILE.exists(),
        "policy_mtime_at": policy_mtime_at,
        "doubao_allowed_tails": allowed_tails,
        "doubao_allowed_route_keys": allowed_route_keys,
        "doubao_fully_disabled": doubao_fully_disabled,
        "doubao_disable_effective_at": policy_mtime_at if doubao_fully_disabled else None,
        "route_doubao_candidates": route_doubao_candidates,
        "default_model": policy.get("default_model"),
        "fallback_model": policy.get("fallback_model"),
    }


def pid_alive(pid: Any) -> bool:
    try:
        pid_int = int(pid)
    except Exception:
        return False
    if pid_int <= 0:
        return False
    stat = Path(f"/proc/{pid_int}/stat")
    if not stat.exists():
        return False
    try:
        text = stat.read_text(encoding="utf-8", errors="replace")
        right = text.rfind(")")
        fields = text[right + 2:].split() if right != -1 else text.split()[2:]
        if fields and fields[0] == "Z":
            return False
    except Exception:
        pass
    try:
        os.kill(pid_int, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return False
    return True


def systemd_show_unit(unit: str) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            ["systemctl", "--user", "show", unit, "-p", "MainPID", "-p", "ActiveState", "-p", "SubState", "--no-pager"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    if proc.returncode != 0:
        return {
            "ok": False,
            "show_exit_code": str(proc.returncode),
            "error": (proc.stderr or proc.stdout or "").strip()[:300],
        }
    state: dict[str, Any] = {"ok": True}
    for line in proc.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            state[key] = value
    return state


def runner_liveness(record: dict[str, Any]) -> dict[str, Any]:
    runner_dir_raw = str(record.get("runner_dir") or "").strip()
    runner_dir = Path(runner_dir_raw) if runner_dir_raw else None
    if runner_dir and (runner_dir / ".runner-exit-marker").exists():
        return {"alive": False, "source": "runner_exit_marker", "pid": record.get("pid")}

    unit = str(record.get("systemd_unit") or "").strip()
    if unit:
        state = systemd_show_unit(unit)
        if state.get("ok"):
            try:
                main_pid = int(state.get("MainPID") or 0)
            except Exception:
                main_pid = 0
            return {
                "alive": bool(main_pid and pid_alive(main_pid)),
                "source": "systemd_main_pid" if main_pid else "systemd_no_main_pid",
                "pid": main_pid,
                "systemd_state": {
                    key: state.get(key)
                    for key in ("MainPID", "ActiveState", "SubState")
                    if key in state
                },
            }
        record_state = record.get("systemd_state") if isinstance(record.get("systemd_state"), dict) else {}
        pid_candidate = record_state.get("MainPID") or record.get("pid")
        try:
            pid_int = int(pid_candidate or 0)
        except Exception:
            pid_int = 0
        if pid_int > 0:
            alive = pid_alive(pid_int)
            return {
                "alive": alive,
                "source": "recorded_systemd_pid_proc_fallback" if alive else "recorded_systemd_pid_missing",
                "pid": pid_int,
                "systemd_unit": unit,
                "systemd_state_from_record": record_state,
                "error": state.get("error") or state.get("show_exit_code") or "systemd_show_failed",
            }
        return {
            "alive": None,
            "source": "systemd_unverified",
            "pid": record.get("pid"),
            "systemd_unit": unit,
            "systemd_state_from_record": record.get("systemd_state") if isinstance(record.get("systemd_state"), dict) else {},
            "error": state.get("error") or state.get("show_exit_code") or "systemd_show_failed",
        }

    return {"alive": pid_alive(record.get("pid")), "source": "record_pid", "pid": record.get("pid")}


def summarize_active_runners() -> dict[str, Any]:
    total = 0
    alive = 0
    dead_records: list[dict[str, Any]] = []
    unverified_records: list[dict[str, Any]] = []
    if not ACTIVE_RUNNERS.exists():
        return {"total": 0, "alive": 0, "dead_or_missing": 0, "unverified": 0, "dead_records": [], "unverified_records": []}
    for path in sorted(ACTIVE_RUNNERS.glob("*.json")):
        total += 1
        record = read_json(path, {}) or {}
        liveness = runner_liveness(record) if isinstance(record, dict) else {"alive": False, "source": "invalid_record"}
        base = {
            "path": str(path),
            "agent_id": record.get("agent_id") if isinstance(record, dict) else None,
            "run_id": record.get("run_id") if isinstance(record, dict) else None,
            "pid": liveness.get("pid"),
            "started_at": record.get("started_at") if isinstance(record, dict) else None,
            "liveness_source": liveness.get("source"),
        }
        if liveness.get("alive") is True:
            alive += 1
        elif liveness.get("alive") is None:
            unverified_records.append({
                **base,
                "systemd_unit": liveness.get("systemd_unit"),
                "systemd_state_from_record": liveness.get("systemd_state_from_record"),
                "error": liveness.get("error"),
            })
        else:
            dead_records.append(base)
    return {
        "total": total,
        "alive": alive,
        "dead_or_missing": len(dead_records),
        "unverified": len(unverified_records),
        "dead_records": dead_records[:20],
        "unverified_records": unverified_records[:20],
    }


def summarize_model_reliability(model_attempt_summary: dict[str, Any]) -> dict[str, Any]:
    lanes = model_attempt_summary.get("lanes") or []
    if not isinstance(lanes, list):
        return {}
    reliability: dict[str, dict[str, Any]] = {}
    for lane in lanes:
        if not isinstance(lane, dict):
            continue
        model = str(lane.get("model") or "").strip()
        backend = str(lane.get("backend") or "").strip()
        if not model or not backend:
            continue
        attempts = int(lane.get("attempts") or 0)
        successes = int(lane.get("successes") or 0)
        failures = int(lane.get("failures") or 0)
        skipped = int(lane.get("skipped") or 0)
        attempts_for_reliability = max(attempts - skipped, 0)
        reliability_key = f"{model}@{backend}"
        reliability[reliability_key] = {
            "model": model,
            "backend": backend,
            "attempts": attempts,
            "successes": successes,
            "failures": failures,
            "skipped": skipped,
            "attempts_for_reliability": attempts_for_reliability,
            "success_rate": safe_rate(successes, attempts_for_reliability),
        }
    return reliability


def summarize_focus_model_reliability(
    model_reliability: dict[str, Any],
    focus_models: list[str] | None,
) -> dict[str, Any]:
    if not model_reliability or not focus_models:
        return {}
    result: dict[str, Any] = {}
    requested = [str(model).strip().lower() for model in focus_models if str(model).strip()]
    if not requested:
        return {}
    requested_set = set(requested)
    buckets: dict[str, list[dict[str, Any]]] = {}
    for row in model_reliability.values():
        if not isinstance(row, dict):
            continue
        model = str(row.get("model") or "").strip().lower()
        if model in requested_set:
            buckets.setdefault(model, []).append(row)
    for requested_model in requested:
        rows = buckets.get(requested_model, [])
        totals = {
            "attempts": 0,
            "successes": 0,
            "failures": 0,
            "skipped": 0,
            "attempts_for_reliability": 0,
        }
        for row in rows:
            totals["attempts"] += int(row.get("attempts") or 0)
            totals["successes"] += int(row.get("successes") or 0)
            totals["failures"] += int(row.get("failures") or 0)
            totals["skipped"] += int(row.get("skipped") or 0)
            totals["attempts_for_reliability"] += int(row.get("attempts_for_reliability") or 0)
        totals["success_rate"] = safe_rate(
            totals["successes"],
            totals["attempts_for_reliability"],
        )
        result[requested_model] = {
            "model": requested_model,
            "lanes": sorted(rows, key=lambda item: f"{item.get('backend')}/{item.get('model')}") if rows else [],
            "summary": totals,
            "present": bool(rows),
        }
    return result


def summarize_collaboration_health() -> dict[str, Any]:
    snapshot = read_json(COLLAB_STATUS_FILE, {})
    if not isinstance(snapshot, dict):
        return {
            "available": False,
            "status_file": str(COLLAB_STATUS_FILE),
            "reason": "missing_or_invalid_snapshot",
        }
    fields = [
        "tracked_tasks",
        "peer_reviewed_task_count",
        "degraded_quorum_task_count",
        "needs_collaboration_review_count",
        "needs_collaboration_repair_count",
        "runner_attention_task_count",
        "runner_degraded_quorum_task_count",
        "runner_partial_attention_task_count",
        "material_stall_task_count",
        "active_material_silence_task_count",
        "material_point_count",
        "peer_uptake_count",
        "peer_challenge_count",
        "summary_point_count",
        "summary_peer_uptake_count",
        "integrated_summary_count",
        "integration_signal_count",
        "blocker_count",
        "open_blocker_count",
        "action_item_count",
        "needs_attention_task_ids",
        "per_agent_material_points",
        "per_agent_peer_uptakes",
        "per_agent_peer_challenges",
        "per_agent_blockers",
        "per_agent_action_items",
        "efficiency_score_avg",
    ]
    health: dict[str, Any] = {
        "available": True,
        "status_file": str(COLLAB_STATUS_FILE),
    }
    for field in fields:
        if field in snapshot:
            health[field] = snapshot[field]
    return health


def build_report(since_hours: float, focus_models: list[str] | None = None) -> dict[str, Any]:
    since = now_dt() - timedelta(hours=since_hours) if since_hours > 0 else None
    comments = collect_comments(since)
    attempts = summarize_model_attempts(comments)
    policy = summarize_policy()
    disable_effective_at = parse_dt(policy.get("doubao_disable_effective_at"))
    doubao_after_disable = []
    for item in attempts.get("doubao_attempts") or []:
        created = parse_dt(item.get("created_at"))
        if disable_effective_at and created and created >= disable_effective_at:
            doubao_after_disable.append(item)
    doubao_violations = []
    if not policy.get("doubao_fully_disabled"):
        doubao_violations.append("policy_allows_doubao_family")
    if doubao_after_disable:
        doubao_violations.append("observed_doubao_model_attempts_after_disable")
    attempt_summary = attempts
    model_reliability = summarize_model_reliability(attempt_summary)
    return {
        "schema": "openclaw.agent_room.model_routing_reliability.v0",
        "generated_at": now_iso(),
        "window": {
            "since_hours": since_hours,
            "since_at": since.isoformat(timespec="seconds") if since else None,
            "comments_scanned": len(comments),
        },
        "model_attempt_summary": attempt_summary,
        "policy_summary": policy,
        "model_reliability": model_reliability,
        "focus_models": focus_models or [],
        "focus_model_reliability": summarize_focus_model_reliability(model_reliability, focus_models),
        "collaboration_health": summarize_collaboration_health(),
        "doubao_disable_coverage": {
            "ok": not doubao_violations,
            "violations": doubao_violations,
            "observed_doubao_attempts_in_window": len(attempts.get("doubao_attempts") or []),
            "observed_doubao_attempts_after_disable": len(doubao_after_disable),
            "doubao_attempts_after_disable": doubao_after_disable[:20],
            "policy_fully_disabled": bool(policy.get("doubao_fully_disabled")),
            "disable_effective_at": policy.get("doubao_disable_effective_at"),
        },
        "active_runner_health": summarize_active_runners(),
    }


def markdown_report(report: dict[str, Any]) -> str:
    window = report.get("window") or {}
    attempt_summary = report.get("model_attempt_summary") or {}
    reliability = report.get("model_reliability") if isinstance(report.get("model_reliability"), dict) else {}
    focus = report.get("focus_model_reliability") if isinstance(report.get("focus_model_reliability"), dict) else {}
    doubao = report.get("doubao_disable_coverage") or {}
    active = report.get("active_runner_health") or {}
    collaboration = report.get("collaboration_health") or {}
    capability = attempt_summary.get("fallback_capability_boundary") if isinstance(attempt_summary.get("fallback_capability_boundary"), dict) else {}
    chain_effectiveness = (
        attempt_summary.get("fallback_chain_effectiveness")
        if isinstance(attempt_summary.get("fallback_chain_effectiveness"), dict)
        else {}
    )
    lines = [
        "# Model routing reliability snapshot",
        "",
        f"- generated_at: {report.get('generated_at')}",
        f"- window_since_hours: {window.get('since_hours')}",
        f"- comments_scanned: {window.get('comments_scanned')}",
        f"- records_with_model_attempts: {attempt_summary.get('records_with_model_attempts')}",
        f"- external_no_tool_fallback_records: {capability.get('external_no_tool_fallback_records')}",
        f"- external_no_tool_with_permitted_tool_surface: {capability.get('external_no_tool_fallbacks_with_permitted_tool_surface')}",
        f"- doubao_disable_ok: {doubao.get('ok')}",
        f"- doubao_attempts_after_disable: {doubao.get('observed_doubao_attempts_after_disable')}",
        f"- active_runners: total={active.get('total')} alive={active.get('alive')} "
        f"dead_or_missing={active.get('dead_or_missing')} unverified={active.get('unverified')}",
        f"- collaboration_health_available: {collaboration.get('available')}",
        (
            f"- degraded_quorum_task_count: {collaboration.get('degraded_quorum_task_count', 'n/a')} "
            f"needs_attention_task_count: {len(collaboration.get('needs_attention_task_ids') or []) if isinstance(collaboration.get('needs_attention_task_ids'), list) else 'n/a'}"
        ),
        f"- peer_uptake_count: {collaboration.get('peer_uptake_count', 'n/a')} "
        f"peer_challenge_count: {collaboration.get('peer_challenge_count', 'n/a')}",
        f"- material_point_count: {collaboration.get('material_point_count', 'n/a')} "
        f"integration_signal_count: {collaboration.get('integration_signal_count', 'n/a')}",
        "",
        "## Lanes",
    ]
    lanes = attempt_summary.get("lanes") or []
    if not lanes:
        lines.append("- no model attempts observed in this window")
    for lane in lanes[:20]:
        lines.append(
            "- "
            f"{lane.get('agent_id')} / {lane.get('backend')} / {lane.get('model')}: "
            f"attempts={lane.get('attempts')} success={lane.get('successes')} "
            f"failure={lane.get('failures')} skipped={lane.get('skipped')} "
            f"success_rate={lane.get('success_rate')}"
        )
    lines.extend(["", "## Model reliability"])
    if not reliability:
        lines.append("- no model reliability summary in report window")
    for key in sorted(reliability):
        row = reliability.get(key, {})
        lines.append(
            "- "
            f"{key}: attempts={row.get('attempts')} attempts_for_reliability={row.get('attempts_for_reliability')} "
            f"successes={row.get('successes')} failures={row.get('failures')} skipped={row.get('skipped')} "
            f"success_rate={row.get('success_rate')}"
        )
    lines.extend(["", "## Focus model reliability"])
    if not focus:
        lines.append("- no focus models configured")
    else:
        for model in sorted(focus):
            item = focus[model]
            summary = item.get("summary", {})
            lines.append(
                "- "
                f"{model}: attempts={summary.get('attempts')} attempts_for_reliability="
                f"{summary.get('attempts_for_reliability')} successes={summary.get('successes')} "
                f"failures={summary.get('failures')} skipped={summary.get('skipped')} "
                f"success_rate={summary.get('success_rate')}"
            )
            for lane in item.get("lanes") or []:
                lines.append(
                    "  - "
                    f"{lane.get('agent_id')} / {lane.get('backend')} / {lane.get('model')}: "
                    f"attempts={lane.get('attempts')} success={lane.get('successes')} "
                    f"failure={lane.get('failures')} skipped={lane.get('skipped')} "
                    f"success_rate={lane.get('success_rate')}"
                )
    lines.extend(["", "## Fallback Paths"])
    paths = attempt_summary.get("fallback_paths") or []
    if not paths:
        lines.append("- no fallback chains observed in this window")
    for path in paths[:20]:
        lines.append(
            "- "
            f"{path.get('path')}: attempts={path.get('attempts')} "
            f"success={path.get('successes')} success_rate={path.get('success_rate')}"
        )
    chain_rows = chain_effectiveness.get("paths") or []
    lines.extend(["", "## Fallback Chain Effectiveness"])
    if not chain_rows:
        lines.append("- no effective fallback chain observed in this window")
    else:
        lines.append(
            "- "
            f"effective_fallback_attempts={chain_effectiveness.get('total_attempts')} "
            f"fallback_successes={chain_effectiveness.get('successes')} "
            f"fallback_success_rate={chain_effectiveness.get('success_rate')}"
        )
        for item in chain_rows[:20]:
            lines.append(
                "- "
                f"{item.get('path')}: attempts={item.get('attempts')} "
                f"successes={item.get('successes')} success_rate={item.get('success_rate')}"
            )
    lines.extend(["", "## Capability Boundary"])
    if not capability.get("external_deepseek_fallback_records"):
        lines.append("- no external DeepSeek fallback observed in this window")
    else:
        lines.append(
            "- "
            f"external_deepseek_fallback_records={capability.get('external_deepseek_fallback_records')} "
            f"no_tool={capability.get('external_no_tool_fallback_records')} "
            f"with_permitted_tool_surface={capability.get('external_no_tool_fallbacks_with_permitted_tool_surface')}"
        )
        for item in (capability.get("latest_records") or [])[-5:]:
            lines.append(
                "- latest "
                f"{item.get('agent_id')} run={item.get('run_id')} model={item.get('model')} "
                f"capability={item.get('capability')} permitted={','.join(item.get('permitted_tool_surface') or []) or 'none'}"
            )
    lines.extend(["", "## Doubao Coverage"])
    if doubao.get("violations"):
        lines.append("- violations: " + ", ".join(str(x) for x in doubao.get("violations") or []))
    else:
        lines.append("- no policy or observed-attempt violation in this window")
    return "\n".join(lines) + "\n"


def artifact_window_label(report: dict[str, Any]) -> str:
    window = report.get("window") if isinstance(report.get("window"), dict) else {}
    since_hours = window.get("since_hours")
    try:
        value = float(since_hours)
    except (TypeError, ValueError):
        return "window-unknown"
    text = f"{value:g}".replace(".", "p").replace("-", "neg")
    return f"{text}h"


def write_artifacts(report: dict[str, Any], out_dir: Path) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    window_label = artifact_window_label(report)
    json_path = out_dir / f"model-routing-reliability-{window_label}-{stamp}.json"
    md_path = out_dir / f"model-routing-reliability-{window_label}-{stamp}.md"
    latest_json_path = out_dir / "latest.json"
    latest_md_path = out_dir / "latest.md"
    json_text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    markdown_text = markdown_report(report)
    json_path.write_text(json_text, encoding="utf-8")
    md_path.write_text(markdown_text, encoding="utf-8")
    latest_json_path.write_text(json_text, encoding="utf-8")
    latest_md_path.write_text(markdown_text, encoding="utf-8")
    return {
        "json": str(json_path),
        "markdown": str(md_path),
        "latest_json": str(latest_json_path),
        "latest_markdown": str(latest_md_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize Agent Room model-routing reliability from local artifacts.")
    parser.add_argument("--since-hours", type=float, default=24.0)
    parser.add_argument("--out-dir", default="")
    parser.add_argument(
        "--focus-models",
        default="glm-5.1",
        help="Comma-separated model names to include in focus model reliability summary.",
    )
    parser.add_argument("--markdown", action="store_true", help="Print markdown instead of JSON.")
    args = parser.parse_args()

    focus_models = [item.strip() for item in str(args.focus_models).split(",") if item.strip()]
    report = build_report(args.since_hours, focus_models=focus_models)
    if args.out_dir:
        report["artifacts"] = write_artifacts(report, Path(args.out_dir))
    if args.markdown:
        print(markdown_report(report), end="")
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
