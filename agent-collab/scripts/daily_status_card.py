#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


WORKSPACE = Path("/home/lcxhh/.openclaw/workspace")
MARKET_PHASES = ("morning", "midday", "close", "night")
MARKET_LABELS = {
    "morning": "晨报",
    "midday": "午报",
    "close": "收盘报",
    "night": "晚报",
}
ACTION_LABELS = {
    "none": "无",
    "auto_retry": "等待自动重试",
    "manual_review": "人工复核",
    "enqueue_retry": "后台补跑",
    "fix_secrets": "修复密钥环境后后台补跑",
    "fix_worker_env": "修复执行链密钥加载后后台补跑",
    "repair_analysis": "补全分析内容后重跑质量门槛",
    "rerun_publish_notify": "从发布/通知阶段补跑",
}
STAGE_ORDER = {
    "collect": 10,
    "validate": 15,
    "auxiliary_checks": 18,
    "classify": 20,
    "analyze": 25,
    "digest": 30,
    "quality_check": 40,
    "render": 50,
    "publish": 60,
    "notify": 70,
}


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"_read_error": str(exc), "_path": str(path)}


def file_mtime_iso(path: Path | None) -> str | None:
    if not path or not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).astimezone().isoformat(
        timespec="seconds"
    )


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def text_from_values(*values: Any) -> str:
    parts: list[str] = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, (dict, list)):
            rendered = json.dumps(value, ensure_ascii=False)
        else:
            rendered = str(value)
        if rendered and rendered != "None":
            parts.append(rendered)
    return " | ".join(parts)


def meaningful_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        parts: list[str] = []
        for key, child in value.items():
            if str(key).startswith("prompt_"):
                continue
            text = meaningful_text(child)
            if text:
                parts.append(text)
        return "\n".join(parts).strip()
    if isinstance(value, list):
        return "\n".join(filter(None, (meaningful_text(child) for child in value))).strip()
    return str(value).strip()


def find_articles_with_analysis(value: Any) -> list[dict[str, Any]]:
    articles: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if "title" in value and "analysis" in value and (
            "url" in value or "paragraphs" in value or "page_no" in value
        ):
            articles.append(value)
        for child in value.values():
            articles.extend(find_articles_with_analysis(child))
    elif isinstance(value, list):
        for child in value:
            articles.extend(find_articles_with_analysis(child))
    return articles


def markdown_empty_analysis_sections(path: Path) -> int | None:
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    empty = 0
    for match in re.finditer(r"(?m)^####\s*解析\s*$", text):
        start = match.end()
        next_heading = re.search(r"(?m)^#{1,4}\s+", text[start:])
        end = start + next_heading.start() if next_heading else len(text)
        if not text[start:end].strip():
            empty += 1
    return empty


def secret_file_has_key(path: Path, key: str) -> bool | None:
    if not path.exists():
        return None
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() == key and bool(value.strip().strip('"').strip("'")):
            return True
    return False


def people_daily_completeness(day_dir: Path, target_date: str, manifest: dict[str, Any]) -> dict[str, Any]:
    markdown_path = day_dir / f"{target_date.replace('-', '')}_people_daily_deep_read.md"
    articles = find_articles_with_analysis(manifest)
    empty_articles = [
        article
        for article in articles
        if not meaningful_text(article.get("analysis"))
    ]
    empty_sections = markdown_empty_analysis_sections(markdown_path)
    ready = bool(articles) and not empty_articles and empty_sections in (0, None)
    return {
        "ready": ready,
        "article_like_items": len(articles),
        "empty_analysis_items": len(empty_articles),
        "empty_markdown_analysis_sections": empty_sections,
    }


def checkpoint_files(checkpoints_dir: Path) -> list[Path]:
    if not checkpoints_dir.exists():
        return []
    return sorted(checkpoints_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)


def load_checkpoints(checkpoints_dir: Path) -> list[dict[str, Any]]:
    checkpoints: list[dict[str, Any]] = []
    for path in checkpoint_files(checkpoints_dir):
        payload = read_json(path) or {}
        payload["_path"] = str(path)
        payload["_mtime"] = file_mtime_iso(path)
        checkpoints.append(payload)
    return checkpoints


