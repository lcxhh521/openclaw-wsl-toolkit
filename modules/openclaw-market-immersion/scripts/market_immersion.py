#!/usr/bin/env python3
"""Market information immersion runner for OpenClaw.

Phase 1 design: collect broad market information and preserve raw source output.
This script intentionally avoids strong filtering and trading advice.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import email.utils
import hashlib
import html
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
import socket
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WORKSPACE_ROOT / "scripts"))
import background_tasks  # noqa: E402
from direct_provider_openclaw_compat import maybe_run_openclaw_agent_direct, run_openclaw_model_call  # noqa: E402


DEFAULT_SOURCE_ALTERNATIVES: dict[str, list[str]] = {
    "东方财富财经资讯": ["东方财富7x24全球直播", "东方财富焦点快讯", "新浪财经滚动"],
    "东方财富焦点快讯": ["东方财富7x24全球直播", "财联社电报", "华尔街见闻7x24"],
    "东方财富7x24全球直播": ["东方财富焦点快讯", "财联社电报", "华尔街见闻7x24"],
    "金十数据快讯": ["华尔街见闻7x24", "新浪财经7x24", "财联社电报"],
    "财联社电报": ["东方财富7x24全球直播", "同花顺实时快讯-全部", "新浪财经7x24"],
    "同花顺实时快讯": ["东方财富上市公司快讯", "东方财富7x24全球直播", "新浪财经滚动"],
    "新浪财经7x24": ["华尔街见闻7x24", "金十数据快讯", "东方财富7x24全球直播"],
    "华尔街见闻7x24": ["金十数据快讯", "新浪财经7x24", "财联社电报"],
    "新浪财经滚动": ["东方财富财经资讯", "同花顺实时快讯-全部"],
}

SOURCE_INTERFACE_VERIFICATION_LATEST = (
    WORKSPACE_ROOT / "market-immersion" / "source-interface-verification" / "latest.json"
)
SOURCE_REGISTRY_PATH = Path(__file__).resolve().parents[1] / "config" / "source_registry.json"



def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def source_registry_primary_by_name(path: Path | None = None) -> dict[str, str]:
    registry_path = path or SOURCE_REGISTRY_PATH
    try:
        registry = load_json(registry_path)
    except Exception:
        return {}
    mapping: dict[str, str] = {}
    for source in registry.get("sources") or []:
        name = str(source.get("name") or "").strip()
        primary = str(source.get("primary") or "").strip()
        if name and primary:
            mapping[name] = primary
    return mapping

def load_latest_source_interface_verification(path: Path | None = None) -> dict[str, Any]:
    latest_path = path or SOURCE_INTERFACE_VERIFICATION_LATEST
    primary_by_name = source_registry_primary_by_name()
    try:
        latest = load_json(latest_path)
        report_path = Path(str(latest.get("report") or "")).expanduser()
        if not report_path.is_absolute():
            report_path = latest_path.parent / report_path
        if not report_path.exists():
            return {"available": False, "reason": "report_missing", "latest_path": str(latest_path)}
        report = load_json(report_path)
        ready: dict[str, list[str]] = {}
        statuses: dict[str, str] = {}
        for row in report.get("results") or []:
            name = str(row.get("source_name") or row.get("source_id") or "")
            if not name:
                continue
            primary = primary_by_name.get(name)
            candidates = [str(item) for item in row.get("backup_ready_candidates") or []]
            # A verified primary endpoint is not a backup/failover interface.
            ready[name] = [item for item in candidates if item and item != primary]
            statuses[name] = str(row.get("status") or "")
        return {
            "available": True,
            "latest_path": str(latest_path),
            "report": str(report_path),
            "summary": report.get("summary") or latest.get("summary") or {},
            "backup_ready_by_source": ready,
            "status_by_source": statuses,
            "policy": "primary_first_failover_only_failback_when_primary_recovers",
        }
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": str(exc), "latest_path": str(latest_path)}


def load_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def safe_name(text: str) -> str:
    keep = []
    for ch in text:
        if ch.isalnum() or ch in ("-", "_"):
            keep.append(ch)
        else:
            keep.append("_")
    name = "".join(keep).strip("._")
    while "__" in name:
        name = name.replace("__", "_")
    return name[:80] or "query"


def normalize_ws(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def now_local() -> dt.datetime:
    return dt.datetime.now().astimezone()


def parse_iso(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value)
    except ValueError:
        return None


PHASE_WINDOWS: dict[str, tuple[int, dt.time, dt.time]] = {
    # phase: (start days back from report date, start time, scheduled end time)
    "morning": (1, dt.time(22, 10), dt.time(9, 5)),
    "midday": (0, dt.time(9, 5), dt.time(12, 15)),
    "close": (0, dt.time(12, 15), dt.time(15, 20)),
    "night": (0, dt.time(15, 20), dt.time(22, 10)),
}


def scheduled_phase_window(phase: str, now: dt.datetime) -> tuple[dt.datetime, dt.datetime] | None:
    spec = PHASE_WINDOWS.get(phase)
    if not spec:
        return None
    start_days_back, start_time, end_time = spec
    report_date = now.date()
    # If a same-day phase is manually retried before its start time, it usually refers
    # to the previous report date rather than a future window. Morning starts the
    # previous evening, so pre-09:05 same-day runs still belong to today's morning window.
    if start_days_back == 0 and now.time() < start_time:
        report_date = report_date - dt.timedelta(days=1)
    window_end = dt.datetime.combine(report_date, end_time, tzinfo=now.tzinfo)
    window_start = dt.datetime.combine(report_date - dt.timedelta(days=start_days_back), start_time, tzinfo=now.tzinfo)
    return window_start, window_end


def compute_window(
    *,
    phase: str,
    output_root: Path,
    end: dt.datetime,
) -> tuple[dt.datetime | None, dt.datetime | None, str]:
    if phase == "smoke":
        return None, None, "smoke"
    scheduled = scheduled_phase_window(phase, end)
    if scheduled:
        return scheduled[0], scheduled[1], "scheduled_phase_window"
    return None, end, "now"


def format_window_for_query(start: dt.datetime | None, end: dt.datetime | None) -> str:
    if not start or not end:
        return ""
    return (
        f"时间范围：{start.strftime('%Y-%m-%d %H:%M')} 至 "
        f"{end.strftime('%Y-%m-%d %H:%M')}。"
        "请尽量只收集这个时间段内出现、发布、更新或发酵的信息；"
        "不要把摘要当成原文替代。"
    )


def run_skill(
    *,
    workspace: Path,
    venv_python: Path,
    skill: str,
    query: str,
    output_dir: Path,
    env: dict[str, str],
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    script_by_skill = {
        "mx-search": workspace / "skills" / "mx-search" / "mx_search.py",
        "mx-data": workspace / "skills" / "mx-data" / "mx_data.py",
        "mx-xuangu": workspace / "skills" / "mx-xuangu" / "mx_xuangu.py",
        "mx-zixuan": workspace / "skills" / "mx-zixuan" / "mx_zixuan.py",
        "mx-moni": workspace / "skills" / "mx-moni" / "mx_moni.py",
    }
    script = script_by_skill.get(skill)
    if not script:
        raise ValueError(f"Unsupported skill: {skill}")
    if not script.exists():
        raise FileNotFoundError(f"Skill script not found: {script}")

    cmd = [str(venv_python), str(script), query, str(output_dir)]
    return subprocess.run(
        cmd,
        cwd=str(workspace),
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def run_process_group(
    cmd: list[str],
    *,
    timeout: int,
    text: bool = True,
    capture_output: bool = True,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run a command in its own process group and kill the whole group on timeout."""
    direct_completed = maybe_run_openclaw_agent_direct(cmd, timeout=timeout)
    if direct_completed is not None:
        if check and direct_completed.returncode != 0:
            raise subprocess.CalledProcessError(direct_completed.returncode, cmd, output=direct_completed.stdout, stderr=direct_completed.stderr)
        return direct_completed
    process = subprocess.Popen(
        cmd,
        text=text,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE if capture_output else None,
        stderr=subprocess.PIPE if capture_output else None,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
        completed = subprocess.CompletedProcess(cmd, process.returncode, stdout, stderr)
        if check and completed.returncode != 0:
            raise subprocess.CalledProcessError(completed.returncode, cmd, output=stdout, stderr=stderr)
        return completed
    except subprocess.TimeoutExpired as exc:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=10)
        except Exception:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except Exception:
                pass
            stdout, stderr = process.communicate(timeout=5)
        raise subprocess.TimeoutExpired(cmd, timeout, output=stdout, stderr=stderr) from exc


def looks_like_gateway_timeout(text: str) -> bool:
    lowered = text.lower()
    return "gateway timeout" in lowered or "failed to resolve secrets from the active gateway snapshot" in lowered


def classify_failure(error_text: str, *, default: str = "unknown_failure") -> str:
    lowered = (error_text or "").lower()
    if not lowered:
        return default
    if "timeoutexpired" in lowered or "timed out after" in lowered:
        return "summary_timeout"
    if "gateway_model_lane_busy" in lowered:
        return "gateway_model_lane_busy"
    if "gateway_unreachable_before_model_call" in lowered or looks_like_gateway_timeout(error_text):
        return "gateway_unavailable"
    if "notion" in lowered and ("validation" in lowered or "invalid" in lowered or "body failed validation" in lowered):
        return "notion_validation_error"
    if "timed out" in lowered or "timeout" in lowered:
        return "provider_timeout"
    return default


