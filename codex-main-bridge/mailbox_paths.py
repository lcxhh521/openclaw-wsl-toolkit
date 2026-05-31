#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

CODE_ROOT = Path(__file__).resolve().parent
ACTIVE_POINTER = Path(os.environ.get("OPENCLAW_MAILBOX_ACTIVE_POINTER", str(CODE_ROOT / "active_mailbox.json")))
LEGACY_ROOT = CODE_ROOT


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig", errors="replace"))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def resolve_mailbox_root() -> Path:
    """Return the active mailbox data root while keeping code under CODE_ROOT.

    Compatibility rule: OPENCLAW_MAILBOX_ROOT remains an explicit override for
    tests and one-off recovery. Normal production uses active_mailbox.json.
    """
    override = os.environ.get("OPENCLAW_MAILBOX_ROOT")
    if override:
        return Path(override).expanduser()
    pointer = _read_json(ACTIVE_POINTER)
    raw = pointer.get("active_data_root") or pointer.get("mailbox_root") or pointer.get("data_root")
    if raw:
        return Path(str(raw)).expanduser()
    return LEGACY_ROOT


MAILBOX_ROOT = resolve_mailbox_root()


def pointer_status() -> dict[str, Any]:
    pointer = _read_json(ACTIVE_POINTER)
    return {
        "schema": "openclaw.codex_main_mailbox.active_pointer_status.v0",
        "code_root": str(CODE_ROOT),
        "pointer_path": str(ACTIVE_POINTER),
        "pointer_exists": ACTIVE_POINTER.exists(),
        "active_data_root": str(MAILBOX_ROOT),
        "legacy_root": str(LEGACY_ROOT),
        "active_epoch": pointer.get("active_epoch") or ("legacy-root" if MAILBOX_ROOT == LEGACY_ROOT else "unknown"),
        "namespace_rollover_active": MAILBOX_ROOT != LEGACY_ROOT,
        "override_env_present": bool(os.environ.get("OPENCLAW_MAILBOX_ROOT")),
    }