def latest_checkpoint(checkpoints: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not checkpoints:
        return None
    return max(
        checkpoints,
        key=lambda item: (
            STAGE_ORDER.get(str(item.get("stage") or ""), -1),
            str(item.get("written_at") or item.get("_mtime") or ""),
        ),
    )


def latest_failed_checkpoint(checkpoints: list[dict[str, Any]]) -> dict[str, Any] | None:
    failed = [
        cp
        for cp in checkpoints
        if str(cp.get("status") or "").lower() in {"failed", "blocked", "error"}
        or cp.get("error")
        or cp.get("exception")
        or cp.get("failure_type")
    ]
    if not failed:
        return None
    return max(
        failed,
        key=lambda item: (
            STAGE_ORDER.get(str(item.get("stage") or ""), -1),
            str(item.get("written_at") or item.get("_mtime") or ""),
        ),
    )


def find_task(workspace: Path, prefixes: list[str]) -> Path | None:
    tasks_dir = workspace / "tasks"
    candidates: list[Path] = []
    for prefix in prefixes:
        candidates.extend(tasks_dir.glob(f"{prefix}*/task.json"))
    candidates = [path for path in candidates if path.exists()]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def classify_blocker(text: str, fallback: str | None = None) -> str | None:
    lowered = text.lower()
    if "empty_report_blocked" in lowered or "empty" in lowered or "采集结果为空" in text:
        return "empty_report_blocked"
    if "volcano_engine_api_key" in lowered or "api_key" in lowered or "missing" in lowered:
        return "missing_secret"
    if "digest" in lowered:
        return "digest_failed"
    if "publish" in lowered or "notion" in lowered or "发布" in text:
        return "publish_failed"
    if "notify" in lowered or "telegram" in lowered:
        return "notify_failed"
    if "analysis" in lowered or "analy" in lowered or "分析" in text:
        return "analysis_failed"
    if "gateway" in lowered or "event loop" in lowered:
        return "gateway_hot"
    return fallback


def concise_summary(text: str, blocker: str | None = None) -> str | None:
    if not text:
        return None
    if "VOLCANO_ENGINE_API_KEY" in text:
        return "缺少 VOLCANO_ENGINE_API_KEY，摘要模型调用未能开始。"
    if "市场简讯采集结果为空" in text:
        return "市场简讯采集结果为空；空占位稿未发布。"
    if "People's Daily analysis failed for 2 article(s)" in text:
        return "人民日报深读仍有 2 篇分析结果未满足发布条件。"
    if blocker == "gateway_hot":
        return "Gateway 当时过载或超时，已避免继续压 Telegram 热路径。"
    return text[:180]


def base_item(key: str, label: str) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "state": "unknown",
        "stage": "unknown",
        "formal": False,
        "notion_url": None,
        "telegram_message_id": None,
        "blocker_kind": None,
        "blocker_summary": None,
        "next_action": "none",
        "task_path": None,
        "manifest_path": None,
        "latest_checkpoint_path": None,
        "latest_checkpoint_mtime": None,
        "artifact_paths": [],
        "confidence": "low",
    }


