#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from mailbox_paths import CODE_ROOT, ACTIVE_POINTER, LEGACY_ROOT, pointer_status

SCHEMA = "openclaw.codex_main_mailbox.epoch_pointer.v0"
DEFAULT_THRESHOLD = int(os.environ.get("OPENCLAW_MAILBOX_NAMESPACE_ROLLOVER_THRESHOLD", "1000"))


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_json(path: Path, default: Any = None) -> Any:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig", errors="replace"))
    except Exception:
        return default
    return value


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def sha256_file(path: Path) -> str:
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def active_pointer(default: bool = True) -> dict[str, Any]:
    pointer = read_json(ACTIVE_POINTER, {})
    if isinstance(pointer, dict) and pointer:
        return pointer
    if not default:
        return {}
    return {
        "schema": SCHEMA,
        "active_epoch": "legacy-root",
        "active_data_root": str(LEGACY_ROOT),
        "code_root": str(CODE_ROOT),
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "policy": {
            "threshold": DEFAULT_THRESHOLD,
            "rollover_kind": "mailbox_namespace",
            "context_summary_is_not_namespace_rollover": True,
        },
        "history": [],
    }


def init_pointer() -> dict[str, Any]:
    pointer = active_pointer(default=True)
    if not ACTIVE_POINTER.exists():
        write_json_atomic(ACTIVE_POINTER, pointer)
    return status()


def status() -> dict[str, Any]:
    pointer = active_pointer(default=True)
    active_root = Path(str(pointer.get("active_data_root") or LEGACY_ROOT)).expanduser()
    turn = read_json(active_root / "turn.json", {})
    context = read_json(active_root / "context_rollover_state.json", {})
    try:
        seq = int((turn or {}).get("seq") or 0)
    except Exception:
        seq = 0
    active_epoch = str(pointer.get("active_epoch") or "legacy-root")
    # Namespace epochs reset their own turn sequence. The source seq only records
    # where the previous namespace ended; it must not be subtracted from the new
    # epoch's local seq.
    turns_in_namespace = seq
    threshold = int(((pointer.get("policy") or {}).get("threshold") or DEFAULT_THRESHOLD))
    staged = pointer.get("staged_epoch") if isinstance(pointer.get("staged_epoch"), dict) else None
    return {
        "schema": "openclaw.codex_main_mailbox.epoch_status.v0",
        "created_at": now_iso(),
        "pointer": pointer_status(),
        "active_epoch": active_epoch,
        "active_data_root": str(active_root),
        "seq": seq,
        "turns_in_namespace": turns_in_namespace,
        "threshold": threshold,
        "namespace_rollover_needed": turns_in_namespace >= threshold,
        "context_rollover": {
            "context_epoch": (turn or {}).get("context_epoch") or (context or {}).get("context_epoch"),
            "context_rollover_source_seq": (turn or {}).get("context_rollover_source_seq") or (context or {}).get("rollover_source_seq"),
            "context_summary_path": (turn or {}).get("context_summary_path") or (context or {}).get("summary_path"),
        },
        "staged_epoch": staged,
        "ready_to_switch": bool(staged) and str((turn or {}).get("needs_reply") or "") in ("none", ""),
        "safe_switch_blockers": [] if str((turn or {}).get("needs_reply") or "") in ("none", "") else [f"active_mailbox_needs_reply={str((turn or {}).get('needs_reply') or '')}"],
    }


def next_epoch_id(pointer: dict[str, Any]) -> str:
    history = pointer.get("history") if isinstance(pointer.get("history"), list) else []
    nums: list[int] = []
    for item in history:
        if not isinstance(item, dict):
            continue
        raw = str(item.get("epoch") or "")
        if raw.startswith("epoch-"):
            try:
                nums.append(int(raw.split("-", 1)[1]))
            except Exception:
                pass
    active_raw = str(pointer.get("active_epoch") or "")
    if active_raw.startswith("epoch-"):
        try:
            nums.append(int(active_raw.split("-", 1)[1]))
        except Exception:
            pass
    staged = pointer.get("staged_epoch") if isinstance(pointer.get("staged_epoch"), dict) else None
    if staged:
        raw = str(staged.get("epoch") or "")
        if raw.startswith("epoch-"):
            try:
                nums.append(int(raw.split("-", 1)[1]))
            except Exception:
                pass
    return f"epoch-{(max(nums) + 1) if nums else 3:04d}"


