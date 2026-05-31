#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def check(name: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(name)
    print(f"ok {name}")


def load_watcher(tmp_root: Path):
    os.environ["OPENCLAW_MAILBOX_ROOT"] = str(tmp_root)
    os.environ["OPENCLAW_MAIN_ARK_FINAL_FALLBACK_RETRY_DELAY_SECONDS"] = "1"
    os.environ["OPENCLAW_MAIN_ARK_FINAL_FALLBACK_MAX_RETRIES_PER_ITEM"] = "2"
    spec = importlib.util.spec_from_file_location("openclaw_main_mailbox_watch_smoke", ROOT / "openclaw-main-mailbox-watch.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load openclaw-main-mailbox-watch.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="openclaw-main-ark-final-fallback-") as raw_tmp:
        tmp_root = Path(raw_tmp)
        watcher = load_watcher(tmp_root)
        state: dict = {}

        first = watcher.register_main_no_tool_fallback(
            state,
            "42",
            "ark_fallback_all_models_failed",
            detail="first all-lane cooldown",
            model="minimax-m2.7,deepseek-v4-pro,glm-5.1,kimi-k2.6",
        )
        check("initial fallback item is queued", first.get("status") == "queued")
        check("initial retry count starts at zero", first.get("retry_count") == 0)
        check("initial queue is active", state.get("main_local_no_tool_fallback_active") is True)

        first["retry_at_epoch"] = int(time.time()) - 1
        check("due retry has no wait", watcher.main_no_tool_fallback_retry_wait_seconds(state, "42") == 0)
        retry_one = watcher.mark_main_no_tool_fallback_retrying(state, "42")
        check("first retry marks item retrying", retry_one.get("status") == "retrying")
        check("first retry increments count", retry_one.get("retry_count") == 1)

        second_queue = watcher.register_main_no_tool_fallback(
            state,
            "42",
            "ark_fallback_all_models_failed",
            detail="second all-lane cooldown",
        )
        check("retry count is preserved across requeue", second_queue.get("retry_count") == 1)
        check("requeued item remains active before budget is exhausted", state.get("main_local_no_tool_fallback_active") is True)

        second_queue["retry_at_epoch"] = int(time.time()) - 1
        retry_two = watcher.mark_main_no_tool_fallback_retrying(state, "42")
        check("second retry increments count to budget", retry_two.get("retry_count") == 2)

        exhausted = watcher.register_main_no_tool_fallback(
            state,
            "42",
            "ark_fallback_all_models_failed",
            detail="third all-lane cooldown should stop",
        )
        check("max retry budget marks item exhausted", exhausted.get("status") == "retry_exhausted")
        check("exhausted item keeps final retry count", exhausted.get("retry_count") == 2)
        check("exhausted queue is no longer active", state.get("main_local_no_tool_fallback_active") is False)
        check("exhausted queue has no next retry epoch", "main_local_no_tool_fallback_next_retry_epoch" not in state)

        budget_record = watcher.main_no_tool_fallback_retry_budget_exhausted(state, "42")
        check("budget gate returns exhausted record", budget_record.get("status") == "retry_exhausted")

        other = watcher.register_main_no_tool_fallback(
            state,
            "43",
            "ark_fallback_all_models_failed",
            detail="separate seq should remain active",
        )
        check("separate queued item remains active beside exhausted item", other.get("status") == "queued")
        check("queue active reflects non-terminal items", state.get("main_local_no_tool_fallback_active") is True)
        check("next retry epoch ignores exhausted item but keeps active item", state.get("main_local_no_tool_fallback_next_retry_epoch") == other.get("retry_at_epoch"))

        print(
            json.dumps(
                {
                    "ok": True,
                    "tmp_root": str(tmp_root),
                    "final_status": exhausted.get("status"),
                    "retry_count": exhausted.get("retry_count"),
                    "max_retries": exhausted.get("max_retries"),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
