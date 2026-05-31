#!/usr/bin/env python3
"""Local recall smoke for OpenClaw memory-enhancement work.

This script does not call remote embedding APIs. It checks whether a known term is:
- present in workspace files;
- present in the local OpenClaw memory SQLite index;
- present in local FTS/chunk tables.

Use it before any remote reindex to distinguish write/file issues from index
freshness and semantic retrieval issues.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

DEFAULT_ROOT = Path.home() / ".openclaw" / "workspace"
DEFAULT_DBS = {
    "main": Path.home() / ".openclaw" / "memory" / "main.sqlite",
    "telegram": Path.home() / ".openclaw" / "memory" / "telegram.sqlite",
    "codex": Path.home() / ".openclaw" / "memory" / "codex.sqlite",
}


def relpath(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except Exception:
        return str(path)


def scan_files(root: Path, term: str, paths: list[str]) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    candidates: list[Path] = []
    if paths:
        candidates = [root / p for p in paths]
    else:
        candidates = [root / "MEMORY.md"] + sorted((root / "memory").glob("*.md"))
    for path in candidates:
        try:
            lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        except FileNotFoundError:
            continue
        for line_no, line in enumerate(lines, 1):
            if term in line:
                hits.append({
                    "path": relpath(path, root),
                    "line": line_no,
                    "preview": line[:300],
                })
    return hits


def table_exists(cur: sqlite3.Cursor, table: str) -> bool:
    cur.execute("select 1 from sqlite_master where type='table' and name=?", (table,))
    return cur.fetchone() is not None


def query_db(db: Path, term: str, target_path_like: str | None) -> dict[str, Any]:
    result: dict[str, Any] = {"dbPath": str(db), "exists": db.exists()}
    if not db.exists():
        return result
    con = sqlite3.connect(db)
    cur = con.cursor()
    try:
        result["tables"] = [row[0] for row in cur.execute("select name from sqlite_master where type='table' order by name")]
        if table_exists(cur, "files"):
            if target_path_like:
                cur.execute("select path, size, mtime from files where path like ? order by path", (target_path_like,))
            else:
                cur.execute("select path, size, mtime from files order by mtime desc limit 10")
            rows = []
            for path, size, mtime in cur.fetchall():
                rows.append({"path": path, "size": size, "mtime": mtime})
            result["fileRows"] = rows
        if table_exists(cur, "chunks"):
            cur.execute("select count(*) from chunks")
            result["chunkCount"] = cur.fetchone()[0]
            cur.execute("select path, start_line, end_line, substr(text,1,500) from chunks where text like ? order by path, start_line limit 20", (f"%{term}%",))
            result["chunkHits"] = [
                {"path": row[0], "startLine": row[1], "endLine": row[2], "preview": row[3]}
                for row in cur.fetchall()
            ]
            if target_path_like:
                cur.execute("select path, start_line, end_line, substr(text,1,240) from chunks where path like ? order by start_line limit 20", (target_path_like,))
                result["targetPathChunks"] = [
                    {"path": row[0], "startLine": row[1], "endLine": row[2], "preview": row[3]}
                    for row in cur.fetchall()
                ]
        if table_exists(cur, "chunks_fts"):
            try:
                cur.execute("select path, start_line, end_line, substr(text,1,500) from chunks_fts where chunks_fts match ? limit 20", (term,))
                result["ftsHits"] = [
                    {"path": row[0], "startLine": row[1], "endLine": row[2], "preview": row[3]}
                    for row in cur.fetchall()
                ]
            except Exception as exc:
                result["ftsError"] = f"{type(exc).__name__}: {exc}"
    finally:
        con.close()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Local-only recall smoke for OpenClaw memory index freshness")
    parser.add_argument("--term", required=True, help="Exact marker/term to check locally")
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    parser.add_argument("--path", action="append", default=[], help="Relative file path(s) to scan; default scans MEMORY.md and memory/*.md")
    parser.add_argument("--target-path-like", default=None, help="SQLite LIKE pattern for expected indexed path, e.g. %2026-05-25.md%")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    root = Path(os.path.expanduser(args.root)).resolve()
    file_hits = scan_files(root, args.term, args.path)
    db_results = {name: query_db(path, args.term, args.target_path_like) for name, path in DEFAULT_DBS.items()}
    result = {
        "schema": "openclaw.volcengine_coding_plan.local_recall_smoke.v0",
        "generatedAt": int(time.time()),
        "root": str(root),
        "term": args.term,
        "fileHits": file_hits,
        "dbResults": db_results,
        "summary": {
            "fileHitCount": len(file_hits),
            "dbChunkHitCount": sum(len((db_results[name].get("chunkHits") or [])) for name in db_results),
            "dbFtsHitCount": sum(len((db_results[name].get("ftsHits") or [])) for name in db_results if isinstance(db_results[name].get("ftsHits"), list)),
            "targetPathIndexed": any(bool(db_results[name].get("targetPathChunks")) for name in db_results),
        },
        "notes": [
            "Local-only: no remote embeddings or OpenClaw CLI search calls were made.",
            "If fileHits > 0 but db hits are 0, the issue is index freshness/coverage, not write failure.",
        ],
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    # Return non-zero only if the marker is absent from files; DB freshness gaps are diagnostic, not script failure.
    return 0 if file_hits else 2


if __name__ == "__main__":
    raise SystemExit(main())
