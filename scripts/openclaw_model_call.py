#!/usr/bin/env python3
"""CLI wrapper for the shared background OpenClaw model lane.

Usage:
  python3 scripts/openclaw_model_call.py --timeout 330 -- openclaw agent ...
"""
from __future__ import annotations

import argparse
import sys

from gateway_model_lane import run_openclaw_model_call


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=int, default=330)
    parser.add_argument("--wait-seconds", type=int, default=None)
    parser.add_argument("cmd", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    cmd = list(args.cmd)
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        parser.error("missing command after --")
    completed = run_openclaw_model_call(cmd, timeout=args.timeout, wait_seconds=args.wait_seconds)
    if completed.stdout:
        sys.stdout.write(completed.stdout)
    if completed.stderr:
        sys.stderr.write(completed.stderr)
    return int(completed.returncode or 0)


if __name__ == "__main__":
    raise SystemExit(main())