def derive_market_item(workspace: Path, target_date: str, phase: str) -> dict[str, Any]:
    slug_date = target_date.replace("-", "")
    run_slug = f"{slug_date}_{phase}"
    item = base_item(f"market:{phase}", MARKET_LABELS[phase])
    day_dir = workspace / "market-immersion" / "daily" / target_date
    manifest_path = day_dir / f"{run_slug}.manifest.json"
    manifest = read_json(manifest_path) or {}
    item["manifest_path"] = str(manifest_path) if manifest_path.exists() else None

    task_path = find_task(workspace, [f"market_immersion_{phase}-{run_slug}"])
    task = read_json(task_path) if task_path else None
    item["task_path"] = str(task_path) if task_path else None

    checkpoints_dir = Path(
        manifest.get("checkpoints_dir")
        or day_dir / "checkpoints" / run_slug
    )
    checkpoints = load_checkpoints(checkpoints_dir)
    latest = latest_checkpoint(checkpoints)
    failed = latest_failed_checkpoint(checkpoints)
    if latest:
        item["latest_checkpoint_path"] = latest.get("_path")
        item["latest_checkpoint_mtime"] = latest.get("_mtime")

    checkpoint = str(manifest.get("checkpoint") or "")
    validation = manifest.get("validation") or {}
    delivery = manifest.get("delivery") or {}
    publish_cp = next((cp for cp in checkpoints if cp.get("stage") == "publish"), {})
    notify_cp = next((cp for cp in checkpoints if cp.get("stage") == "notify"), {})
    digest_cp = next((cp for cp in checkpoints if cp.get("stage") == "digest"), {})

    item["artifact_paths"] = [str(day_dir / f"{run_slug}.md")]
    item["notion_url"] = (
        manifest.get("notion_url")
        or delivery.get("notion_url")
        or publish_cp.get("url")
        or notify_cp.get("notion_url")
    )
    item["telegram_message_id"] = delivery.get("message_id") or notify_cp.get("message_id")

    task_status = str((task or {}).get("status") or "").lower()
    task_kind = str((task or {}).get("kind") or "")
    task_metadata = (task or {}).get("metadata") or {}
    task_events = as_list((task or {}).get("events"))
    task_failed = bool(
        (task or {}).get("failed_at")
        or (task or {}).get("error_kind")
        or any((event or {}).get("kind") == "failed" for event in task_events if isinstance(event, dict))
    )
    task_error_text = text_from_values(
        (task or {}).get("error_kind"),
        (task or {}).get("error_summary"),
        ((task_metadata.get("failure_diagnosis") or {}) if isinstance(task_metadata, dict) else {}).get("root_cause"),
    )

    manifest_text = text_from_values(
        checkpoint,
        validation.get("reason"),
        delivery.get("message"),
        digest_cp.get("error"),
        digest_cp.get("exception"),
        failed.get("error") if failed else None,
        failed.get("reason") if failed else None,
    )

    if checkpoint == "empty_report_blocked" or validation.get("empty_report"):
        item["notion_url"] = None
        item.update(
            {
                "state": "failed_blocked",
                "stage": "collect",
                "formal": False,
                "blocker_kind": "empty_report_blocked",
                "blocker_summary": concise_summary(
                    validation.get("reason") or "市场简讯采集结果为空",
                    "empty_report_blocked",
                ),
                "next_action": "enqueue_retry",
            }
        )
        item["confidence"] = "medium" if publish_cp.get("status") == "done" else "high"
        return item

    publish_done = str(publish_cp.get("status") or "").lower() == "done"
    notify_done = str(notify_cp.get("status") or "").lower() == "done"
    notify_formal = bool(notify_cp.get("formal", True))
    manifest_done = checkpoint == "notify_done"
    if manifest_done and publish_done and notify_done and notify_formal:
        item.update(
            {
                "state": "succeeded",
                "stage": "notify",
                "formal": True,
                "next_action": "none",
                "confidence": "high",
            }
        )
        return item

    if checkpoint.endswith("_failed") or failed or task_failed:
        stage = checkpoint.removesuffix("_failed") if checkpoint.endswith("_failed") else None
        stage = stage or str((failed or {}).get("stage") or "unknown")
        if stage == "unknown" and isinstance(task_metadata, dict):
            stage = str(task_metadata.get("last_stage") or "unknown")
        combined_text = text_from_values(manifest_text, task_error_text)
        blocker = classify_blocker(combined_text, f"{stage}_failed" if stage else None)
        summary = concise_summary(combined_text, blocker)
        next_action = "fix_secrets" if blocker == "missing_secret" else "enqueue_retry"
        if blocker == "missing_secret" and phase == "night":
            secret_path = workspace.parent / "secrets" / "volcengine.env"
            if secret_file_has_key(secret_path, "VOLCANO_ENGINE_API_KEY"):
                blocker = "worker_env_missing_secret"
                summary = "密钥文件存在，但后台 worker/direct-provider 执行链未加载到 VOLCANO_ENGINE_API_KEY。"
                next_action = "fix_worker_env"
        item.update(
            {
                "state": "failed_blocked",
                "stage": stage,
                "formal": False,
                "blocker_kind": blocker,
                "blocker_summary": summary,
                "next_action": next_action,
            }
        )
        item["confidence"] = "high"
        return item

    if task_status in {"running", "queued", "pending"}:
        item.update({"state": "running", "stage": task_kind or "unknown", "next_action": "none"})
        item["confidence"] = "medium"
        return item
    if "retry" in task_status:
        item.update({"state": "retry_scheduled", "next_action": "auto_retry"})
        item["confidence"] = "medium"
        return item

    if manifest_path.exists() or checkpoints:
        item["state"] = "running" if latest else "unknown"
        item["stage"] = str((latest or {}).get("stage") or "unknown")
        item["next_action"] = "none" if item["state"] == "running" else "manual_review"
        item["confidence"] = "low"
    else:
        item["state"] = "not_started"
        item["next_action"] = "none"
        item["confidence"] = "medium"
    return item


