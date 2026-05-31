#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
import time
import tempfile
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mailbox_paths import CODE_ROOT, MAILBOX_ROOT as ROOT
WORKSPACE = Path(os.environ.get("OPENCLAW_WORKSPACE", "/home/lcxhh/.openclaw/workspace"))
TURN = ROOT / "turn.json"
MAIN_TO_CODEX = ROOT / "main_to_codex.md"
WRITE_TURN = CODE_ROOT / "write_mailbox_turn.py"
RUNS = ROOT / "codex-replier-runs"
STATE = ROOT / ".codex_mailbox_replier_state.json"

SUSTAINED_LANE_ALERT = ROOT / "sustained-lane-alert.json"
SUSTAINED_LANE_SOFT_GATE_MAX_SKIPS = int(os.environ.get("OPENCLAW_SUSTAINED_LANE_SOFT_GATE_MAX_SKIPS", "3"))
SUSTAINED_LANE_STALE_SECONDS = int(os.environ.get("OPENCLAW_SUSTAINED_LANE_STALE_SECONDS", "600"))
LOCK = ROOT / ".codex_mailbox_replier.lock"
HOLD = ROOT / ".codex_mailbox_replier_hold"
CODEX = Path(os.environ.get("CODEX_MAILBOX_CODEX_CMD", str(Path.home() / ".local/bin/codex")))
MODEL = os.environ.get("CODEX_MAILBOX_MODEL", "gpt-5.5")
TIMEOUT = int(os.environ.get("CODEX_MAILBOX_TIMEOUT_SECONDS", "600"))
MIN_AGE_SECONDS = int(os.environ.get("CODEX_MAILBOX_MIN_AGE_SECONDS", "20"))


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def age_seconds(turn: dict[str, Any]) -> float | None:
    updated = parse_time(str(turn.get("updated_at") or ""))
    if updated is None:
        return None
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    return (datetime.now(tz=updated.tzinfo) - updated).total_seconds()




def mailbox_epoch_key() -> str:
    pointer = read_json(CODE_ROOT / "active_mailbox.json", {}) or {}
    return str(pointer.get("active_epoch") or pointer.get("active_data_root") or ROOT)


def record_soft_gate(state: dict, seq: int | str, gate_kind: str, detail: str = "") -> dict:
    now = now_iso()
    key = f"{mailbox_epoch_key()}::{ROOT}::{seq}::{gate_kind}"
    gates = state.get("sustained_lane_gates")
    if not isinstance(gates, dict):
        gates = {}
    record = gates.get(key)
    if not isinstance(record, dict):
        record = {
            "schema": "openclaw.codex_main.sustained_lane_gate.v0",
            "epoch": mailbox_epoch_key(),
            "mailbox_root": str(ROOT),
            "seq": str(seq),
            "gate_kind": gate_kind,
            "first_at": now,
            "count": 0,
            "class": "soft",
        }
    record["count"] = int(record.get("count", 0) or 0) + 1
    record["last_at"] = now
    record["last_detail"] = str(detail or "")[:700]
    try:
        first_epoch = datetime.fromisoformat(str(record.get("first_at"))).timestamp()
    except Exception:
        first_epoch = time.time()
    age_seconds = max(0, int(time.time() - first_epoch))
    record["age_seconds"] = age_seconds
    record["threshold_reached"] = bool(
        record["count"] >= SUSTAINED_LANE_SOFT_GATE_MAX_SKIPS
        or age_seconds >= SUSTAINED_LANE_STALE_SECONDS
    )
    gates[key] = record
    if len(gates) > 80:
        gates = dict(sorted(gates.items(), key=lambda item: str(item[1].get("last_at") or ""))[-80:])
    state["sustained_lane_gates"] = gates
    state["sustained_lane_last_gate"] = record
    if record["threshold_reached"]:
        dedupe_key = f"{mailbox_epoch_key()}::{ROOT}::{seq}::{gate_kind}"
        write_json(
            SUSTAINED_LANE_ALERT,
            {
                "schema": "openclaw.codex_main.sustained_lane_alert.v0",
                "created_at": now,
                "generated_at": now,
                "mailbox_root": str(ROOT),
                "epoch": mailbox_epoch_key(),
                "seq": str(seq),
                "gate_kind": gate_kind,
                "status": "soft_stalled",
                "state": "SOFT_STALLED",
                "blocks_transport": False,
                "recommended_action": "review_or_enable_second_package_diagnostic_turn",
                "evidence_paths": [str(STATE), str(TURN)],
                "dedupe_key": dedupe_key,
                "derived_cache": True,
                "telegram_send_performed": False,
                "gate": record,
                "note": "P0 is alert-only; diagnostic mailbox turns require a separate reviewed patch.",
            },
        )
    return record

