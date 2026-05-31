#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path.home() / ".openclaw" / "workspace" / "codex-main-bridge"
ROOM = ROOT / "agent-room"
TOOLS = ROOM / "tools"
DEFAULT_FIXTURES = ROOM / "fixtures" / "telegram_group_ingress"
DEFAULT_ARTIFACTS = ROOM / "artifacts" / "telegram_group_ingress_dry_run"


def load_bridge() -> Any:
    path = TOOLS / "telegram_agent_bridge.py"
    spec = importlib.util.spec_from_file_location("telegram_agent_bridge", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def load_updates(fixture_dir: Path) -> list[dict[str, Any]]:
    updates: list[dict[str, Any]] = []
    for path in sorted(fixture_dir.glob("*.json")):
        data = read_json(path)
        if isinstance(data, list):
            updates.extend(data)
        else:
            updates.append(data)
    return updates


def main() -> int:
    parser = argparse.ArgumentParser(description="Dry-run Telegram group/private ingress normalization for Agent Room.")
    parser.add_argument("--fixture-dir", default=str(DEFAULT_FIXTURES))
    parser.add_argument("--out-root", default=str(DEFAULT_ARTIFACTS))
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args()

    bridge = load_bridge()
    fixture_dir = Path(args.fixture_dir)
    run_id = args.run_id or datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out_root) / run_id
    updates = load_updates(fixture_dir)
    result = bridge.normalize_updates(updates, out_dir)
    result.update({
        "schema": "openclaw.agent_room.telegram_group_ingress_normalizer_dry_run.v0",
        "run_id": run_id,
        "fixture_dir": str(fixture_dir),
        "artifact_dir": str(out_dir),
        "dry_run_only": True,
        "secret_values_printed": False,
        "telegram_outbound": False,
        "external_side_effects": False,
        "canonical_state_advanced": False,
    })
    write_json(out_dir / "dry_run_result.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
