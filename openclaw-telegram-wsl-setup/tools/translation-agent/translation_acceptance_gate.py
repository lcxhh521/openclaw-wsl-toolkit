#!/usr/bin/env python3
"""Run main-side acceptance checks for a Translation Agent run.

This gate sits above `translation_artifact_gate.py`: it verifies that a run is
actually ready for main acceptance before Alex sees a success claim.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any


WORKSPACE = Path(__file__).resolve().parents[3]


def now_cst() -> str:
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(timespec="seconds")


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def resolve_under_run(run_dir: Path, value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = run_dir / path
    return path.resolve()


def path_inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def collect_manifest_artifacts(manifest: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for item in as_list(manifest.get("artifacts")):
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict):
            path = item.get("path") or item.get("file") or item.get("artifact")
            if path:
                out.append(str(path))
    outputs = manifest.get("outputs")
    if isinstance(outputs, dict):
        for value in outputs.values():
            if isinstance(value, str):
                out.append(value)
            elif isinstance(value, list):
                out.extend(str(item) for item in value if isinstance(item, str))
    deliverables = manifest.get("deliverables")
    if isinstance(deliverables, dict):
        out.extend(str(value) for value in deliverables.values() if isinstance(value, str))
    elif isinstance(deliverables, list):
        out.extend(str(item) for item in deliverables if isinstance(item, str))
    return list(dict.fromkeys(item for item in out if item))


def collect_sources(run_dir: Path, ledger: dict[str, Any], manifest: dict[str, Any]) -> list[str]:
    sources: list[str] = []
    for value in as_list(ledger.get("sources")) + as_list(manifest.get("sources")):
        if isinstance(value, str):
            sources.append(value)
        elif isinstance(value, dict):
            path = value.get("path") or value.get("file") or value.get("source")
            if path:
                sources.append(str(path))
    source = manifest.get("source")
    if isinstance(source, str):
        sources.append(source)
    return list(dict.fromkeys(item for item in sources if item))


def source_exists(run_dir: Path, source: str) -> bool:
    path = Path(source)
    candidates = [path] if path.is_absolute() else [run_dir / path, WORKSPACE / path]
    return any(candidate.exists() for candidate in candidates)


def run_gate(run_dir: Path, min_artifact_bytes: int) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    ledger = read_json(run_dir / "task_ledger.json")
    acceptance = read_json(run_dir / "acceptance_plan.json")
    manifest_path = run_dir / "manifest.json"
    manifest = read_json(manifest_path)
    failures: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    checked_artifacts: list[dict[str, Any]] = []

    if not (run_dir / "user_request.md").exists():
        failures.append({"gate": "missing_user_request", "path": "user_request.md"})
    if not (run_dir / "handoff_brief.md").exists():
        failures.append({"gate": "missing_handoff_brief", "path": "handoff_brief.md"})
    if not ledger:
        failures.append({"gate": "missing_or_unreadable_task_ledger", "path": "task_ledger.json"})
    if not acceptance:
        warnings.append({"gate": "missing_or_unreadable_acceptance_plan", "path": "acceptance_plan.json"})

    worker_status = str(ledger.get("translation_worker_status") or "")
    if worker_status in {"", "not_started"}:
        failures.append({"gate": "translation_worker_not_started", "translation_worker_status": worker_status or "missing"})

    sources = collect_sources(run_dir, ledger, manifest)
    for source in sources:
        if not source_exists(run_dir, source):
            failures.append({"gate": "source_missing", "source": source})

    if not manifest_path.exists():
        failures.append({"gate": "missing_manifest", "path": "manifest.json"})
        artifacts: list[str] = []
    elif not manifest:
        failures.append({"gate": "unreadable_manifest", "path": "manifest.json"})
        artifacts = []
    else:
        artifacts = collect_manifest_artifacts(manifest)
        if not artifacts:
            failures.append({"gate": "manifest_has_no_artifacts", "path": "manifest.json"})

    for artifact in artifacts:
        path = resolve_under_run(run_dir, artifact)
        if not path_inside(path, run_dir):
            failures.append({"gate": "artifact_path_escapes_run_dir", "artifact": artifact})
            continue
        if not path.exists():
            failures.append({"gate": "artifact_missing", "artifact": artifact})
            continue
        size = path.stat().st_size
        checked_artifacts.append({"artifact": artifact, "bytes": size})
        if size < min_artifact_bytes:
            failures.append({"gate": "artifact_too_small", "artifact": artifact, "bytes": size, "min_bytes": min_artifact_bytes})

    status = "PASSED" if not failures else "FAILED"
    next_action = (
        "main can proceed to delivery gate"
        if status == "PASSED"
        else "repair blocker before claiming Translation Agent success"
    )
    return {
        "schema": "openclaw.translation_acceptance_gate.v0",
        "checked_at": now_cst(),
        "run_dir": str(run_dir),
        "status": status,
        "failures": failures,
        "warnings": warnings,
        "sources_checked": sources,
        "artifacts_checked": checked_artifacts,
        "next_action": next_action,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--min-artifact-bytes", type=int, default=20)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    report = run_gate(args.run_dir, args.min_artifact_bytes)
    if args.out:
        out = args.out if args.out.is_absolute() else args.run_dir / args.out
        write_json(out, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("TRANSLATION_ACCEPTANCE_GATE_OK" if report["status"] == "PASSED" else "TRANSLATION_ACCEPTANCE_GATE_FAILED")
    return 0 if report["status"] == "PASSED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
