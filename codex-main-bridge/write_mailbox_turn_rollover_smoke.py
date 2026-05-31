#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCRIPTS = ["mailbox_paths.py", "write_mailbox_turn.py", "archive_mailbox_turn.py", "context_rollover.py"]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="mailbox-writer-rollover-") as tmpdir:
        temp_root = Path(tmpdir)
        for name in SCRIPTS:
            shutil.copy2(ROOT / name, temp_root / name)
        (temp_root / "turn.json").write_text(json.dumps({"seq": 2, "needs_reply": "codex"}), encoding="utf-8")
        (temp_root / "codex_to_main.md").write_text("old codex", encoding="utf-8")
        (temp_root / "main_to_codex.md").write_text("old main", encoding="utf-8")
        write_jsonl(
            temp_root / "archive" / "mailbox-turns.jsonl",
            [
                {"seq": 1, "actor": "codex", "event": "smoke", "codex_to_main": "status: one", "main_to_codex": ""},
                {"seq": 2, "actor": "main", "event": "smoke", "codex_to_main": "", "main_to_codex": "status: two"},
            ],
        )
        content = temp_root / "content.md"
        content.write_text("new codex turn at threshold", encoding="utf-8")
        env = os.environ.copy()
        env["OPENCLAW_MAILBOX_ROOT"] = str(temp_root)
        env["OPENCLAW_MAILBOX_CONTEXT_ROLLOVER_THRESHOLD"] = "3"
        proc = subprocess.run(
            [
                sys.executable,
                str(temp_root / "write_mailbox_turn.py"),
                "--writer",
                "codex",
                "--needs-reply",
                "main",
                "--content-file",
                str(content),
                "--note",
                "rollover smoke",
                "--event",
                "rollover_smoke",
            ],
            cwd=str(temp_root),
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr
        result = json.loads(proc.stdout.strip().splitlines()[-1])
        assert result["seq"] == 3, result
        assert result["context_rollover_ok"] is True, result
        assert result["context_epoch"] == 1, result
        assert result["context_next_rollover_seq"] == 6, result
        turn = json.loads((temp_root / "turn.json").read_text(encoding="utf-8"))
        assert turn["context_epoch"] == 1, turn
        assert turn["context_rollover_source_seq"] == 3, turn
        assert Path(turn["context_summary_path"]).exists(), turn
        print(json.dumps({"ok": True, "result": result, "turn_context_epoch": turn["context_epoch"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