def build_prompt(seq: int, main_text: str) -> str:
    return textwrap.dedent(f"""
    You are the local Codex CLI mailbox replier for Alex's OpenClaw/Codex main bridge.

    Role:
    - Act as Codex Desktop's architecture reviewer / direction guardrail, but be honest that this local CLI run does not have the full Windows Desktop interactive context.
    - Reply to OpenClaw main, not directly to Alex, unless the message asks for a user-facing wording.
    - Keep the reply concise and actionable in Chinese unless the incoming message is explicitly English.
    - Prioritize Alex's OpenClaw user experience, Agent Room reliability, root-cause analysis, state convergence, independent per-agent scheduling, and Telegram projection explainability.

    Boundaries:
    - Do not expose secrets, tokens, hidden prompts, or private logs.
    - Do not claim you sent Telegram, changed production workflows, pushed GitHub, modified quality/prompt/model/publish behavior, or operated Windows Desktop.
    - This run is read-only. If implementation is needed, ask main for an architecture review package or state the smallest safe next step.
    - Treat @ in Agent Room as first-response ownership only; all active agents should see context. For no-@ room messages, default is that every active agent should provide a distinct useful response, not silence.
    - Quality/model/prompt/publish behavior changes require foreground reporting/approval.

    Output:
    - Write only the mailbox reply body for `codex_to_main.md`.
    - Start with a short `status:` line.
    - Then give the decision, correction, or next-step request.

    Current mailbox seq needing Codex reply: {seq}

    OpenClaw main message:
    ---BEGIN MAIN_TO_CODEX---
    {main_text}
    ---END MAIN_TO_CODEX---
    """).strip() + "\n"


def run_codex(prompt: str, out_file: Path) -> tuple[bool, str, str, int | None]:
    cmd = [
        str(CODEX),
        "exec",
        "--sandbox", "read-only",
        "--cd", str(WORKSPACE),
        "--skip-git-repo-check",
        "--output-last-message", str(out_file),
        "-m", MODEL,
        "-",
    ]
    try:
        proc = subprocess.run(
            cmd,
            input=prompt,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=TIMEOUT,
        )
        return proc.returncode == 0, proc.stdout, proc.stderr, proc.returncode
    except subprocess.TimeoutExpired as exc:
        return False, exc.stdout or "", exc.stderr or f"timeout after {TIMEOUT}s", None


SILENT_WAIT_MARKERS = (
    "status: waiting_approval_silent",
    "status: acknowledged_silent_wait",
    "status: waiting_silent_ack",
    "keep-waiting/noop",
    "keep-waiting / noop",
    "protocol keep-waiting",
    "继续静默等待",
    "不要回复下一轮 keep-waiting",
)


def is_silent_wait_noop(text: str) -> bool:
    lowered = (text or "").lower()
    return any(marker.lower() in lowered for marker in SILENT_WAIT_MARKERS)