def sanitize_user_reason(reason: str) -> str:
    failure_type = classify_failure(reason)
    messages = {
        "summary_timeout": "收盘报摘要生成超时，已停止本轮刷屏式重试，稍后由 supervisor 自动补跑。",
        "gateway_unavailable": "收盘报暂时无法连接本地 OpenClaw Gateway，已停止刷屏式重试，稍后自动补跑。",
        "gateway_model_lane_busy": "后台模型通道正忙，已延后本轮摘要生成，稍后自动补跑。",
        "notion_validation_error": "收盘报 Notion 内容校验失败，已停止自动重试，等待修正。",
        "provider_timeout": "收盘报数据源或模型调用超时，已停止本轮刷屏式重试，稍后自动补跑。",
    }
    if failure_type in messages:
        return messages[failure_type]
    cleaned_lines: list[str] = []
    for line in str(reason or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("[plugins]") or "installed bundled runtime deps" in stripped:
            continue
        if len(stripped) > 260:
            stripped = stripped[:260] + "…"
        cleaned_lines.append(stripped)
        if len(cleaned_lines) >= 2:
            break
    return "；".join(cleaned_lines) or "每日快讯简报失败，已记录技术日志。"


def failure_notification_status(reason: str) -> str:
    reason_text = str(reason or "")
    if classify_failure(reason_text) == "gateway_model_lane_busy" or "后台模型通道" in reason_text:
        return "delayed"
    return "failed"


def transient_retry_sleep(error_text: str, attempt: int) -> None:
    if looks_like_gateway_timeout(error_text):
        time.sleep(min(180, 45 * attempt))
    else:
        time.sleep(5)


def fetch_json_url(url: str, timeout: int = 30) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 OpenClaw Market Immersion",
            "Accept": "application/json,text/plain,*/*",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def source_family(source: Any) -> str:
    name = str(source or "").strip()
    if name.startswith("同花顺实时快讯"):
        return "同花顺实时快讯"
    return name


def source_alternatives_for(source: str, feed_config: dict[str, Any]) -> list[str]:
    configured = feed_config.get("source_alternatives") or {}
    candidates = configured.get(source)
    if candidates is None:
        candidates = configured.get(source_family(source))
    if candidates is None:
        candidates = DEFAULT_SOURCE_ALTERNATIVES.get(source)
    if candidates is None:
        candidates = DEFAULT_SOURCE_ALTERNATIVES.get(source_family(source), [])
    return [str(item) for item in candidates or [] if str(item).strip()]


def verified_backup_candidates_for(source: str, verification: dict[str, Any]) -> list[str]:
    if not verification.get("available"):
        return []
    mapping = verification.get("backup_ready_by_source") or {}
    candidates = mapping.get(source) or mapping.get(source_family(source)) or []
    return [str(item) for item in candidates if str(item).strip()]


def build_feed_health_report(
    coverage: list[dict[str, Any]],
    *,
    feed_config: dict[str, Any],
    verification: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Summarize source-level health for operator review.

    This is intentionally diagnostic only.  It must not silently downgrade
    publishing rules; external publication decisions remain gated by the
    normal validation/publish policy and by explicit operator approval.
    """
    sources: list[dict[str, Any]] = []
    failed: list[str] = []
    incomplete: list[str] = []
    ok: list[str] = []
    grouped: dict[str, dict[str, Any]] = {}
    verification = verification or {}

    for item in coverage:
        source = str(item.get("source") or "").strip()
        if not source:
            continue
        has_error = bool(item.get("error"))
        reached = bool(item.get("reached_window_start"))
        count = int(item.get("item_count") or 0)
        status = "ok"
        issue = ""
        if has_error:
            status = "error"
            issue = str(item.get("error") or "")
            failed.append(source)
        elif not reached:
            status = "incomplete_window"
            issue = "oldest item is still newer than window start"
            incomplete.append(source)
        else:
            ok.append(source)

        verified_backups = verified_backup_candidates_for(source, verification)
        record = {
            "source": source,
            "family": source_family(source),
            "status": status,
            "item_count": count,
            "newest_time": item.get("newest_time"),
            "oldest_time": item.get("oldest_time"),
            "reached_window_start": reached,
            "error": item.get("error"),
            "issue": issue,
            "alternatives": source_alternatives_for(source, feed_config),
            "verified_backup_candidates": verified_backups,
            "failover_policy": "primary_first; use verified backup only if primary fails this run; fail back as soon as primary recovers",
            "failover_ready": bool(verified_backups),
            "needs_recovery_check": status != "ok",
        }
        sources.append(record)
        family = record["family"]
        bucket = grouped.setdefault(
            family,
            {"family": family, "sources": [], "failed": 0, "incomplete": 0, "ok": 0, "alternatives": []},
        )
        bucket["sources"].append(source)
        bucket[status if status in {"ok"} else "failed" if status == "error" else "incomplete"] += 1
        for alt in record["alternatives"]:
            if alt not in bucket["alternatives"]:
                bucket["alternatives"].append(alt)

    health_status = "ok"
    if failed:
        health_status = "error"
    elif incomplete:
        health_status = "incomplete"

    return {
        "status": health_status,
        "failed_sources": failed,
        "incomplete_sources": incomplete,
        "ok_sources": ok,
        "sources": sources,
        "groups": list(grouped.values()),
        "operator_action": (
            "check_recovery_then_use_verified_backup_or_request_manual_approval"
            if failed or incomplete
            else "none"
        ),
        "publication_policy": "primary_first; backup_only_on_primary_failure; automatic_failback; no_degraded_publication_without_explicit_approval",
        "verification": {
            "available": bool(verification.get("available")),
            "report": verification.get("report"),
            "summary": verification.get("summary"),
        },
    }


def fetch_text_url(url: str, timeout: int = 30, headers: dict[str, str] | None = None) -> str:
    request_headers = {
        "User-Agent": "Mozilla/5.0 OpenClaw Market Immersion",
        "Accept": "application/rss+xml,application/xml,text/xml,text/plain,*/*",
    }
    if headers:
        request_headers.update(headers)
    request = urllib.request.Request(url, headers=request_headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def parse_rss_datetime(value: Any) -> dt.datetime | None:
    if not value:
        return None
    try:
        parsed = email.utils.parsedate_to_datetime(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=now_local().tzinfo)
        return parsed.astimezone(now_local().tzinfo)
    except (TypeError, ValueError):
        return parse_item_datetime(value, now_local().tzinfo)


def dedupe_key_for_item(*, title: str, content: str, url: str, code: str = "") -> str:
    normalized = " ".join((title or content or url or code).lower().split())
    raw = url.strip() or code.strip() or normalized
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def normalize_for_duplicate(text: Any, limit: int = 120) -> str:
    value = strip_html(text)
    value = re.sub(r"【([^】]{4,100})】", r"\1", value)
    value = re.sub(r"(财联社|金十数据|央视新闻|新华社)?\d{1,2}月\d{1,2}日电[，,]?", "", value)
    value = re.sub(r"据[^，,。]{2,40}(介绍|消息|报道|透露|表示)[，,]?", "", value)
    value = re.sub(r"[^\w\u4e00-\u9fff]+", "", value.lower())
    return value[:limit]


def duplicate_keys_for_item(item: dict[str, Any]) -> list[str]:
    title = str(item.get("title") or "")
    content = str(item.get("content") or "")
    keys: list[str] = []
    bracket_match = re.match(r"\s*【([^】]{4,100})】", content)
    for value in (
        title,
        bracket_match.group(1) if bracket_match else "",
        content,
    ):
        normalized = normalize_for_duplicate(value)
        if len(normalized) >= 12 and normalized not in keys:
            keys.append(normalized)
    for key in content_fingerprint_keys(item):
        if key not in keys:
            keys.append(key)
    return keys


def split_bracketed_title_content(content: Any) -> tuple[str, str]:
    """Sina live items commonly encode title as leading 【title】 and body after it."""
    text = " ".join(str(content or "").split())
    match = re.match(r"^【([^】]{4,100})】\s*(.*)$", text)
    if not match:
        return "", text
    title = match.group(1).strip()
    body = match.group(2).lstrip(" ：:，,。")
    return title, body


def duplicate_ngrams(text: str, *, n: int = 3, limit: int = 360) -> set[str]:
    normalized = normalize_for_duplicate(text, limit=limit)
    if len(normalized) < n:
        return {normalized} if normalized else set()
    return {normalized[i : i + n] for i in range(0, len(normalized) - n + 1)}


def jaccard_similarity(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def number_tokens(text: str) -> set[str]:
    return set(re.findall(r"\d+(?:\.\d+)?%?", str(text or "")))


def _number_token_value(token: str) -> tuple[float, bool] | None:
    text = str(token or "").strip()
    if not text:
        return None
    is_percent = text.endswith("%")
    try:
        return float(text.rstrip("%")), is_percent
    except ValueError:
        return None


def entity_like_terms(text: str) -> set[str]:
    pattern = (
        r"[\u4e00-\u9fffA-Za-z0-9]{2,24}(?:集团|公司|银行|证券|基金|交易所|委员会|管理局|"
        r"部门|组织|机构|口岸|海峡|隧道|机场|铁路|高速|景区|油田|油轮|法案|指数|学校|单位|省|市)"
    )
    return set(re.findall(pattern, str(text or "")))


def market_quote_terms(text: str) -> set[str]:
    value = str(text or "")
    terms: set[str] = set()
    for pattern in (
        r"(?:布伦特|WTI|美|伦敦|纽约|COMEX|LME)?[\u4e00-\u9fffA-Za-z0-9]{0,12}(?:原油|黄金|白银|铜|铝|锌|镍|天然气|期货)",
        r"[\u4e00-\u9fffA-Za-z0-9]{2,20}(?:美股|港股|盘前|盘后)",
        r"[\u4e00-\u9fffA-Za-z0-9]{2,20}(?:科技|集团|公司|芯片|半导体|汽车|银行)",
    ):
        for term in re.findall(pattern, value):
            cleaned = term.strip(" ：:，,。！？；;、")
            if 3 <= len(cleaned) <= 24:
                terms.add(cleaned)
                for marker in ("原油", "黄金", "白银", "天然气", "科技", "芯片", "半导体"):
                    if marker in cleaned:
                        terms.add(cleaned[: cleaned.find(marker) + len(marker)])
    return terms


def content_fingerprint_keys(item: dict[str, Any]) -> list[str]:
    title = str(item.get("title") or "")
    content = str(item.get("content") or "")
    normalized = normalize_for_duplicate(content, limit=500)
    if len(normalized) < 40:
        return []
    numbers = sorted(number_tokens(title + content))[:8]
    entities = sorted(entity_like_terms(title + content))[:6]
    keys: list[str] = []
    if numbers and entities:
        raw = "num_entity:" + "|".join(numbers) + "|" + "|".join(entities)
        keys.append(hashlib.sha1(raw.encode("utf-8")).hexdigest())
    prefix = normalized[:80]
    if len(prefix) >= 40:
        keys.append(hashlib.sha1(("body_prefix:" + prefix).encode("utf-8")).hexdigest())
    return keys


def numbers_compatible(left: set[str], right: set[str]) -> bool:
    if not left or not right:
        return True
    matched = 0
    right_values = [value for value in (_number_token_value(token) for token in right) if value is not None]
    for token in left:
        parsed = _number_token_value(token)
        if parsed is None:
            continue
        left_value, left_percent = parsed
        for right_value, right_percent in right_values:
            if left_percent != right_percent:
                continue
            tolerance = max(0.02, abs(left_value) * 0.003, abs(right_value) * 0.003)
            if abs(left_value - right_value) <= tolerance:
                matched += 1
                break
    return matched > 0 and matched / min(len(left), len(right)) >= 0.5


def content_overlap_duplicate(existing: dict[str, Any], incoming: dict[str, Any]) -> bool:
    existing_title = str(existing.get("title") or "")
    incoming_title = str(incoming.get("title") or "")
    existing_content = str(existing.get("content") or "")
    incoming_content = str(incoming.get("content") or "")
    existing_body = normalize_for_duplicate(existing_content, limit=500)
    incoming_body = normalize_for_duplicate(incoming_content, limit=500)
    if len(existing_body) < 16 or len(incoming_body) < 16:
        return False

    title_similarity = jaccard_similarity(
        duplicate_ngrams(existing_title, n=2, limit=120),
        duplicate_ngrams(incoming_title, n=2, limit=120),
    )
    body_similarity = jaccard_similarity(
        duplicate_ngrams(existing_content, n=3, limit=500),
        duplicate_ngrams(incoming_content, n=3, limit=500),
    )
    number_support = numbers_compatible(
        number_tokens(existing_title + existing_content),
        number_tokens(incoming_title + incoming_content),
    )
    shorter, longer = sorted((existing_body, incoming_body), key=len)
    containment = len(shorter) >= 40 and shorter in longer

    if containment and (title_similarity >= 0.25 or number_support):
        return True
    if body_similarity >= 0.72 and number_support:
        return True
    if body_similarity >= 0.55 and title_similarity >= 0.35 and number_support:
        return True
    if title_similarity >= 0.82 and body_similarity >= 0.32 and number_support:
        return True
    common_quote_terms = market_quote_terms(existing_title + existing_content) & market_quote_terms(incoming_title + incoming_content)
    if common_quote_terms and number_support and body_similarity >= 0.2:
        return True
    return False


def source_priority(source: str) -> int:
    priorities = {
        "财联社": 5,
        "东方财富快讯": 4,
        "东方财富": 4,
        "华尔街见闻": 3,
        "金十数据": 2,
        "新浪财经": 1,
    }
    return priorities.get(source, 0)


def item_richness_score(item: dict[str, Any]) -> int:
    title = " ".join(str(item.get("title") or "").split())
    content = " ".join(str(item.get("content") or "").split())
    entity = " ".join(str(item.get("entity") or "").split())
    quality = item.get("content_quality") or content_quality(title=title, content=content)
    quality_bonus = {"body": 1000, "title_like": 120, "title_only": 0, "missing": -200}.get(str(quality), 0)
    detail_bonus = min(len(re.findall(r"\d+(?:\.\d+)?%?", content)) * 20, 200)
    entity_bonus = (120 if entity else 0) + min(len(entity_like_terms(title + content)) * 15, 180)
    punctuation_bonus = min(content.count("，") + content.count("。") + content.count("；"), 30) * 4
    url_bonus = 80 if item.get("url") else 0
    return (
        len(content)
        + quality_bonus
        + detail_bonus
        + entity_bonus
        + punctuation_bonus
        + url_bonus
        + source_priority(str(item.get("source") or "")) * 10
    )


def merge_duplicate_item(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    sources = list(existing.get("duplicate_sources") or [existing.get("source") or "未知"])
    source = str(incoming.get("source") or "未知")
    if source not in sources:
        sources.append(source)
    existing["duplicate_sources"] = sources
    existing["duplicate_count"] = int(existing.get("duplicate_count") or 1) + 1

    if item_richness_score(incoming) > item_richness_score(existing):
        for key in ("title", "content", "content_quality", "date", "source", "type", "entity", "url", "raw_file", "bucket", "parsed_at", "entry_id"):
            if incoming.get(key):
                existing[key] = incoming[key]
    return existing


def strip_html(text: Any) -> str:
    value = html.unescape(str(text or ""))
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    value = re.sub(r"<[^>]+>", "", value)
    return " ".join(value.split())


def choose_content(*values: Any, title: str = "") -> str:
    candidates = []
    normalized_title = " ".join(str(title or "").split())
    for value in values:
        text = strip_html(value)
        if not text:
            continue
        candidates.append(text)
    if not candidates:
        return normalized_title

    non_title_candidates = [
        text
        for text in candidates
        if text != normalized_title and len(text) > len(normalized_title) + 8
    ]
    if non_title_candidates:
        return max(non_title_candidates, key=len)
    return max(candidates, key=len)


def content_quality(*, title: str, content: str) -> str:
    normalized_title = " ".join(str(title or "").split())
    normalized_content = " ".join(str(content or "").split())
    if not normalized_content:
        return "missing"
    if normalized_content == normalized_title:
        return "title_only"
    if normalized_title and len(normalized_content) <= len(normalized_title) + 8:
        return "title_like"
    return "body"


def cls_query_sign(query: str) -> str:
    sha1_value = hashlib.sha1(query.encode("utf-8")).hexdigest()
    return hashlib.md5(sha1_value.encode("utf-8")).hexdigest()


def classify_time_bucket(
    *,
    value: Any,
    window_start: dt.datetime | None,
    window_end: dt.datetime | None,
) -> tuple[str, dt.datetime | None]:
    parsed = parse_item_datetime(value, window_end.tzinfo if window_end else None)
    if not window_start or not window_end:
        return "unwindowed", parsed
    if not parsed:
        return "undated", None
    if window_start <= parsed <= window_end:
        return "in_window", parsed
    if parsed < window_start:
        return "carryover", parsed
    return "future_or_clock_skew", parsed


def should_keep_feed_item(
    *,
    value: Any,
    window_start: dt.datetime | None,
    window_end: dt.datetime | None,
) -> tuple[bool, bool, str, dt.datetime | None]:
    bucket, parsed = classify_time_bucket(
        value=value,
        window_start=window_start,
        window_end=window_end,
    )
    if bucket == "future_or_clock_skew":
        return False, False, bucket, parsed
    if bucket == "carryover" and window_start and parsed and parsed < window_start:
        return False, True, bucket, parsed
    return True, False, bucket, parsed


def collect_market_feed_entry(
    *,
    config: dict[str, Any],
    output_dir: Path,
    window_start: dt.datetime | None,
    window_end: dt.datetime | None,
) -> dict[str, Any] | None:
    feed_config = config.get("eastmoney_feed") or {}
    if not feed_config.get("enabled", False):
        return None
    output_dir.mkdir(parents=True, exist_ok=True)

    columns = feed_config.get("columns") or []

    page_size = int(feed_config.get("page_size") or 100)
    max_pages = int(feed_config.get("max_pages") or 20)
    timeout = int(feed_config.get("timeout") or 30)
    raw_files: list[str] = []
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    counts = {
        "in_window": 0,
        "carryover": 0,
        "future_or_clock_skew": 0,
        "undated": 0,
        "unwindowed": 0,
    }
    started = time.time()
    messages: list[str] = []
    coverage: list[dict[str, Any]] = []
    source_interface_verification = load_latest_source_interface_verification()

    def begin_coverage(source: str, expected: str) -> dict[str, Any]:
        item = {
            "source": source,
            "expected": expected,
            "item_count": 0,
            "newest_time": None,
            "oldest_time": None,
            "reached_window_start": window_start is None,
            "error": None,
            "_newest_ts": None,
            "_oldest_ts": None,
        }
        coverage.append(item)
        return item

    def note_coverage(item: dict[str, Any], parsed: dt.datetime | None) -> None:
        if not parsed:
            return
        timestamp = parsed.timestamp()
        newest = item.get("_newest_ts")
        oldest = item.get("_oldest_ts")
        if newest is None or timestamp > float(newest):
            item["_newest_ts"] = timestamp
            item["newest_time"] = parsed.isoformat(timespec="seconds")
        if oldest is None or timestamp < float(oldest):
            item["_oldest_ts"] = timestamp
            item["oldest_time"] = parsed.isoformat(timespec="seconds")
        if window_start and parsed <= window_start:
            item["reached_window_start"] = True

    def finish_coverage() -> None:
        for item in coverage:
            item.pop("_newest_ts", None)
            item.pop("_oldest_ts", None)

    def effective_max_pages(value: int) -> int:
        if window_start is None:
            return max(1, min(value, int(feed_config.get("smoke_max_pages") or 1)))
        return value

    def smoke_source_done(item: dict[str, Any]) -> bool:
        if window_start is not None:
            return False
        return int(item.get("item_count") or 0) >= int(feed_config.get("smoke_max_items_per_source") or 15)

    for column in columns:
        column_id = str(column.get("column") or "").strip()
        column_name = str(column.get("name") or column_id).strip()
        if not column_id:
            continue
        source_coverage = begin_coverage(
            column_name,
            "东方财富栏目资讯流按页拉取，直到最旧消息早于本次窗口起点。",
        )
        stop_column = False
        for page in range(1, effective_max_pages(max_pages) + 1):
            params = {
                "client": "web",
                "biz": "web_news_col",
                "column": column_id,
                "pageSize": page_size,
                "page": page,
                "req_trace": str(int(time.time() * 1000)),
            }
            url = "https://np-listapi.eastmoney.com/comm/web/getNewsByColumns?" + urllib.parse.urlencode(params)
            raw_path = output_dir / f"eastmoney_feed_{safe_name(column_name)}_p{page}.json"
            try:
                data = fetch_json_url(url, timeout=timeout)
                raw_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                raw_files.append(str(raw_path))
            except Exception as exc:  # noqa: BLE001
                messages.append(f"{column_name} page {page}: {exc}")
                source_coverage["error"] = str(exc)
                break

            rows = (((data.get("data") or {}).get("list")) or [])
            if not rows:
                break

            for index, row in enumerate(rows, 1):
                if not isinstance(row, dict):
                    continue
                code = str(row.get("code") or row.get("newsId") or row.get("uniqueUrl") or row.get("url") or "")
                title = str(row.get("title") or "").strip()
                content = choose_content(
                    row.get("content"),
                    row.get("summary"),
                    row.get("digest"),
                    title=title,
                )
                date = str(row.get("showTime") or row.get("time") or row.get("date") or "").strip()
                dedupe_key = dedupe_key_for_item(
                    title=title,
                    content=content,
                    url=str(row.get("uniqueUrl") or row.get("url") or ""),
                    code=code,
                )
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                bucket, parsed = classify_time_bucket(
                    value=date,
                    window_start=window_start,
                    window_end=window_end,
                )
                note_coverage(source_coverage, parsed)
                if bucket == "future_or_clock_skew":
                    continue
                if bucket == "carryover" and window_start and parsed and parsed < window_start:
                    stop_column = True
                    continue
                item = {
                    "index": len(items) + 1,
                    "code": code,
                    "title": title,
                    "content": content,
                    "content_quality": content_quality(title=title, content=content),
                    "date": date,
                    "source": row.get("mediaName") or "东方财富",
                    "type": column_name,
                    "entity": "",
                    "url": row.get("uniqueUrl") or row.get("url") or "",
                    "raw_file": str(raw_path),
                    "bucket": bucket,
                    "parsed_at": parsed.isoformat(timespec="seconds") if parsed else None,
                }
                items.append(item)
                source_coverage["item_count"] += 1
                counts[bucket] += 1
                if smoke_source_done(source_coverage):
                    stop_column = True
                    break
            if stop_column:
                break

    for feed in feed_config.get("eastmoney_7x24") or []:
        name = str(feed.get("name") or "东方财富7x24快讯")
        source_coverage = begin_coverage(
            name,
            "东方财富官网快讯接口按 fastColumn/sortEnd 游标拉取，直到最旧消息早于本次窗口起点。",
        )
        column = str(feed.get("column") or feed.get("type") or "102")
        limit = int(feed.get("limit") or 50)
        max_pages_feed = effective_max_pages(int(feed.get("max_pages") or 20))
        api_url = str(
            feed.get("url")
            or "https://np-weblist.eastmoney.com/comm/web/getFastNewsList"
        ).strip()
        sort_end = ""
        stop_feed = False
        for page in range(1, max_pages_feed + 1):
            params = {
                "client": "web",
                "biz": "web_724",
                "fastColumn": column,
                "sortEnd": sort_end,
                "pageSize": limit,
                "req_trace": str(int(time.time() * 1000)),
            }
            url = api_url + ("&" if "?" in api_url else "?") + urllib.parse.urlencode(params)
            raw_path = output_dir / f"feed_eastmoney_fastnews_{safe_name(name)}_{column}_p{page}.json"
            try:
                data = fetch_json_url(url, timeout=timeout)
                raw_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                raw_files.append(str(raw_path))
                rows = (((data.get("data") or {}).get("fastNewsList")) or [])
            except Exception as exc:  # noqa: BLE001
                messages.append(f"{name} page {page}: {exc}")
                source_coverage["error"] = str(exc)
                break
            if not rows:
                break
            next_sort_end = str((((data.get("data") or {}).get("sortEnd")) or "")).strip()
            for row in rows:
                if not isinstance(row, dict):
                    continue
                date_text = str(row.get("showTime") or row.get("showtime") or row.get("ordertime") or "").strip()
                keep, stop, bucket, parsed = should_keep_feed_item(
                    value=date_text,
                    window_start=window_start,
                    window_end=window_end,
                )
                note_coverage(source_coverage, parsed)
                if stop:
                    stop_feed = True
                    continue
                if not keep:
                    continue
                title = str(row.get("title") or "").strip()
                content = choose_content(row.get("summary"), row.get("digest"), row.get("simdigest"), title=title)
                code = str(row.get("code") or row.get("newsid") or row.get("id") or "").strip()
                url_value = str(
                    row.get("url_unique")
                    or row.get("url_w")
                    or row.get("url_m")
                    or (f"https://finance.eastmoney.com/a/{code}.html" if code else "")
                    or ""
                ).strip()
                key = dedupe_key_for_item(
                    title=title,
                    content=content,
                    url=url_value,
                    code=code,
                )
                if key in seen:
                    continue
                seen.add(key)
                item = {
                    "index": len(items) + 1,
                    "code": code,
                    "title": title,
                    "content": content or title,
                    "content_quality": content_quality(title=title, content=content),
                    "date": date_text,
                    "source": "东方财富快讯",
                    "type": name,
                    "entity": "",
                    "url": url_value,
                    "raw_file": str(raw_path),
                    "bucket": bucket,
                    "parsed_at": parsed.isoformat(timespec="seconds") if parsed else None,
                }
                items.append(item)
                source_coverage["item_count"] += 1
                counts[bucket] += 1
                if smoke_source_done(source_coverage):
                    stop_feed = True
                    break
            if next_sort_end and next_sort_end != sort_end:
                sort_end = next_sort_end
            elif rows:
                sort_end = str(rows[-1].get("realSort") or sort_end)
            if stop_feed:
                break

    for feed in feed_config.get("cls_telegraph") or []:
        name = str(feed.get("name") or "财联社电报")
        source_coverage = begin_coverage(
            name,
            "财联社电报接口按 lastTime/last_time 翻页拉取，直到最旧消息早于本次窗口起点。",
        )
        rn = int(feed.get("rn") or 200)
        max_pages_feed = effective_max_pages(int(feed.get("max_pages") or 20))
        last_time = int(now_local().timestamp())
        stop_feed = False
        for page in range(1, max_pages_feed + 1):
            query = (
                f"app=CailianpressWeb&category=&lastTime={last_time}&last_time={last_time}"
                f"&os=web&refresh_type=1&rn={rn}&sv=8.4.6"
            )
            url = "https://www.cls.cn/nodeapi/telegraphList?" + query + "&sign=" + cls_query_sign(query)
            raw_path = output_dir / f"feed_cls_{safe_name(name)}_p{page}.json"
            try:
                request = urllib.request.Request(
                    url,
                    headers={
                        "User-Agent": "Mozilla/5.0 OpenClaw Market Immersion",
                        "Referer": "https://www.cls.cn/telegraph",
                        "Accept": "application/json,text/plain,*/*",
                    },
                )
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    data = json.loads(response.read().decode("utf-8"))
                raw_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                raw_files.append(str(raw_path))
                rows = (((data.get("data") or {}).get("roll_data")) or [])
            except Exception as exc:  # noqa: BLE001
                messages.append(f"{name} page {page}: {exc}")
                source_coverage["error"] = str(exc)
                break
            if not rows:
                break
            for row in rows:
                if not isinstance(row, dict):
                    continue
                ts = row.get("ctime")
                parsed = dt.datetime.fromtimestamp(int(ts), tz=window_end.tzinfo if window_end else now_local().tzinfo) if ts else None
                date_text = parsed.isoformat(sep=" ", timespec="seconds") if parsed else ""
                keep, stop, bucket, parsed = should_keep_feed_item(
                    value=date_text,
                    window_start=window_start,
                    window_end=window_end,
                )
                note_coverage(source_coverage, parsed)
                if stop:
                    stop_feed = True
                    continue
                if not keep:
                    continue
                title = str(row.get("title") or row.get("brief") or "").strip()
                content = choose_content(row.get("content"), row.get("brief"), title=title)
                url_value = str(row.get("shareurl") or row.get("url") or "https://www.cls.cn/telegraph").strip()
                key = dedupe_key_for_item(title=title, content=content, url=url_value, code=str(row.get("id") or ""))
                if key in seen:
                    continue
                seen.add(key)
                item = {
                    "index": len(items) + 1,
                    "code": str(row.get("id") or ""),
                    "title": title,
                    "content": content or title,
                    "content_quality": content_quality(title=title, content=content),
                    "date": date_text,
                    "source": "财联社",
                    "type": name,
                    "entity": "",
                    "url": url_value,
                    "raw_file": str(raw_path),
                    "bucket": bucket,
                    "parsed_at": parsed.isoformat(timespec="seconds") if parsed else None,
                }
                items.append(item)
                source_coverage["item_count"] += 1
                counts[bucket] += 1
                if smoke_source_done(source_coverage):
                    stop_feed = True
                    break
            last_times = [int(row.get("ctime")) for row in rows if isinstance(row, dict) and row.get("ctime")]
            if last_times:
                last_time = min(last_times)
            if stop_feed:
                break

    for feed in feed_config.get("jin10_flash") or []:
        name = str(feed.get("name") or "金十数据")
        source_coverage = begin_coverage(
            name,
            "金十快讯接口按 max_time 翻页，直到最旧消息早于本次窗口起点。",
        )
        channel = str(feed.get("channel") or "-8200")
        max_pages_feed = effective_max_pages(int(feed.get("max_pages") or 10))
        max_time = ""
        for page in range(1, max_pages_feed + 1):
            params = {"channel": channel, "vip": "1"}
            if max_time:
                params["max_time"] = max_time
            url = "https://flash-api.jin10.com/get_flash_list?" + urllib.parse.urlencode(params)
            raw_path = output_dir / f"feed_jin10_{safe_name(name)}_p{page}.json"
            try:
                request = urllib.request.Request(
                    url,
                    headers={
                        "User-Agent": "Mozilla/5.0 OpenClaw Market Immersion",
                        "x-app-id": "bVBF4FyRTn5NJF5n",
                        "x-version": "1.0.0",
                        "Accept": "application/json,text/plain,*/*",
                    },
                )
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    data = json.loads(response.read().decode("utf-8"))
                raw_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                raw_files.append(str(raw_path))
                rows = data.get("data") or []
            except Exception as exc:  # noqa: BLE001
                messages.append(f"{name} page {page}: {exc}")
                source_coverage["error"] = str(exc)
                break
            if not rows:
                break
            stop_feed = False
            for row in rows:
                if not isinstance(row, dict):
                    continue
                date_text = str(row.get("time") or "")
                keep, stop, bucket, parsed = should_keep_feed_item(
                    value=date_text,
                    window_start=window_start,
                    window_end=window_end,
                )
                note_coverage(source_coverage, parsed)
                if stop:
                    stop_feed = True
                    continue
                if not keep:
                    continue
                data_obj = row.get("data") or {}
                title = str(data_obj.get("title") or data_obj.get("content") or "").strip()
                content = choose_content(data_obj.get("content"), title=title)
                url_value = str(data_obj.get("link") or "https://www.jin10.com/flash")
                key = dedupe_key_for_item(title=title, content=content, url=url_value, code=str(row.get("id") or ""))
                if key in seen:
                    continue
                seen.add(key)
                item = {
                    "index": len(items) + 1,
                    "code": str(row.get("id") or ""),
                    "title": title,
                    "content": content or title,
                    "content_quality": content_quality(title=title, content=content),
                    "date": date_text,
                    "source": "金十数据",
                    "type": name,
                    "entity": "",
                    "url": url_value,
                    "raw_file": str(raw_path),
                    "bucket": bucket,
                    "parsed_at": parsed.isoformat(timespec="seconds") if parsed else None,
                }
                items.append(item)
                source_coverage["item_count"] += 1
                counts[bucket] += 1
                if smoke_source_done(source_coverage):
                    stop_feed = True
                    break
            max_time = str(rows[-1].get("time") or "")
            if stop_feed:
                break

    for feed in feed_config.get("tonghuashun_flash") or []:
        name = str(feed.get("name") or "同花顺实时快讯")
        source_coverage = begin_coverage(
            name,
            "同花顺新闻实时快讯接口按 page/tag 拉取，直到最旧消息早于本次窗口起点。",
        )
        tag = str(feed.get("tag") or "21101").strip()
        page_size_feed = int(feed.get("page_size") or 50)
        max_pages_feed = effective_max_pages(int(feed.get("max_pages") or 20))
        api_url = str(
            feed.get("url")
            or "https://news.10jqka.com.cn/tapp/news/push/stock/"
        ).strip()
        stop_feed = False
        for page in range(1, max_pages_feed + 1):
            params = {
                "page": page,
                "track": "website",
                "pagesize": page_size_feed,
            }
            if tag:
                params["tag"] = tag
            url = api_url + ("&" if "?" in api_url else "?") + urllib.parse.urlencode(params)
            raw_path = output_dir / f"feed_tonghuashun_flash_{safe_name(name)}_{safe_name(tag or 'all')}_p{page}.json"
            try:
                data = fetch_json_url(url, timeout=timeout)
                raw_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                raw_files.append(str(raw_path))
                rows = (((data.get("data") or {}).get("list")) or [])
                if str(data.get("code") or "") not in ("", "200"):
                    raise RuntimeError(f"code={data.get('code')} msg={data.get('msg')}")
            except Exception as exc:  # noqa: BLE001
                messages.append(f"{name} page {page}: {exc}")
                source_coverage["error"] = str(exc)
                break
            if not rows:
                break
            for row in rows:
                if not isinstance(row, dict):
                    continue
                date_text = str(row.get("rtime") or row.get("ctime") or "").strip()
                keep, stop, bucket, parsed = should_keep_feed_item(
                    value=date_text,
                    window_start=window_start,
                    window_end=window_end,
                )
                note_coverage(source_coverage, parsed)
                if stop:
                    stop_feed = True
                    continue
                if not keep:
                    continue
                title = str(row.get("title") or "").strip()
                content = choose_content(row.get("digest"), row.get("short"), title=title)
                code = str(row.get("seq") or row.get("id") or "").strip()
                url_value = str(row.get("url") or row.get("shareUrl") or row.get("appUrl") or "").strip()
                key = dedupe_key_for_item(
                    title=title,
                    content=content,
                    url=url_value,
                    code=code,
                )
                if key in seen:
                    continue
                seen.add(key)
                item = {
                    "index": len(items) + 1,
                    "code": code,
                    "title": title,
                    "content": content or title,
                    "content_quality": content_quality(title=title, content=content),
                    "date": parsed.astimezone(now_local().tzinfo).strftime("%Y-%m-%d %H:%M:%S") if parsed else date_text,
                    "source": "同花顺快讯",
                    "type": name,
                    "entity": ",".join(tag_item.get("name", "") for tag_item in (row.get("tagInfo") or []) if isinstance(tag_item, dict)),
                    "url": url_value,
                    "raw_file": str(raw_path),
                    "bucket": bucket,
                    "parsed_at": parsed.isoformat(timespec="seconds") if parsed else None,
                }
                items.append(item)
                source_coverage["item_count"] += 1
                counts[bucket] += 1
                if smoke_source_done(source_coverage):
                    stop_feed = True
                    break
            if stop_feed:
                break

    for feed in feed_config.get("sina_7x24") or []:
        name = str(feed.get("name") or "新浪财经7x24")
        source_coverage = begin_coverage(
            name,
            "新浪财经7x24直播接口按 page/page_size 翻页拉取，直到最旧消息早于本次窗口起点。",
        )
        zhibo_id = str(feed.get("zhibo_id") or "152")
        page_size = int(feed.get("page_size") or 100)
        max_pages_feed = effective_max_pages(int(feed.get("max_pages") or 20))
        api_url = str(feed.get("url") or "http://zhibo.sina.com.cn/api/zhibo/feed").strip()
        stop_feed = False
        for page in range(1, max_pages_feed + 1):
            params = {"page": page, "page_size": page_size, "zhibo_id": zhibo_id}
            url = api_url + ("&" if "?" in api_url else "?") + urllib.parse.urlencode(params)
            raw_path = output_dir / f"feed_sina_7x24_{safe_name(name)}_p{page}.json"
            try:
                data = fetch_json_url(url, timeout=timeout)
                raw_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                raw_files.append(str(raw_path))
                rows = (((((data.get("result") or {}).get("data") or {}).get("feed") or {}).get("list")) or [])
            except Exception as exc:  # noqa: BLE001
                messages.append(f"{name} page {page}: {exc}")
                source_coverage["error"] = str(exc)
                break
            if not rows:
                break
            for row in rows:
                if not isinstance(row, dict):
                    continue
                date_text = str(row.get("create_time") or row.get("update_time") or "").strip()
                keep, stop, bucket, parsed = should_keep_feed_item(
                    value=date_text,
                    window_start=window_start,
                    window_end=window_end,
                )
                note_coverage(source_coverage, parsed)
                if stop:
                    stop_feed = True
                    continue
                if not keep:
                    continue
                raw_content = strip_html(row.get("rich_text") or "")
                bracket_title, bracket_body = split_bracketed_title_content(raw_content)
                # Sina 7x24 often has no standalone title: the whole rich_text is
                # the flash body. Do not truncate the body into a fake title.
                title = bracket_title
                content = bracket_body or raw_content
                url_value = str(row.get("docurl") or "").strip()
                key = dedupe_key_for_item(title=title, content=content, url=url_value, code=str(row.get("id") or ""))
                if key in seen:
                    continue
                seen.add(key)
                tag_names = [
                    str(tag.get("name"))
                    for tag in row.get("tag") or []
                    if isinstance(tag, dict) and tag.get("name")
                ]
                item = {
                    "index": len(items) + 1,
                    "code": str(row.get("id") or ""),
                    "title": title,
                    "content": content or title,
                    "content_quality": content_quality(title=title, content=content),
                    "date": date_text,
                    "source": "新浪财经",
                    "type": name + (f" / {','.join(tag_names)}" if tag_names else ""),
                    "entity": "",
                    "url": url_value,
                    "raw_file": str(raw_path),
                    "bucket": bucket,
                    "parsed_at": parsed.isoformat(timespec="seconds") if parsed else None,
                }
                items.append(item)
                source_coverage["item_count"] += 1
                counts[bucket] += 1
                if smoke_source_done(source_coverage):
                    stop_feed = True
                    break
            if stop_feed:
                break

    for feed in feed_config.get("wallstreetcn_live") or []:
        name = str(feed.get("name") or "华尔街见闻7x24")
        source_coverage = begin_coverage(
            name,
            "华尔街见闻7x24接口按 cursor 翻页拉取，直到最旧消息早于本次窗口起点。",
        )
        channel = str(feed.get("channel") or "global-channel")
        limit = int(feed.get("limit") or 50)
        max_pages_feed = effective_max_pages(int(feed.get("max_pages") or 20))
        api_url = str(feed.get("url") or "https://api-one-wscn.awtmt.com/apiv1/content/lives").strip()
        cursor = ""
        stop_feed = False
        for page in range(1, max_pages_feed + 1):
            params = {"channel": channel, "client": "pc", "limit": limit}
            if cursor:
                params["cursor"] = cursor
            url = api_url + ("&" if "?" in api_url else "?") + urllib.parse.urlencode(params)
            raw_path = output_dir / f"feed_wallstreetcn_{safe_name(name)}_p{page}.json"
            try:
                data = fetch_json_url(url, timeout=timeout)
                raw_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                raw_files.append(str(raw_path))
                data_obj = data.get("data") or {}
                rows = data_obj.get("items") or []
                cursor = str(data_obj.get("next_cursor") or "")
            except Exception as exc:  # noqa: BLE001
                messages.append(f"{name} page {page}: {exc}")
                source_coverage["error"] = str(exc)
                break
            if not rows:
                break
            for row in rows:
                if not isinstance(row, dict):
                    continue
                raw_time = row.get("display_time")
                parsed_dt = parse_item_datetime(raw_time, window_end.tzinfo if window_end else now_local().tzinfo)
                normalized_date = parsed_dt.isoformat(sep=" ", timespec="seconds") if parsed_dt else str(raw_time or "")
                keep, stop, bucket, parsed = should_keep_feed_item(
                    value=normalized_date,
                    window_start=window_start,
                    window_end=window_end,
                )
                note_coverage(source_coverage, parsed)
                if stop:
                    stop_feed = True
                    continue
                if not keep:
                    continue
                content = strip_html(row.get("content_text") or row.get("content") or "")
                title = str(row.get("title") or "").strip()
                url_value = str(row.get("uri") or "").strip()
                key = dedupe_key_for_item(title=title, content=content, url=url_value, code=str(row.get("id") or ""))
                if key in seen:
                    continue
                seen.add(key)
                item = {
                    "index": len(items) + 1,
                    "code": str(row.get("id") or ""),
                    "title": title,
                    "content": content or title,
                    "content_quality": content_quality(title=title, content=content),
                    "date": normalized_date,
                    "source": "华尔街见闻",
                    "type": name,
                    "entity": "",
                    "url": url_value,
                    "raw_file": str(raw_path),
                    "bucket": bucket,
                    "parsed_at": parsed.isoformat(timespec="seconds") if parsed else None,
                }
                items.append(item)
                source_coverage["item_count"] += 1
                counts[bucket] += 1
                if smoke_source_done(source_coverage):
                    stop_feed = True
                    break
            if stop_feed or not cursor:
                break

    for feed in feed_config.get("sina_roll") or []:
        name = str(feed.get("name") or "新浪财经滚动")
        source_coverage = begin_coverage(
            name,
            "新浪财经滚动资讯接口按 page 翻页，直到最旧消息早于本次窗口起点。",
        )
        pageid = str(feed.get("pageid") or "153")
        lid = str(feed.get("lid") or "2509")
        num = int(feed.get("num") or 100)
        max_pages_feed = effective_max_pages(int(feed.get("max_pages") or 20))
        stop_feed = False
        for page in range(1, max_pages_feed + 1):
            params = {
                "pageid": pageid,
                "lid": lid,
                "num": num,
                "page": page,
            }
            url = "https://feed.sina.com.cn/api/roll/get?" + urllib.parse.urlencode(params)
            raw_path = output_dir / f"feed_sina_roll_{safe_name(name)}_p{page}.json"
            try:
                data = fetch_json_url(url, timeout=timeout)
                raw_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                raw_files.append(str(raw_path))
                rows = (((data.get("result") or {}).get("data")) or [])
            except Exception as exc:  # noqa: BLE001
                messages.append(f"{name} page {page}: {exc}")
                source_coverage["error"] = str(exc)
                break
            if not rows:
                break
            for row in rows:
                if not isinstance(row, dict):
                    continue
                raw_time = row.get("ctime") or row.get("intime") or row.get("time")
                parsed_dt = parse_item_datetime(raw_time, window_end.tzinfo if window_end else now_local().tzinfo)
                normalized_date = parsed_dt.isoformat(sep=" ", timespec="seconds") if parsed_dt else str(raw_time or "")
                keep, stop, bucket, parsed = should_keep_feed_item(
                    value=normalized_date,
                    window_start=window_start,
                    window_end=window_end,
                )
                note_coverage(source_coverage, parsed)
                if stop:
                    stop_feed = True
                    continue
                if not keep:
                    continue
                title = str(row.get("title") or "").strip()
                content = choose_content(row.get("content"), row.get("summary"), row.get("intro"), title=title)
                url_value = str(row.get("url") or row.get("wapurl") or "").strip()
                key = dedupe_key_for_item(title=title, content=content, url=url_value, code=str(row.get("id") or ""))
                if key in seen:
                    continue
                seen.add(key)
                item = {
                    "index": len(items) + 1,
                    "code": str(row.get("id") or ""),
                    "title": title,
                    "content": content or title,
                    "content_quality": content_quality(title=title, content=content),
                    "date": normalized_date,
                    "source": str(row.get("media_name") or row.get("source") or "新浪财经"),
                    "type": name,
                    "entity": "",
                    "url": url_value,
                    "raw_file": str(raw_path),
                    "bucket": bucket,
                    "parsed_at": parsed.isoformat(timespec="seconds") if parsed else None,
                }
                items.append(item)
                source_coverage["item_count"] += 1
                counts[bucket] += 1
                if smoke_source_done(source_coverage):
                    stop_feed = True
                    break
            if stop_feed:
                break

    if not raw_files and not items:
        return None

    finish_coverage()
    if window_start and bool(feed_config.get("require_complete_window", True)):
        incomplete = [
            item
            for item in coverage
            if item.get("error") or not item.get("reached_window_start")
        ]
        for item in incomplete:
            reason = item.get("error") or "oldest item is still newer than window start"
            messages.append(f"{item.get('source')}: incomplete window coverage: {reason}")

    stdout = f"多源资讯流采集完成：{len(items)} 条。"
    if messages:
        stdout += "\n" + "\n".join(messages)
    feed_health = build_feed_health_report(
        coverage,
        feed_config=feed_config,
        verification=source_interface_verification,
    )
    return {
        "id": "market_feed",
        "skill": "market-feed",
        "base_query": "多源市场资讯流按时间窗口分页采集",
        "query": "多源市场资讯流按时间窗口分页采集",
        "returncode": 0 if not messages else 1,
        "api_ok": not messages,
        "api_messages": messages,
        "classification": {"counts": counts, "items": items},
        "feed_coverage": coverage,
        "feed_health": feed_health,
        "duration_ms": int((time.time() - started) * 1000),
        "stdout": stdout,
        "stderr": "",
        "stdout_file": "",
        "stderr_file": "",
        "raw_files": raw_files,
    }


def collect_eastmoney_feed_entry(
    *,
    config: dict[str, Any],
    output_dir: Path,
    window_start: dt.datetime | None,
    window_end: dt.datetime | None,
) -> dict[str, Any] | None:
    # Backward-compatible alias for older snapshot scripts/configs.
    return collect_market_feed_entry(
        config=config,
        output_dir=output_dir,
        window_start=window_start,
        window_end=window_end,
    )


def inspect_api_files(raw_files: list[str]) -> tuple[bool, list[str]]:
    if not raw_files:
        return True, []
    ok = True
    messages: list[str] = []
    for raw_file in raw_files:
        try:
            data = json.loads(Path(raw_file).read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - keep diagnostics in manifest
            ok = False
            messages.append(f"{raw_file}: JSON read failed: {exc}")
            continue
        status = data.get("status")
        message = data.get("message")
        if status not in (None, 0, "0"):
            ok = False
            messages.append(f"{raw_file}: status={status} message={message}")
    return ok, messages


def parse_item_datetime(value: Any, tz: dt.tzinfo | None) -> dt.datetime | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if text.isdigit():
        try:
            timestamp = int(text)
            if timestamp > 10_000_000_000:
                timestamp = timestamp / 1000
            return dt.datetime.fromtimestamp(timestamp, tz=tz or now_local().tzinfo)
        except (OverflowError, OSError, ValueError):
            pass
    candidates = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%Y.%m.%d %H:%M:%S",
        "%Y.%m.%d %H:%M",
        "%Y.%m.%d",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y/%m/%d",
    ]
    for fmt in candidates:
        try:
            parsed = dt.datetime.strptime(text[: len(dt.datetime.now().strftime(fmt))], fmt)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=tz)
            return parsed
        except ValueError:
            continue
    try:
        parsed = dt.datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=tz)
        return parsed
    except ValueError:
        return None


def extract_mx_search_items(raw_file: str) -> list[dict[str, Any]]:
    data = json.loads(Path(raw_file).read_text(encoding="utf-8"))
    items = (
        data.get("data", {})
        .get("data", {})
        .get("llmSearchResponse", {})
        .get("data", [])
    )
    if not isinstance(items, list):
        return []
    extracted: list[dict[str, Any]] = []
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict):
            continue
        extracted.append(
            {
                "index": index,
                "code": item.get("code") or "",
                "title": item.get("title") or "",
                "content": item.get("content") or "",
                "date": item.get("date") or "",
                "source": item.get("insName") or item.get("source") or "",
                "type": item.get("informationType") or "",
                "entity": item.get("entityFullName") or "",
                "url": item.get("jumpUrl") or "",
                "raw_file": raw_file,
                "content_quality": content_quality(
                    title=str(item.get("title") or ""),
                    content=str(item.get("content") or ""),
                ),
            }
        )
    return extracted


def classify_items(
    *,
    raw_files: list[str],
    window_start: dt.datetime | None,
    window_end: dt.datetime | None,
) -> dict[str, Any]:
    classified: list[dict[str, Any]] = []
    counts = {
        "in_window": 0,
        "carryover": 0,
        "future_or_clock_skew": 0,
        "undated": 0,
        "unwindowed": 0,
    }
    for raw_file in raw_files:
        try:
            items = extract_mx_search_items(raw_file)
        except Exception as exc:  # noqa: BLE001 - keep collector resilient
            classified.append(
                {
                    "bucket": "undated",
                    "title": f"raw item extraction failed: {exc}",
                    "date": "",
                    "source": "",
                    "type": "",
                    "entity": "",
                    "raw_file": raw_file,
                }
            )
            counts["undated"] += 1
            continue
        for item in items:
            parsed = parse_item_datetime(item.get("date"), window_end.tzinfo if window_end else None)
            if not window_start or not window_end:
                bucket = "unwindowed"
            elif not parsed:
                bucket = "undated"
            elif window_start <= parsed <= window_end:
                bucket = "in_window"
            elif parsed < window_start:
                bucket = "carryover"
            else:
                bucket = "future_or_clock_skew"
            item["bucket"] = bucket
            item["parsed_at"] = parsed.isoformat(timespec="seconds") if parsed else None
            item["content_quality"] = content_quality(
                title=str(item.get("title") or ""),
                content=str(item.get("content") or ""),
            )
            classified.append(item)
            counts[bucket] += 1
    return {"counts": counts, "items": classified}


def entry_status(entry: dict[str, Any]) -> str:
    if entry["returncode"] != 0:
        return f"FAILED({entry['returncode']})"
    if not entry["api_ok"]:
        return "API_ERROR"
    return "OK"


def entry_counts(entry: dict[str, Any]) -> str:
    counts = (entry.get("classification") or {}).get("counts") or {}
    visible = [f"{key}={value}" for key, value in counts.items() if value]
    return ", ".join(visible) if visible else "无可归类标题"


def entry_items(entry: dict[str, Any]) -> list[dict[str, Any]]:
    return (entry.get("classification") or {}).get("items") or []


def item_sort_key(item: dict[str, Any]) -> tuple[str, str]:
    return (str(item.get("parsed_at") or item.get("date") or ""), str(item.get("title") or ""))


def item_identity(item: dict[str, Any]) -> tuple[str, int, str, str]:
    return (
        str(item.get("raw_file") or ""),
        int(item.get("index") or 0),
        str(item.get("title") or ""),
        str(item.get("date") or ""),
    )


def format_item_line(item: dict[str, Any]) -> str:
    meta = []
    if item.get("date"):
        meta.append(str(item["date"]))
    if item.get("type"):
        meta.append(str(item["type"]))
    if item.get("source"):
        meta.append(str(item["source"]))
    if item.get("entity"):
        meta.append(str(item["entity"]))
    bucket = item.get("bucket") or "unknown"
    suffix = f" ({' | '.join(meta)})" if meta else ""
    return f"- [{bucket}] {item.get('title') or '[无标题]'}{suffix}"


def short_text(text: str, limit: int = 180) -> str:
    cleaned = " ".join(str(text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "..."


def summary_sentence(item: dict[str, Any]) -> str:
    title = " ".join(str(item.get("title") or "").split())
    content = " ".join(str(item.get("content") or "").split())
    if not content:
        return title or "[无标题]"
    if title and content.startswith(title):
        base = content
    elif title:
        base = f"{title}：{content}"
    else:
        base = content
    return short_text(base, 260)


def deterministic_degraded_digest(
    *,
    all_items: list[dict[str, Any]],
    phase_label: str,
    failure_type: str,
    error: str,
) -> dict[str, Any]:
    """Fallback digest when the LLM/Gateway path is unavailable.

    This is intentionally conservative: it does not pretend to be a full model
    judgment.  It preserves the highest-signal source items so the daily brief
    can still publish a degraded but useful artifact instead of blocking at the
    digest stage forever.
    """
    items = all_items[:24]
    if not items:
        return {
            "enabled": True,
            "attempted": False,
            "degraded": True,
            "fallback_reason": failure_type,
            "summary_paragraphs": [f"{phase_label}摘要模型暂不可用，本轮未采集到可整理信息。"],
        }
    buckets: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        source = str(item.get("source") or item.get("bucket") or "信息流").strip() or "信息流"
        buckets.setdefault(source, []).append(item)
    paragraphs: list[str] = []
    for source, rows in list(buckets.items())[:5]:
        sentences = [summary_sentence(row).rstrip("。") for row in rows[:4] if summary_sentence(row).strip()]
        if sentences:
            paragraphs.append(f"{source}：" + "；".join(sentences) + "。")
    if not paragraphs:
        fallback_sentences = [summary_sentence(item).rstrip("。") for item in items[:8]]
        paragraphs = ["；".join(sentence for sentence in fallback_sentences if sentence) + "。"]
    return {
        "enabled": True,
        "attempted": True,
        "degraded": True,
        "summary_status": "degraded_fallback",
        "fallback_reason": failure_type,
        "fallback_error_summary": sanitize_user_reason(error),
        "summary_paragraphs": paragraphs[:6],
        "quality_warnings": ["摘要模型/Gateway 不可用，已使用本地确定性降级摘要；需以后续复盘替换为正式模型摘要。"],
    }


def display_body_without_title(*, title: str, content: str) -> str:
    normalized_title = " ".join(str(title or "").split())
    normalized_content = " ".join(str(content or "").split())
    if not normalized_content:
        return ""
    if not normalized_title:
        return normalized_content

    bracketed_title = f"【{normalized_title}】"
    if normalized_content.startswith(bracketed_title):
        return normalized_content[len(bracketed_title) :].lstrip(" ：:，,。")
    if normalized_content.startswith(normalized_title):
        return normalized_content[len(normalized_title) :].lstrip(" ：:，,。")
    return normalized_content


def display_date_for_item(item: dict[str, Any]) -> str:
    raw_date = str(item.get("date") or "").strip()
    parsed_text = str(item.get("parsed_at") or "").strip()
    if raw_date:
        parsed = parse_item_datetime(raw_date, now_local().tzinfo)
    elif parsed_text:
        parsed = parse_item_datetime(parsed_text, now_local().tzinfo)
    else:
        parsed = None
    if parsed:
        return parsed.astimezone(now_local().tzinfo).strftime("%Y-%m-%d %H:%M:%S")
    return raw_date


def raw_message_meta_line(item: dict[str, Any]) -> str:
    parts = []
    display_date = display_date_for_item(item)
    if display_date:
        parts.append(display_date)
    if item.get("source"):
        sources = [str(x) for x in item.get("duplicate_sources") or [] if x]
        if len(sources) > 1:
            parts.append(f"{item['source']}等{len(sources)}源")
        else:
            parts.append(str(item["source"]))
    return " | ".join(parts)


def build_report_items(
    entries: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[tuple[str, int, str, str], str]]:
    all_items: list[dict[str, Any]] = []
    key_to_item: dict[str, dict[str, Any]] = {}
    identity_to_item: dict[tuple[str, int, str, str], dict[str, Any]] = {}
    for entry in entries:
        for item in entry_items(entry):
            copied = dict(item)
            copied["entry_id"] = entry["id"]
            copied["duplicate_sources"] = [str(copied.get("source") or "未知")]
            copied["duplicate_count"] = 1
            keys = duplicate_keys_for_item(copied)
            existing = next((key_to_item[key] for key in keys if key in key_to_item), None)
            if existing is None:
                existing = next(
                    (
                        item
                        for item in all_items
                        if content_overlap_duplicate(item, copied)
                    ),
                    None,
                )
            if existing is None:
                all_items.append(copied)
                for key in keys:
                    key_to_item[key] = copied
                identity_to_item[item_identity(copied)] = copied
                continue

            merge_duplicate_item(existing, copied)
            for key in set(keys + duplicate_keys_for_item(existing)):
                key_to_item[key] = existing
            identity_to_item[item_identity(copied)] = existing

    all_items.sort(key=item_sort_key)
    serial_by_item: dict[tuple[str, int, str, str], str] = {}
    for index, item in enumerate(all_items, 1):
        item["serial"] = str(index)
    for identity, item in identity_to_item.items():
        serial_by_item[identity] = item["serial"]
    return all_items, serial_by_item


def extract_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("OpenClaw summary did not contain a JSON object")
    return json.loads(cleaned[start : end + 1])


RAW_FLOW_CATEGORIES = [
    "宏观/政策/监管",
    "市场价格/资金/交易结构",
    "行业/产业/科技",
    "公司/公告/订单/业绩",
    "海外市场/国际宏观",
    "商品/汇率/利率",
    "风险事件/舆情/合规",
    "事件日历/数据发布",
    "其他",
]


def deterministic_raw_flow_classification(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Neutral, non-judgmental fallback grouping for raw market messages."""
    buckets = {name: [] for name in RAW_FLOW_CATEGORIES}
    rules: list[tuple[str, tuple[str, ...]]] = [
        ("宏观/政策/监管", ("政策", "监管", "央行", "财政", "发改委", "国务院", "证监", "关税", "会议")),
        ("市场价格/资金/交易结构", ("涨", "跌", "成交", "资金", "指数", "板块", "异动", "涨停", "跌停")),
        ("行业/产业/科技", ("产业", "芯片", "半导体", "AI", "机器人", "新能源", "医药", "算力", "数据中心")),
        ("公司/公告/订单/业绩", ("公司", "公告", "订单", "合同", "业绩", "减持", "增持", "并购", "重组")),
        ("海外市场/国际宏观", ("美股", "港股", "中概", "欧洲", "日本", "美国", "海外", "国际", "外资")),
        ("商品/汇率/利率", ("原油", "黄金", "白银", "铜", "商品", "汇率", "美元", "人民币", "利率", "债券")),
        ("风险事件/舆情/合规", ("风险", "处罚", "调查", "诉讼", "违约", "退市", "事故", "制裁", "预警")),
        ("事件日历/数据发布", ("日历", "申购", "解禁", "披露", "发布", "公布", "数据")),
    ]
    for item in items:
        text = " ".join(str(item.get(key) or "") for key in ("title", "content", "type", "source"))
        category = "其他"
        for name, keywords in rules:
            if any(keyword.lower() in text.lower() for keyword in keywords):
                category = name
                break
        buckets[category].append(str(item.get("serial") or ""))
    return {
        "enabled": True,
        "attempted": False,
        "provider": "deterministic",
        "judgment_policy": "neutral classification only; no value ranking",
        "categories": [
            {"name": name, "serials": [serial for serial in serials if serial]}
            for name, serials in buckets.items()
            if serials
        ],
    }


def build_auxiliary_flow_checks(items: list[dict[str, Any]], entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Cheap deterministic checks that can run beside model-based classification."""
    source_counts: dict[str, int] = {}
    bucket_counts: dict[str, int] = {}
    title_counts: dict[str, int] = {}
    for item in items:
        source = str(item.get("source") or "未知来源")
        bucket = str(item.get("bucket") or item.get("type") or "未分类")
        title = normalize_ws(item.get("title"))
        source_counts[source] = source_counts.get(source, 0) + 1
        bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1
        if title:
            title_counts[title] = title_counts.get(title, 0) + 1
    return {
        "enabled": True,
        "item_count": len(items),
        "entry_count": len(entries),
        "source_counts": sorted(source_counts.items(), key=lambda pair: (-pair[1], pair[0]))[:20],
        "bucket_counts": sorted(bucket_counts.items(), key=lambda pair: (-pair[1], pair[0]))[:20],
        "exact_repeated_titles": [
            {"title": title, "count": count}
            for title, count in sorted(title_counts.items(), key=lambda pair: (-pair[1], pair[0]))
            if count > 1
        ][:20],
        "collection_failures": [entry.get("id") for entry in entries if entry.get("returncode") != 0 or not entry.get("api_ok")],
    }


def build_raw_flow_classification_prompt(items: list[dict[str, Any]]) -> str:
    payload = build_openclaw_payload_items(all_items=items, max_item_chars=360)
    return (
        "你是市场原始信息流整理助手。只做中性分类、去重线索和聚类，不做价值判断。\n"
        "严格禁止判断高价值/低价值、主线/噪音、投资含义、涨跌影响；这些由后续 GPT 主模型决定。\n"
        "把消息按主题/资产/行业/地区/事件类型归类；同一消息可以放入一个最合适类别。\n"
        "如果不同标题/不同来源/不同表述可能是同一事件，只输出疑似语义重复组；不要删除、合并或改写原始消息。\n"
        "语义重复组必须给出理由，理由应基于同一主体、同一事件、同一数字/时间/公告/价格变量等证据。\n"
        "返回严格 JSON，不要 Markdown。格式："
        '{"categories":[{"name":"宏观/政策/监管","serials":["1","2"],"note":"中性归类说明"}],"possible_duplicates":[{"serials":["3","8"],"reason":"同一主体同一公告"}]}\n'
        f"允许类别：{json.dumps(RAW_FLOW_CATEGORIES, ensure_ascii=False)}\n"
        f"原始消息：{json.dumps(payload, ensure_ascii=False)}"
    )


def classify_raw_flow_shard(
    *,
    config: dict[str, Any],
    items: list[dict[str, Any]],
    run_slug: str,
    shard_index: int,
    shard_total: int,
) -> dict[str, Any]:
    classify_config = config.get("raw_flow_classification") or {}
    started_at = now_local()
    openclaw_bin = Path(config.get("openclaw_bin") or "openclaw").expanduser()
    model = str(classify_config.get("model") or "volcengine-plan/glm-5.1")
    timeout = int(classify_config.get("timeout") or 180)
    prompt = build_raw_flow_classification_prompt(items)
    cmd = [
        str(openclaw_bin),
        "agent",
        "--local",
        "--agent",
        str(classify_config.get("agent") or "ark-review"),
        "--session-id",
        f"market-raw-flow-classify-{run_slug}-s{shard_index}",
        "--json",
        "--thinking",
        str(classify_config.get("thinking") or "low"),
        "--timeout",
        str(timeout),
        "--model",
        model,
        "--message",
        prompt,
    ]
    try:
        completed = run_process_group(cmd, text=True, capture_output=True, timeout=timeout + 30, check=False)
        completed_at = now_local()
        base = {
            "shard_index": shard_index,
            "shard_total": shard_total,
            "item_count": len(items),
            "serial_range": [items[0].get("serial"), items[-1].get("serial")] if items else [],
            "provider": "ark",
            "model": model,
            "model_started_at": started_at.isoformat(timespec="seconds"),
            "model_completed_at": completed_at.isoformat(timespec="seconds"),
            "model_duration_ms": int((completed_at - started_at).total_seconds() * 1000),
        }
        if completed.returncode != 0:
            return {**base, "error": completed.stderr[-1200:] or completed.stdout[-1200:]}
        data = extract_json_object(completed.stdout)
        text = "\n".join(
            payload.get("text", "")
            for payload in data.get("payloads", [])
            if isinstance(payload, dict)
        )
        result = extract_json_object(text)
        return {
            **base,
            "categories": result.get("categories") or [],
            "possible_duplicates": result.get("possible_duplicates") or [],
            "usage": (data.get("meta") or {}).get("agentMeta", {}).get("usage"),
        }
    except Exception as exc:  # noqa: BLE001
        completed_at = now_local()
        return {
            "shard_index": shard_index,
            "shard_total": shard_total,
            "item_count": len(items),
            "provider": "ark",
            "model": str((config.get("raw_flow_classification") or {}).get("model") or "volcengine-plan/glm-5.1"),
            "model_started_at": started_at.isoformat(timespec="seconds"),
            "model_completed_at": completed_at.isoformat(timespec="seconds"),
            "model_duration_ms": int((completed_at - started_at).total_seconds() * 1000),
            "error": str(exc),
        }


def merge_raw_flow_classification_shards(shards: list[dict[str, Any]]) -> dict[str, Any]:
    category_map: dict[str, dict[str, Any]] = {}
    duplicates: list[dict[str, Any]] = []
    usage: list[Any] = []
    for shard in sorted(shards, key=lambda item: int(item.get("shard_index") or 0)):
        if shard.get("usage"):
            usage.append(shard.get("usage"))
        for category in shard.get("categories") or []:
            name = str(category.get("name") or "其他")
            target = category_map.setdefault(name, {"name": name, "serials": [], "notes": []})
            for serial in category.get("serials") or []:
                serial_text = str(serial)
                if serial_text not in target["serials"]:
                    target["serials"].append(serial_text)
            note = normalize_ws(category.get("note"))
            if note and note not in target["notes"]:
                target["notes"].append(note)
        for duplicate in shard.get("possible_duplicates") or []:
            serials = [str(serial) for serial in duplicate.get("serials") or [] if str(serial).strip()]
            if not serials:
                continue
            key = tuple(sorted(serials))
            if not any(tuple(sorted([str(s) for s in item.get("serials") or []])) == key for item in duplicates):
                duplicates.append({"serials": serials, "reason": duplicate.get("reason") or "shard duplicate signal"})
    categories = []
    for name, value in category_map.items():
        categories.append({
            "name": name,
            "serials": sorted(value["serials"], key=lambda text: int(text) if text.isdigit() else 10**9),
            "note": "；".join(value["notes"][:3]),
        })
    category_order = {name: index for index, name in enumerate(RAW_FLOW_CATEGORIES)}
    categories.sort(key=lambda item: category_order.get(item["name"], 999))
    return {"categories": categories, "possible_duplicates": duplicates[:80], "usage_by_shard": usage}


def generate_raw_flow_classification(
    *,
    config: dict[str, Any],
    items: list[dict[str, Any]],
    run_slug: str,
    use_model: bool = True,
) -> dict[str, Any]:
    classify_config = config.get("raw_flow_classification") or {}
    if not classify_config.get("enabled", True):
        return {"enabled": False, "attempted": False}
    if not items:
        return {"enabled": True, "attempted": False, "reason": "no items"}
    if not use_model:
        return deterministic_raw_flow_classification(items)
    started_at = now_local()
    model = str(classify_config.get("model") or "volcengine-plan/glm-5.1")
    max_items = int(classify_config.get("max_items") or 260)
    selected_items = items[:max_items]
    shard_size = int(classify_config.get("shard_size") or max_items)
    parallelism = int(classify_config.get("parallelism") or 1)
    if shard_size > 0 and len(selected_items) > shard_size:
        shards_items = [selected_items[i : i + shard_size] for i in range(0, len(selected_items), shard_size)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(parallelism, len(shards_items)))) as executor:
            futures = [
                executor.submit(
                    classify_raw_flow_shard,
                    config=config,
                    items=shard_items,
                    run_slug=run_slug,
                    shard_index=index,
                    shard_total=len(shards_items),
                )
                for index, shard_items in enumerate(shards_items, 1)
            ]
            shard_results = [future.result() for future in concurrent.futures.as_completed(futures)]
        shard_results.sort(key=lambda item: int(item.get("shard_index") or 0))
        errors = [result for result in shard_results if result.get("error")]
        completed_at = now_local()
        if errors:
            return {
                "enabled": True,
                "attempted": True,
                "provider": "ark",
                "model": model,
                "mode": "sharded",
                "model_started_at": started_at.isoformat(timespec="seconds"),
                "model_completed_at": completed_at.isoformat(timespec="seconds"),
                "model_duration_ms": int((completed_at - started_at).total_seconds() * 1000),
                "shard_count": len(shard_results),
                "shards": shard_results,
                "error": f"{len(errors)} raw-flow classification shard(s) failed",
            }
        merged = merge_raw_flow_classification_shards(shard_results)
        return {
            "enabled": True,
            "attempted": True,
            "provider": "ark",
            "model": model,
            "mode": "sharded",
            "model_started_at": started_at.isoformat(timespec="seconds"),
            "model_completed_at": completed_at.isoformat(timespec="seconds"),
            "model_duration_ms": int((completed_at - started_at).total_seconds() * 1000),
            "judgment_policy": "neutral classification only; no value ranking",
            "item_count": len(selected_items),
            "shard_size": shard_size,
            "parallelism": parallelism,
            "shard_count": len(shard_results),
            "shards": shard_results,
            **merged,
        }

    shard = classify_raw_flow_shard(
        config=config,
        items=selected_items,
        run_slug=run_slug,
        shard_index=1,
        shard_total=1,
    )
    completed_at = now_local()
    if shard.get("error"):
        return {"enabled": True, "attempted": True, "provider": "ark", "model": model, "mode": "single", "model_started_at": started_at.isoformat(timespec="seconds"), "model_completed_at": completed_at.isoformat(timespec="seconds"), "model_duration_ms": int((completed_at - started_at).total_seconds() * 1000), "error": shard.get("error"), "shards": [shard]}
    return {
        "enabled": True,
        "attempted": True,
        "provider": "ark",
        "model": model,
        "mode": "single",
        "model_started_at": started_at.isoformat(timespec="seconds"),
        "model_completed_at": completed_at.isoformat(timespec="seconds"),
        "model_duration_ms": int((completed_at - started_at).total_seconds() * 1000),
        "judgment_policy": "neutral classification only; no value ranking",
        "categories": shard.get("categories") or [],
        "possible_duplicates": shard.get("possible_duplicates") or [],
        "usage": shard.get("usage"),
        "shards": [shard],
    }



def generate_raw_flow_quality_check(
    *,
    config: dict[str, Any],
    items: list[dict[str, Any]],
    openclaw_digest: dict[str, Any],
    run_slug: str,
    use_model: bool = True,
) -> dict[str, Any]:
    check_config = config.get("raw_flow_quality_check") or {}
    if not check_config.get("enabled", True):
        return {"enabled": False, "attempted": False}
    if not items or not (openclaw_digest.get("summary_paragraphs") or []):
        return {"enabled": True, "attempted": False, "reason": "missing items or GPT digest"}
    if not use_model:
        return {"enabled": True, "attempted": False, "reason": "model disabled for dry-run/smoke"}
    if str(check_config.get("mode") or "large_context").strip() == "large_context":
        return generate_raw_flow_quality_check_large_context(
            config=config,
            run_slug=run_slug,
            check_config=check_config,
        )
    return generate_raw_flow_quality_check_artifact(
        config=config,
        items=items,
        openclaw_digest=openclaw_digest,
        run_slug=run_slug,
        check_config=check_config,
    )
    started_at = now_local()
    openclaw_bin = Path(config.get("openclaw_bin") or "openclaw").expanduser()
    model = str(check_config.get("model") or "volcengine-plan/glm-5.1")
    timeout = int(check_config.get("timeout") or 180)
    configured_max_items = int(check_config.get("max_items") or 0)
    max_item_chars = int(check_config.get("max_item_chars") or 300)
    max_message_chars = int(check_config.get("max_message_chars") or 50000)

    selected_items = items if configured_max_items <= 0 else items[:configured_max_items]
    payload = build_openclaw_payload_items(
        all_items=selected_items,
        max_item_chars=max_item_chars,
    )
    payload_text = json.dumps(payload, ensure_ascii=False)
    chunks = chunk_text(payload_text, max_message_chars)
    instruction_message = (
        "你是日报后置质检模型。GPT 已经生成正式日报，你不能直接改稿，只能提出质检意见。\n"
        "你的边界是事实覆盖、范围边界、明显遗漏和可证伪问题；不要把自己的写作偏好、保守化表述或官方话术偏好包装成质量要求。\n"
        "允许指出：可能被忽略的高价值信号、事实不一致、过度推断、阶段稿越权做全天判断、写成财经编辑式摘要。\n"
        "不得要求 GPT 削弱清晰判断、政策洞察、责任/成本分配分析或有证据的风险提示；如果你认为某个判断过强，必须指出对应原文证据不足在哪里。\n"
        "不要重写日报，不要输出正式稿，不要替 GPT 下最终结论。\n"
        "接下来我会分片发送原始消息 JSON。请先只确认已接收，不要生成质检结果；等我发送完毕后，再基于全部分片返回严格 JSON，不要 Markdown。\n"
        '{"possible_overlooked_signals":[{"serial":"1","reason":"..."}],"fact_or_scope_warnings":["..."],"style_warnings":["..."]}\n'
        f"GPT日报：{json.dumps(openclaw_digest.get('summary_paragraphs') or [], ensure_ascii=False)}\n"
        f"原始消息总数：{len(payload)}，分片数：{len(chunks)}。"
    )
    messages = [instruction_message]
    messages.extend(
        f"原始消息分片 {index}/{len(chunks)}：\n{chunk}"
        for index, chunk in enumerate(chunks, 1)
    )
    messages.append(
        "以上原始消息已经发送完毕。现在请基于全部分片做后置质检。"
        "只返回 JSON 对象，不要 Markdown，不要代码块。"
        "possible_overlooked_signals 只列真正可能影响日报质量的遗漏，不要为了覆盖而罗列低价值消息；"
        "fact_or_scope_warnings 关注事实、数字、范围、阶段/全天边界问题；"
        "style_warnings 只关注表达是否空泛、是否财经编辑式、是否无证据过度推断；不得把尖锐但有证据的判断、政策/产业洞察、责任与成本分配分析判为风格问题。"
    )
    cmd = [
        str(openclaw_bin),
        "agent",
        "--local",
        "--agent",
        str(check_config.get("agent") or "ark-review"),
        "--session-id",
        f"market-raw-flow-qc-{run_slug}",
        "--json",
        "--thinking",
        str(check_config.get("thinking") or "low"),
        "--timeout",
        str(timeout),
        "--model",
        model,
    ]
    try:
        completed = None
        for message in messages:
            completed = run_process_group(
                [*cmd, "--message", message],
                text=True,
                capture_output=True,
                timeout=timeout + 30,
                check=False,
            )
            if completed.returncode != 0:
                break
        if completed is None:
            completed_at = now_local()
            return {"enabled": True, "attempted": True, "provider": "ark", "model": model, "model_started_at": started_at.isoformat(timespec="seconds"), "model_completed_at": completed_at.isoformat(timespec="seconds"), "model_duration_ms": int((completed_at - started_at).total_seconds() * 1000), "error": "quality check produced no turn"}
        if completed.returncode != 0:
            completed_at = now_local()
            return {"enabled": True, "attempted": True, "provider": "ark", "model": model, "model_started_at": started_at.isoformat(timespec="seconds"), "model_completed_at": completed_at.isoformat(timespec="seconds"), "model_duration_ms": int((completed_at - started_at).total_seconds() * 1000), "error": completed.stderr[-1200:] or completed.stdout[-1200:]}
        data = extract_json_object(completed.stdout)
        text = "\n".join(
            payload.get("text", "")
            for payload in data.get("payloads", [])
            if isinstance(payload, dict)
        )
        result = extract_json_object(text)
        completed_at = now_local()
        return {
            "enabled": True,
            "attempted": True,
            "provider": "ark",
            "model": model,
            "model_started_at": started_at.isoformat(timespec="seconds"),
            "model_completed_at": completed_at.isoformat(timespec="seconds"),
            "model_duration_ms": int((completed_at - started_at).total_seconds() * 1000),
            "role": "post_generation_quality_check_only",
            "coverage": {"items": len(payload), "chunks": len(chunks), "max_item_chars": max_item_chars},
            "possible_overlooked_signals": result.get("possible_overlooked_signals") or [],
            "fact_or_scope_warnings": result.get("fact_or_scope_warnings") or [],
            "style_warnings": result.get("style_warnings") or [],
            "usage": (data.get("meta") or {}).get("agentMeta", {}).get("usage"),
        }
    except Exception as exc:  # noqa: BLE001 - QC must not block publication
        completed_at = now_local()
        return {"enabled": True, "attempted": True, "provider": "ark", "model": model, "model_started_at": started_at.isoformat(timespec="seconds"), "model_completed_at": completed_at.isoformat(timespec="seconds"), "model_duration_ms": int((completed_at - started_at).total_seconds() * 1000), "error": str(exc)}


def day_from_run_slug(run_slug: str) -> str:
    yyyymmdd = run_slug.split("_", 1)[0]
    return f"{yyyymmdd[0:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}"


def generate_raw_flow_quality_check_large_context(
    *,
    config: dict[str, Any],
    run_slug: str,
    check_config: dict[str, Any],
) -> dict[str, Any]:
    """Run Kimi/Ark large-context advisory QC as the production default.

    This replaces the legacy GLM chunk/session QC path.  It is intentionally
    advisory/non-blocking: failures are surfaced as warnings, never as a reason
    to block a completed GPT digest from rendering/publishing.
    """
    started_at = now_local()
    model = str(check_config.get("model") or "kimi-k2.6")
    timeout = int(check_config.get("timeout") or 900)
    retries = int(check_config.get("retries") or 2)
    retry_wait = int(check_config.get("retry_wait") or 90)
    raw_max_tokens = check_config.get("max_output_tokens", check_config.get("max_tokens"))
    max_tokens = int(raw_max_tokens) if raw_max_tokens not in (None, "") else 0
    include_report_chars = int(check_config.get("include_report_chars") or 40000)
    runner_timeout = int(check_config.get("runner_timeout") or (timeout * (retries + 1) + retry_wait * retries + 180))

    script = WORKSPACE_ROOT / "market-immersion" / "qc-v3" / "ark_qc_v3_large_context.py"
    day = day_from_run_slug(run_slug)
    checkpoint_file = WORKSPACE_ROOT / "market-immersion" / "daily" / day / "checkpoints" / run_slug / "quality_check_v3_large_context.json"
    advisory_file = WORKSPACE_ROOT / "market-immersion" / "qc-v3" / run_slug / "large_context_advisory.json"
    if not script.exists():
        completed_at = now_local()
        return {
            "enabled": True,
            "attempted": True,
            "provider": "ark",
            "mode": "large_context",
            "model": model,
            "model_started_at": started_at.isoformat(timespec="seconds"),
            "model_completed_at": completed_at.isoformat(timespec="seconds"),
            "model_duration_ms": int((completed_at - started_at).total_seconds() * 1000),
            "error": f"large-context QC script missing: {script}",
            "checkpoint_file": str(checkpoint_file),
        }

    env = os.environ.copy()
    env.setdefault("OPENCLAW_WORKSPACE", str(WORKSPACE_ROOT))
    provider_secrets_env = str(check_config.get("provider_secrets_env") or "~/.openclaw/secrets/volcengine.env").strip()
    if provider_secrets_env:
        for key, value in load_env_file(Path(provider_secrets_env).expanduser()).items():
            env.setdefault(key, value)
    env["MARKET_QC_RUN_SLUG"] = run_slug
    env["ARK_QC_V3_MODEL"] = model
    env["ARK_QC_V3_TIMEOUT"] = str(timeout)
    env["ARK_QC_V3_RETRIES"] = str(retries)
    env["ARK_QC_V3_RETRY_WAIT"] = str(retry_wait)
    if max_tokens > 0:
        env["ARK_QC_V3_MAX_TOKENS"] = str(max_tokens)
    else:
        env.pop("ARK_QC_V3_MAX_TOKENS", None)
    env["ARK_QC_V3_REPORT_CHARS"] = str(include_report_chars)

    cmd = [
        sys.executable,
        str(script),
        "--model",
        model,
        "--timeout",
        str(timeout),
        "--include-report-chars",
        str(include_report_chars),
        "--retries",
        str(retries),
        "--retry-wait",
        str(retry_wait),
        "--non-blocking",
    ]
    if max_tokens > 0:
        cmd.extend(["--max-tokens", str(max_tokens)])
    try:
        completed = subprocess.run(
            cmd,
            cwd=str(WORKSPACE_ROOT),
            env=env,
            text=True,
            capture_output=True,
            timeout=runner_timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        completed_at = now_local()
        return {
            "enabled": True,
            "attempted": True,
            "provider": "ark",
            "mode": "large_context",
            "model": model,
            "model_started_at": started_at.isoformat(timespec="seconds"),
            "model_completed_at": completed_at.isoformat(timespec="seconds"),
            "model_duration_ms": int((completed_at - started_at).total_seconds() * 1000),
            "error": f"large-context QC runner timed out after {runner_timeout}s: {str(exc)[:800]}",
            "checkpoint_file": str(checkpoint_file),
        }

    completed_at = now_local()
    checkpoint: dict[str, Any] = {}
    if checkpoint_file.exists():
        try:
            checkpoint = load_json(checkpoint_file)
        except Exception as exc:  # noqa: BLE001
            checkpoint = {"checkpoint_read_error": str(exc)}
    advisory: dict[str, Any] = {}
    if advisory_file.exists():
        try:
            advisory = load_json(advisory_file)
        except Exception as exc:  # noqa: BLE001
            advisory = {"advisory_read_error": str(exc)}

    result: dict[str, Any] = {
        "enabled": True,
        "attempted": True,
        "provider": "ark",
        "mode": "large_context",
        "model": model,
        "model_started_at": started_at.isoformat(timespec="seconds"),
        "model_completed_at": completed_at.isoformat(timespec="seconds"),
        "model_duration_ms": int((completed_at - started_at).total_seconds() * 1000),
        "runner_returncode": completed.returncode,
        "checkpoint_file": str(checkpoint_file),
        "advisory_file": str(advisory_file) if advisory_file.exists() else None,
        "prompt_artifact_file": checkpoint.get("prompt_file"),
        "prompt_sha256": checkpoint.get("prompt_sha256"),
        "prompt_chars": checkpoint.get("prompt_chars"),
        "qc_validated": checkpoint.get("qc_validated"),
        "validation_errors": checkpoint.get("validation_errors") or advisory.get("_validation_errors") or [],
        "validation_notes": advisory.get("validation_notes") or [],
        "gpt_review_required": checkpoint.get("gpt_review_required") if "gpt_review_required" in checkpoint else advisory.get("gpt_review_required"),
        "audit_pack_strategy": checkpoint.get("audit_pack_strategy") or advisory.get("audit_pack_strategy"),
        "possible_overlooked_signals": advisory.get("possible_overlooked_signals") or [],
        "fact_or_scope_warnings": advisory.get("fact_or_scope_warnings") or [],
        "weighting_or_priority_warnings": advisory.get("weighting_or_priority_warnings") or [],
        "style_warnings": advisory.get("style_warnings") or [],
        "usage": checkpoint.get("usage"),
    }
    status = str(checkpoint.get("status") or "").strip()
    if completed.returncode != 0 or status == "failed":
        failure = checkpoint.get("failure_class") or checkpoint.get("failure_type") or "large_context_qc_failed"
        validation_errors = checkpoint.get("validation_errors") or []
        result["error"] = json.dumps(
            {
                "failure_class": failure,
                "validation_errors": validation_errors[:3] if isinstance(validation_errors, list) else validation_errors,
                "stdout_tail": (completed.stdout or "")[-1200:],
                "stderr_tail": (completed.stderr or "")[-1200:],
            },
            ensure_ascii=False,
        )
    return result


def review_has_findings(review: dict[str, Any]) -> bool:
    style_warnings = review.get("style_warnings")
    weighting_warnings = review.get("weighting_or_priority_warnings")
    # A non-blocking Ark/QC invocation failure is an execution warning, not a
    # substantive review finding.  It must not trigger a second GPT pass that
    # rereads the full source file and risks another timeout.
    if (
        not review.get("possible_overlooked_signals")
        and not review.get("fact_or_scope_warnings")
        and not weighting_warnings
        and isinstance(style_warnings, list)
        and style_warnings
        and all("后置质检调用失败" in str(item) for item in style_warnings)
    ):
        return False
    return bool(
        review.get("possible_overlooked_signals")
        or review.get("fact_or_scope_warnings")
        or weighting_warnings
        or style_warnings
    )


def gpt_decide_ark_review_and_finalize(
    *,
    config: dict[str, Any],
    phase_label: str,
    window: dict[str, Any],
    openclaw_digest: dict[str, Any],
    ark_review: dict[str, Any],
    run_slug: str,
) -> dict[str, Any]:
    """Let GPT decide whether to accept Ark's advisory review.

    Ark is not allowed to overwrite the formal report.  The daily-writer reads
    the full original source file again, checks Ark's suggestions against the
    source material, and returns the final digest plus accept/reject reasons.
    """
    check_config = config.get("raw_flow_quality_check") or {}
    if not check_config.get("gpt_review_decision_enabled", False):
        return openclaw_digest
    if not review_has_findings(ark_review):
        return openclaw_digest
    input_file = openclaw_digest.get("source_input_file")
    if not input_file:
        return {**openclaw_digest, "ark_review_decision_skipped": "missing source_input_file"}

    openclaw_bin = Path(config.get("openclaw_bin") or "openclaw").expanduser()
    agent = str(check_config.get("gpt_review_agent") or (config.get("openclaw_summary") or {}).get("agent") or "daily-writer")
    model = str(check_config.get("gpt_review_model") or (config.get("openclaw_summary") or {}).get("model") or "openai-codex/gpt-5.5")
    timeout = int((config.get("openclaw_summary") or {}).get("timeout") or 300)
    ark_review_status = {
        "qc_validated": ark_review.get("qc_validated"),
        "validation_errors": ark_review.get("validation_errors") or [],
        "validation_notes": ark_review.get("validation_notes") or [],
        "gpt_review_required": ark_review.get("gpt_review_required"),
        "audit_pack_strategy": ark_review.get("audit_pack_strategy"),
        "input_transport": ark_review.get("input_transport"),
        "prompt_artifact_file": ark_review.get("prompt_artifact_file"),
        "audit_pack_file": ark_review.get("audit_pack_file"),
        "source_input_file": ark_review.get("source_input_file"),
        "coverage": ark_review.get("coverage") or {},
    }
    ark_review_findings = {
        key: ark_review.get(key)
        for key in [
            "possible_overlooked_signals",
            "fact_or_scope_warnings",
            "weighting_or_priority_warnings",
            "style_warnings",
        ]
    }
    prompt = (
        "你是日报正式写作 worker。Ark 已给出后置质检意见，但 Ark 只提供建议，不能直接改稿；最终是否采纳由 GPT 基于完整原文独立决定。\n"
        f"请先完整读取原始消息文件：{input_file}\n"
        "必须基于文件中的全部原文复核 Ark 意见，不得只根据 Ark 意见或当前稿件判断；也不得因为 Ark 声称已读完整信息流就省略自己的复核。\n"
        "如果 Ark 质检状态显示 qc_validated=false、full_raw_flow_not_read、audit_pack_not_sufficient_for_global_review 或其他 validation_errors，你仍可把 Ark 意见当作审稿线索，但不能当成已经验证的结论；每一条采纳都必须由你重新在原始消息文件中找到证据。\n"
        "不要因为 Ark 提出保守化、表层化或官方话术化建议，就削弱原稿中有证据的清晰判断、市场洞察、政策意图拆解、责任/成本分配分析和风险提示。\n"
        "采纳 Ark 意见的前提是它指出了明确事实错误、范围错误、遗漏的高价值原文证据、权重明显失衡或无证据过度推断；若 Ark 只是偏好更圆滑、更概括或更像官方表述，应明确 rejected。\n"
        "最终 summary_paragraphs 是给用户直接阅读的正式终稿，不是版本对比稿；禁止出现内部修订/评价框架，例如“原稿如何”“上一版遗漏”“本次修订”“质检认为”“Ark/Kimi/GPT review 建议”“应补入正文”。注意限制的是内部改稿视角，不是具体词语本身；“市场低估风险”这类自然市场判断可以使用。所有采纳项都要自然融入市场叙事。\n"
        f"阶段：{phase_label}\n窗口：{window.get('start') or '无'} 至 {window.get('end') or '无'}\n"
        f"当前 GPT 正式稿：{json.dumps(openclaw_digest.get('summary_paragraphs') or [], ensure_ascii=False)}\n"
        f"Ark 质检状态：{json.dumps(ark_review_status, ensure_ascii=False)}\n"
        f"Ark 质检意见：{json.dumps(ark_review_findings, ensure_ascii=False)}\n"
        "请输出最终 JSON：如果 Ark 意见正确，可以修改 summary_paragraphs；如果 Ark 误判或教条化，可以保持原稿。\n"
        "必须返回严格 JSON，不要 Markdown："
        "{\"summary_paragraphs\":[\"...\"],\"observation\":{\"repeated_words\":[\"...\"],\"multi_day_themes\":[\"...\"],\"watch_next\":[\"...\"]},\"ark_review_decisions\":[{\"suggestion\":\"...\",\"source_refs\":[\"...\"],\"decision\":\"accepted|rejected\",\"reason\":\"...\",\"evidence_basis\":\"full_raw_flow|not_supported|already_covered|style_preference\"}]}"
    )
    started_at = now_local()
    completed = run_openclaw_model_call(
        [
            str(openclaw_bin),
            "agent",
            "--local",
            "--agent",
            agent,
            "--session-id",
            f"market-digest-gpt-ark-decision-{run_slug}",
            "--json",
            "--thinking",
            str(check_config.get("gpt_review_thinking") or "high"),
            "--timeout",
            str(timeout),
            "--model",
            model,
            "--message",
            prompt,
        ],
        text=True,
        capture_output=True,
        timeout=timeout + 30,
        check=False,
    )
    completed_at = now_local()
    if completed.returncode != 0:
        return {
            **openclaw_digest,
            "ark_review_decision_error": completed.stderr[-1200:] or completed.stdout[-1200:],
            "ark_review_decision_model_duration_ms": int((completed_at - started_at).total_seconds() * 1000),
        }
    try:
        data = extract_json_object(completed.stdout)
        text = "\n".join(payload.get("text", "") for payload in data.get("payloads", []) if isinstance(payload, dict))
        revised = extract_json_object(text)
        paragraphs = normalize_summary_paragraphs(revised)
        if not paragraphs:
            raise RuntimeError("GPT review decision returned empty summary_paragraphs")
        return {
            **openclaw_digest,
            "summary_paragraphs": paragraphs,
            "observation": revised.get("observation") or openclaw_digest.get("observation") or {},
            "ark_review_decisions": revised.get("ark_review_decisions") or [],
            "ark_review_decision_agent": agent,
            "ark_review_decision_model": model,
            "ark_review_decision_model_duration_ms": int((completed_at - started_at).total_seconds() * 1000),
            "ark_review_decision_usage": (data.get("meta") or {}).get("agentMeta", {}).get("usage"),
        }
    except Exception as exc:  # noqa: BLE001 - keep original GPT draft if the decision pass fails
        return {
            **openclaw_digest,
            "ark_review_decision_error": str(exc),
            "ark_review_decision_model_duration_ms": int((completed_at - started_at).total_seconds() * 1000),
        }


def market_scope_rule_for_phase(phase_label: str) -> str:
    label = str(phase_label or "")
    if "夜" in label or "晚" in label or "归档" in label or "复盘" in label:
        return "夜间/综合复盘可以形成全天市场判断，但仍必须基于全天信息流证据，不得脱离材料。"
    return (
        "本阶段简报只能形成阶段性市场洞察，不能凭晨报、午报或收盘报某一阶段信息流下全天结论；"
        "禁止写“今天市场如何”“全天判断”“今天不是/是……的一天”等全日定性。"
    )


def market_scope_rule_short_for_phase(phase_label: str) -> str:
    label = str(phase_label or "")
    if "夜" in label or "晚" in label or "归档" in label or "复盘" in label:
        return "夜间/综合复盘可以形成全天判断，但必须基于全天证据。"
    return "只能写阶段性市场洞察，不能把晨报、午报或收盘报上升为全天判断；禁止“今天市场如何/今天不是……的一天”等全日定性。"


def build_openclaw_summary_prompt(
    *,
    phase_label: str,
    window: dict[str, Any],
    all_items: list[dict[str, Any]],
    max_item_chars: int,
    max_model_items: int = 0,
    raw_flow_classification: dict[str, Any] | None = None,
) -> str:
    if max_model_items > 0:
        all_items = all_items[:max_model_items]
    payload_items = build_openclaw_payload_items(
        all_items=all_items,
        max_item_chars=max_item_chars,
    )
    phase_scope_rule = market_scope_rule_for_phase(phase_label)
    return (
        "你是市场信息流研判助手。任务不是财经媒体摘要，也不是交易建议，而是全面吸收阶段性信息流，过滤噪音，提炼对市场理解有帮助的洞察。\n"
        "请基于原始消息，生成前后连贯、逻辑清晰、兼顾覆盖与降噪的阶段性市场洞察。\n"
        "规则：\n"
        "1. 只整理事实和消息含义，不预测涨跌，不给买卖建议。\n"
        "2. 不要按栏目拆分，不要写编号列表，不要输出信源、原文编号、状态、时间归类、信息类型等工程字段。\n"
        "3. 输入里的 content 是正文材料；title 只作为判断归类的辅助证据。content_quality 为 title_only/title_like 的消息要当作短快讯，不要扩写成不存在的正文。\n"
        "4. 只有当时间是消息报道的事件时间、截止时间、会议时间、披露时间等内容本身的一部分时，才写入整理正文。\n"
        "4a. 窗口时间只用于后台筛选，禁止在正文里说明“本次窗口”“补发/重跑”“没有混入某时间之后的新闻”等工程口径。\n"
        f"4b. {phase_scope_rule}\n"
        "4c. 不要教条化：以下覆盖类别只是后台检查工具，不是前台填空模板；没有增量的类别可以不写，低价值信息不能为凑覆盖而进入总览。\n"
        "5. 目标是全面但降噪：高频重复只合并写一次；低频但有订单、审批、政策、监管、价格异动、产业链数据、出海验证的信息要进入候选池。\n"
        "6. 每段都必须包含具体主体、关键数字或明确事件细节；不能只写“热度较高、值得关注、提供线索、存在扰动”这类空话。\n"
        "7. 对重复、同主题、跨信源的信息要合并表达，写出它们之间的关系：是互相印证、边际变化、分歧，还是同一事件的不同侧面。\n"
        "7a. 如果中性分类结果包含 possible_duplicates，它只是疑似语义重复提示；写作时可以合并表达，但不得因此忽略其中的分歧、不同数字或不同来源限定。\n"
        "8. 必须保留重要分歧和风险表述；遇到情绪化或观点化消息，要保留其观点属性，不把它写成事实。\n"
        "9. 可以有市场洞察，但必须克制：判断只能来自原文中相邻事实的直接关系，不能从阶段信息流上升为全天结论。\n"
        "10. 汇总部分默认写成 4-6 个自然段，每段 180-360 个中文字符；但段落数是弹性建议，不是硬性上限。若信息密度确实需要，可以超过 6 段，但不能靠空话扩段。段落之间要有承接关系，像读完信息流后的市场理解，不像摘要拼接。\n"
        "11. 判断句必须带证据，不要写没有原文支撑的因果、目的和趋势；优先使用“这一阶段的信息流显示”“截至本阶段”“仍需区分”“尚不能推出”等低强度表达。\n"
        "12. 禁止使用空泛或过度分析句，例如“后续值得关注”“整体来看”“提供线索”“需要继续观察”“共同指向”“出现错位”“对冲不确定性”“结构活跃”。\n"
        "13. 禁止出现后台写作/批改口吻，例如“不该写成”“不能简单归纳为”“后续简报应”“真正要保留的判断是”。\n"
        "14. 不要为了显得有洞察而替消息下结论；如果只能看到并列事实，就写成并列事实，不要强行提炼大主题。\n"
        "15. 返回严格 JSON，不要 Markdown，不要代码块。\n"
        "JSON 格式：\n"
        '{"summary_paragraphs":["...","..."],"observation":{"repeated_words":["..."],"multi_day_themes":["..."],"watch_next":["..."]}}\n'
        f"阶段：{phase_label}\n"
        f"窗口：{window.get('start') or '无'} 至 {window.get('end') or '无'}\n"
        "中性分类结果（仅供材料定位，不代表价值判断；正式取舍由你基于原始信息决定）：\n"
        f"{json.dumps(raw_flow_classification or {}, ensure_ascii=False)}\n"
        "原始消息：\n"
        f"{json.dumps(payload_items, ensure_ascii=False)}"
    )


def build_openclaw_payload_items(
    *,
    all_items: list[dict[str, Any]],
    max_item_chars: int,
) -> list[dict[str, Any]]:
    payload_items = []
    for item in all_items:
        content = " ".join(str(item.get("content") or "").split())
        if max_item_chars > 0:
            content = short_text(content, max_item_chars)
        payload_items.append(
            {
                "serial": item.get("serial"),
                "entry_id": item.get("entry_id"),
                "title": item.get("title"),
                "date": item.get("date"),
                "source": item.get("source"),
                "type": item.get("type"),
                "bucket": item.get("bucket"),
                "content_quality": item.get("content_quality") or content_quality(
                    title=str(item.get("title") or ""),
                    content=content,
                ),
                "content": content,
            }
        )
    return payload_items


def write_market_model_input_file(
    *,
    config: dict[str, Any],
    run_slug: str,
    phase_label: str,
    window: dict[str, Any],
    all_items: list[dict[str, Any]],
    raw_flow_classification: dict[str, Any] | None = None,
) -> Path:
    """Persist the full source material for model-side reading.

    Formal daily writing must be based on the complete original feed.  This file
    is the handoff artifact from the local pipeline to the GPT daily-writer; it
    deliberately keeps every item and does not apply model-input truncation.
    """
    output_dir = Path(config.get("output_dir") or "~/.openclaw/workspace/market-immersion").expanduser()
    target_dir = output_dir / "model-inputs" / safe_name(run_slug)
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / "full_raw_flow_for_gpt.json"
    payload = {
        "schema": "openclaw.market_immersion.full_raw_flow.v1",
        "run_slug": run_slug,
        "phase_label": phase_label,
        "window": window,
        "raw_flow_classification": raw_flow_classification or {},
        "item_count": len(all_items),
        "items": build_openclaw_payload_items(all_items=all_items, max_item_chars=0),
        "contract": {
            "formal_writer": "GPT daily-writer must read and use all items in this file before drafting.",
            "no_truncation": True,
            "no_candidate_substitution": True,
            "ark_review_role": "advisory only; GPT decides whether to accept suggestions",
        },
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_market_model_prompt_artifact(*, config: dict[str, Any], run_slug: str, attempt: int, prompt: str) -> tuple[Path, str]:
    """Persist exact formal prompt bytes for non-argv native OpenClaw transport."""
    output_dir = Path(config.get("output_dir") or "~/.openclaw/workspace/market-immersion").expanduser()
    target_dir = output_dir / "model-inputs" / safe_name(run_slug) / "prompt-artifacts"
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"formal_digest_prompt_attempt{attempt}.txt"
    path.write_text(prompt, encoding="utf-8")
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    return path, digest


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_market_qc_input_file(
    *,
    config: dict[str, Any],
    run_slug: str,
    phase_label: str,
    window: dict[str, Any],
    items: list[dict[str, Any]],
    openclaw_digest: dict[str, Any],
) -> Path:
    """Persist full, untruncated source material for advisory QC."""
    output_dir = Path(config.get("output_dir") or "~/.openclaw/workspace/market-immersion").expanduser()
    target_dir = output_dir / "model-inputs" / safe_name(run_slug)
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / "full_raw_flow_for_ark_qc.json"
    payload = {
        "schema": "market_immersion_ark_qc_input.v1",
        "phase_label": phase_label,
        "window": window,
        "item_count": len(items),
        "items": build_openclaw_payload_items(all_items=items, max_item_chars=0),
        "gpt_digest": {
            "summary_paragraphs": openclaw_digest.get("summary_paragraphs") or [],
            "observation": openclaw_digest.get("observation") or {},
            "source_input_file": openclaw_digest.get("source_input_file"),
        },
        "contract": {
            "qc_role": "advisory_only",
            "formal_writer": "gpt",
            "gpt_decides_adoption": True,
            "no_input_truncation": True,
            "do_not_rewrite_formal_report": True,
        },
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_market_qc_prompt_artifact(*, config: dict[str, Any], run_slug: str, prompt: str) -> tuple[Path, str]:
    output_dir = Path(config.get("output_dir") or "~/.openclaw/workspace/market-immersion").expanduser()
    target_dir = output_dir / "model-inputs" / safe_name(run_slug) / "prompt-artifacts"
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / "ark_qc_prompt.txt"
    path.write_text(prompt, encoding="utf-8")
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    return path, digest


MARKET_QC_HIGH_SIGNAL_KEYWORDS = [
    "CPI",
    "通胀",
    "降息",
    "加息",
    "美联储",
    "央行",
    "国债",
    "收益率",
    "汇率",
    "日元",
    "美元",
    "黄金",
    "白银",
    "原油",
    "铜",
    "铝",
    "财报",
    "业绩",
    "回购",
    "并购",
    "监管",
    "政策",
    "AI",
    "算力",
    "芯片",
    "英伟达",
    "台积电",
    "汽车",
    "新能源",
    "储能",
    "医药",
    "地产",
    "债务",
    "中东",
    "俄乌",
]


def market_qc_clip(value: Any, limit: int) -> str:
    text = "" if value is None else str(value).replace("\n", " ").strip()
    if limit <= 0 or len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def market_qc_item_ref(item: dict[str, Any]) -> str:
    return str(item.get("serial") or item.get("entry_id") or "")


def compact_digest_for_qc(openclaw_digest: dict[str, Any]) -> dict[str, Any]:
    return {
        "summary_paragraphs": openclaw_digest.get("summary_paragraphs") or [],
        "observation": openclaw_digest.get("observation") or {},
        "coverage_check": openclaw_digest.get("coverage_check") or {},
        "model": openclaw_digest.get("model"),
        "agent": openclaw_digest.get("agent"),
        "input_transport": openclaw_digest.get("input_transport"),
    }


def build_market_qc_audit_pack(
    *,
    items: list[dict[str, Any]],
    openclaw_digest: dict[str, Any],
    run_slug: str,
    source_input_file: Path,
    source_input_sha256: str,
    summary_file: Path | None,
    summary_file_sha256: str | None,
    evidence_limit: int,
    evidence_excerpt_chars: int,
) -> dict[str, Any]:
    coverage: list[list[str]] = []
    evidence_map: dict[str, dict[str, Any]] = {}
    for item in items:
        ref = market_qc_item_ref(item)
        title = str(item.get("title") or "")
        content = str(item.get("content") or "")
        source = str(item.get("source") or "")
        coverage.append(
            [
                ref,
                market_qc_clip(item.get("bucket"), 8),
                market_qc_clip(source, 12),
                market_qc_clip(title, 24),
            ]
        )
        haystack = title + " " + content
        score = sum(8 for word in MARKET_QC_HIGH_SIGNAL_KEYWORDS if word and word in haystack)
        if item.get("bucket") == "in_window":
            score += 2
        if score <= 0:
            continue
        evidence_map[ref] = {
            "ref": ref,
            "title": market_qc_clip(title, 90),
            "date": item.get("date"),
            "source": source,
            "type": item.get("type"),
            "bucket": item.get("bucket"),
            "reason": "keyword_high_signal",
            "excerpt": market_qc_clip(content, evidence_excerpt_chars),
            "_score": score,
        }
    evidence = sorted(
        evidence_map.values(),
        key=lambda row: (-int(row.get("_score") or 0), str(row.get("ref") or "")),
    )[: max(0, evidence_limit)]
    for row in evidence:
        row.pop("_score", None)
    return {
        "schema_version": "raw_flow_audit_pack_v1",
        "run_slug": run_slug,
        "strategy": "coverage_index_plus_focused_evidence_v1",
        "source_input_file": str(source_input_file),
        "source_input_sha256": source_input_sha256,
        "summary_file": str(summary_file) if summary_file is not None else None,
        "summary_file_sha256": summary_file_sha256,
        "raw_source_item_count": len(items),
        "coverage_index_item_count": len(coverage),
        "coverage_index_columns": ["ref", "bucket", "source", "title24"],
        "coverage_index": coverage,
        "evidence_item_count": len(evidence),
        "evidence_pack_scope": f"keyword_high_signal_top{max(0, evidence_limit)}",
        "focused_evidence_pack": evidence,
        "gpt_formal_output_under_review": compact_digest_for_qc(openclaw_digest),
        "gpt_digest": compact_digest_for_qc(openclaw_digest),
    }


def write_market_qc_audit_pack_artifact(
    *,
    config: dict[str, Any],
    run_slug: str,
    pack: dict[str, Any],
) -> tuple[Path, str]:
    output_dir = Path(config.get("output_dir") or "~/.openclaw/workspace/market-immersion").expanduser()
    target_dir = output_dir / "model-inputs" / safe_name(run_slug) / "qc-artifacts"
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / "raw_flow_audit_pack.json"
    text = json.dumps(pack, ensure_ascii=False, indent=2)
    path.write_text(text, encoding="utf-8")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return path, digest


def build_ark_qc_artifact_prompt(*, phase_label: str, window: dict[str, Any], pack: dict[str, Any]) -> str:
    coverage_text = "\n".join("|".join(map(str, row)) for row in pack.get("coverage_index") or [])
    evidence_text = json.dumps(pack.get("focused_evidence_pack") or [], ensure_ascii=False, separators=(",", ":"))
    formal_output_text = json.dumps(
        pack.get("gpt_formal_output_under_review") or pack.get("gpt_digest") or {},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"""你是市场日报后置质检员，不是主笔，也不是改写模型。

你的任务：
同时对照【GPT 已生成、准备给用户看的正式稿】和【GPT 写作时使用的同一份完整原始/去重信息流】，判断正式稿是否存在重大遗漏、事实/范围错误、权重失衡或表达风险。

你的输出只是给 GPT daily-writer 的结构化审稿意见。最终是否采纳、如何修订、是否发布，都由 GPT 和主流程决定。

关键边界：
- Ark/Kimi/GLM 只做 advisory QC。
- GPT 是正式主笔和最终 accept/reject reviewer。
- 不要写新正文，不要给可直接发布的改写稿。
- 不要输出“原稿如何、修订后如何、质检认为”等用户可见的内部批改口吻；这些只能作为给 GPT 的审稿意见。
- 只基于本 prompt 和明确给出的本地文件材料判断。
- 如果你没有实际读取完整信息流，只读到了覆盖索引或聚焦证据包，必须明确标记 full_raw_flow_read=false，并把 audit_pack_sufficient_for_global_review 设为 false 或在 validation_notes 里说明限制。
- source_refs 必须来自 Layer A 覆盖索引。
- 只返回严格 JSON，不要 Markdown，不要代码块。

输入关系：
- 被审对象是 GPT 正式稿；本轮如果只有 digest paragraphs，它们就是本轮准备发布给用户的正式内容。
- 信息源是 GPT 写作时使用的完整原始/去重信息流文件；它是判断“漏、错、权重不对”的主要依据。
- Layer A / Layer B 只是审计导航：
  - Layer A 是全部 raw-flow items 的完整覆盖索引，用来保证全局 refs 可追踪。
  - Layer B 是聚焦证据包，含 excerpt，用来帮助快速审查事实、范围和权重。
  - Layer A / Layer B 不能替代完整原始信息流；如果无法读取完整信息流，不得声称完成了无保留的全局质检。

输入元数据：
- run_slug: {pack.get("run_slug")}
- phase: {phase_label}
- window: {window.get("start") or "无"} 至 {window.get("end") or "无"}
- full_raw_flow_file: {pack.get("source_input_file")}
- raw_flow_sha256: {pack.get("source_input_sha256")}
- gpt_formal_output_file: {pack.get("summary_file")}
- digest_sha256: {pack.get("summary_file_sha256")}
- raw_source_item_count: {pack.get("raw_source_item_count")}
- coverage_index_item_count: {pack.get("coverage_index_item_count")}
- evidence_item_count: {pack.get("evidence_item_count")}
- audit_pack_strategy: coverage_index_plus_focused_evidence_v1
- evidence_pack_scope: {pack.get("evidence_pack_scope")}

【被审对象：GPT 正式稿 / digest paragraphs】
{formal_output_text}

【主信息源：GPT 写作时使用的完整原始/去重信息流文件】
请尽可能完整读取这个 UTF-8 JSON 文件后再判断：
{pack.get("source_input_file")}

【Layer A: 完整覆盖索引 TSV，列为 ref|bucket|source|title24】
{coverage_text}

【Layer B: 聚焦证据包 JSON】
{evidence_text}

请输出严格 JSON，字段如下：
{{
  "schema_version": "ark_qc_v3_global_v1",
  "run_slug": "{pack.get("run_slug")}",
  "input_status": "ok | missing_or_invalid",
  "formal_output_reviewed": true,
  "full_raw_flow_read": true,
  "audit_pack_strategy": "coverage_index_plus_focused_evidence_v1",
  "raw_source_item_count": {pack.get("raw_source_item_count")},
  "coverage_index_item_count": {pack.get("coverage_index_item_count")},
  "coverage_index_complete": true,
  "evidence_item_count": {pack.get("evidence_item_count")},
  "evidence_pack_scope": "{pack.get("evidence_pack_scope")}",
  "audit_pack_sufficient_for_global_review": true,
  "possible_overlooked_signals": [
    {{
      "severity": "high | medium | low",
      "source_refs": [],
      "evidence_basis": "full_raw_flow | evidence_excerpt | coverage_index | v2_candidate",
      "signal": "",
      "why_it_matters": "",
      "gpt_report_coverage": "absent | underweighted | covered",
      "suggested_review_question_for_gpt": ""
    }}
  ],
  "fact_or_scope_warnings": [
    {{
      "severity": "high | medium | low",
      "source_refs": [],
      "evidence_basis": "full_raw_flow | evidence_excerpt | v2_candidate",
      "report_location_or_quote": "",
      "issue": "",
      "suggested_check": ""
    }}
  ],
  "weighting_or_priority_warnings": [
    {{
      "severity": "high | medium | low",
      "source_refs": [],
      "evidence_basis": "full_raw_flow | evidence_excerpt | coverage_index | v2_candidate",
      "issue": "",
      "why_priority_should_change": "",
      "suggested_review_question_for_gpt": ""
    }}
  ],
  "style_warnings": [
    {{
      "severity": "medium | low",
      "report_location_or_quote": "",
      "issue": "",
      "why_it_matters": "",
      "suggested_check": ""
    }}
  ],
  "validation_notes": [],
  "gpt_review_required": true
}}

规则：
- formal_output_reviewed、full_raw_flow_read、audit_pack_sufficient_for_global_review 和 gpt_review_required 必须按实际判断输出 true/false，不要机械照抄。
- 只有当你确实读取了 GPT 正式稿和完整原始/去重信息流，且材料足够支撑全局审稿时，audit_pack_sufficient_for_global_review 才能为 true。
- gpt_review_required=true 当任一 warning/finding 非空，或 audit_pack_sufficient_for_global_review=false。
- fact_or_scope_warnings 的 evidence_basis 不能只用 coverage_index，必须是 full_raw_flow、evidence_excerpt 或 v2_candidate。
- 所有 source_refs 必须存在于 Layer A。
- 不要把“GPT 已覆盖但你表达偏好不同”的内容列为问题；只有漏掉、事实/范围错误、权重明显失衡或表达会误导用户时才列。
- 不要输出 schema 外字段。
"""


def validate_market_qc_advisory(result: dict[str, Any], pack: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    refs = {str(row[0]) for row in pack.get("coverage_index") or []}
    if result.get("schema_version") != "ark_qc_v3_global_v1":
        errors.append("bad_schema_version")
    if result.get("input_status") != "ok":
        errors.append("input_status_not_ok")
    if result.get("formal_output_reviewed") is not True:
        errors.append("formal_output_not_reviewed")
    if result.get("full_raw_flow_read") is not True:
        errors.append("full_raw_flow_not_read")
    if result.get("audit_pack_strategy") != "coverage_index_plus_focused_evidence_v1":
        errors.append("bad_audit_pack_strategy")
    if result.get("audit_pack_sufficient_for_global_review") is not True:
        errors.append("audit_pack_not_sufficient_for_global_review")
    if int(result.get("raw_source_item_count") or 0) != int(pack.get("raw_source_item_count") or 0):
        errors.append("raw_source_item_count_mismatch")
    if int(result.get("coverage_index_item_count") or 0) != int(pack.get("coverage_index_item_count") or 0):
        errors.append("coverage_index_item_count_mismatch")
    if result.get("coverage_index_complete") is not True:
        errors.append("coverage_index_not_complete")
    for key in ("possible_overlooked_signals", "fact_or_scope_warnings", "weighting_or_priority_warnings", "style_warnings"):
        for finding in result.get(key) or []:
            if not isinstance(finding, dict):
                continue
            for ref in finding.get("source_refs") or []:
                if str(ref) not in refs:
                    errors.append(f"unknown_source_ref:{ref}")
            if key == "fact_or_scope_warnings" and finding.get("evidence_basis") == "coverage_index":
                errors.append("fact_scope_uses_coverage_index_only")
    return errors


def generate_raw_flow_quality_check_artifact(
    *,
    config: dict[str, Any],
    items: list[dict[str, Any]],
    openclaw_digest: dict[str, Any],
    run_slug: str,
    check_config: dict[str, Any],
) -> dict[str, Any]:
    started_at = now_local()
    openclaw_bin = Path(config.get("openclaw_bin") or "openclaw").expanduser()
    model = str(check_config.get("model") or "volcengine-plan/glm-5.1")
    timeout = int(check_config.get("timeout") or 180)
    phase_label = str(openclaw_digest.get("phase_label") or run_slug)
    window = openclaw_digest.get("window") if isinstance(openclaw_digest.get("window"), dict) else {}

    source_input_file: Path | None = None
    raw_source_input_file = str(openclaw_digest.get("source_input_file") or "").strip()
    if raw_source_input_file:
        candidate = Path(raw_source_input_file).expanduser()
        if candidate.exists():
            source_input_file = candidate
    if source_input_file is None:
        source_input_file = write_market_qc_input_file(
            config=config,
            run_slug=run_slug,
            phase_label=phase_label,
            window=window,
            items=items,
            openclaw_digest=openclaw_digest,
        )
    source_input_sha256 = sha256_file(source_input_file)

    summary_file: Path | None = None
    raw_summary_file = str(openclaw_digest.get("openclaw_summary_file") or "").strip()
    if raw_summary_file:
        candidate = Path(raw_summary_file).expanduser()
        if candidate.exists():
            summary_file = candidate
    summary_file_sha256 = sha256_file(summary_file) if summary_file is not None else None

    evidence_limit = int(check_config.get("evidence_item_limit") or 65)
    evidence_excerpt_chars = int(check_config.get("evidence_excerpt_chars") or 260)
    audit_pack = build_market_qc_audit_pack(
        items=items,
        openclaw_digest=openclaw_digest,
        run_slug=run_slug,
        source_input_file=source_input_file,
        source_input_sha256=source_input_sha256,
        summary_file=summary_file,
        summary_file_sha256=summary_file_sha256,
        evidence_limit=evidence_limit,
        evidence_excerpt_chars=evidence_excerpt_chars,
    )
    audit_pack_file, audit_pack_sha256 = write_market_qc_audit_pack_artifact(
        config=config,
        run_slug=run_slug,
        pack=audit_pack,
    )

    prompt = build_ark_qc_artifact_prompt(
        phase_label=phase_label,
        window=window,
        pack=audit_pack,
    )
    prompt_file, prompt_sha256 = write_market_qc_prompt_artifact(config=config, run_slug=run_slug, prompt=prompt)
    native_message = (
        "Read the UTF-8 prompt file completely and execute it.\n"
        f"Prompt file: {prompt_file}\n"
        f"Prompt sha256: {prompt_sha256}\n"
        "Return only the strict JSON requested by that prompt. Do not summarize the file path."
    )
    cmd = [
        str(openclaw_bin),
        "agent",
        "--local",
        "--agent",
        str(check_config.get("agent") or "ark-review"),
        "--session-id",
        f"market-raw-flow-qc-{run_slug}",
        "--json",
        "--thinking",
        str(check_config.get("thinking") or "low"),
        "--timeout",
        str(timeout),
        "--model",
        model,
        "--message",
        native_message,
    ]
    try:
        completed = run_process_group(
            cmd,
            text=True,
            capture_output=True,
            timeout=timeout + 30,
            check=False,
        )
        completed_at = now_local()
        base = {
            "enabled": True,
            "attempted": True,
            "provider": "ark",
            "model": model,
            "model_started_at": started_at.isoformat(timespec="seconds"),
            "model_completed_at": completed_at.isoformat(timespec="seconds"),
            "model_duration_ms": int((completed_at - started_at).total_seconds() * 1000),
            "role": "post_generation_quality_check_only",
            "input_transport": "artifact_file_reference",
            "prompt_artifact_file": str(prompt_file),
            "prompt_artifact_sha256": prompt_sha256,
            "audit_pack_strategy": "coverage_index_plus_focused_evidence_v1",
            "audit_pack_file": str(audit_pack_file),
            "audit_pack_sha256": audit_pack_sha256,
            "source_input_file": str(source_input_file),
            "source_input_sha256": source_input_sha256,
            "summary_file": str(summary_file) if summary_file is not None else None,
            "summary_file_sha256": summary_file_sha256,
            "coverage": {
                "items": len(items),
                "chunks": 0,
                "max_item_chars": 0,
                "input_transport": "artifact_file_reference",
                "audit_pack_strategy": "coverage_index_plus_focused_evidence_v1",
                "coverage_index_item_count": audit_pack.get("coverage_index_item_count"),
                "evidence_item_count": audit_pack.get("evidence_item_count"),
                "evidence_pack_scope": audit_pack.get("evidence_pack_scope"),
                "audit_pack_file": str(audit_pack_file),
                "prompt_artifact_file": str(prompt_file),
                "source_input_file": str(source_input_file),
            },
        }
        if completed.returncode != 0:
            return {**base, "error": completed.stderr[-1200:] or completed.stdout[-1200:]}
        data = extract_json_object(completed.stdout)
        text = "\n".join(
            payload.get("text", "")
            for payload in data.get("payloads", [])
            if isinstance(payload, dict)
        )
        result = extract_json_object(text)
        validation_errors = validate_market_qc_advisory(result, audit_pack)
        style_warnings = result.get("style_warnings") or []
        if validation_errors and not style_warnings:
            style_warnings = ["后置质检未完成可验证的全量审计，已要求 GPT 复核。"]
        return {
            **base,
            "qc_validated": not validation_errors,
            "validation_errors": validation_errors,
            "possible_overlooked_signals": result.get("possible_overlooked_signals") or [],
            "fact_or_scope_warnings": result.get("fact_or_scope_warnings") or [],
            "weighting_or_priority_warnings": result.get("weighting_or_priority_warnings") or [],
            "style_warnings": style_warnings,
            "validation_notes": result.get("validation_notes") or [],
            "gpt_review_required": bool(result.get("gpt_review_required") or validation_errors),
            "usage": (data.get("meta") or {}).get("agentMeta", {}).get("usage"),
        }
    except Exception as exc:  # noqa: BLE001 - QC must not block publication
        completed_at = now_local()
        return {
            "enabled": True,
            "attempted": True,
            "provider": "ark",
            "model": model,
            "model_started_at": started_at.isoformat(timespec="seconds"),
            "model_completed_at": completed_at.isoformat(timespec="seconds"),
            "model_duration_ms": int((completed_at - started_at).total_seconds() * 1000),
            "input_transport": "artifact_file_reference",
            "prompt_artifact_file": str(prompt_file) if "prompt_file" in locals() else None,
            "audit_pack_strategy": "coverage_index_plus_focused_evidence_v1",
            "audit_pack_file": str(audit_pack_file) if "audit_pack_file" in locals() else None,
            "source_input_file": str(source_input_file) if "source_input_file" in locals() and source_input_file is not None else None,
            "error": str(exc),
        }


def build_openclaw_summary_file_prompt(
    *,
    phase_label: str,
    window: dict[str, Any],
    input_file: Path,
    item_count: int,
) -> str:
    phase_scope_rule = market_scope_rule_for_phase(phase_label)
    return (
        "你是日报正式写作 worker，使用 GPT 负责正式市场信息浸泡日报，不是质检模型，也不是财经媒体摘要器。\n"
        "正式写作必须基于完整原文，不得只看候选包、中间笔记、文件开头或抽样内容。\n"
        f"完整原始输入文件已由外层流水线生成并记录：{input_file}\n"
        f"文件中 items 数组共有 {item_count} 条原始消息。若调用方直接提供分片内容，你必须基于全部分片生成正式信息汇总；不要裁剪、不要跳过长正文、不要用摘要替代原文，也不要自行用工具分页读取该文件。\n"
        "写作规则：\n"
        "1. 只整理事实和消息含义，不预测涨跌，不给买卖建议。\n"
        "2. 不要按栏目拆分，不要写编号列表，不要输出信源、原文编号、状态、时间归类、信息类型等工程字段。\n"
        "3. title 只作归类辅助，content 是正文材料；content_quality 为 title_only/title_like 的消息只能当短快讯处理，不得扩写。\n"
        "4. 窗口时间只用于后台筛选，禁止在正文里说明本次窗口、补发/重跑、没有混入某时间之后新闻等工程口径。\n"
        f"5. {phase_scope_rule}\n"
        "6. 全面但降噪：高频重复只合并写一次；低频但有订单、审批、政策、监管、价格异动、产业链数据、出海验证的信息要进入判断。\n"
        "7. 每段必须包含具体主体、关键数字或明确事件细节；判断只能来自原文中相邻事实的直接关系。\n"
        "8. 后台覆盖类别只是检查工具，不是前台填空模板；不要为了覆盖而塞低价值信息。\n"
        "9. 禁止空泛或过度分析句，例如“后续值得关注”“整体来看”“提供线索”“需要继续观察”“共同指向”“出现错位”“对冲不确定性”“结构活跃”。\n"
        "10. 禁止后台写作/批改口吻，例如“不该写成”“不能简单归纳为”“后续简报应”“真正要保留的判断是”。\n"
        "11. 返回严格 JSON，不要 Markdown，不要代码块。\n"
        "JSON 格式：{\"summary_paragraphs\":[\"...\",\"...\"],\"observation\":{\"repeated_words\":[\"...\"],\"multi_day_themes\":[\"...\"],\"watch_next\":[\"...\"]}}\n"
        f"阶段：{phase_label}\n"
        f"窗口：{window.get('start') or '无'} 至 {window.get('end') or '无'}\n"
    )


def chunk_text(text: str, max_chars: int) -> list[str]:
    if max_chars <= 0:
        return [text]
    return [text[i : i + max_chars] for i in range(0, len(text), max_chars)] or [""]


def build_openclaw_chunked_messages(
    *,
    phase_label: str,
    window: dict[str, Any],
    all_items: list[dict[str, Any]],
    max_item_chars: int,
    max_model_items: int = 0,
    chunk_chars: int,
    raw_flow_classification: dict[str, Any] | None = None,
) -> list[str]:
    if max_model_items > 0:
        all_items = all_items[:max_model_items]
    payload_items = build_openclaw_payload_items(
        all_items=all_items,
        max_item_chars=max_item_chars,
    )
    payload_text = json.dumps(payload_items, ensure_ascii=False)
    chunks = chunk_text(payload_text, chunk_chars)
    phase_scope_rule = market_scope_rule_short_for_phase(phase_label)
    serials = [str(item.get("serial") or "") for item in payload_items if item.get("serial") is not None]
    messages = [
        (
            "你是市场信息流研判助手。任务不是财经媒体摘要，也不是交易建议，而是全面吸收阶段性信息流，过滤噪音，提炼对市场理解有帮助的洞察。\n"
            "接下来我会分片发送原始消息 JSON。所有分片合起来就是本轮完整输入；这不是抽样、不是裁剪、不是候选包。请先只确认已接收，不要生成日报，也不要调用文件读取工具。\n"
            "最终要求：生成前后连贯、逻辑清晰、兼顾覆盖与降噪的阶段性市场洞察；不要按栏目拆分，不要写编号列表；"
            "输入里的content是正文材料，title只作归类辅助；content_quality为title_only/title_like的消息按短快讯处理，不要扩写；"
            "窗口时间只用于后台筛选，禁止在正文里说明本次窗口、补发/重跑、没有混入某时间之后的新闻等工程口径；"
            f"{phase_scope_rule}"
            "不要教条化：覆盖类别只是后台检查工具，不是前台填空模板；没有增量的类别可以不写，低价值信息不能为凑覆盖而进入总览；"
            "全面但降噪：高频重复只合并写一次；低频但有订单、审批、政策、监管、价格异动、产业链数据、出海验证的信息要进入候选池；"
            "每段必须包含具体主体、关键数字或明确事件细节；重复和同主题信息要合并出互相印证、边际变化、分歧或同一事件不同侧面；"
            "possible_duplicates 只是疑似语义重复提示；可合并表达但不能忽略差异；"
            "可以有市场洞察，但判断只能来自原文中相邻事实的直接关系，不能从阶段信息流上升为全天结论；"
            "优先使用“这一阶段的信息流显示”“截至本阶段”“同时出现”“相互印证”“仍需区分”“尚不能推出”等低强度表达；"
            "禁止空泛或过度分析句，例如“后续值得关注”“整体来看”“提供线索”“需要继续观察”“共同指向”“出现错位”“对冲不确定性”“结构活跃”；"
            "禁止后台写作/批改口吻，例如“不该写成”“不能简单归纳为”“后续简报应”“真正要保留的判断是”；"
            "如果只能看到并列事实，就写成并列事实，不要强行提炼大主题；必须基于全部分片完成全覆盖吸收，不能只看开头、结尾或高频主题；返回严格 JSON，不要 Markdown。\n"
            'JSON格式：{"summary_paragraphs":["...","..."],"observation":'
            '{"repeated_words":["..."],"multi_day_themes":["..."],"watch_next":["..."]},"coverage_check":{"input_item_count":数字,"used_all_chunks":true,"notes":"..."}}\n'
            f"阶段：{phase_label}\n"
            f"窗口：{window.get('start') or '无'} 至 {window.get('end') or '无'}\n"
            "中性分类结果只供材料定位，不代表价值判断；正式取舍由你基于原始信息决定：\n"
            f"{json.dumps(raw_flow_classification or {}, ensure_ascii=False)}\n"
            f"原始消息总数：{len(payload_items)}，分片数：{len(chunks)}，serial范围/集合：{','.join(serials[:80])}{'...' if len(serials) > 80 else ''}"
        )
    ]
    for index, chunk in enumerate(chunks, 1):
        messages.append(f"原始消息分片 {index}/{len(chunks)}：\n{chunk}")
    messages.append(
        "以上原始消息已经发送完毕。现在请根据全部分片生成最终 JSON。"
        "只返回 JSON 对象，不要 Markdown，不要代码块。"
        f"完整性要求：本轮共有 {len(payload_items)} 条原始消息、{len(chunks)} 个分片；你必须基于全部分片生成，不得抽样、不得裁剪、不得用局部信息代替完整信息。"
        "质量要求：默认 4-6 个自然段，每段 180-360 个中文字符；段落数是弹性建议，不是硬性上限，信息密度确实需要时可以超过 6 段；每段至少包含两个具体事实颗粒，不能写空泛综述，也不能把阶段信息流拔高为全天判断。"
    )
    return messages


def write_summary_input_coverage_manifest(
    *,
    config: dict[str, Any],
    run_slug: str,
    all_items: list[dict[str, Any]],
    messages: list[str],
    source_input_file: Path | None,
) -> Path:
    """Write a local audit artifact proving the model transport covered every input item.

    This is not a quality downgrade.  It records that the outer pipeline, rather
    than the agent, split the complete input into deterministic chunks so the
    writer does not waste the run budget paging through a file with tools.
    """
    output_dir = Path(config.get("output_dir") or "~/.openclaw/workspace/market-immersion").expanduser()
    target_dir = output_dir / "model-inputs" / safe_name(run_slug)
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / "summary_input_coverage_manifest.json"
    serials = [item.get("serial") for item in all_items]
    payload = {
        "schema": "openclaw.market_immersion.summary_input_coverage.v1",
        "run_slug": run_slug,
        "source_input_file": str(source_input_file) if source_input_file else None,
        "input_item_count": len(all_items),
        "input_serials": serials,
        "transport": "full_coverage_chunked_messages",
        "message_count": len(messages),
        "message_chars": [len(message) for message in messages],
        "no_input_truncation": True,
        "no_degraded_output": True,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def generate_openclaw_digest(
    *,
    config: dict[str, Any],
    phase: str,
    phase_label: str,
    window: dict[str, Any],
    all_items: list[dict[str, Any]],
    run_slug: str,
    raw_flow_classification: dict[str, Any] | None = None,
    use_model: bool = True,
) -> dict[str, Any]:
    summary_config = config.get("openclaw_summary") or {}
    if not summary_config.get("enabled", False):
        return {"enabled": False, "attempted": False}
    if phase == "smoke" and not summary_config.get("summarize_smoke", False):
        return {"enabled": True, "attempted": False, "reason": "smoke summary disabled"}
    if not all_items:
        return {"enabled": True, "attempted": False, "reason": "no items"}
    if not use_model:
        fallback = [summary_sentence(item).rstrip("。") for item in all_items[:6]]
        return {
            "enabled": True,
            "attempted": False,
            "reason": "model disabled for dry-run/smoke",
            "summary_paragraphs": ["。".join(sentence for sentence in fallback if sentence) + "。"] if fallback else [],
        }


    openclaw_bin = Path(config.get("openclaw_bin") or "openclaw").expanduser()
    max_message_chars = int(summary_config.get("max_message_chars") or 60000)
    source_input_file: Path | None = None
    coverage_manifest_file: Path | None = None
    messages: list[str] | None = None
    if str(summary_config.get("input_mode") or "full_file") == "full_file":
        source_input_file = write_market_model_input_file(
            config=config,
            run_slug=run_slug,
            phase_label=phase_label,
            window=window,
            all_items=all_items,
            raw_flow_classification=raw_flow_classification,
        )
        # Do not ask the writer agent to page through the large JSON via read()
        # tools.  The outer pipeline already has the full input in memory, so it
        # sends deterministic full-coverage chunks and records a local coverage
        # manifest.  This preserves input completeness while avoiding the tool
        # read loop that caused summary_timeout.
        messages = build_openclaw_chunked_messages(
            phase_label=phase_label,
            window=window,
            all_items=all_items,
            max_item_chars=0,
            max_model_items=0,
            chunk_chars=max_message_chars,
            raw_flow_classification=raw_flow_classification,
        )
        coverage_manifest_file = write_summary_input_coverage_manifest(
            config=config,
            run_slug=run_slug,
            all_items=all_items,
            messages=messages,
            source_input_file=source_input_file,
        )
    else:
        prompt = build_openclaw_summary_prompt(
            phase_label=phase_label,
            window=window,
            all_items=all_items,
            max_item_chars=int(summary_config.get("max_item_chars") or 0),
            max_model_items=int(summary_config.get("max_model_items") or 0),
            raw_flow_classification=raw_flow_classification,
        )
    started = now_local().isoformat(timespec="seconds")
    model_started_at = now_local()
    model = str(summary_config.get("model") or "openai-codex/gpt-5.5")
    agent = str(summary_config.get("agent") or "daily-writer")
    retries = int(summary_config.get("retries") or 3)
    timeout = int(summary_config.get("timeout") or 300)
    last_error = ""
    invocation_slug = now_local().strftime("%Y%m%d%H%M%S")
    for attempt in range(1, retries + 1):
        # Use a fresh agent session for each digest invocation.  Reusing the
        # old run_slug-only session can inherit a previous failed tool-reading
        # trajectory and pollute a corrected resume run.
        session_id = f"market-immersion-summary-{run_slug}-{invocation_slug}-{attempt}"
        base_cmd = [
            str(openclaw_bin),
            "agent",
            "--local",
            "--agent",
            agent,
            "--session-id",
            session_id,
            "--json",
            "--thinking",
            str(summary_config.get("thinking") or "high"),
            "--timeout",
            str(timeout),
            "--model",
            model,
        ]
        if messages is None:
            if len(prompt) <= max_message_chars:
                messages = [prompt]
            else:
                messages = build_openclaw_chunked_messages(
                    phase_label=phase_label,
                    window=window,
                    all_items=all_items,
                    max_item_chars=int(summary_config.get("max_item_chars") or 0),
                    max_model_items=int(summary_config.get("max_model_items") or 0),
                    chunk_chars=max_message_chars,
                    raw_flow_classification=raw_flow_classification,
                )
                coverage_manifest_file = write_summary_input_coverage_manifest(
                    config=config,
                    run_slug=run_slug,
                    all_items=all_items,
                    messages=messages,
                    source_input_file=source_input_file,
                )
        if messages is None:
            messages = build_openclaw_chunked_messages(
                phase_label=phase_label,
                window=window,
                all_items=all_items,
                max_item_chars=int(summary_config.get("max_item_chars") or 0),
                max_model_items=int(summary_config.get("max_model_items") or 0),
                chunk_chars=max_message_chars,
                raw_flow_classification=raw_flow_classification,
            )
        completed = None
        # Direct Provider V0 is stateless per call.  The legacy embedded-agent
        # path accepted chunked messages as a session conversation, but sending
        # those chunks as separate direct-provider calls means the final call
        # only sees the short "generate now" instruction and returns an empty
        # digest.  Preserve the complete input by combining the deterministic
        # chunks into one provider prompt for the digest invocation.
        combined_message = "\n\n---\n\n".join(messages)
        prompt_artifact_file: Path | None = None
        prompt_artifact_sha256: str | None = None
        prompt_transport = "argv_message"
        native_prompt_threshold = int(summary_config.get("native_prompt_artifact_threshold_chars") or 120000)
        if len(combined_message) > native_prompt_threshold:
            prompt_artifact_file, prompt_artifact_sha256 = write_market_model_prompt_artifact(
                config=config,
                run_slug=run_slug,
                attempt=attempt,
                prompt=combined_message,
            )
            prompt_transport = "artifact_file_reference"
            native_message = (
                "你是日报正式写作 worker。外层流水线已将本次正式市场摘要的完整原始 prompt "
                f"逐字写入本地文件：{prompt_artifact_file}\n"
                f"该文件 UTF-8 文本的 sha256 是：{prompt_artifact_sha256}\n"
                "请先用工具完整读取该文件，确认内容后严格执行文件内的原始指令并返回同一 JSON 输出契约。"
                "不得省略、压缩、改写或抽样该文件内容；不得输出 Markdown。"
            )
        else:
            native_message = combined_message
        cmd = [*base_cmd, "--message", native_message]
        try:
            completed = run_openclaw_model_call(
                cmd,
                text=True,
                capture_output=True,
                timeout=timeout + 30,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            completed_at = now_local()
            return {
                "enabled": True,
                "attempted": True,
                "started_at": started,
                "model_started_at": model_started_at.isoformat(timespec="seconds"),
                "model_completed_at": completed_at.isoformat(timespec="seconds"),
                "model_duration_ms": int((completed_at - model_started_at).total_seconds() * 1000),
                "model": model,
                "agent": agent,
                "source_input_file": str(source_input_file) if source_input_file else None,
                "coverage_manifest_file": str(coverage_manifest_file) if coverage_manifest_file else None,
                "source_item_count": len(all_items),
                "input_transport": "full_coverage_chunked_messages" if coverage_manifest_file else "single_message_or_file_prompt",
                "message_count": len(messages),
                "attempts": attempt,
                "summary_status": "timeout",
                "failure_type": "summary_timeout",
                "error": f"summary generation timed out after {timeout + 30}s",
                "stderr": str(exc.stderr or "")[-4000:],
                "stdout": str(exc.output or "")[-4000:],
            }
        if completed is None:
            last_error = "OpenClaw summary produced no turn"
            transient_retry_sleep(last_error, attempt)
            continue
        if completed.returncode != 0:
            last_error = completed.stderr[-2000:] or completed.stdout[-2000:]
            if looks_like_gateway_timeout(last_error):
                completed_at = now_local()
                return {
                    "enabled": True,
                    "attempted": True,
                    "started_at": started,
                    "model_started_at": model_started_at.isoformat(timespec="seconds"),
                    "model_completed_at": completed_at.isoformat(timespec="seconds"),
                    "model_duration_ms": int((completed_at - model_started_at).total_seconds() * 1000),
                    "model": model,
                    "agent": agent,
                    "source_input_file": str(source_input_file) if source_input_file else None,
                    "coverage_manifest_file": str(coverage_manifest_file) if coverage_manifest_file else None,
                    "source_item_count": len(all_items),
                    "input_transport": "full_coverage_chunked_messages" if coverage_manifest_file else "single_message_or_file_prompt",
                    "message_count": len(messages),
                    "attempts": attempt,
                    "summary_status": "gateway_unavailable",
                    "failure_type": "gateway_unavailable",
                    "error": "gateway timeout while resolving active gateway snapshot",
                    "stderr": completed.stderr[-4000:],
                    "stdout": completed.stdout[-4000:],
                }
            transient_retry_sleep(last_error, attempt)
            continue
        try:
            data = extract_json_object(completed.stdout)
            agent_meta = ((data.get("meta") or {}).get("agentMeta") or {})
            direct_meta = (agent_meta.get("directProvider") or {})
            execution_trace = ((data.get("meta") or {}).get("executionTrace") or {})
            text = "\n".join(
                payload.get("text", "")
                for payload in data.get("payloads", [])
                if isinstance(payload, dict)
            )
            digest = extract_json_object(text)
            summary_paragraphs = normalize_summary_paragraphs(digest)
            quality_warnings = summary_quality_warnings(
                summary_paragraphs,
                all_items,
                phase_label=phase_label,
            )
            if quality_warnings and attempt < retries:
                last_error = "low quality summary: " + "; ".join(quality_warnings)
                transient_retry_sleep(last_error, attempt)
                continue
            completed_at = now_local()
            return {
                "enabled": True,
                "attempted": True,
                "started_at": started,
                "model_started_at": model_started_at.isoformat(timespec="seconds"),
                "model_completed_at": completed_at.isoformat(timespec="seconds"),
                "model_duration_ms": int((completed_at - model_started_at).total_seconds() * 1000),
                "requested_model": model,
                "model": model,
                "resolved_lane": "direct_provider" if direct_meta else "native_openclaw_gateway",
                "actual_provider_profile": direct_meta.get("provider_profile") or agent_meta.get("provider") or execution_trace.get("winnerProvider"),
                "actual_model": direct_meta.get("model") or agent_meta.get("model") or execution_trace.get("winnerModel") or model,
                "direct_provider_compat_used": bool(direct_meta),
                "fallback_used": bool(execution_trace.get("fallbackUsed")) if execution_trace else None,
                "runner": execution_trace.get("runner"),
                "final_body_provenance": "native_openclaw_gateway_gpt" if not direct_meta else "direct_provider_non_gpt_blocker",
                "agent": agent,
                "source_input_file": str(source_input_file) if source_input_file else None,
                "coverage_manifest_file": str(coverage_manifest_file) if coverage_manifest_file else None,
                "prompt_artifact_file": str(prompt_artifact_file) if prompt_artifact_file else None,
                "prompt_artifact_sha256": prompt_artifact_sha256,
                "source_item_count": len(all_items),
                "input_transport": prompt_transport,
                "message_count": len(messages),
                "attempts": attempt,
                "summary_paragraphs": summary_paragraphs,
                "quality_warnings": quality_warnings,
                "sections": digest.get("sections") or {},
                "observation": digest.get("observation") or {},
                "coverage_check": digest.get("coverage_check") or {},
                "usage": (data.get("meta") or {}).get("agentMeta", {}).get("usage"),
            }
        except Exception as exc:  # noqa: BLE001 - retry malformed model output
            last_error = str(exc)
            time.sleep(5)
    completed_at = now_local()
    return {
        "enabled": True,
        "attempted": True,
        "started_at": started,
        "model_started_at": model_started_at.isoformat(timespec="seconds"),
        "model_completed_at": completed_at.isoformat(timespec="seconds"),
        "model_duration_ms": int((completed_at - model_started_at).total_seconds() * 1000),
        "model": model,
        "agent": agent,
        "source_input_file": str(source_input_file) if source_input_file else None,
        "coverage_manifest_file": str(coverage_manifest_file) if coverage_manifest_file else None,
        "source_item_count": len(all_items),
        "input_transport": "full_coverage_chunked_messages" if coverage_manifest_file else "single_message_or_file_prompt",
        "message_count": len(messages or []),
        "attempts": retries,
        "error": last_error or "OpenClaw summary failed",
    }


def normalize_summary_paragraphs(digest: dict[str, Any]) -> list[str]:
    paragraphs = digest.get("summary_paragraphs")
    if isinstance(paragraphs, str):
        paragraphs = [paragraphs]
    if isinstance(paragraphs, list):
        cleaned = [" ".join(str(text).split()) for text in paragraphs if str(text).strip()]
        if cleaned:
            return cleaned

    summary = digest.get("summary")
    if isinstance(summary, str) and summary.strip():
        return [part.strip() for part in re.split(r"\n{2,}", summary) if part.strip()]

    sections = digest.get("sections") or {}
    if isinstance(sections, dict):
        flattened: list[str] = []
        for rows in sections.values():
            if not isinstance(rows, list):
                continue
            for row in rows:
                if isinstance(row, dict):
                    text = " ".join(str(row.get("text") or "").split())
                    if text:
                        flattened.append(text)
                elif str(row).strip():
                    flattened.append(" ".join(str(row).split()))
        if flattened:
            return ["。".join(text.rstrip("。") for text in flattened[:8]) + "。"]
    return []


def summary_quality_warnings(
    paragraphs: list[str],
    all_items: list[dict[str, Any]],
    *,
    phase_label: str = "",
) -> list[str]:
    if not paragraphs:
        return ["empty summary"]

    text = "".join(paragraphs)
    warnings: list[str] = []
    if len(all_items) >= 30 and len(paragraphs) < 4:
        warnings.append("summary has too few paragraphs for market insight overview")
    if len(paragraphs) > 8:
        warnings.append("summary may be too fragmented; paragraph count exceeds flexible range")
    if len(all_items) >= 8 and len(text) < 480:
        warnings.append("summary too short for item volume")
    if len(all_items) >= 8 and not re.search(r"\d", text):
        warnings.append("summary has no numeric detail")

    meta_phrases = [
        "本次窗口",
        "早报窗口",
        "晨报窗口",
        "午报窗口",
        "收盘报窗口",
        "晚报窗口",
        "补发",
        "重跑",
        "没有混入",
        "未混入",
        "窗口固定",
    ]
    meta_hits = [phrase for phrase in meta_phrases if phrase in text]
    if meta_hits:
        warnings.append("summary leaks backend window/delivery wording: " + "、".join(meta_hits[:5]))

    backend_phrases = [
        "不该写成",
        "不能简单归纳为",
        "后续简报应",
        "真正要保留的判断是",
        "财经编辑",
        "作为编辑",
        "摘要应该",
        "原稿",
        "上一版",
        "本次修订",
        "质检",
        "Ark",
        "Kimi",
        "GPT review",
    ]
    backend_hits = [phrase for phrase in backend_phrases if phrase in text]
    backend_context_patterns = [
        r"(原稿|上一版|前稿|旧稿).{0,24}(低估|遗漏|未覆盖|没有覆盖|应补|需要补)",
        r"(被|为)(原稿|上一版|前稿|旧稿).{0,12}(低估|遗漏)",
        r"(应|需要|建议).{0,12}(补入|加入|写入|纳入).{0,12}(正文|终稿|稿件|报告|段落)",
        r"(质检|Ark|Kimi|GPT review).{0,24}(认为|建议|指出|采纳|拒绝|修订)",
        r"(本次修订|修订目标|采纳项|版本对比|内部改稿)",
    ]
    backend_hits.extend(
        match.group(0)
        for pattern in backend_context_patterns
        for match in re.finditer(pattern, text, flags=re.IGNORECASE)
    )
    if backend_hits:
        warnings.append("summary leaks drafting/meta commentary: " + "、".join(backend_hits[:5]))

    if "夜" not in str(phase_label) and "晚" not in str(phase_label) and "复盘" not in str(phase_label):
        all_day_phrases = [
            "今天市场",
            "全天判断",
            "全天来看",
            "全日",
            "今天不是",
            "今天是",
            "这一整天",
        ]
        all_day_hits = [phrase for phrase in all_day_phrases if phrase in text]
        if all_day_hits:
            warnings.append("stage summary makes all-day judgment: " + "、".join(all_day_hits[:5]))

    generic_phrases = [
        "整体来看",
        "总体来看",
        "值得关注",
        "后续关注",
        "继续观察",
        "提供线索",
        "存在扰动",
        "需要放在",
        "同一背景",
        "不适合拆成",
        "共同指向",
        "出现错位",
        "对冲不确定性",
        "结构活跃",
    ]
    generic_hits = [phrase for phrase in generic_phrases if phrase in text]
    if len(generic_hits) >= 3:
        warnings.append("summary uses too many generic phrases: " + "、".join(generic_hits[:5]))

    detail_terms: set[str] = set()
    for item in all_items[:80]:
        source = str(item.get("source") or "").strip()
        if source:
            detail_terms.add(source)
        title = str(item.get("title") or "")
        for term in re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{3,12}", title):
            if len(term) >= 3 and not term.isdigit():
                detail_terms.add(term)
    concrete_hits = sum(1 for term in detail_terms if term and term in text)
    if len(all_items) >= 12 and concrete_hits < 4:
        warnings.append("summary mentions too few concrete names")
    return warnings


def append_summary_digest(
    lines: list[str],
    openclaw_digest: dict[str, Any],
    all_items: list[dict[str, Any]],
) -> None:
    lines.append("## 信息汇总")
    lines.append("")

    paragraphs = openclaw_digest.get("summary_paragraphs") or []
    if isinstance(paragraphs, str):
        paragraphs = [paragraphs]
    cleaned = [" ".join(str(text).split()) for text in paragraphs if str(text).strip()]
    if not cleaned:
        fallback_sentences = [summary_sentence(item).rstrip("。") for item in all_items[:8]]
        cleaned = ["。".join(sentence for sentence in fallback_sentences if sentence) + "。"] if fallback_sentences else []

    if not cleaned:
        lines.append("本轮暂无可整理信息。")
        lines.append("")
        return

    for paragraph in cleaned:
        lines.append(paragraph)
        lines.append("")


def append_raw_message_flow(lines: list[str], items: list[dict[str, Any]]) -> None:
    while len(lines) >= 2 and not lines[-1].strip():
        if lines[-2].strip() == "## 原始消息流":
            return
        break
    lines.append("## 原始消息流")
    lines.append("")
    if not items:
        lines.append("- 本轮暂无可解析原始消息。")
        lines.append("")
        return
    for item in items:
        title = " ".join(str(item.get("title") or "").split())
        content = " ".join(str(item.get("content") or "").split())
        source = str(item.get("source") or "").strip()
        if source == "新浪财经" and title and (
            title == content or (len(title) >= 50 and content.startswith(title))
        ):
            # Legacy/fallback Sina rows often had no title and used the first
            # characters of the body as title. Render them as no-title flashes.
            title = ""
        display_title = f"{item['serial']}. {title}" if title else str(item["serial"])
        body = display_body_without_title(title=title, content=content)
        meta = raw_message_meta_line(item)
        lines.append(f"### {display_title}")
        lines.append("")
        if body:
            lines.append(body)
            lines.append("")
        if meta:
            lines.append(f"{meta}")
            lines.append("")
        lines.append("")


def append_raw_flow_classification(
    lines: list[str],
    items: list[dict[str, Any]],
    raw_flow_classification: dict[str, Any],
) -> None:
    lines.append("## 原始消息流分类")
    lines.append("")
    if not items:
        lines.append("- 本轮暂无可分类原始消息。")
        lines.append("")
        return
    by_serial = {str(item.get("serial") or ""): item for item in items}
    categories = raw_flow_classification.get("categories") or []
    if not isinstance(categories, list) or not categories:
        categories = deterministic_raw_flow_classification(items).get("categories") or []
    for category in categories:
        if not isinstance(category, dict):
            continue
        name = str(category.get("name") or "其他").strip() or "其他"
        serials = [str(serial) for serial in category.get("serials") or [] if str(serial) in by_serial]
        if not serials:
            continue
        lines.append(f"### {name}")
        lines.append("")
        for serial in serials[:80]:
            item = by_serial[serial]
            title = " ".join(str(item.get("title") or "[无标题]").split())
            meta = raw_message_meta_line(item)
            lines.append(f"- {serial}. {title}" + (f"（{meta}）" if meta else ""))
        lines.append("")
    possible_duplicates = raw_flow_classification.get("possible_duplicates") or []
    if isinstance(possible_duplicates, list) and possible_duplicates:
        lines.append("### 疑似同一事件组")
        lines.append("")
        for index, group in enumerate(possible_duplicates[:30], 1):
            if isinstance(group, dict):
                serials = [str(serial) for serial in group.get("serials") or [] if str(serial) in by_serial]
                reason = " ".join(str(group.get("reason") or "").split())
            elif isinstance(group, list):
                serials = [str(serial) for serial in group if str(serial) in by_serial]
                reason = ""
            else:
                continue
            if len(serials) < 2:
                continue
            label = "、".join(serials)
            lines.append(f"- 组{index}：原文 {label}" + (f"；理由：{reason}" if reason else ""))
        lines.append("")
    lines.append("> 分类仅用于原始信息流整理，不代表高低价值、主线或投资含义判断。")
    lines.append("> 疑似同一事件组仅供 GPT 写作时合并表达参考，不会删除原始消息。")
    lines.append("")


def write_markdown_report(
    *,
    path: Path,
    phase: str,
    phase_label: str,
    run_started: str,
    window: dict[str, Any],
    entries: list[dict[str, Any]],
    manifest_path: Path,
    all_items: list[dict[str, Any]],
    serial_by_item: dict[tuple[str, int, str, str], str],
    openclaw_digest: dict[str, Any],
    raw_flow_classification: dict[str, Any],
) -> None:
    lines: list[str] = []

    append_summary_digest(lines, openclaw_digest, all_items)
    append_source_health_section(lines, entries)
    if raw_flow_classification.get("enabled") is not False:
        append_raw_flow_classification(lines, all_items, raw_flow_classification)
    append_raw_message_flow(lines, all_items)
    lines = collapse_duplicate_section_headings(lines)
    path.write_text("\n".join(lines), encoding="utf-8")


def should_block_empty_market_report(*, phase: str, config: dict[str, Any], all_items: list[dict[str, Any]]) -> bool:
    """Return True when a scheduled market report has no usable content.

    A successful Notion/Telegram delivery is not a successful market brief when
    the report body is only the deterministic "no items" placeholder.  Smoke
    runs remain exempt, and an explicit config escape hatch is available for a
    future consciously-labelled placeholder mode; the default production
    behavior is to block publication and let the task retry / surface failure.
    """
    if phase == "smoke":
        return False
    if all_items:
        return False
    pipeline_config = config.get("pipeline") or {}
    return not bool(pipeline_config.get("allow_empty_publication", False))


def append_source_health_section(lines: list[str], entries: list[dict[str, Any]]) -> None:
    # Source-health diagnostics are for manifests/operator review. Do not expose
    # them in user-facing daily reports unless explicitly requested for debugging.
    if os.environ.get("OPENCLAW_MARKET_REPORT_SOURCE_HEALTH") != "1":
        return
    reports = [e.get("feed_health") for e in entries if isinstance(e.get("feed_health"), dict)]
    problem_sources: list[dict[str, Any]] = []
    for report in reports:
        for source in report.get("sources") or []:
            if isinstance(source, dict) and source.get("status") != "ok":
                problem_sources.append(source)
    if not problem_sources:
        return
    lines.append("")
    lines.append("## 数据源健康检查")
    lines.append("")
    lines.append(
        "以下为本次采集未完全正常的数据源。该信息只用于定位和人工决策；未获明确批准时，不应自动发布降级版。"
    )
    lines.append("")
    for source in problem_sources:
        status_label = "失败" if source.get("status") == "error" else "窗口未覆盖完整"
        alternatives = "、".join(source.get("alternatives") or []) or "暂无明确替代源"
        verified = "、".join(source.get("verified_backup_candidates") or []) or "暂无已验证备用接口"
        issue = source.get("error") or source.get("issue") or "未知原因"
        lines.append(
            f"- {source.get('source')}：{status_label}；items={source.get('item_count')}; "
            f"newest={source.get('newest_time') or '-'}; oldest={source.get('oldest_time') or '-'}；"
            f"原因：{issue}；候选替代源：{alternatives}；已验证备用接口：{verified}。"
        )


def collapse_duplicate_section_headings(lines: list[str]) -> list[str]:
    cleaned: list[str] = []
    last_heading = ""
    only_blank_since_heading = False
    for line in lines:
        if line.startswith("## "):
            if only_blank_since_heading and line == last_heading:
                continue
            last_heading = line
            only_blank_since_heading = True
            cleaned.append(line)
            continue
        if line.strip():
            only_blank_since_heading = False
        cleaned.append(line)
    return cleaned


def notion_text(text: str) -> list[dict[str, Any]]:
    text = str(text)
    if not text:
        text = " "
    return [
        {"type": "text", "text": {"content": text[i : i + 1900]}}
        for i in range(0, len(text), 1900)
    ]


def notion_block(kind: str, text: str) -> dict[str, Any]:
    if kind in {"heading_1", "heading_2", "heading_3", "paragraph", "quote", "bulleted_list_item"}:
        return {
            "object": "block",
            "type": kind,
            kind: {"rich_text": notion_text(text)},
        }
    if kind == "code":
        return {
            "object": "block",
            "type": "code",
            "code": {"rich_text": notion_text(text), "language": "plain text"},
        }
    raise ValueError(f"Unsupported Notion block kind: {kind}")


def markdown_to_notion_blocks(markdown: str) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    code_lines: list[str] | None = None
    for line in markdown.splitlines():
        if line.startswith("```"):
            if code_lines is None:
                code_lines = []
            else:
                code_text = "\n".join(code_lines) or " "
                for i in range(0, len(code_text), 18000):
                    blocks.append(notion_block("code", code_text[i : i + 18000]))
                code_lines = None
            continue
        if code_lines is not None:
            code_lines.append(line)
            continue
        if not line.strip():
            continue
        if line.startswith("# "):
            blocks.append(notion_block("heading_1", line[2:].strip()))
        elif line.startswith("## "):
            blocks.append(notion_block("heading_2", line[3:].strip()))
        elif line.startswith("### "):
            blocks.append(notion_block("heading_3", line[4:].strip()))
        elif line.startswith("#### "):
            blocks.append(notion_block("heading_3", line[5:].strip()))
        elif line.startswith("> "):
            blocks.append(notion_block("quote", line[2:].strip()))
        elif line.startswith("- "):
            blocks.append(notion_block("bulleted_list_item", line[2:].strip()))
        else:
            blocks.append(notion_block("paragraph", line.strip()))
    if code_lines is not None:
        code_text = "\n".join(code_lines) or " "
        for i in range(0, len(code_text), 18000):
            blocks.append(notion_block("code", code_text[i : i + 18000]))
    return blocks


def notion_request(
    *,
    method: str,
    url: str,
    token: str,
    payload: dict[str, Any] | None = None,
    timeout: int,
) -> dict[str, Any]:
    body = None
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": "2022-06-28",
    }
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    last_error: Exception | None = None
    for attempt in range(5):
        request = urllib.request.Request(
            url,
            data=body,
            method=method,
            headers=headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            last_error = RuntimeError(f"Notion API {exc.code}: {details}")
            if exc.code == 429 or 500 <= exc.code <= 599:
                retry_after = exc.headers.get("Retry-After")
                try:
                    delay = float(retry_after) if retry_after else min(2**attempt, 15)
                except ValueError:
                    delay = min(2**attempt, 15)
                time.sleep(delay)
                continue
            raise last_error from exc
        except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
            last_error = RuntimeError(f"Notion transport error: {exc}")
            time.sleep(min(2**attempt, 15))
            continue
    assert last_error is not None
    raise last_error


def list_notion_children(block_id: str, token: str, timeout: int) -> list[dict[str, Any]]:
    children: list[dict[str, Any]] = []
    cursor = ""
    while True:
        url = f"https://api.notion.com/v1/blocks/{block_id}/children?page_size=100"
        if cursor:
            url += "&start_cursor=" + cursor
        payload = notion_request(method="GET", url=url, token=token, timeout=timeout)
        children.extend(payload.get("results") or [])
        if not payload.get("has_more"):
            return children
        cursor = payload.get("next_cursor") or ""


def list_notion_child_pages(parent_page_id: str, token: str, timeout: int) -> list[dict[str, Any]]:
    return [child for child in list_notion_children(parent_page_id, token, timeout) if child.get("type") == "child_page"]


def replace_notion_page_children(page_id: str, token: str, blocks: list[dict[str, Any]], timeout: int) -> None:
    for child in list_notion_children(page_id, token, timeout):
        child_id = child.get("id")
        if not child_id:
            continue
        try:
            notion_request(
                method="PATCH",
                url=f"https://api.notion.com/v1/blocks/{child_id}",
                token=token,
                timeout=timeout,
                payload={"archived": True},
            )
        except RuntimeError as exc:
            # Notion may return recently archived children during pagination. Treat
            # those as already deleted so reentrant updates do not fail midway.
            if "Can't edit block that is archived" not in str(exc):
                raise
    for i in range(0, len(blocks), 80):
        notion_request(
            method="PATCH",
            url=f"https://api.notion.com/v1/blocks/{page_id}/children",
            token=token,
            timeout=timeout,
            payload={"children": blocks[i : i + 80]},
        )


def find_notion_child_page_by_title(
    *,
    parent_page_id: str,
    token: str,
    title: str,
    timeout: int,
) -> dict[str, Any] | None:
    wanted = normalize_ws(title)
    for child in list_notion_child_pages(parent_page_id, token, timeout):
        child_title = normalize_ws((child.get("child_page") or {}).get("title") or "")
        if child_title == wanted:
            return child
    return None


def retrieve_notion_page(page_id: str, token: str, timeout: int) -> dict[str, Any]:
    return notion_request(
        method="GET",
        url=f"https://api.notion.com/v1/pages/{page_id}",
        token=token,
        timeout=timeout,
    )


def report_title_for_phase(*, report_path: Path, phase: str, phase_label: str) -> str:
    date_token = report_path.stem[:8]
    try:
        date_label = dt.datetime.strptime(date_token, "%Y%m%d").strftime("%Y年%m月%d日")
    except ValueError:
        date_label = date_token
    names = {
        "morning": "晨报",
        "midday": "午报",
        "close": "收盘报",
        "night": "晚报",
    }
    if phase == "smoke":
        return f"{date_label}测试日报"
    variant = report_variant_title(report_path)
    return f"{date_label}{names.get(phase, phase_label)}{variant}"


def report_variant_title(report_path: Path) -> str:
    stem = report_path.stem.lower()
    if stem.endswith("_reviewed") or stem.endswith("_review") or stem.endswith("_复核版"):
        return "复核版"
    return ""


def report_date_title(report_path: Path) -> str:
    date_token = report_path.stem[:8]
    try:
        return dt.datetime.strptime(date_token, "%Y%m%d").strftime("%Y年%m月%d日")
    except ValueError:
        return date_token


def report_phase_title(*, phase: str, phase_label: str) -> str:
    names = {
        "morning": "晨报",
        "midday": "午报",
        "close": "收盘报",
        "night": "晚报",
    }
    return names.get(phase, phase_label)


def ensure_notion_child_page(
    *,
    parent_page_id: str,
    token: str,
    title: str,
    timeout: int,
) -> dict[str, Any]:
    existing_page = find_notion_child_page_by_title(
        parent_page_id=parent_page_id,
        token=token,
        title=title,
        timeout=timeout,
    )
    if existing_page and existing_page.get("id"):
        payload = retrieve_notion_page(str(existing_page["id"]), token, timeout)
        return {
            "page_id": str(existing_page["id"]),
            "url": payload.get("url") or existing_page.get("url"),
            "created": False,
        }
    page = notion_request(
        method="POST",
        url="https://api.notion.com/v1/pages",
        token=token,
        timeout=timeout,
        payload={
            "parent": {"type": "page_id", "page_id": parent_page_id},
            "properties": {
                "title": {"title": [{"type": "text", "text": {"content": title}}]}
            },
        },
    )
    return {"page_id": str(page["id"]), "url": page.get("url"), "created": True}


def publish_notion_page(
    *,
    config: dict[str, Any],
    env: dict[str, str],
    phase: str,
    phase_label: str,
    report_path: Path,
) -> dict[str, Any]:
    notion = config.get("notion") or {}
    if not notion.get("enabled"):
        return {"enabled": False, "attempted": False}
    if phase == "smoke" and not notion.get("publish_smoke", False):
        return {"enabled": True, "attempted": False, "reason": "smoke publish disabled"}

    token_env = str(notion.get("token_env") or "NOTION_TOKEN")
    parent_env = str(notion.get("parent_page_id_env") or "NOTION_PARENT_PAGE_ID")
    token = env.get(token_env, "").strip()
    parent_page_id = str(notion.get("parent_page_id") or env.get(parent_env) or "").strip()
    if not token:
        return {"enabled": True, "attempted": False, "reason": f"missing {token_env}"}
    if not parent_page_id:
        return {"enabled": True, "attempted": False, "reason": f"missing {parent_env}"}

    title = report_title_for_phase(report_path=report_path, phase=phase, phase_label=phase_label)
    variant_title = report_variant_title(report_path)
    variant_key = ":reviewed" if variant_title else ""
    publication_key = f"{report_path.stem[:8]}:{phase}{variant_key}"
    publication_state_path = report_path.parent / "notion_publications.json"
    publication_state: dict[str, Any] = {}
    if publication_state_path.exists():
        try:
            publication_state = json.loads(publication_state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            publication_state = {}
    existing_publication = publication_state.get(publication_key)

    markdown = report_path.read_text(encoding="utf-8")
    blocks = markdown_to_notion_blocks(markdown)
    timeout = int(notion.get("timeout") or 120)
    started = now_local().isoformat(timespec="seconds")

    if phase != "smoke":
        date_title = report_date_title(report_path)
        phase_title = f"{report_phase_title(phase=phase, phase_label=phase_label)}{variant_title}"
        date_page = ensure_notion_child_page(
            parent_page_id=parent_page_id,
            token=token,
            title=date_title,
            timeout=timeout,
        )
        date_page_id = str(date_page["page_id"])
        title = phase_title
        existing_publication_valid = bool(
            isinstance(existing_publication, dict)
            and existing_publication.get("page_id")
            and existing_publication.get("parent_mode") == "date_phase"
            and existing_publication.get("date_page_id") == date_page_id
        )
        if not existing_publication_valid:
            existing_publication = None
        existing_phase_page = find_notion_child_page_by_title(
            parent_page_id=date_page_id,
            token=token,
            title=phase_title,
            timeout=timeout,
        )
        if existing_phase_page and existing_phase_page.get("id"):
            existing_publication = {
                **(existing_publication or {}),
                "page_id": str(existing_phase_page["id"]),
                "url": existing_phase_page.get("url"),
                "title": phase_title,
                "parent_mode": "date_phase",
                "date_page_id": date_page_id,
                "date_page_url": date_page.get("url"),
                "date_title": date_title,
            }
        else:
            existing_publication = None
        parent_page_id = date_page_id

    if isinstance(existing_publication, dict) and existing_publication.get("page_id"):
        page_id = str(existing_publication.get("page_id"))
        try:
            replace_notion_page_children(page_id, token, blocks, timeout)
            page_payload: dict[str, Any] = {}
            if not existing_publication.get("url"):
                page_payload = retrieve_notion_page(page_id, token, timeout)
            publication_state[publication_key] = {
                **existing_publication,
                "title": title,
                "url": existing_publication.get("url") or page_payload.get("url"),
                "updated_at": now_local().isoformat(timespec="seconds"),
                "report_path": str(report_path),
            }
            publication_state_path.write_text(
                json.dumps(publication_state, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return {
                "enabled": True,
                "attempted": True,
                "updated_existing": True,
                "publication_key": publication_key,
                "page_id": page_id,
                "url": existing_publication.get("url") or page_payload.get("url"),
                "title": title,
                "block_count": len(blocks),
                "parent_mode": existing_publication.get("parent_mode"),
                "date_page_id": existing_publication.get("date_page_id"),
                "date_page_url": existing_publication.get("date_page_url"),
            }
        except Exception as exc:
            return {
                "enabled": True,
                "attempted": True,
                "error": str(exc),
                "publication_key": publication_key,
                "page_id": page_id,
            }
    existing_page = find_notion_child_page_by_title(
        parent_page_id=parent_page_id,
        token=token,
        title=title,
        timeout=timeout,
    )
    if existing_page and existing_page.get("id"):
        page_id = str(existing_page["id"])
        page_payload: dict[str, Any] = {}
        try:
            replace_notion_page_children(page_id, token, blocks, timeout)
            page_payload = retrieve_notion_page(page_id, token, timeout)
        except Exception as exc:
            return {
                "enabled": True,
                "attempted": True,
                "error": str(exc),
                "publication_key": publication_key,
                "page_id": page_id,
                "title": title,
                "source": "notion_title_check",
            }
        publication_state[publication_key] = {
            "page_id": page_id,
            "url": page_payload.get("url") or existing_page.get("url"),
            "title": title,
            "published_at": now_local().isoformat(timespec="seconds"),
            "updated_at": now_local().isoformat(timespec="seconds"),
            "report_path": str(report_path),
            "discovered_from_notion": True,
            **(
                {
                    "parent_mode": "date_phase",
                    "date_page_id": parent_page_id,
                }
                if phase != "smoke"
                else {}
            ),
        }
        try:
            publication_state_path.write_text(
                json.dumps(publication_state, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass
        return {
            "enabled": True,
            "attempted": True,
            "updated_existing": True,
            "discovered_from_notion": True,
            "publication_key": publication_key,
            "page_id": page_id,
            "url": page_payload.get("url") or existing_page.get("url"),
            "title": title,
            "block_count": len(blocks),
            "source": "notion_title_check",
        }

    try:
        page = notion_request(
            method="POST",
            url="https://api.notion.com/v1/pages",
            token=token,
            timeout=timeout,
            payload={
                "parent": {"type": "page_id", "page_id": parent_page_id},
                "properties": {
                    "title": {"title": [{"type": "text", "text": {"content": title}}]}
                },
                "children": blocks[:80],
            },
        )
        page_id = page["id"]
        for i in range(80, len(blocks), 80):
            notion_request(
                method="PATCH",
                url=f"https://api.notion.com/v1/blocks/{page_id}/children",
                token=token,
                timeout=timeout,
                payload={"children": blocks[i : i + 80]},
            )
        state_write_error = None
        publication_state[publication_key] = {
            "page_id": page_id,
            "url": page.get("url"),
            "title": title,
            "published_at": now_local().isoformat(timespec="seconds"),
            "report_path": str(report_path),
            **(
                {
                    "parent_mode": "date_phase",
                    "date_page_id": parent_page_id,
                }
                if phase != "smoke"
                else {}
            ),
        }
        try:
            publication_state_path.write_text(
                json.dumps(publication_state, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:  # noqa: BLE001 - publishing already succeeded
            state_write_error = str(exc)
        return {
            "enabled": True,
            "attempted": True,
            "started_at": started,
            "publication_key": publication_key,
            "page_id": page_id,
            "url": page.get("url"),
            "block_count": len(blocks),
            "parent_mode": "date_phase" if phase != "smoke" else None,
            "date_page_id": parent_page_id if phase != "smoke" else None,
            "state_write_error": state_write_error,
        }
    except Exception as exc:  # noqa: BLE001 - Notion delivery should not break collection
        return {
            "enabled": True,
            "attempted": True,
            "started_at": started,
            "error": str(exc),
        }


def deliver_report(
    *,
    config: dict[str, Any],
    phase: str,
    phase_label: str,
    report_path: Path,
    manifest_path: Path,
    notion_url: str | None = None,
    status: str = "complete",
    reason: str = "",
) -> dict[str, Any]:
    telegram = config.get("telegram") or {}
    if not telegram.get("enabled"):
        return {"enabled": False, "attempted": False}
    if os.environ.get("OPENCLAW_BACKGROUND_SILENT_TELEGRAM") == "1":
        return {
            "enabled": True,
            "attempted": False,
            "reason": "background_silent_telegram",
            "provider": "suppressed",
            "pending_main_notification": True,
            "status": status,
            "notion_url": notion_url,
        }
    if phase == "smoke" and not telegram.get("send_smoke", False):
        return {"enabled": True, "attempted": False, "reason": "smoke delivery disabled"}

    if os.environ.get("MARKET_IMMERSION_SUPPRESS_FAILURE_TELEGRAM") == "1" and status != "complete":
        return {
            "enabled": True,
            "attempted": False,
            "reason": "failure_telegram_suppressed_for_retry",
            "provider": "suppressed",
            "status": status,
            "notion_url": notion_url,
            "retry_suppressed": True,
        }

    target = str(telegram.get("target") or "").strip()
    if not target:
        return {"enabled": True, "attempted": False, "reason": "missing telegram target"}

    openclaw_bin = Path(config.get("openclaw_bin") or "openclaw").expanduser()
    title = report_title_for_phase(report_path=report_path, phase=phase, phase_label=phase_label)
    status_labels = {
        "complete": "完成",
        "degraded": "部分完成",
        "delayed": "排队中",
        "failed": "失败",
    }
    status_label = status_labels.get(status, status)
    lines = [f"每日快讯简报{status_label}：{title}"]
    if notion_url:
        lines.append(f"Notion：{notion_url}")
    elif status == "failed":
        lines.append("未发布到 Notion。")
    elif status == "delayed":
        lines.append("后台模型通道排队中，尚未发布到 Notion。")
    else:
        lines.append("Markdown 简报已生成。")
    if reason:
        lines.append(f"状态：{sanitize_user_reason(reason)}")
    message = "\n".join(lines)
    if str(telegram.get("channel") or "telegram") == "telegram" and str(
        telegram.get("delivery_method") or ""
    ).strip().lower() == "bot_api":
        started = now_local().isoformat(timespec="seconds")
        try:
            openclaw_config_path = Path(
                telegram.get("openclaw_config_path") or "~/.openclaw/openclaw.json"
            ).expanduser()
            openclaw_config = json.loads(openclaw_config_path.read_text(encoding="utf-8"))
            bot_token = (
                (openclaw_config.get("channels") or {}).get("telegram") or {}
            ).get("botToken")
            if not bot_token:
                return {
                    "enabled": True,
                    "attempted": False,
                    "reason": "missing channels.telegram.botToken",
                }
            chat_id = target.removeprefix("telegram:")
            body = urllib.parse.urlencode(
                {
                    "chat_id": chat_id,
                    "text": message,
                    "disable_web_page_preview": "false",
                }
            ).encode("utf-8")
            timeout = int(telegram.get("timeout") or 120)
            attempts = int(telegram.get("retry_attempts") or 4)
            last_exc: Exception | None = None
            payload: dict[str, Any] = {}
            for attempt in range(max(1, attempts)):
                request = urllib.request.Request(
                    f"https://api.telegram.org/bot{bot_token}/sendMessage",
                    data=body,
                    method="POST",
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                try:
                    with urllib.request.urlopen(request, timeout=timeout) as response:
                        payload = json.loads(response.read().decode("utf-8"))
                    last_exc = None
                    break
                except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
                    last_exc = exc
                    if attempt + 1 >= max(1, attempts):
                        raise
                    time.sleep(min(2**attempt, 10))
            return {
                "enabled": True,
                "attempted": True,
                "started_at": started,
                "returncode": 0 if payload.get("ok") else 1,
                "provider": "telegram_bot_api",
                "message_id": ((payload.get("result") or {}).get("message_id")),
                "status": status,
                "notion_url": notion_url,
                "message": message,
                "attempts": max(1, attempts) if last_exc else attempt + 1,
            }
        except Exception as exc:  # noqa: BLE001 - delivery is best effort
            return {
                "enabled": True,
                "attempted": True,
                "started_at": started,
                "provider": "telegram_bot_api",
                "exception": str(exc),
                "status": status,
                "notion_url": notion_url,
            }
    cmd = [
        str(openclaw_bin),
        "message",
        "send",
        "--channel",
        str(telegram.get("channel") or "telegram"),
        "--target",
        target,
        "--message",
        message,
    ]
    send_mode = str(telegram.get("send_mode") or "link").strip().lower()
    if send_mode in {"document", "media"} or (not notion_url and send_mode != "link"):
        cmd.extend(["--media", str(report_path), "--force-document"])
    account = str(telegram.get("account") or "").strip()
    if account:
        cmd.extend(["--account", account])

    started = now_local().isoformat(timespec="seconds")
    try:
        proc = subprocess.Popen(
            cmd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            stdout, stderr = proc.communicate(timeout=int(telegram.get("timeout") or 120))
        except subprocess.TimeoutExpired as exc:
            try:
                os.killpg(proc.pid, 9)
            except Exception:
                pass
            stdout, stderr = proc.communicate()
            return {
                "enabled": True,
                "attempted": True,
                "started_at": started,
                "exception": str(exc),
                "stdout": stdout,
                "stderr": stderr,
            }
        return {
            "enabled": True,
            "attempted": True,
            "started_at": started,
            "returncode": proc.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "status": status,
            "notion_url": notion_url,
            "message": message,
        }
    except Exception as exc:  # noqa: BLE001 - delivery is best effort
        return {
            "enabled": True,
            "attempted": True,
            "started_at": started,
            "exception": str(exc),
            "status": status,
            "notion_url": notion_url,
        }


def notify_market_failure(
    *,
    config: dict[str, Any],
    phase: str,
    phase_label: str,
    report_path: Path,
    manifest_path: Path,
    reason: str,
) -> dict[str, Any]:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    if not report_path.exists():
        report_path.write_text("## 信息汇总\n\n正式日报未完成，本轮不发布。\n", encoding="utf-8")
    return deliver_report(
        config=config,
        phase=phase,
        phase_label=phase_label,
        report_path=report_path,
        manifest_path=manifest_path,
        status=failure_notification_status(reason),
        reason=reason,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/market_immersion_config.json")
    parser.add_argument("--phase", default="morning")
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument(
        "--as-of",
        default="",
        help="Historical replay clock in ISO format; default uses current time. Intended for explicit backfill only.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--resume", action="store_true", help="Resume from the existing daily phase manifest when possible")
    parser.add_argument(
        "--from-stage",
        choices=["collect", "classify", "digest", "quality_check", "render", "publish", "notify"],
        default="",
        help="Force rerun from this stage onward while reusing earlier manifest data",
    )
    parser.add_argument(
        "--stop-after",
        choices=["digest", "quality_check", "render"],
        default="",
        help="Stop after this stage and do not publish or notify. Intended for safe recovery preflight.",
    )
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    module_dir = script_dir.parent
    config_path = (module_dir / args.config).resolve()
    config = load_json(config_path)

    workspace = Path(config["workspace_dir"]).expanduser()
    output_root = Path(config["output_dir"]).expanduser()
    venv_python = Path(config["venv_python"]).expanduser()
    secrets_env = Path(config["secrets_env"]).expanduser()
    notion_secrets_env = Path(
        (config.get("notion") or {}).get("secrets_env")
        or "~/.openclaw/secrets/notion.env"
    ).expanduser()
    run_def = config["runs"].get(args.phase)
    if not run_def:
        raise SystemExit(f"Unknown phase: {args.phase}")

    env = os.environ.copy()
    env.update(load_env_file(secrets_env))
    env.update(load_env_file(notion_secrets_env))
    if not env.get("MX_APIKEY"):
        raise SystemExit("MX_APIKEY is not available. Check ~/.openclaw/secrets/mx.env")

    as_of = parse_iso(args.as_of)
    if args.as_of and as_of is None:
        raise SystemExit(f"Invalid --as-of ISO datetime: {args.as_of}")
    if as_of and as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=now_local().tzinfo)
    started = as_of.astimezone(now_local().tzinfo) if as_of else now_local()
    started_iso = started.isoformat(timespec="seconds")
    window_start, window_end, window_source = compute_window(
        phase=args.phase,
        output_root=output_root,
        end=started,
    )
    window_prefix = format_window_for_query(window_start, window_end)
    window = {
        "start": window_start.isoformat(timespec="seconds") if window_start else None,
        "end": window_end.isoformat(timespec="seconds") if window_end else None,
        "source": window_source,
    }
    report_clock = window_end or started
    day = report_clock.strftime("%Y-%m-%d")
    date_slug = report_clock.strftime("%Y%m%d")
    stamp = started.strftime("%Y%m%d_%H%M%S")
    # Canonical daily phase output: one report/manifest per day+phase.
    # Retries update the same files instead of creating a new markdown on every failed attempt.
    run_slug = f"{date_slug}_{args.phase}"
    attempt_slug = f"{stamp}_{args.phase}"

    daily_dir = output_root / "daily" / day
    raw_dir = daily_dir / "raw"
    stdout_dir = daily_dir / "stdout"
    skill_output_dir = daily_dir / "skill-output" / attempt_slug
    for d in (daily_dir, raw_dir, stdout_dir, skill_output_dir):
        d.mkdir(parents=True, exist_ok=True)
    manifest_path = daily_dir / f"{run_slug}.manifest.json"
    report_path = daily_dir / f"{run_slug}.md"
    checkpoints_dir = daily_dir / "checkpoints" / run_slug
    checkpoints_dir.mkdir(parents=True, exist_ok=True)
    task = background_tasks.create_or_resume_task(
        task_type=f"market_immersion_{args.phase}",
        task_id=f"market_immersion_{args.phase}-{run_slug}",
        key=run_slug,
        requested_by=os.environ.get("OPENCLAW_REQUESTED_BY", "systemd/supervisor"),
        input_summary=f"生成{day}{run_def['label']}，完成标准：Markdown、Notion 发布、Telegram Notion 链接通知。",
        success_criteria=[
            str(report_path),
            str(manifest_path),
            str(checkpoints_dir / "publish.json"),
            str(checkpoints_dir / "notify.json"),
        ],
        retry_policy={"mode": "supervisor", "max_immediate_retries": 0, "retry_delay_minutes": 15},
        review_required=False,
        metadata={"phase": args.phase, "date": day, "run_slug": run_slug, "attempt_slug": attempt_slug},
    )
    background_task_id = str(task["task_id"])
    background_tasks.add_artifacts(background_task_id, [manifest_path, report_path, checkpoints_dir])

    def write_checkpoint(stage: str, payload: dict[str, Any]) -> None:
        checkpoint = {
            "stage": stage,
            "run_slug": run_slug,
            "attempt_slug": attempt_slug,
            "written_at": now_local().isoformat(timespec="seconds"),
            **payload,
        }
        (checkpoints_dir / f"{stage}.json").write_text(
            json.dumps(checkpoint, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        background_tasks.update_task(
            background_task_id,
            checkpoint_path=str(checkpoints_dir / f"{stage}.json"),
            metadata={**(background_tasks.load_task(background_task_id).get("metadata") or {}), "last_stage": stage},
            event=f"checkpoint:{stage}",
        )

    previous_manifest: dict[str, Any] = {}
    if (args.resume or args.from_stage) and manifest_path.exists():
        try:
            previous_manifest = load_json(manifest_path)
        except Exception:
            previous_manifest = {}
    digest_result_path = checkpoints_dir / "openclaw_summary.json"
    force_from = args.from_stage or ""
    stop_after = args.stop_after or ""
    stage_order = ["collect", "classify", "digest", "quality_check", "render", "publish", "notify"]
    dag_spec = {
        "mode": "background_dag",
        "nodes": [
            {"name": "collect", "parallel": True},
            {"name": "auxiliary_checks", "depends_on": ["collect"], "parallel_with": ["classify"]},
            {"name": "classify", "depends_on": ["collect"], "parallel_with": ["auxiliary_checks"]},
            {"name": "digest", "depends_on": ["collect", "classify"], "parallel": False, "writer": "single_global_gpt"},
            {"name": "quality_check", "depends_on": ["digest"]},
            {"name": "render", "depends_on": ["quality_check"]},
            {"name": "publish", "depends_on": ["render"]},
            {"name": "notify", "depends_on": ["publish"], "telegram": "final_link_or_short_failure_only"},
        ],
    }

    def is_valid_reusable_digest(summary: dict[str, Any]) -> bool:
        if not summary:
            return False
        summary_config = config.get("openclaw_summary") or {}
        valid_transports = {"full_coverage_chunked_messages", "artifact_file_reference"}
        return bool(
            not summary.get("degraded")
            and not summary.get("error")
            and (summary.get("summary_status") or "ok") not in {"degraded_fallback", "timeout", "gateway_unavailable"}
            and summary.get("failure_type") not in {"summary_timeout", "gateway_unavailable", "provider_timeout"}
            and summary.get("agent") == str(summary_config.get("agent") or "daily-writer")
            and summary.get("model") == str(summary_config.get("model") or "openai-codex/gpt-5.5")
            and summary.get("input_transport") in valid_transports
            and bool(summary.get("coverage_manifest_file"))
            and (str(summary_config.get("input_mode") or "full_file") != "full_file" or bool(summary.get("source_input_file")))
            and bool(summary.get("summary_paragraphs"))
        )

    def load_reusable_digest_result() -> dict[str, Any]:
        candidates: list[Path] = []
        if isinstance(previous_manifest.get("openclaw_summary_file"), str):
            candidates.append(Path(str(previous_manifest.get("openclaw_summary_file"))))
        digest_meta_path = checkpoints_dir / "digest.json"
        if digest_meta_path.exists():
            try:
                digest_meta = load_json(digest_meta_path)
                if isinstance(digest_meta.get("openclaw_summary_file"), str):
                    candidates.append(Path(str(digest_meta.get("openclaw_summary_file"))))
            except Exception:
                pass
        candidates.append(digest_result_path)
        for candidate in candidates:
            try:
                if candidate.exists():
                    loaded = load_json(candidate)
                    if is_valid_reusable_digest(loaded):
                        return loaded
            except Exception:
                continue
        return {}

    reusable_digest_result = load_reusable_digest_result()
    if reusable_digest_result and not previous_manifest.get("openclaw_summary"):
        previous_manifest["openclaw_summary"] = reusable_digest_result
        previous_manifest.setdefault("openclaw_summary_file", str(digest_result_path))

    def persist_openclaw_summary(summary: dict[str, Any]) -> Path:
        digest_result_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return digest_result_path

    def can_reuse(stage: str) -> bool:
        if not previous_manifest:
            return False
        if force_from:
            if stage == "digest" and stage_order.index(stage) < stage_order.index(force_from):
                return is_valid_reusable_digest(previous_manifest.get("openclaw_summary") or {})
            return stage_order.index(stage) < stage_order.index(force_from)
        previous_summary = previous_manifest.get("openclaw_summary") or {}
        previous_validation = previous_manifest.get("validation") or {}
        previous_requires_formal_retry = bool(
            previous_summary.get("degraded")
            or previous_summary.get("summary_status") == "degraded_fallback"
            or previous_summary.get("summary_status") in {"timeout", "gateway_unavailable"}
            or previous_summary.get("failure_type") in {"summary_timeout", "gateway_unavailable", "provider_timeout"}
            or previous_summary.get("error")
            or previous_validation.get("requires_formal_retry")
            or previous_validation.get("formal_digest") is False
        )
        summary_config = config.get("openclaw_summary") or {}
        valid_summary_transports = {"full_coverage_chunked_messages", "artifact_file_reference"}
        previous_summary_contract_mismatch = bool(
            previous_summary
            and stage_order.index(stage) >= stage_order.index("digest")
            and (
                previous_summary.get("agent") != str(summary_config.get("agent") or "daily-writer")
                or previous_summary.get("model") != str(summary_config.get("model") or "openai-codex/gpt-5.5")
                or previous_summary.get("input_transport") not in valid_summary_transports
                or not previous_summary.get("coverage_manifest_file")
                or (
                    str(summary_config.get("input_mode") or "full_file") == "full_file"
                    and not previous_summary.get("source_input_file")
                )
                or (stage == "digest" and not is_valid_reusable_digest(previous_summary))
            )
        )
        if (previous_requires_formal_retry or previous_summary_contract_mismatch) and stage_order.index(stage) >= stage_order.index("digest"):
            return False
        if stage_order.index(stage) >= stage_order.index("quality_check"):
            qc_config = config.get("raw_flow_quality_check") or {}
            previous_qc = previous_manifest.get("raw_flow_quality_check") or {}
            if previous_qc and (
                previous_qc.get("mode") != str(qc_config.get("mode") or "large_context")
                or previous_qc.get("model") != str(qc_config.get("model") or "kimi-k2.6")
            ):
                return False
        return args.resume

    entries: list[dict[str, Any]] = []
    if can_reuse("collect") and previous_manifest.get("entries"):
        entries = previous_manifest.get("entries") or []

    mx_supplement = config.get("mx_search_supplement") or {}
    run_mx_queries = bool(mx_supplement.get("enabled", False)) or (
        args.phase == "smoke" and bool(mx_supplement.get("enabled_for_smoke", True))
    )
    query_defs = [] if can_reuse("collect") else (run_def["queries"] if run_mx_queries else [])

    def collect_query_entry(query_def: dict[str, Any]) -> dict[str, Any]:
        query_id = query_def["id"]
        skill = query_def["skill"]
        base_query = query_def["query"]
        query = f"{window_prefix}{base_query}" if window_prefix else base_query
        entry_slug = safe_name(f"{attempt_slug}_{query_id}")
        stdout_path = stdout_dir / f"{entry_slug}.stdout.txt"
        stderr_path = stdout_dir / f"{entry_slug}.stderr.txt"
        query_output_dir = skill_output_dir / safe_name(query_id)
        query_output_dir.mkdir(parents=True, exist_ok=True)

        t0 = time.time()
        if args.dry_run:
            completed = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=f"DRY RUN: {skill} {shlex.quote(query)}\n",
                stderr="",
            )
        else:
            completed = run_skill(
                workspace=workspace,
                venv_python=venv_python,
                skill=skill,
                query=query,
                output_dir=query_output_dir,
                env=env,
                timeout=args.timeout,
            )
        duration_ms = int((time.time() - t0) * 1000)
        stdout_path.write_text(completed.stdout or "", encoding="utf-8")
        stderr_path.write_text(completed.stderr or "", encoding="utf-8")

        raw_files = sorted(str(p) for p in query_output_dir.glob("*.json") if p.is_file())
        api_ok, api_messages = inspect_api_files(raw_files)
        classification = classify_items(
            raw_files=raw_files,
            window_start=window_start,
            window_end=window_end,
        )
        return {
            "id": query_id,
            "skill": skill,
            "base_query": base_query,
            "query": query,
            "returncode": completed.returncode,
            "api_ok": api_ok,
            "api_messages": api_messages,
            "classification": classification,
            "duration_ms": duration_ms,
            "stdout": completed.stdout or "",
            "stderr": completed.stderr or "",
            "stdout_file": str(stdout_path),
            "stderr_file": str(stderr_path),
            "raw_files": raw_files,
        }

    def collect_feed_entry_safe() -> dict[str, Any] | None:
        return collect_market_feed_entry(
            config=config,
            output_dir=skill_output_dir / "market_feed",
            window_start=window_start,
            window_end=window_end,
        )

    if not can_reuse("collect"):
        pipeline_config = config.get("pipeline") or {}
        parallelism = int(pipeline_config.get("collection_parallelism") or min(4, max(1, len(query_defs) + 1)))
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, parallelism)) as executor:
            feed_future = executor.submit(collect_feed_entry_safe)
            future_by_index = {
                executor.submit(collect_query_entry, query_def): index
                for index, query_def in enumerate(query_defs)
            }
            query_entries: list[tuple[int, dict[str, Any]]] = []
            for future in concurrent.futures.as_completed(future_by_index):
                index = future_by_index[future]
                try:
                    query_entries.append((index, future.result()))
                except Exception as exc:  # noqa: BLE001 - preserve failure as an entry for validation
                    query_def = query_defs[index]
                    query_entries.append(
                        (
                            index,
                            {
                                "id": query_def.get("id"),
                                "skill": query_def.get("skill"),
                                "base_query": query_def.get("query"),
                                "query": f"{window_prefix}{query_def.get('query')}" if window_prefix else query_def.get("query"),
                                "returncode": 1,
                                "api_ok": False,
                                "api_messages": [str(exc)],
                                "classification": {"items": []},
                                "duration_ms": 0,
                                "stdout": "",
                                "stderr": str(exc),
                                "stdout_file": "",
                                "stderr_file": "",
                                "raw_files": [],
                            },
                        )
                    )
            try:
                feed_entry = feed_future.result()
                if feed_entry:
                    entries.append(feed_entry)
            except Exception as exc:  # noqa: BLE001 - preserve feed failure as an entry for validation
                entries.append(
                    {
                        "id": "market_feed",
                        "skill": "market_feed_snapshot",
                        "base_query": "market feed snapshot",
                        "query": "market feed snapshot",
                        "returncode": 1,
                        "api_ok": False,
                        "api_messages": [str(exc)],
                        "classification": {"items": []},
                        "duration_ms": 0,
                        "stdout": "",
                        "stderr": str(exc),
                        "stdout_file": "",
                        "stderr_file": "",
                        "raw_files": [],
                    }
                )
            entries.extend(entry for _, entry in sorted(query_entries, key=lambda pair: pair[0]))

    write_checkpoint(
        "collect",
        {
            "status": "done",
            "entry_count": len(entries),
            "parallelism": int((config.get("pipeline") or {}).get("collection_parallelism") or min(4, max(1, len(query_defs) or 1))),
        },
    )

    all_items, serial_by_item = build_report_items(entries)
    auxiliary_flow_checks: dict[str, Any] = {}
    if can_reuse("classify") and previous_manifest.get("raw_flow_classification"):
        raw_flow_classification = previous_manifest.get("raw_flow_classification") or {}
        auxiliary_flow_checks = previous_manifest.get("auxiliary_flow_checks") or build_auxiliary_flow_checks(all_items, entries)
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            classify_future = executor.submit(
                generate_raw_flow_classification,
                config=config,
                items=all_items,
                run_slug=run_slug,
                use_model=not args.dry_run and args.phase != "smoke",
            )
            aux_future = executor.submit(build_auxiliary_flow_checks, all_items, entries)
            auxiliary_flow_checks = aux_future.result()
            raw_flow_classification = classify_future.result()
    write_checkpoint(
        "auxiliary_checks",
        {
            "status": "done",
            "item_count": auxiliary_flow_checks.get("item_count"),
            "collection_failures": auxiliary_flow_checks.get("collection_failures") or [],
        },
    )
    if raw_flow_classification.get("error"):
        write_checkpoint(
            "classify",
            {"status": "failed", "error": raw_flow_classification.get("error")},
        )
        manifest = {
            "version": 1,
            "phase": args.phase,
            "phase_label": run_def["label"],
            "started_at": started_iso,
            "run_slug": run_slug,
            "attempt_slug": attempt_slug,
            "window": window,
            "config_path": str(config_path),
            "checkpoints_dir": str(checkpoints_dir),
            "dag": dag_spec,
            "entries": entries,
            "auxiliary_flow_checks": auxiliary_flow_checks,
            "raw_flow_classification": raw_flow_classification,
            "checkpoint": "classification_failed",
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        failure_delivery = notify_market_failure(
            config=config,
            phase=args.phase,
            phase_label=run_def["label"],
            report_path=report_path,
            manifest_path=manifest_path,
            reason=f"Ark classification failed: {raw_flow_classification.get('error')}",
        )
        manifest["delivery"] = failure_delivery
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return 5
    write_checkpoint(
        "classify",
        {
            "status": "done",
            "attempted": bool(raw_flow_classification.get("attempted")),
            "model_duration_ms": raw_flow_classification.get("model_duration_ms"),
        },
    )
    if can_reuse("digest") and previous_manifest.get("openclaw_summary"):
        openclaw_digest = previous_manifest.get("openclaw_summary") or {}
    else:
        openclaw_digest = generate_openclaw_digest(
            config=config,
            phase=args.phase,
            phase_label=run_def["label"],
            window=window,
            all_items=all_items,
            run_slug=run_slug,
            raw_flow_classification=raw_flow_classification,
            use_model=not args.dry_run and args.phase != "smoke",
        )
    if openclaw_digest.get("error"):
        failure_type = openclaw_digest.get("failure_type") or classify_failure(
            str(openclaw_digest.get("error") or ""),
            default="summary_failed",
        )
        fallback_enabled = bool((config.get("openclaw_summary") or {}).get("allow_degraded_fallback", False))
        if fallback_enabled and failure_type in {"summary_timeout", "gateway_unavailable", "provider_timeout"}:
            original_error = str(openclaw_digest.get("error") or "")
            openclaw_digest = {
                **deterministic_degraded_digest(
                    all_items=all_items,
                    phase_label=run_def["label"],
                    failure_type=failure_type,
                    error=original_error,
                ),
                "original_failure": {
                    "failure_type": failure_type,
                    "error": original_error[-4000:],
                    "model_duration_ms": openclaw_digest.get("model_duration_ms"),
                },
            }
    openclaw_summary_file = persist_openclaw_summary(openclaw_digest)
    write_checkpoint(
        "digest",
        {
            "status": "failed" if openclaw_digest.get("error") else "degraded" if openclaw_digest.get("degraded") else "done",
            "attempted": bool(openclaw_digest.get("attempted")),
            "summary_status": openclaw_digest.get("summary_status") or ("ok" if not openclaw_digest.get("error") else classify_failure(str(openclaw_digest.get("error") or ""), default="summary_failed")),
            "failure_type": openclaw_digest.get("failure_type") or (classify_failure(str(openclaw_digest.get("error") or ""), default="summary_failed") if openclaw_digest.get("error") else None),
            "degraded": bool(openclaw_digest.get("degraded")),
            "fallback_reason": openclaw_digest.get("fallback_reason"),
            "model_duration_ms": openclaw_digest.get("model_duration_ms"),
            "model": openclaw_digest.get("model"),
            "agent": openclaw_digest.get("agent"),
            "source_input_file": openclaw_digest.get("source_input_file"),
            "coverage_manifest_file": openclaw_digest.get("coverage_manifest_file"),
            "input_transport": openclaw_digest.get("input_transport"),
            "prompt_artifact_file": openclaw_digest.get("prompt_artifact_file"),
            "prompt_artifact_sha256": openclaw_digest.get("prompt_artifact_sha256"),
            "fallback_used": bool(openclaw_digest.get("fallback_used")),
            "runner": openclaw_digest.get("runner"),
            "openclaw_summary_file": str(openclaw_summary_file),
            "message_count": openclaw_digest.get("message_count"),
            "source_item_count": openclaw_digest.get("source_item_count"),
            "error": openclaw_digest.get("error"),
            "stderr": openclaw_digest.get("stderr"),
            "stdout": openclaw_digest.get("stdout"),
        },
    )

    if stop_after == "digest":
        manifest = {
            "version": 1,
            "phase": args.phase,
            "phase_label": run_def["label"],
            "started_at": started_iso,
            "run_slug": run_slug,
            "attempt_slug": attempt_slug,
            "window": window,
            "config_path": str(config_path),
            "checkpoints_dir": str(checkpoints_dir),
            "dag": dag_spec,
            "entries": entries,
            "auxiliary_flow_checks": auxiliary_flow_checks,
            "raw_flow_classification": raw_flow_classification,
            "openclaw_summary": openclaw_digest,
            "openclaw_summary_file": str(openclaw_summary_file),
            "validation": {
                "published": False,
                "delivery_blocked": True,
                "reason": "stopped after digest by operator recovery preflight",
            },
            "resume": {
                "enabled": bool(args.resume or args.from_stage),
                "from_stage": force_from or None,
                "reused_manifest": str(manifest_path) if previous_manifest else None,
            },
            "stop_after": "digest",
            "checkpoint": "digest_done_stop_after",
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"manifest={manifest_path}")
        print(f"entries={len(entries)}")
        print("stopped_after=digest")
        return 0

    if openclaw_digest.get("degraded"):
        error_summary = "正式摘要生成失败；降级摘要只允许本地留痕，禁止 Notion 发布和 Telegram 推送。"
        manifest = {
            "version": 1,
            "phase": args.phase,
            "phase_label": run_def["label"],
            "started_at": started_iso,
            "run_slug": run_slug,
            "attempt_slug": attempt_slug,
            "window": window,
            "config_path": str(config_path),
            "checkpoints_dir": str(checkpoints_dir),
            "dag": dag_spec,
            "entries": entries,
            "auxiliary_flow_checks": auxiliary_flow_checks,
            "raw_flow_classification": raw_flow_classification,
            "openclaw_summary": openclaw_digest,
            "openclaw_summary_file": str(openclaw_summary_file),
            "validation": {
                "degraded": True,
                "published": False,
                "delivery_blocked": True,
                "reason": error_summary,
            },
            "checkpoint": "digest_degraded_blocked",
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        report_path.write_text(
            "## 信息汇总\n\n正式摘要生成失败；本轮不发布 Notion，也不推送 Telegram。\n",
            encoding="utf-8",
        )
        error_path = background_tasks.write_error(
            background_task_id,
            error_kind="formal_digest_required",
            error_summary=error_summary,
            details={"digest": openclaw_digest, "checkpoint": str(checkpoints_dir / "digest.json")},
        )
        background_tasks.fail_task(
            background_task_id,
            error_kind="formal_digest_required",
            error_summary=error_summary,
            checkpoint_path=checkpoints_dir / "digest.json",
            artifacts=[manifest_path, report_path, error_path],
            needs_review=False,
        )
        background_tasks.queue_notification(
            background_task_id,
            kind="market_failed",
            title=f"{day}{run_def['label']}未发布",
            summary=error_summary,
            severity="warning",
            artifact_paths=[manifest_path, report_path, error_path],
        )
        return 3

    if should_block_empty_market_report(phase=args.phase, config=config, all_items=all_items):
        error_summary = "市场简讯采集结果为空；空占位稿不得作为正式简报发布。"
        manifest = {
            "version": 1,
            "phase": args.phase,
            "phase_label": run_def["label"],
            "started_at": started_iso,
            "run_slug": run_slug,
            "attempt_slug": attempt_slug,
            "window": window,
            "config_path": str(config_path),
            "checkpoints_dir": str(checkpoints_dir),
            "dag": dag_spec,
            "entries": entries,
            "auxiliary_flow_checks": auxiliary_flow_checks,
            "raw_flow_classification": raw_flow_classification,
            "openclaw_summary": openclaw_digest,
            "openclaw_summary_file": str(openclaw_summary_file),
            "validation": {
                "published": False,
                "delivery_blocked": True,
                "reason": error_summary,
                "empty_report": True,
                "item_count": 0,
            },
            "checkpoint": "empty_report_blocked",
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        report_path.write_text(
            "## 信息汇总\n\n市场简讯采集结果为空；本轮不发布 Notion，也不按完成推送 Telegram。\n",
            encoding="utf-8",
        )
        failure_delivery = notify_market_failure(
            config=config,
            phase=args.phase,
            phase_label=run_def["label"],
            report_path=report_path,
            manifest_path=manifest_path,
            reason=error_summary,
        )
        manifest["delivery"] = failure_delivery
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        error_path = background_tasks.write_error(
            background_task_id,
            error_kind="no_market_items_collected",
            error_summary=error_summary,
            details={"checkpoint": "empty_report_blocked", "item_count": 0},
        )
        background_tasks.fail_task(
            background_task_id,
            error_kind="no_market_items_collected",
            error_summary=error_summary,
            checkpoint_path=manifest_path,
            artifacts=[manifest_path, report_path, error_path],
            needs_review=False,
        )
        background_tasks.queue_notification(
            background_task_id,
            kind="market_failed",
            title=f"{day}{run_def['label']}未发布",
            summary=error_summary,
            severity="warning",
            artifact_paths=[manifest_path, report_path, error_path],
        )
        return 3

    summary_config = config.get("openclaw_summary") or {}
    summary_required = bool(summary_config.get("required", True))
    if (
        summary_required
        and all_items
        and (
            openclaw_digest.get("error")
            or not (openclaw_digest.get("summary_paragraphs") or [])
        )
    ):
        manifest = {
            "version": 1,
            "phase": args.phase,
            "phase_label": run_def["label"],
            "started_at": started_iso,
            "run_slug": run_slug,
            "attempt_slug": attempt_slug,
            "window": window,
            "config_path": str(config_path),
            "checkpoints_dir": str(checkpoints_dir),
            "dag": dag_spec,
            "entries": entries,
            "auxiliary_flow_checks": auxiliary_flow_checks,
            "raw_flow_classification": raw_flow_classification,
            "openclaw_summary": openclaw_digest,
            "openclaw_summary_file": str(openclaw_summary_file),
            "checkpoint": "digest_failed",
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        report_path.write_text(
            "## 信息汇总\n\nGPT 正式生成失败，本轮不发布正式简报。\n",
            encoding="utf-8",
        )
        failure_delivery = notify_market_failure(
            config=config,
            phase=args.phase,
            phase_label=run_def["label"],
            report_path=report_path,
            manifest_path=manifest_path,
            reason=sanitize_user_reason(str(openclaw_digest.get("error") or "empty summary")),
        )
        manifest["delivery"] = failure_delivery
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        error_kind = openclaw_digest.get("failure_type") or classify_failure(str(openclaw_digest.get("error") or ""), default="summary_failed")
        error_path = background_tasks.write_error(
            background_task_id,
            error_kind=error_kind,
            error_summary=sanitize_user_reason(str(openclaw_digest.get("error") or "empty summary")),
            details={"digest": openclaw_digest, "checkpoint": str(checkpoints_dir / "digest.json")},
        )
        background_tasks.fail_task(
            background_task_id,
            error_kind=error_kind,
            error_summary=sanitize_user_reason(str(openclaw_digest.get("error") or "empty summary")),
            checkpoint_path=checkpoints_dir / "digest.json",
            artifacts=[manifest_path, report_path, error_path],
            needs_review=False,
        )
        background_tasks.queue_notification(
            background_task_id,
            kind="market_failed",
            title=f"{day}{run_def['label']}未发布",
            summary=sanitize_user_reason(str(openclaw_digest.get("error") or "empty summary")),
            severity="warning",
            artifact_paths=[manifest_path, report_path, error_path],
        )
        return 3

    write_checkpoint(
        "quality_check",
        {
            "status": "started",
            "reused_digest": bool(can_reuse("digest")),
            "openclaw_summary_file": str(openclaw_summary_file),
        },
    )
    if can_reuse("quality_check") and previous_manifest.get("raw_flow_quality_check"):
        raw_flow_quality_check = previous_manifest.get("raw_flow_quality_check") or {}
    else:
        raw_flow_quality_check = generate_raw_flow_quality_check(
            config=config,
            items=all_items,
            openclaw_digest=openclaw_digest,
            run_slug=run_slug,
            use_model=not args.dry_run and args.phase != "smoke" and not openclaw_digest.get("degraded"),
        )
    if raw_flow_quality_check.get("error"):
        # The post-generation QC model is advisory. A QC transport failure must
        # not block a completed digest/report from being rendered and published.
        raw_flow_quality_check["non_blocking_error"] = raw_flow_quality_check.get("error")
        raw_flow_quality_check.pop("error", None)
        raw_flow_quality_check.setdefault("style_warnings", []).append(
            "后置质检调用失败，已按非阻塞告警处理。"
        )
        write_checkpoint(
            "quality_check",
            {"status": "warning", "error": raw_flow_quality_check.get("non_blocking_error")},
        )
    write_checkpoint(
        "quality_check",
        {
            "status": "done",
            "attempted": bool(raw_flow_quality_check.get("attempted")),
            "model_duration_ms": raw_flow_quality_check.get("model_duration_ms"),
        },
    )

    revised_digest = gpt_decide_ark_review_and_finalize(
        config=config,
        phase_label=run_def["label"],
        window=window,
        openclaw_digest=openclaw_digest,
        ark_review=raw_flow_quality_check,
        run_slug=run_slug,
    )
    if revised_digest is not openclaw_digest:
        openclaw_digest = revised_digest
        openclaw_summary_file = persist_openclaw_summary(openclaw_digest)
        write_checkpoint(
            "digest",
            {
                "status": "done" if not openclaw_digest.get("error") else "failed",
                "attempted": bool(openclaw_digest.get("attempted")),
                "summary_status": openclaw_digest.get("summary_status") or ("ok" if not openclaw_digest.get("error") else classify_failure(str(openclaw_digest.get("error") or ""), default="summary_failed")),
                "model_duration_ms": openclaw_digest.get("model_duration_ms"),
                "model": openclaw_digest.get("model"),
                "agent": openclaw_digest.get("agent"),
                "source_input_file": openclaw_digest.get("source_input_file"),
                "coverage_manifest_file": openclaw_digest.get("coverage_manifest_file"),
                "input_transport": openclaw_digest.get("input_transport"),
                "prompt_artifact_file": openclaw_digest.get("prompt_artifact_file"),
                "prompt_artifact_sha256": openclaw_digest.get("prompt_artifact_sha256"),
                "fallback_used": bool(openclaw_digest.get("fallback_used")),
                "runner": openclaw_digest.get("runner"),
                "openclaw_summary_file": str(openclaw_summary_file),
                "source_item_count": openclaw_digest.get("source_item_count"),
                "ark_review_decisions": openclaw_digest.get("ark_review_decisions"),
                "ark_review_decision_error": openclaw_digest.get("ark_review_decision_error"),
            },
        )

    manifest = {
        "version": 1,
        "phase": args.phase,
        "phase_label": run_def["label"],
        "started_at": started_iso,
        "run_slug": run_slug,
        "attempt_slug": attempt_slug,
        "window": window,
        "config_path": str(config_path),
        "checkpoints_dir": str(checkpoints_dir),
        "dag": dag_spec,
        "entries": entries,
        "auxiliary_flow_checks": auxiliary_flow_checks,
        "raw_flow_classification": raw_flow_classification,
        "openclaw_summary": openclaw_digest,
        "openclaw_summary_file": str(openclaw_summary_file),
        "raw_flow_quality_check": raw_flow_quality_check,
        "validation": {
            "degraded": bool(openclaw_digest.get("degraded")),
            "degraded_reason": openclaw_digest.get("fallback_reason") if openclaw_digest.get("degraded") else "",
            "published": False,
        },
        "resume": {
            "enabled": bool(args.resume or args.from_stage),
            "from_stage": force_from or None,
            "reused_manifest": str(manifest_path) if previous_manifest else None,
        },
        "checkpoint": "quality_check_done",
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    if stop_after == "quality_check":
        manifest["validation"]["delivery_blocked"] = True
        manifest["validation"]["reason"] = "stopped after quality_check by operator recovery preflight"
        manifest["stop_after"] = "quality_check"
        manifest["checkpoint"] = "quality_check_done_stop_after"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"manifest={manifest_path}")
        print(f"entries={len(entries)}")
        print("stopped_after=quality_check")
        return 0

    write_markdown_report(
        path=report_path,
        phase=args.phase,
        phase_label=run_def["label"],
        run_started=started_iso,
        window=window,
        entries=entries,
        manifest_path=manifest_path,
        all_items=all_items,
        serial_by_item=serial_by_item,
        openclaw_digest=openclaw_digest,
        raw_flow_classification=raw_flow_classification,
    )

    latest_path = output_root / "latest.md"
    latest_path.parent.mkdir(parents=True, exist_ok=True)
    latest_path.write_text(report_path.read_text(encoding="utf-8"), encoding="utf-8")
    write_checkpoint(
        "render",
        {"status": "done", "report_path": str(report_path), "latest_path": str(latest_path)},
    )

    if stop_after == "render":
        manifest["validation"]["delivery_blocked"] = True
        manifest["validation"]["reason"] = "stopped after render by operator recovery preflight"
        manifest["stop_after"] = "render"
        manifest["checkpoint"] = "render_done_stop_after"
        manifest["render"] = {"report_path": str(report_path), "latest_path": str(latest_path)}
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"report={report_path}")
        print(f"manifest={manifest_path}")
        print(f"entries={len(entries)}")
        print("stopped_after=render")
        return 0

    failures = [e for e in entries if e["returncode"] != 0]
    api_failures = [e for e in entries if not e["api_ok"]]
    feed_health_reports = {
        e.get("id"): e.get("feed_health")
        for e in entries
        if isinstance(e.get("feed_health"), dict)
    }
    allow_degraded_publication = bool((config.get("pipeline") or {}).get("allow_degraded_publication", False))
    if failures or api_failures:
        manifest["validation"] = {
            "failures": [e["id"] for e in failures],
            "api_failures": [e["id"] for e in api_failures],
            "published": False,
            "degraded": bool(all_items and allow_degraded_publication),
            "reason": "collection had partial failures",
            "source_health": feed_health_reports,
            "operator_action": "check failed sources, verify recovery, then decide whether to retry, use alternatives, or approve degraded publication",
            "publication_policy": "degraded publication requires explicit approval; do not auto-publish partial collection by default",
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        if not all_items or not allow_degraded_publication:
            failure_delivery = deliver_report(
                config=config,
                phase=args.phase,
                phase_label=run_def["label"],
                report_path=report_path,
                manifest_path=manifest_path,
                status="failed",
                reason="collection failed; no reliable degraded report was published",
            )
            manifest["delivery"] = failure_delivery
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"report={report_path}")
            print(f"manifest={manifest_path}")
            print(f"entries={len(entries)}")
            if failures:
                print(f"failures={len(failures)}", file=sys.stderr)
                return 2
            print(f"api_failures={len(api_failures)}", file=sys.stderr)
            return 3
        print(
            f"degraded_collection=failures:{len(failures)},api_failures:{len(api_failures)}; publishing with available items",
            file=sys.stderr,
        )

    previous_notion = previous_manifest.get("notion") if isinstance(previous_manifest.get("notion"), dict) else {}
    previous_notion_failed = bool(
        not previous_notion
        or previous_notion.get("error")
        or previous_notion.get("attempted") is False
        or not previous_notion.get("url")
    )
    if can_reuse("publish") and previous_notion and not previous_notion_failed:
        notion_delivery = previous_notion or {}
    else:
        notion_delivery = publish_notion_page(
            config=config,
            env=env,
            phase=args.phase,
            phase_label=run_def["label"],
            report_path=report_path,
        )
    write_checkpoint(
        "publish",
        {
            "status": "failed" if notion_delivery.get("error") else "done",
            "attempted": bool(notion_delivery.get("attempted")),
            "url": notion_delivery.get("url"),
            "error": notion_delivery.get("error"),
            "failure_type": classify_failure(str(notion_delivery.get("error") or ""), default="notion_publish_failed") if notion_delivery.get("error") else None,
            "reason": notion_delivery.get("reason"),
        },
    )
    notion_config = config.get("notion") or {}
    notion_publish_failed = bool(
        args.phase != "smoke"
        and notion_config.get("enabled")
        and (not notion_delivery.get("attempted") or notion_delivery.get("error"))
    )
    validation_degraded = bool((manifest.get("validation") or {}).get("degraded"))
    delivery_status = "failed" if notion_publish_failed else "degraded" if validation_degraded else "complete"
    delivery_reason = ""
    if notion_publish_failed:
        delivery_reason = f"Notion publish failed: {notion_delivery.get('error') or notion_delivery.get('reason') or 'not attempted'}"
    elif validation_degraded:
        delivery_reason = "部分数据源失败，已用可用信息发布降级版"
    previous_delivery = previous_manifest.get("delivery") if isinstance(previous_manifest.get("delivery"), dict) else {}
    if not previous_delivery:
        notify_checkpoint_path = checkpoints_dir / "notify.json"
        if notify_checkpoint_path.exists():
            try:
                notify_checkpoint = load_json(notify_checkpoint_path)
                if isinstance(notify_checkpoint, dict):
                    previous_delivery = {
                        "attempted": notify_checkpoint.get("attempted"),
                        "returncode": notify_checkpoint.get("returncode"),
                        "exception": notify_checkpoint.get("exception"),
                        "notion_url": notify_checkpoint.get("notion_url"),
                        "status": notify_checkpoint.get("delivery_status"),
                        "reason": notify_checkpoint.get("reason"),
                    }
            except Exception:
                previous_delivery = {}
    previous_delivery_failed = bool(
        previous_delivery.get("attempted") is False
        or previous_delivery.get("reason") == "background_silent_telegram"
        or previous_delivery.get("exception")
        or (previous_delivery.get("returncode") is not None and int(previous_delivery.get("returncode") or 0) != 0)
    )
    previous_delivery_message = str(previous_delivery.get("message") or "")
    previous_delivery_reason = str(previous_delivery.get("reason") or "")
    previous_delivery_matches = bool(
        previous_delivery
        and previous_delivery.get("status") == delivery_status
        and (
            (
                previous_delivery.get("notion_url")
                and previous_delivery.get("notion_url") == notion_delivery.get("url")
            )
            or (
                delivery_status == "failed"
                and not notion_delivery.get("url")
                and (
                    not delivery_reason
                    or previous_delivery_reason == delivery_reason
                    or sanitize_user_reason(delivery_reason) in previous_delivery_message
                )
            )
        )
    )
    if can_reuse("notify") and previous_delivery and not previous_delivery_failed and previous_delivery_matches:
        delivery = previous_delivery or {}
    else:
        delivery = deliver_report(
            config=config,
            phase=args.phase,
            phase_label=run_def["label"],
            report_path=report_path,
            manifest_path=manifest_path,
            notion_url=notion_delivery.get("url"),
            status=delivery_status,
            reason=delivery_reason,
        )
    delivery_failed_for_checkpoint = bool(delivery.get("exception") or (delivery.get("returncode") is not None and int(delivery.get("returncode") or 0) != 0))
    write_checkpoint(
        "notify",
        {
            "status": "failed" if delivery_failed_for_checkpoint else "degraded" if openclaw_digest.get("degraded") else "done",
            "attempted": bool(delivery.get("attempted")),
            "returncode": delivery.get("returncode"),
            "exception": delivery.get("exception"),
            "notion_url": notion_delivery.get("url"),
            "delivery_status": delivery_status,
            "reason": delivery_reason,
            "formal": not bool(openclaw_digest.get("degraded")),
            "requires_formal_retry": bool(openclaw_digest.get("degraded")),
        },
    )
    manifest["notion"] = notion_delivery
    manifest["delivery"] = delivery
    if isinstance(manifest.get("validation"), dict):
        manifest["validation"]["published"] = bool(notion_delivery.get("attempted") and not notion_delivery.get("error"))
        if manifest["validation"]["published"] and not delivery_failed_for_checkpoint:
            manifest["validation"]["delivery_blocked"] = False
            manifest["validation"].pop("requires_formal_retry", None)
    if notion_publish_failed:
        manifest["checkpoint"] = "publish_failed"
    elif delivery_failed_for_checkpoint:
        manifest["checkpoint"] = "notify_failed"
    else:
        manifest["checkpoint"] = "notify_done"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"report={report_path}")
    print(f"manifest={manifest_path}")
    print(f"entries={len(entries)}")
    if delivery.get("attempted"):
        print(f"delivery_returncode={delivery.get('returncode', 'exception')}")
    telegram_config = config.get("telegram") or {}
    if args.phase != "smoke" and telegram_config.get("enabled") and delivery.get("attempted"):
        delivery_failed = delivery.get("exception") or (
            delivery.get("returncode") is not None and int(delivery.get("returncode") or 0) != 0
        )
        if delivery_failed:
            print(
                f"telegram_delivery_failed={delivery.get('exception') or delivery.get('stderr') or delivery.get('returncode')}",
                file=sys.stderr,
            )
            background_tasks.fail_task(
                background_task_id,
                error_kind="telegram_delivery_failed",
                error_summary="Telegram Notion 链接通知失败，前序产物已生成。",
                checkpoint_path=checkpoints_dir / "notify.json",
                artifacts=[manifest_path, report_path],
                needs_review=False,
            )
            background_tasks.queue_notification(
                background_task_id,
                kind="market_delivery_failed",
                title=f"{day}{run_def['label']}通知失败",
                summary="Telegram Notion 链接通知失败，前序产物已生成。",
                severity="warning",
                artifact_paths=[manifest_path, report_path, checkpoints_dir / "notify.json"],
            )
            return 7
    if args.phase != "smoke" and notion_config.get("enabled"):
        if notion_publish_failed:
            print(
                f"notion_publish_failed={notion_delivery.get('error') or notion_delivery.get('reason') or 'not attempted'}",
                file=sys.stderr,
            )
            if classify_failure(str(notion_delivery.get("error") or notion_delivery.get("reason") or "")) == "notion_validation_error":
                background_tasks.fail_task(
                    background_task_id,
                    error_kind="notion_validation_error",
                    error_summary=sanitize_user_reason(str(notion_delivery.get("error") or notion_delivery.get("reason") or "")),
                    checkpoint_path=checkpoints_dir / "publish.json",
                    artifacts=[manifest_path, report_path],
                    needs_review=True,
                )
                background_tasks.queue_notification(
                    background_task_id,
                    kind="market_needs_review",
                    title=f"{day}{run_def['label']}需要审核",
                    summary=sanitize_user_reason(str(notion_delivery.get("error") or notion_delivery.get("reason") or "")),
                    severity="error",
                    artifact_paths=[manifest_path, report_path, checkpoints_dir / "publish.json"],
                )
                return 3
            background_tasks.fail_task(
                background_task_id,
                error_kind="notion_publish_failed",
                error_summary=sanitize_user_reason(str(notion_delivery.get("error") or notion_delivery.get("reason") or "not attempted")),
                checkpoint_path=checkpoints_dir / "publish.json",
                artifacts=[manifest_path, report_path],
                needs_review=False,
            )
            background_tasks.queue_notification(
                background_task_id,
                kind="market_failed",
                title=f"{day}{run_def['label']}未发布",
                summary=sanitize_user_reason(str(notion_delivery.get("error") or notion_delivery.get("reason") or "not attempted")),
                severity="warning",
                artifact_paths=[manifest_path, report_path, checkpoints_dir / "publish.json"],
            )
            return 6
    if args.phase != "smoke":
        state_path = output_root / "state.json"
        state_path.write_text(
            json.dumps(
                {
                    "last_success_at": started_iso,
                    "last_success_phase": args.phase,
                    "last_success_report": str(report_path),
                    "last_success_manifest": str(manifest_path),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    if openclaw_digest.get("degraded"):
        background_tasks.fail_task(
            background_task_id,
            error_kind="formal_digest_required",
            error_summary="当前只生成了降级摘要占位，不满足正式简报要求；等待 supervisor 后续重跑正式 digest。",
            checkpoint_path=checkpoints_dir / "digest.json",
            artifacts=[manifest_path, report_path, checkpoints_dir / "publish.json", checkpoints_dir / "notify.json"],
            needs_review=False,
        )
        background_tasks.queue_notification(
            background_task_id,
            kind="market_failed",
            title=f"{day}{run_def['label']}未发布",
            summary="当前只生成了降级摘要占位，不满足正式简报要求；等待 supervisor 后续重跑正式 digest。",
            severity="warning",
            artifact_paths=[manifest_path, report_path, checkpoints_dir / "publish.json", checkpoints_dir / "notify.json"],
        )
    else:
        background_tasks.finish_task(
            background_task_id,
            artifacts=[manifest_path, report_path, checkpoints_dir / "publish.json", checkpoints_dir / "notify.json"],
            summary=f"{day}{run_def['label']}完成；Notion={notion_delivery.get('url') or ''}",
            main_review_required=True,
        )
        background_tasks.queue_notification(
            background_task_id,
            kind="market_completed",
            title=f"{day}{run_def['label']}完成",
            summary=f"Notion={notion_delivery.get('url') or ''}",
            severity="info",
            artifact_paths=[manifest_path, report_path, checkpoints_dir / "publish.json", checkpoints_dir / "notify.json"],
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