def derive_people_daily_item(workspace: Path, target_date: str) -> dict[str, Any]:
    slug_date = target_date.replace("-", "")
    item = base_item("people_daily", "人民日报深读")
    day_dir = workspace / "people-daily-deep-read" / target_date
    manifest_path = day_dir / "manifest.json"
    manifest = read_json(manifest_path) or {}
    item["manifest_path"] = str(manifest_path) if manifest_path.exists() else None
    item["artifact_paths"] = [
        str(day_dir / f"{slug_date}_people_daily_deep_read.md"),
        str(day_dir / f"{slug_date}_people_daily_deep_read.html"),
    ]

    task_path = find_task(workspace, [f"people_daily_deep_read-{target_date}"])
    task = read_json(task_path) if task_path else None
    item["task_path"] = str(task_path) if task_path else None

    checkpoints = load_checkpoints(day_dir / "checkpoints")
    latest = latest_checkpoint(checkpoints)
    failed = latest_failed_checkpoint(checkpoints)
    if latest:
        item["latest_checkpoint_path"] = latest.get("_path")
        item["latest_checkpoint_mtime"] = latest.get("_mtime")

    publish_cp = next((cp for cp in checkpoints if cp.get("stage") == "publish"), {})
    notify_cp = next((cp for cp in checkpoints if cp.get("stage") == "notify"), {})
    analyze_cp = next((cp for cp in checkpoints if cp.get("stage") == "analyze"), {})

    item["notion_url"] = publish_cp.get("url") or notify_cp.get("notion_url")
    item["telegram_message_id"] = notify_cp.get("message_id")

    publish_status = str(publish_cp.get("status") or "").lower()
    notify_status = str(notify_cp.get("status") or "").lower()
    analyze_status = str(analyze_cp.get("status") or "").lower()
    task_status = str((task or {}).get("status") or "").lower()
    task_metadata = (task or {}).get("metadata") or {}
    task_events = as_list((task or {}).get("events"))
    task_failed = bool(
        (task or {}).get("failed_at")
        or (task or {}).get("error_kind")
        or any((event or {}).get("kind") == "failed" for event in task_events if isinstance(event, dict))
    )
    task_error_text = text_from_values(
        (task or {}).get("error_kind"),
        (task or {}).get("error_summary"),
        ((task_metadata.get("failure_diagnosis") or {}) if isinstance(task_metadata, dict) else {}).get("root_cause"),
    )

    completeness = people_daily_completeness(day_dir, target_date, manifest)
    if manifest_path.exists() and not completeness["ready"]:
        item.update(
            {
                "state": "failed_blocked",
                "stage": "quality_check",
                "formal": False,
                "blocker_kind": "analysis_incomplete",
                "blocker_summary": (
                    "质量门槛误判："
                    f"{completeness['empty_analysis_items']} 个分析项为空，"
                    f"{completeness['empty_markdown_analysis_sections']} 个 Markdown 解析段为空。"
                ),
                "next_action": "repair_analysis",
                "confidence": "high",
            }
        )
        return item

    if publish_status == "done" and notify_status == "done" and notify_cp.get("formal", True):
        item.update(
            {
                "state": "succeeded",
                "stage": "notify",
                "formal": True,
                "next_action": "none",
                "confidence": "high",
            }
        )
        return item

    if analyze_status in {"failed", "blocked"}:
        summary = text_from_values(analyze_cp.get("error"), analyze_cp.get("reason"))
        item.update(
            {
                "state": "failed_blocked",
                "stage": "analyze",
                "formal": False,
                "blocker_kind": "analysis_failed",
                "blocker_summary": concise_summary(summary, "analysis_failed"),
                "next_action": "manual_review",
                "confidence": "high",
            }
        )
        return item

    if publish_status in {"failed", "blocked"} or notify_status in {"failed", "blocked"} or failed or task_failed:
        source = publish_cp if publish_status in {"failed", "blocked"} else notify_cp or failed or {}
        summary = text_from_values(
            source.get("error"),
            source.get("reason"),
            source.get("exception"),
            task_error_text,
        )
        blocker = classify_blocker(summary, "publish_failed" if source is publish_cp else "notify_failed")
        item.update(
            {
                "state": "failed_blocked",
                "stage": str(source.get("stage") or "publish"),
                "formal": False,
                "blocker_kind": blocker,
                "blocker_summary": concise_summary(summary, blocker),
                "next_action": "rerun_publish_notify",
                "confidence": "high",
            }
        )
        return item

    if task_status in {"running", "queued", "pending"}:
        item.update({"state": "running", "stage": "unknown", "next_action": "none"})
        item["confidence"] = "medium"
        return item
    if "retry" in task_status:
        item.update({"state": "retry_scheduled", "next_action": "auto_retry"})
        item["confidence"] = "medium"
        return item

    if manifest_path.exists() or checkpoints:
        item["state"] = "running" if latest else "unknown"
        item["stage"] = str((latest or {}).get("stage") or "unknown")
        item["next_action"] = "none" if item["state"] == "running" else "manual_review"
        item["confidence"] = "low"
    else:
        item["state"] = "not_started"
        item["next_action"] = "none"
        item["confidence"] = "medium"
    return item