def stage_epoch(force: bool = False) -> dict[str, Any]:
    pointer = active_pointer(default=True)
    st = status()
    if pointer.get("staged_epoch") and not force:
        return {"ok": True, "status": "already_staged", "epoch_status": st}
    epoch = next_epoch_id(pointer)
    dst = CODE_ROOT / "mailbox-epochs" / epoch
    dst.mkdir(parents=True, exist_ok=True)
    for sub in ["archive/snapshots", "context-rollovers"]:
        (dst / sub).mkdir(parents=True, exist_ok=True)
    active_root = Path(st["active_data_root"])
    summary_path = str((st.get("context_rollover") or {}).get("context_summary_path") or "")
    summary_text = ""
    if summary_path and Path(summary_path).exists():
        summary_text = Path(summary_path).read_text(encoding="utf-8", errors="replace")
    if not summary_text:
        summary_text = "No context summary was available; use previous mailbox archive for exact history."
    (dst / "mailbox-start-context.md").write_text(summary_text + "\n", encoding="utf-8")
    initial_turn = {
        "bridge": "codex-main-mailbox",
        "seq": 0,
        "last_writer": "system",
        "needs_reply": "none",
        "updated_at": now_iso(),
        "codex_file": str(dst / "codex_to_main.md"),
        "main_file": str(dst / "main_to_codex.md"),
        "note": f"Staged mailbox namespace {epoch}; not active until active_mailbox.json switches.",
        "mailbox_epoch": epoch,
        "previous_mailbox_root": str(active_root),
        "previous_mailbox_seq": st.get("seq"),
        "startup_context_path": str(dst / "mailbox-start-context.md"),
        "startup_context_sha256": sha256_file(dst / "mailbox-start-context.md"),
    }
    write_json_atomic(dst / "turn.json", initial_turn)
    (dst / "codex_to_main.md").write_text("", encoding="utf-8")
    (dst / "main_to_codex.md").write_text("", encoding="utf-8")
    pointer["staged_epoch"] = {
        "epoch": epoch,
        "data_root": str(dst),
        "created_at": now_iso(),
        "source_data_root": str(active_root),
        "source_seq": st.get("seq"),
        "startup_context_path": str(dst / "mailbox-start-context.md"),
        "startup_context_sha256": sha256_file(dst / "mailbox-start-context.md"),
    }
    pointer["updated_at"] = now_iso()
    write_json_atomic(ACTIVE_POINTER, pointer)
    return {"ok": True, "status": "staged", "staged_epoch": pointer["staged_epoch"], "epoch_status": status()}


def switch_epoch(force: bool = False) -> dict[str, Any]:
    pointer = active_pointer(default=True)
    staged = pointer.get("staged_epoch") if isinstance(pointer.get("staged_epoch"), dict) else None
    if not staged:
        return {"ok": False, "error": "no_staged_epoch", "epoch_status": status()}
    st = status()
    blockers = list(st.get("safe_switch_blockers") or [])
    if blockers and not force:
        return {"ok": False, "error": "safe_switch_blocked", "blockers": blockers, "epoch_status": st}
    prev = {
        "epoch": pointer.get("active_epoch") or "legacy-root",
        "data_root": pointer.get("active_data_root") or str(LEGACY_ROOT),
        "ended_at": now_iso(),
        "ended_seq": st.get("seq"),
    }
    history = pointer.get("history") if isinstance(pointer.get("history"), list) else []
    history.append(prev)
    pointer["history"] = history
    pointer["active_epoch"] = staged["epoch"]
    pointer["active_data_root"] = staged["data_root"]
    pointer["active_epoch_created_from_seq"] = staged.get("source_seq")
    pointer["activated_at"] = now_iso()
    pointer["updated_at"] = now_iso()
    pointer.pop("staged_epoch", None)
    write_json_atomic(ACTIVE_POINTER, pointer)
    return {"ok": True, "status": "switched", "pointer": pointer_status(), "epoch_status": status()}


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage Codex/Main mailbox namespace epochs.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    sub.add_parser("status")
    stage = sub.add_parser("stage")
    stage.add_argument("--force", action="store_true")
    switch = sub.add_parser("switch")
    switch.add_argument("--force", action="store_true")
    parser.add_argument("--json", action="store_true")
    args, unknown = parser.parse_known_args()
    # Accept --json before or after the subcommand; operators naturally try both.
    if unknown:
        if unknown == ["--json"]:
            args.json = True
        else:
            parser.error("unrecognized arguments: " + " ".join(unknown))
    if args.cmd == "init":
        result = init_pointer()
    elif args.cmd == "status":
        init_pointer()
        result = status()
    elif args.cmd == "stage":
        init_pointer()
        result = stage_epoch(force=args.force)
    elif args.cmd == "switch":
        init_pointer()
        result = switch_epoch(force=args.force)
    else:
        raise SystemExit(2)
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.json else None, sort_keys=True))
    return 0 if result.get("ok", True) is not False else 1


if __name__ == "__main__":
    raise SystemExit(main())