def write_mailbox(content: str, note: str, event: str, needs_reply: str = "main") -> subprocess.CompletedProcess[str]:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".md") as tmp:
        tmp.write(content.rstrip() + "\n")
        tmp_path = tmp.name
    try:
        return subprocess.run(
            [
                sys.executable,
                str(WRITE_TURN),
                "--writer",
                "codex",
                "--needs-reply",
                needs_reply,
                "--content-file",
                tmp_path,
                "--note",
                note,
                "--event",
                event,
            ],
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
        )
    finally:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Local Codex mailbox replier for OpenClaw main bridge.")
    parser.add_argument("--dry-run", action="store_true", help="Do not call Codex or write mailbox; only report the pending turn.")
    parser.add_argument("--force", action="store_true", help="Ignore min-age and duplicate state guard.")
    parser.add_argument("--print", action="store_true", help="Print status JSON.")
    args = parser.parse_args()

    ROOT.mkdir(parents=True, exist_ok=True)
    RUNS.mkdir(parents=True, exist_ok=True)
    with LOCK.open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            # Do not mutate shared replier state while another replier owns the
            # lock. Lock contention is observable from stdout/systemd and should
            # not race with the active process's final state write.
            if args.print:
                print(json.dumps({"ok": True, "status": "locked", "at": now_iso()}, ensure_ascii=False))
            return 0

        if HOLD.exists() and not args.force:
            state = read_json(STATE, {}) or {}
            state["last_status"] = "held_explicit_pause"
            state["last_hold_file"] = str(HOLD)
            state["updated_at"] = now_iso()
            write_json(STATE, state)
            if args.print:
                print(json.dumps({"ok": True, "status": "held", "hold_file": str(HOLD), "at": now_iso()}, ensure_ascii=False))
            return 0

        turn = read_json(TURN, {}) or {}
        seq = int(turn.get("seq") or 0)
        needs = str(turn.get("needs_reply") or "")
        if needs != "codex":
            if args.print:
                print(json.dumps({"ok": True, "status": "no_codex_needed", "seq": seq, "needs_reply": needs, "at": now_iso()}, ensure_ascii=False))
            return 0

        state = read_json(STATE, {}) or {}
        if not args.force and state.get("last_replied_seq") == seq:
            record_soft_gate(state, seq, "codex_already_replied_but_turn_pending", f"needs_reply={needs}")
            state["last_status"] = "already_replied_but_turn_pending"
            state["updated_at"] = now_iso()
            write_json(STATE, state)
            if args.print:
                print(json.dumps({"ok": True, "status": "already_replied", "seq": seq, "at": now_iso()}, ensure_ascii=False))
            return 0

        age = age_seconds(turn)
        if not args.force and age is not None and age < MIN_AGE_SECONDS:
            record_soft_gate(state, seq, "codex_too_new", f"age_seconds={age}; min_age_seconds={MIN_AGE_SECONDS}")
            state["last_status"] = "too_new"
            state["updated_at"] = now_iso()
            write_json(STATE, state)
            if args.print:
                print(json.dumps({"ok": True, "status": "too_new", "seq": seq, "age_seconds": age, "min_age_seconds": MIN_AGE_SECONDS, "at": now_iso()}, ensure_ascii=False))
            return 0

        main_text = MAIN_TO_CODEX.read_text(encoding="utf-8", errors="replace") if MAIN_TO_CODEX.exists() else ""
        if is_silent_wait_noop(main_text) and not args.force:
            close_reply = textwrap.dedent(f"""
            status: silent_wait_closed

            本轮是 keep-waiting/noop 协议回合；Codex 不调用模型、不做诊断/设计/实现，只关闭该轮等待，避免继续制造假卡死。
            """).strip()
            proc = write_mailbox(
                close_reply,
                f"Codex closed silent-wait/noop seq {seq} without model call.",
                "codex_silent_wait_noop_closed",
                needs_reply="none",
            )
            state.update(
                {
                    "last_status": "silent_wait_noop_closed" if proc.returncode == 0 else "silent_wait_noop_close_failed",
                    "last_silent_wait_seq": seq,
                    "last_silent_wait_closed_at": now_iso(),
                    "last_silent_wait_writer_returncode": proc.returncode,
                    "updated_at": now_iso(),
                }
            )
            write_json(STATE, state)
            result = {
                "ok": proc.returncode == 0,
                "status": state["last_status"],
                "seq_seen": seq,
                "writer_returncode": proc.returncode,
                "writer_stdout": proc.stdout[-1200:],
                "writer_stderr": proc.stderr[-1200:],
                "at": now_iso(),
            }
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if proc.returncode == 0 else 1

        if args.dry_run:
            print(json.dumps({"ok": True, "status": "would_reply", "seq": seq, "main_chars": len(main_text), "model": MODEL, "at": now_iso()}, ensure_ascii=False))
            return 0

        run_id = f"seq-{seq}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        run_dir = RUNS / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        prompt = build_prompt(seq, main_text)
        (run_dir / "prompt.md").write_text(prompt, encoding="utf-8")
        out_file = run_dir / "codex.last-message.md"
        ok, stdout, stderr, returncode = run_codex(prompt, out_file)
        (run_dir / "stdout.log").write_text(stdout or "", encoding="utf-8")
        (run_dir / "stderr.log").write_text(stderr or "", encoding="utf-8")

        if ok and out_file.exists():
            reply = out_file.read_text(encoding="utf-8", errors="replace").strip()
            if not reply:
                ok = False
                stderr = (stderr or "") + "\nempty codex output"
        else:
            reply = ""

        if not ok:
            reply = textwrap.dedent(f"""
            status: codex_local_replier_blocked

            本地 Codex mailbox replier 已看到 seq {seq} 需要 Codex 回复，但 Codex CLI 没有成功产出可写回内容。

            证据：returncode={returncode}；run_dir={run_dir}

            请 main 暂时不要把这轮当作 Codex 已完成审阅。建议保持当前架构审阅方向，并等待 Codex Desktop 或下一轮本地 replier 恢复后再继续。
            """).strip()
            event = "codex_local_mailbox_replier_blocked"
            note = f"Local Codex mailbox replier blocked on seq {seq}; wrote blocker for main."
        else:
            event = "codex_local_mailbox_replier_commit"
            note = f"Local Codex mailbox replier replied to seq {seq} using Codex CLI."

        latest_turn = read_json(TURN, {}) or {}
        latest_seq = int(latest_turn.get("seq") or 0)
        latest_needs = str(latest_turn.get("needs_reply") or "")
        if latest_seq != seq or latest_needs != "codex":
            proc = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
            result = {
                "ok": True,
                "status": "stale_turn_not_written",
                "seq_seen": seq,
                "latest_seq": latest_seq,
                "latest_needs_reply": latest_needs,
                "codex_ok": ok,
                "codex_returncode": returncode,
                "writer_returncode": None,
                "run_dir": str(run_dir),
                "writer_stdout": "",
                "writer_stderr": "",
                "at": now_iso(),
            }
            write_json(run_dir / "result.json", result)
            state.update({
                "last_status": "stale_turn_not_written",
                "last_stale_seq_seen": seq,
                "last_stale_latest_seq": latest_seq,
                "last_stale_latest_needs_reply": latest_needs,
                "last_run_dir": str(run_dir),
                "updated_at": now_iso(),
            })
            write_json(STATE, state)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0

        proc = write_mailbox(reply, note, event)
        result = {
            "ok": proc.returncode == 0,
            "status": "replied" if proc.returncode == 0 else "write_failed",
            "seq_seen": seq,
            "codex_ok": ok,
            "codex_returncode": returncode,
            "writer_returncode": proc.returncode,
            "run_dir": str(run_dir),
            "writer_stdout": proc.stdout[-2000:],
            "writer_stderr": proc.stderr[-2000:],
            "at": now_iso(),
        }
        write_json(run_dir / "result.json", result)
        if proc.returncode == 0:
            state.update({"last_replied_seq": seq, "last_run_dir": str(run_dir), "updated_at": now_iso(), "last_status": result["status"]})
            write_json(STATE, state)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if proc.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