def overall_state(items: list[dict[str, Any]]) -> str:
    states = {str(item["state"]) for item in items}
    if states <= {"succeeded"}:
        return "ok"
    if "running" in states or "retry_scheduled" in states:
        return "running"
    if "failed_blocked" in states or "needs_review" in states:
        return "blocked" if "succeeded" not in states else "partial"
    if "unknown" in states:
        return "unknown"
    return "partial"


def build_status(workspace: Path, target_date: str) -> dict[str, Any]:
    items = [derive_market_item(workspace, target_date, phase) for phase in MARKET_PHASES]
    items.append(derive_people_daily_item(workspace, target_date))
    return {
        "schema": "openclaw.daily_status.v0",
        "generated_at": now_iso(),
        "target_date": target_date,
        "overall": overall_state(items),
        "items": items,
    }


def item_text(item: dict[str, Any]) -> str:
    state = item["state"]
    label = item["label"]
    notion = item.get("notion_url")
    message_id = item.get("telegram_message_id")
    blocker = item.get("blocker_kind")
    summary = item.get("blocker_summary")
    next_action = item.get("next_action")

    if state == "succeeded":
        suffix = []
        if notion:
            suffix.append("Notion")
        if message_id:
            suffix.append(f"Telegram #{message_id}")
        return f"- {label}: 已正式发布 OK" + (f" {' / '.join(suffix)}" if suffix else "")
    if state == "failed_blocked":
        summary_text = f"；{summary}" if summary else ""
        action_label = ACTION_LABELS.get(str(next_action), str(next_action))
        action_text = f"；下一步：{action_label}" if next_action and next_action != "none" else ""
        return f"- {label}: 失败/已拦截 {blocker or 'blocked'}{summary_text}{action_text}"
    if state == "running":
        return f"- {label}: 运行中；阶段：{item.get('stage') or 'unknown'}"
    if state == "retry_scheduled":
        return f"- {label}: 已安排重试；阶段：{item.get('stage') or 'unknown'}"
    if state == "not_started":
        return f"- {label}: 未开始"
    return f"- {label}: 状态不明；需要人工复核"


def telegram_text(status: dict[str, Any]) -> str:
    lines = [f"{status['target_date']} 自动简报状态（只读）"]
    lines.extend(item_text(item) for item in status["items"])
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only OpenClaw daily status card.")
    parser.add_argument("--date", required=True, help="Target date in YYYY-MM-DD.")
    parser.add_argument("--workspace", default=str(WORKSPACE), help="OpenClaw workspace root.")
    parser.add_argument("--json", action="store_true", help="Print machine JSON.")
    parser.add_argument("--telegram-text", action="store_true", help="Print Telegram text card.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    workspace = Path(args.workspace)
    status = build_status(workspace, args.date)
    if args.telegram_text:
        print(telegram_text(status))
    if args.json or not args.telegram_text:
        print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
