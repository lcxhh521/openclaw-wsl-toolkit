#!/usr/bin/env python3
"""High-frequency raw feed snapshot for market immersion.

Stateful retention layer:
- Uses last_success_at as the next window start, with overlap for safety.
- Preserves raw source responses per attempt.
- Appends only deduplicated normalized items to daily items.jsonl.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
MI_PATH = SCRIPT_DIR / "market_immersion.py"
spec = importlib.util.spec_from_file_location("market_immersion", MI_PATH)
if not spec or not spec.loader:
    raise RuntimeError(f"cannot load {MI_PATH}")
mi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mi)  # type: ignore[union-attr]


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_iso(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value)
    except ValueError:
        return None


def item_key(item: dict[str, Any]) -> str:
    raw = "\u241f".join(
        str(item.get(k) or "").strip()
        for k in ("source", "type", "code", "url", "title", "date")
    )
    if not raw.strip("\u241f"):
        raw = json.dumps(item, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def load_seen_keys(path: Path) -> set[str]:
    seen: set[str] = set()
    if not path.exists():
        return seen
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        key = row.get("dedupe_key") or item_key(row)
        seen.add(str(key))
    return seen


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture raw market feed snapshot.")
    parser.add_argument("--config", default="../config/market_immersion_config.json")
    parser.add_argument("--lookback-minutes", type=int, default=90, help="Fallback lookback when no state exists.")
    parser.add_argument("--overlap-minutes", type=int, default=10, help="Overlap before last success to tolerate clock/API lag.")
    parser.add_argument("--max-lookback-minutes", type=int, default=180, help="Cap catch-up window after downtime.")
    args = parser.parse_args()

    config_path = (SCRIPT_DIR / args.config).resolve()
    config = load_json(config_path)
    config.setdefault("eastmoney_feed", {})["require_complete_window"] = False

    now = mi.now_local()
    output_root = Path(config.get("output_dir") or "~/.openclaw/workspace/market-immersion").expanduser()
    snapshot_root = output_root / "snapshots"
    state_path = snapshot_root / "state.json"
    state: dict[str, Any] = {}
    if state_path.exists():
        try:
            state = load_json(state_path)
        except Exception:
            state = {}

    last_success = parse_iso(state.get("last_success_at"))
    fallback_start = now - dt.timedelta(minutes=max(1, args.lookback_minutes))
    if last_success and last_success < now:
        window_start = last_success - dt.timedelta(minutes=max(0, args.overlap_minutes))
        max_start = now - dt.timedelta(minutes=max(1, args.max_lookback_minutes))
        if window_start < max_start:
            window_start = max_start
        window_source = "last_success_with_overlap"
    else:
        window_start = fallback_start
        window_source = "fallback_lookback"

    day_dir = snapshot_root / now.strftime("%Y-%m-%d")
    slug = now.strftime("%Y%m%d_%H%M%S_snapshot")
    raw_dir = day_dir / "raw" / slug
    raw_dir.mkdir(parents=True, exist_ok=True)

    entry = mi.collect_market_feed_entry(
        config=config,
        output_dir=raw_dir,
        window_start=window_start,
        window_end=now,
    )
    items = [] if not entry else ((entry.get("classification") or {}).get("items") or [])

    jsonl_path = day_dir / "items.jsonl"
    seen = load_seen_keys(jsonl_path)
    new_items = []
    duplicate_count = 0
    for item in items:
        key = item_key(item)
        if key in seen:
            duplicate_count += 1
            continue
        seen.add(key)
        new_items.append({"snapshot": slug, "dedupe_key": key, **item})
    with jsonl_path.open("a", encoding="utf-8") as f:
        for item in new_items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    manifest = {
        "version": 1,
        "kind": "market_feed_snapshot",
        "started_at": now.isoformat(timespec="seconds"),
        "window": {
            "start": window_start.isoformat(timespec="seconds"),
            "end": now.isoformat(timespec="seconds"),
            "source": window_source,
            "lookback_minutes": args.lookback_minutes,
            "overlap_minutes": args.overlap_minutes,
            "max_lookback_minutes": args.max_lookback_minutes,
        },
        "config_path": str(config_path),
        "entry": entry,
        "items_seen": len(items),
        "items_new": len(new_items),
        "items_duplicate": duplicate_count,
        "items_jsonl": str(jsonl_path),
    }
    manifest_path = day_dir / f"{slug}.manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "last_success_at": now.isoformat(timespec="seconds"),
                "last_success_manifest": str(manifest_path),
                "last_window_start": window_start.isoformat(timespec="seconds"),
                "last_items_seen": len(items),
                "last_items_new": len(new_items),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    latest_path = snapshot_root / "latest_manifest.json"
    latest_path.write_text(
        json.dumps({"manifest": str(manifest_path), "items_seen": len(items), "items_new": len(new_items)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"manifest={manifest_path}")
    print(f"items_seen={len(items)}")
    print(f"items_new={len(new_items)}")
    print(f"items_duplicate={duplicate_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
