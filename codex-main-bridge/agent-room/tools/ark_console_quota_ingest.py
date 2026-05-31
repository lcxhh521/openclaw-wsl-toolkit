#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOM = Path.home() / ".openclaw/workspace/codex-main-bridge/agent-room"
USAGE = ROOM / "token_channel_usage.json"
STATUS = ROOM / "ark_console_quota_ingest.status.json"
HOST = "127.0.0.1"
PORT = 18793


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def number(value):
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if n < 0 or n > 100:
        return None
    return n


def normalize_window(label: str) -> str | None:
    key = (label or "").strip().lower()
    if key in {"5h", "5hour", "five_hour", "rolling_5h"}:
        return "5h"
    if key in {"week", "weekly", "7d"}:
        return "Week"
    if key in {"month", "monthly"}:
        return "Month"
    return None


def merge_payload(payload: dict) -> dict:
    observed_at = payload.get("observed_at") or now_iso()
    raw_windows = payload.get("windows") if isinstance(payload.get("windows"), list) else []
    windows = {}
    for row in raw_windows:
        if not isinstance(row, dict):
            continue
        label = normalize_window(str(row.get("label") or ""))
        used = number(row.get("used_percent"))
        if not label or used is None:
            continue
        windows[label] = {
            "used_percent": used,
            "source": "volcengine_console_dom_bridge",
            "observed_at": observed_at,
            "reset_text": str(row.get("reset_text") or "")[:80],
        }
    if not windows:
        raise ValueError("no valid quota windows in payload")

    existing = {}
    if USAGE.exists():
        try:
            existing = json.loads(USAGE.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
    channels = existing.setdefault("channels", {})
    channel = channels.setdefault("ark-coding-plan", {})
    channel["source"] = "volcengine_console_dom_bridge"
    channel["observed_at"] = observed_at
    current_windows = channel.setdefault("windows", {})
    current_windows.update(windows)
    write_json(USAGE, existing)

    # Fast local cache merge. This does not touch OpenClaw gateway or models.
    subprocess.run([str(Path.home() / ".local/bin/openclaw-token-channel-cache")], timeout=20, check=False)
    status = {
        "updated_at": now_iso(),
        "status": "ok",
        "windows": sorted(windows.keys()),
        "openclaw_gateway_used": False,
    }
    write_json(STATUS, status)
    return status


class Handler(BaseHTTPRequestHandler):
    def _headers(self, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS, GET")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_OPTIONS(self):
        self._headers(204)

    def do_GET(self):
        self._headers(200)
        data = {"status": "ok", "service": "ark_console_quota_ingest", "updated_at": now_iso()}
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def do_POST(self):
        try:
            length = min(int(self.headers.get("Content-Length", "0")), 65536)
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            result = merge_payload(payload if isinstance(payload, dict) else {})
            self._headers(200)
            self.wfile.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))
        except Exception as exc:
            write_json(STATUS, {"updated_at": now_iso(), "status": "failed", "reason": str(exc)[:300]})
            self._headers(400)
            self.wfile.write(json.dumps({"status": "failed", "reason": str(exc)}, ensure_ascii=False).encode("utf-8"))

    def log_message(self, fmt, *args):
        return


if __name__ == "__main__":
    ROOM.mkdir(parents=True, exist_ok=True)
    write_json(STATUS, {"updated_at": now_iso(), "status": "listening", "host": HOST, "port": PORT})
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
