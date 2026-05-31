#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "agent-room" / "tools"
DRY_RUN = ROOT / "agent-room" / "dry-runs" / "model-routing-reliability-smoke"
ARK_BACKEND = "ark-coding-plan-official-claude-endpoint"


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def iso(offset_seconds: int = 0) -> str:
    return (datetime.now(timezone.utc).astimezone() + timedelta(seconds=offset_seconds)).isoformat(timespec="seconds")


def check(name: str, condition: bool, failures: list[str]) -> None:
    if not condition:
        failures.append(name)


def lane(report: dict[str, Any], *, agent_id: str, backend: str, model: str) -> dict[str, Any]:
    summary = report.get("model_attempt_summary") if isinstance(report.get("model_attempt_summary"), dict) else {}
    for item in summary.get("lanes") or []:
        if (
            isinstance(item, dict)
            and item.get("agent_id") == agent_id
            and item.get("backend") == backend
            and item.get("model") == model
        ):
            return item
    return {}


def fallback_chain(report: dict[str, Any], path: str) -> dict[str, Any]:
    summary = report.get("model_attempt_summary") if isinstance(report.get("model_attempt_summary"), dict) else {}
    chains = summary.get("fallback_chain_effectiveness", {}) if isinstance(summary.get("fallback_chain_effectiveness"), dict) else {}
    for item in chains.get("paths") or []:
        if isinstance(item, dict) and item.get("path") == path:
            return item
    return {}


def fallback_path(report: dict[str, Any], path: str) -> dict[str, Any]:
    summary = report.get("model_attempt_summary") if isinstance(report.get("model_attempt_summary"), dict) else {}
    for item in summary.get("fallback_paths") or []:
        if isinstance(item, dict) and item.get("path") == path:
            return item
    return {}


def model_reliability(report: dict[str, Any], model: str, backend: str) -> dict[str, Any]:
    reliability = report.get("model_reliability") if isinstance(report.get("model_reliability"), dict) else {}
    return reliability.get(f"{model}@{backend}", {}) if isinstance(reliability, dict) else {}


