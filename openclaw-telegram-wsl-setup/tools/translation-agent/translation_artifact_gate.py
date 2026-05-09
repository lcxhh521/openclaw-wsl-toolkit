#!/usr/bin/env python3
"""Verify translation worker artifacts before accepting a subagent run.

Usage:
  python3 tools/translation_artifact_gate.py --cwd RUN_DIR --expect file.md --expect patch.py --min-bytes 20
  python3 tools/translation_artifact_gate.py --cwd RUN_DIR --expect report.md:marker1 --expect patch.py

Exits nonzero on missing/empty artifacts or missing markers.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import sys


def parse_expect(spec: str) -> tuple[str, list[str]]:
    if ':' not in spec:
        return spec, []
    name, markers = spec.split(':', 1)
    return name, [m for m in markers.split('|') if m]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--cwd', default='.', help='Run directory containing expected artifacts')
    ap.add_argument('--expect', action='append', required=True, help='Expected file, optionally file:marker|marker2')
    ap.add_argument('--min-bytes', type=int, default=1)
    ap.add_argument('--match-reported', action='append', default=[], help='file=bytes assertion from DONE line')
    args = ap.parse_args()

    root = Path(args.cwd).resolve()
    failures: list[str] = []
    reported: dict[str, int] = {}
    for item in args.match_reported:
        if '=' not in item:
            failures.append(f'bad --match-reported format: {item}')
            continue
        k, v = item.split('=', 1)
        try:
            reported[k] = int(v)
        except ValueError:
            failures.append(f'bad reported byte count: {item}')

    for spec in args.expect:
        name, markers = parse_expect(spec)
        path = (root / name).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            failures.append(f'path escapes cwd: {name}')
            continue
        if not path.exists():
            failures.append(f'missing: {name}')
            continue
        size = path.stat().st_size
        if size < args.min_bytes:
            failures.append(f'too small: {name} ({size} bytes < {args.min_bytes})')
        if name in reported and reported[name] != size:
            failures.append(f'byte mismatch: {name} reported {reported[name]} actual {size}')
        if markers:
            text = path.read_text(errors='ignore')
            for marker in markers:
                if marker not in text:
                    failures.append(f'missing marker in {name}: {marker!r}')
        print(f'OK {name} {size}')

    if failures:
        print('ARTIFACT_GATE_FAILED', file=sys.stderr)
        for f in failures:
            print(f'- {f}', file=sys.stderr)
        return 2
    print('ARTIFACT_GATE_OK')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
