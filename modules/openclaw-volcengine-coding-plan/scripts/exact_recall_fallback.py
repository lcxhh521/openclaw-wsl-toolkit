#!/usr/bin/env python3
"""Local exact-string recall fallback for OpenClaw memory/architecture artifacts.

Why this exists:
- Semantic memory search can miss fresh notes if the memory index is stale.
- Embedding calls can be rate-limited.
- IDs, run IDs, error codes, file names, and unique markers should not require
  remote embeddings at all.

This script is local-only: it reads configured workspace files and prints bounded
matches. It does not call OpenClaw, providers, or remote APIs.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

DEFAULT_ROOT = Path.home() / ".openclaw" / "workspace"
DEFAULT_GLOBS = [
    "MEMORY.md",
    "memory/*.md",
    "memory/**/*.md",
    "modules/**/*.md",
    "modules/**/*.json",
    "modules/**/*.py",
    "codex-main-bridge/agent-room/artifacts/**/*.md",
    "codex-main-bridge/agent-room/artifacts/**/*.json",
    "codex-main-bridge/*.md",
    "codex-main-bridge/tmp/**/*.md",
    "codex-main-bridge/agent-room/rooms/**/*.json",
    "codex-main-bridge/agent-room/config/**/*.json",
]
DEFAULT_EXCLUDE_PARTS = {
    ".git",
    "__pycache__",
    ".venv",
    "node_modules",
}
MAX_FILE_BYTES_DEFAULT = 2_000_000


@dataclass
class Hit:
    path: str
    line: int
    column: int
    kind: str
    preview: str


def is_excluded(path: Path) -> bool:
    return any(part in DEFAULT_EXCLUDE_PARTS for part in path.parts)


def iter_candidate_files(root: Path, globs: list[str], max_file_bytes: int) -> Iterable[Path]:
    seen: set[Path] = set()
    for pattern in globs:
        for path in root.glob(pattern):
            if path in seen or not path.is_file() or is_excluded(path):
                continue
            seen.add(path)
            try:
                if path.stat().st_size > max_file_bytes:
                    continue
            except OSError:
                continue
            yield path


def make_preview(line: str, start: int, end: int, width: int = 220) -> str:
    left = max(0, start - width // 2)
    right = min(len(line), end + width // 2)
    prefix = "…" if left > 0 else ""
    suffix = "…" if right < len(line) else ""
    return prefix + line[left:right].strip() + suffix


def search_file(path: Path, root: Path, terms: list[str], regexes: list[re.Pattern[str]], ignore_case: bool) -> list[Hit]:
    hits: list[Hit] = []
    try:
        lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    except Exception:
        return hits
    rel = str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
    cmp_terms = [term.lower() for term in terms] if ignore_case else terms
    for line_no, line in enumerate(lines, 1):
        cmp_line = line.lower() if ignore_case else line
        for original, term in zip(terms, cmp_terms):
            start = cmp_line.find(term)
            if start >= 0:
                hits.append(Hit(rel, line_no, start + 1, f"literal:{original}", make_preview(line, start, start + len(term))))
        for pattern in regexes:
            for match in pattern.finditer(line):
                hits.append(Hit(rel, line_no, match.start() + 1, f"regex:{pattern.pattern}", make_preview(line, match.start(), match.end())))
    return hits


def classify_term(term: str) -> str:
    if re.search(r"[A-Za-z]+[-_][A-Za-z0-9_.:-]+", term):
        return "id_or_artifact_token"
    if re.fullmatch(r"[A-Fa-f0-9]{8,}", term):
        return "hash_like"
    if "/" in term or term.endswith((".md", ".json", ".py")):
        return "path_like"
    return "literal"


def main() -> int:
    parser = argparse.ArgumentParser(description="Local exact-string fallback over memory and architecture artifacts")
    parser.add_argument("terms", nargs="*", help="Literal terms to find")
    parser.add_argument("--term", action="append", default=[], help="Literal term; may be repeated")
    parser.add_argument("--regex", action="append", default=[], help="Regex pattern; may be repeated")
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    parser.add_argument("--glob", action="append", default=[], help="Override/add search glob; repeatable. If omitted, safe defaults are used.")
    parser.add_argument("--ignore-case", action="store_true")
    parser.add_argument("--max-results", type=int, default=40)
    parser.add_argument("--max-file-bytes", type=int, default=MAX_FILE_BYTES_DEFAULT)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    root = Path(os.path.expanduser(args.root)).resolve()
    terms = [t for t in [*args.terms, *args.term] if t]
    flags = re.IGNORECASE if args.ignore_case else 0
    regexes = [re.compile(pattern, flags) for pattern in args.regex]
    globs = args.glob or DEFAULT_GLOBS

    if not terms and not regexes:
        raise SystemExit("Provide at least one literal term or --regex pattern")

    all_hits: list[Hit] = []
    file_count = 0
    for path in iter_candidate_files(root, globs, args.max_file_bytes):
        file_count += 1
        hits = search_file(path, root, terms, regexes, args.ignore_case)
        if hits:
            all_hits.extend(hits)
        if len(all_hits) >= args.max_results:
            all_hits = all_hits[: args.max_results]
            break

    result: dict[str, Any] = {
        "schema": "openclaw.local_exact_recall_fallback.v0",
        "generatedAt": int(time.time()),
        "root": str(root),
        "terms": [{"term": term, "class": classify_term(term)} for term in terms],
        "regexes": [pattern.pattern for pattern in regexes],
        "globs": globs,
        "searchedFiles": file_count,
        "hitCount": len(all_hits),
        "truncated": len(all_hits) >= args.max_results,
        "hits": [hit.__dict__ for hit in all_hits],
        "notes": [
            "Local-only fallback: no remote embeddings or OpenClaw memory_search calls.",
            "Use for exact IDs, run IDs, file names, markers, error codes, and suspicious semantic zero-hit cases.",
        ],
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for hit in result["hits"]:
            print(f"{hit['path']}:{hit['line']}:{hit['column']} [{hit['kind']}] {hit['preview']}")
        if not result["hits"]:
            print("NO_LOCAL_EXACT_HITS")
    return 0 if all_hits else 1


if __name__ == "__main__":
    raise SystemExit(main())