def main() -> int:
    if DRY_RUN.exists():
        shutil.rmtree(DRY_RUN)
    bridge_root = DRY_RUN / "codex-main-bridge"
    room = bridge_root / "agent-room"
    comment_root = bridge_root / "agent-comments"
    out_dir = DRY_RUN / "out"
    failures: list[str] = []

    routing = load_module(TOOLS / "model_routing_reliability.py", "model_routing_reliability_smoke")
    routing.ROOT = bridge_root
    routing.ROOM = room
    routing.COMMENT_ROOT = comment_root
    routing.ACTIVE_RUNNERS = room / "active-runners"
    routing.POLICY_FILE = room / "config" / "claude-code-model-policy.json"

    saved_systemd_show_unit = routing.systemd_show_unit
    saved_pid_alive = routing.pid_alive
    routing.systemd_show_unit = lambda unit: (
        {"ok": True, "MainPID": "424242", "ActiveState": "active", "SubState": "running"}
        if str(unit).endswith("live")
        else {"ok": False, "error": "Failed to connect to bus: Operation not permitted"}
    )
    routing.pid_alive = lambda pid: int(pid or 0) == 424242

    write_json(
        routing.POLICY_FILE,
        {
            "schema": "openclaw.agent_room.claude_code_model_policy.v0",
            "doubao_family": {
                "allowed_tails": [],
                "allowed_route_keys": [],
            },
            "routes": {
                "workspace_write": {
                    "candidates": ["glm-5.1", "deepseek-v4-pro", "minimax-m2.7"],
                }
            },
        },
    )
    write_json(
        routing.ACTIVE_RUNNERS / "claude-code-live-systemd.json",
        {
            "schema": "openclaw.agent_room.active_runner.v0",
            "status": "running",
            "agent_id": "claude-code",
            "run_id": "live-systemd",
            "pid": 999999998,
            "systemd_unit": "openclaw-agent-runner-smoke-live",
            "started_at": iso(-90),
        },
    )
    write_json(
        routing.ACTIVE_RUNNERS / "codex-unverified-systemd.json",
        {
            "schema": "openclaw.agent_room.active_runner.v0",
            "status": "running",
            "agent_id": "codex",
            "run_id": "unverified-systemd",
            "pid": 999999997,
            "systemd_unit": "openclaw-agent-runner-smoke-no-bus",
            "systemd_state": {"MainPID": "999999997", "ActiveState": "active", "SubState": "running"},
            "started_at": iso(-80),
        },
    )
    write_json(
        routing.ACTIVE_RUNNERS / "codex-unverified-no-pid-systemd.json",
        {
            "schema": "openclaw.agent_room.active_runner.v0",
            "status": "running",
            "agent_id": "codex",
            "run_id": "unverified-no-pid-systemd",
            "pid": 0,
            "systemd_unit": "openclaw-agent-runner-smoke-no-bus-no-pid",
            "started_at": iso(-75),
        },
    )
    write_json(
        routing.ACTIVE_RUNNERS / "codex-dead-pid.json",
        {
            "schema": "openclaw.agent_room.active_runner.v0",
            "status": "running",
            "agent_id": "codex",
            "run_id": "dead-pid",
            "pid": 999999996,
            "started_at": iso(-70),
        },
    )
    append_jsonl(
        comment_root / "claude-code.jsonl",
        [
            {
                "agent_id": "claude-code",
                "run_id": "smoke-success-chain",
                "created_at": iso(-120),
                "backend": ARK_BACKEND,
                "model_attempts": [
                    {"model": "glm-5.1", "status": "completed", "ok": True},
                    {"model": "deepseek-v4-pro", "status": "failed", "ok": False, "reason": "timeout"},
                    {"model": "minimax-m2.7", "status": "skipped_usage_limit", "reason": "usage_limit"},
                ],
            },
            {
                "agent_id": "claude-code",
                "run_id": "smoke-fallback-chain",
                "created_at": iso(-60),
                "backend": ARK_BACKEND,
                "model_attempts": [
                    {"model": "glm-5.1", "status": "failed", "ok": False, "reason": "timeout"},
                    {"model": "deepseek-v4-pro", "status": "completed", "ok": True},
                ],
            },
        ],
    )
    append_jsonl(
        comment_root / "claude-code.jsonl",
        [
            {
                "agent_id": "claude-code",
                "run_id": "smoke-no-fallback-chain",
                "created_at": iso(-90),
                "backend": ARK_BACKEND,
                "model_attempts": [
                    {"model": "glm-5.1", "status": "completed", "ok": True},
                    {"model": "deepseek-v4-pro", "status": "completed", "ok": True},
                ],
            }
        ],
    )

    report = routing.build_report(24, focus_models=["glm-5.1"])
    focus = report.get("focus_model_reliability") if isinstance(report.get("focus_model_reliability"), dict) else {}
    glm_lane = lane(report, agent_id="claude-code", backend=ARK_BACKEND, model="glm-5.1")
    deepseek_lane = lane(report, agent_id="claude-code", backend=ARK_BACKEND, model="deepseek-v4-pro")
    fallback = fallback_path(report, "glm-5.1->deepseek-v4-pro")
    chain_fallback = fallback_chain(report, "glm-5.1->deepseek-v4-pro")
    glm_reliability = model_reliability(report, "glm-5.1", ARK_BACKEND)
    doubao = report.get("doubao_disable_coverage") if isinstance(report.get("doubao_disable_coverage"), dict) else {}
    active = report.get("active_runner_health") if isinstance(report.get("active_runner_health"), dict) else {}
    artifacts = routing.write_artifacts(report, out_dir)
    second_report = dict(report)
    second_report["window"] = dict(report.get("window") or {})
    second_report["window"]["since_hours"] = 2.0
    second_artifacts = routing.write_artifacts(second_report, out_dir)

    check(
        "glm lane success rate excludes skipped attempts",
        glm_lane.get("attempts") == 3
        and glm_lane.get("successes") == 2
        and glm_lane.get("failures") == 1
        and glm_lane.get("success_rate") == 0.6667,
        failures,
    )
    check(
        "fallback lane captures successful second-hop recovery",
        deepseek_lane.get("attempts") == 3
        and deepseek_lane.get("successes") == 2
        and fallback.get("attempts") == 2
        and fallback.get("successes") == 2
        and chain_fallback.get("attempts") == 1
        and chain_fallback.get("successes") == 1,
        failures,
    )
    check(
        "fallback chain effectiveness excludes non-fallback multi-attempt paths",
        chain_fallback.get("attempts") == 1
        and chain_fallback.get("successes") == 1
        and chain_fallback.get("success_rate") == 1.0,
        failures,
    )
    check(
        "glm-5.1 reliability summary is available for ark backend",
        bool(glm_reliability)
        and glm_reliability.get("attempts") == 3
        and glm_reliability.get("attempts_for_reliability") == 3,
        failures,
    )
    check(
        "glm-5.1 reliability gate keeps non-skipped success rate at least 0.5",
        isinstance(glm_reliability.get("success_rate"), float)
        and glm_reliability.get("success_rate") >= 0.5,
        failures,
    )
    check(
        "focus model summary includes glm-5.1 aggregate",
        focus.get("glm-5.1", {}).get("present") is True
        and focus.get("glm-5.1", {}).get("summary", {}).get("attempts") == 3,
        failures,
    )
    check(
        "focus model summary markdown is rendered",
        "## Focus model reliability" in routing.markdown_report(report),
        failures,
    )
    check(
        "doubao policy and observed attempts are clean",
        doubao.get("ok") is True and doubao.get("observed_doubao_attempts_after_disable") == 0,
        failures,
    )
    check(
        "active runner health separates live dead and systemd-unverified records",
        active.get("total") == 4
        and active.get("alive") == 1
        and active.get("dead_or_missing") == 2
        and active.get("unverified") == 1
        and (active.get("unverified_records") or [{}])[0].get("liveness_source") == "systemd_unverified",
        failures,
    )
    check(
        "artifact writer maintains stable latest files",
        Path(str(artifacts.get("latest_json") or "")).exists()
        and Path(str(artifacts.get("latest_markdown") or "")).exists(),
        failures,
    )
    check(
        "artifact writer keeps distinct files for adjacent tracking windows",
        artifacts.get("json") != second_artifacts.get("json")
        and artifacts.get("markdown") != second_artifacts.get("markdown")
        and Path(str(artifacts.get("json") or "")).exists()
        and Path(str(second_artifacts.get("json") or "")).exists(),
        failures,
    )

    append_jsonl(
        comment_root / "claude-code.jsonl",
        [
            {
                "agent_id": "claude-code",
                "run_id": "smoke-external-no-tools-fallback",
                "created_at": iso(-30),
                "backend": "external-deepseek-openai-compatible-worker",
                "model": "deepseek-v4-pro",
                "effective_permissions": {
                    "source_edit": True,
                    "telegram_send": False,
                    "global_state_change": True,
                },
                "model_attempts": [
                    {
                        "model": "glm-5.1",
                        "status": "skipped_cooldown",
                        "reason": "usage_limit",
                        "backend": ARK_BACKEND,
                    },
                    {
                        "model": "deepseek-v4-pro",
                        "status": "completed",
                        "ok": True,
                        "backend": "external-deepseek-openai-compatible-worker",
                    },
                ],
                "external_deepseek_fallback": {
                    "enabled": True,
                    "capability": "text_review_or_blocker_no_tools_no_file_edits",
                    "status_model": "external-deepseek-openai-compatible-worker/deepseek-v4-pro",
                    "model_source": "ark_cooldown.deepseek-v4-pro",
                },
            }
        ],
    )
    boundary_report = routing.build_report(24)
    capability = (
        boundary_report.get("model_attempt_summary", {}).get("fallback_capability_boundary")
        if isinstance(boundary_report.get("model_attempt_summary"), dict)
        else {}
    )
    check(
        "external no-tools fallback boundary is surfaced separately",
        isinstance(capability, dict)
        and capability.get("external_deepseek_fallback_records") == 1
        and capability.get("external_no_tool_fallback_records") == 1
        and capability.get("external_no_tool_fallbacks_with_permitted_tool_surface") == 1,
        failures,
    )

    append_jsonl(
        comment_root / "claude-code.jsonl",
        [
            {
                "agent_id": "claude-code",
                "run_id": "smoke-doubao-violation",
                "created_at": iso(2),
                "backend": ARK_BACKEND,
                "model_attempts": [
                    {"model": "doubao-seed-2.0-code", "status": "completed", "ok": True},
                ],
            }
        ],
    )
    violation_report = routing.build_report(24)
    violation = (
        violation_report.get("doubao_disable_coverage")
        if isinstance(violation_report.get("doubao_disable_coverage"), dict)
        else {}
    )
    check(
        "doubao attempt after disable is flagged",
        violation.get("ok") is False
        and "observed_doubao_model_attempts_after_disable" in (violation.get("violations") or []),
        failures,
    )

    result = {
        "ok": not failures,
        "checks": {
            "failed": failures,
            "count": 13,
        },
        "artifact_paths": artifacts,
        "tokens_printed": False,
    }
    routing.systemd_show_unit = saved_systemd_show_unit
    routing.pid_alive = saved_pid_alive
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
