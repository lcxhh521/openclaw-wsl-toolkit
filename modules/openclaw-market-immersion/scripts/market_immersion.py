#!/usr/bin/env python3
"""Market information immersion runner for OpenClaw.

Phase 1 design: collect broad market information and preserve raw source output.
This script intentionally avoids strong filtering and trading advice.
"""

from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import hashlib
import html
import json
import os
import re
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


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


def default_window_start(phase: str, end: dt.datetime) -> dt.datetime:
    boundaries = {
        "morning": (1, dt.time(22, 10)),
        "midday": (0, dt.time(9, 5)),
        "close": (0, dt.time(12, 15)),
        "night": (0, dt.time(15, 20)),
    }
    days_back, boundary_time = boundaries.get(phase, (0, dt.time(0, 0)))
    day = (end - dt.timedelta(days=days_back)).date()
    return dt.datetime.combine(day, boundary_time, tzinfo=end.tzinfo)


def compute_window(
    *,
    phase: str,
    output_root: Path,
    end: dt.datetime,
) -> tuple[dt.datetime | None, dt.datetime | None, str]:
    if phase == "smoke":
        return None, None, "smoke"

    state_path = output_root / "state.json"
    state: dict[str, Any] = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            state = {}

    last_success = parse_iso(state.get("last_success_at"))
    default_start = default_window_start(phase, end)
    if last_success and last_success < end:
        return last_success, end, "last_success"
    return default_start, end, "scheduled_boundary"


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


def entity_like_terms(text: str) -> set[str]:
    pattern = (
        r"[\u4e00-\u9fffA-Za-z0-9]{2,24}(?:集团|公司|银行|证券|基金|交易所|委员会|管理局|"
        r"部门|组织|机构|口岸|海峡|隧道|机场|铁路|高速|景区|油田|油轮|法案|指数|学校|单位|省|市)"
    )
    return set(re.findall(pattern, str(text or "")))


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
    overlap = left & right
    return bool(overlap) and len(overlap) / min(len(left), len(right)) >= 0.5


def content_overlap_duplicate(existing: dict[str, Any], incoming: dict[str, Any]) -> bool:
    existing_title = str(existing.get("title") or "")
    incoming_title = str(incoming.get("title") or "")
    existing_content = str(existing.get("content") or "")
    incoming_content = str(incoming.get("content") or "")
    existing_body = normalize_for_duplicate(existing_content, limit=500)
    incoming_body = normalize_for_duplicate(incoming_content, limit=500)
    if len(existing_body) < 24 or len(incoming_body) < 24:
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


def collect_eastmoney_feed_entry(
    *,
    config: dict[str, Any],
    output_dir: Path,
    window_start: dt.datetime | None,
    window_end: dt.datetime | None,
) -> dict[str, Any] | None:
    feed_config = config.get("eastmoney_feed") or {}
    if not feed_config.get("enabled", False):
        return None

    columns = feed_config.get("columns") or []
    if not columns:
        return None

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
                content = strip_html(row.get("rich_text") or "")
                title = content[:80] or str(row.get("id") or "")
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
                title = str(row.get("title") or "").strip() or content[:80]
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
    return {
        "id": "eastmoney_feed",
        "skill": "eastmoney-feed",
        "base_query": "东方财富资讯流按时间窗口分页采集",
        "query": "东方财富资讯流按时间窗口分页采集",
        "returncode": 0 if not messages else 1,
        "api_ok": not messages,
        "api_messages": messages,
        "classification": {"counts": counts, "items": items},
        "feed_coverage": coverage,
        "duration_ms": int((time.time() - started) * 1000),
        "stdout": stdout,
        "stderr": "",
        "stdout_file": "",
        "stderr_file": "",
        "raw_files": raw_files,
    }


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


def raw_message_meta_line(item: dict[str, Any]) -> str:
    parts = []
    if item.get("date"):
        parts.append(str(item["date"]))
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


def build_openclaw_summary_prompt(
    *,
    phase_label: str,
    window: dict[str, Any],
    all_items: list[dict[str, Any]],
    max_item_chars: int,
) -> str:
    payload_items = build_openclaw_payload_items(
        all_items=all_items,
        max_item_chars=max_item_chars,
    )
    return (
        "你是财经信息日报编辑。任务不是投研判断，也不是交易建议，而是把原始信息压缩成有细节、有主线、克制可靠的高密度综述。\n"
        "请基于原始消息，生成前后连贯、逻辑清晰的中文信息汇总。\n"
        "规则：\n"
        "1. 只整理事实和消息含义，不预测涨跌，不给买卖建议。\n"
        "2. 不要按栏目拆分，不要写编号列表，不要输出信源、原文编号、状态、时间归类、信息类型等工程字段。\n"
        "3. 输入里的 content 是正文材料；title 只作为判断归类的辅助证据。content_quality 为 title_only/title_like 的消息要当作短快讯，不要扩写成不存在的正文。\n"
        "4. 只有当时间是消息报道的事件时间、截止时间、会议时间、披露时间等内容本身的一部分时，才写入整理正文。\n"
        "5. 每段都必须包含具体主体、关键数字或明确事件细节；不能只写“热度较高、值得关注、提供线索、存在扰动”这类空话。\n"
        "6. 对重复、同主题、跨信源的信息要合并表达，写出它们之间的关系：是互相印证、边际变化、分歧，还是同一事件的不同侧面。\n"
        "7. 必须保留重要分歧和风险表述；遇到情绪化或观点化消息，要保留其观点属性，不把它写成事实。\n"
        "8. 可以有轻度判断，但必须克制：判断只能来自原文中相邻事实的直接关系，不能上升到宏观结论或抽象判断。\n"
        "9. 汇总部分写成 3-5 个自然段，每段 160-320 个中文字符；段落之间要有承接关系，像编辑写的日报正文，不像摘要拼接。\n"
        "10. 判断句必须带证据，不要写没有原文支撑的因果、目的和趋势；优先使用“同时出现”“相互印证”“仍需区分”“尚不能推出”等低强度表达。\n"
        "11. 禁止使用空泛或过度分析句，例如“后续值得关注”“整体来看”“提供线索”“需要继续观察”“共同指向”“出现错位”“对冲不确定性”“结构活跃”。\n"
        "12. 不要为了显得有洞察而替消息下结论；如果只能看到并列事实，就写成并列事实，不要强行提炼大主题。\n"
        "13. 返回严格 JSON，不要 Markdown，不要代码块。\n"
        "JSON 格式：\n"
        '{"summary_paragraphs":["...","..."],"observation":{"repeated_words":["..."],"multi_day_themes":["..."],"watch_next":["..."]}}\n'
        f"阶段：{phase_label}\n"
        f"窗口：{window.get('start') or '无'} 至 {window.get('end') or '无'}\n"
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
    chunk_chars: int,
) -> list[str]:
    payload_items = build_openclaw_payload_items(
        all_items=all_items,
        max_item_chars=max_item_chars,
    )
    payload_text = json.dumps(payload_items, ensure_ascii=False)
    chunks = chunk_text(payload_text, chunk_chars)
    messages = [
        (
            "你是财经信息日报编辑。任务不是投研判断，也不是交易建议，而是把原始信息压缩成有细节、有主线、克制可靠的高密度综述。\n"
            "接下来我会分片发送原始消息 JSON。请先只确认已接收，不要生成日报。\n"
            "最终要求：生成前后连贯、逻辑清晰的中文信息汇总；不要按栏目拆分，不要写编号列表；"
            "输入里的content是正文材料，title只作归类辅助；content_quality为title_only/title_like的消息按短快讯处理，不要扩写；"
            "每段必须包含具体主体、关键数字或明确事件细节；重复和同主题信息要合并出互相印证、边际变化、分歧或同一事件不同侧面；"
            "可以有轻度判断，但判断只能来自原文中相邻事实的直接关系，不能上升到宏观结论或抽象判断；"
            "优先使用“同时出现”“相互印证”“仍需区分”“尚不能推出”等低强度表达；"
            "禁止空泛或过度分析句，例如“后续值得关注”“整体来看”“提供线索”“需要继续观察”“共同指向”“出现错位”“对冲不确定性”“结构活跃”；"
            "如果只能看到并列事实，就写成并列事实，不要强行提炼大主题；返回严格 JSON，不要 Markdown。\n"
            'JSON格式：{"summary_paragraphs":["...","..."],"observation":'
            '{"repeated_words":["..."],"multi_day_themes":["..."],"watch_next":["..."]}}\n'
            f"阶段：{phase_label}\n"
            f"窗口：{window.get('start') or '无'} 至 {window.get('end') or '无'}\n"
            f"原始消息总数：{len(payload_items)}，分片数：{len(chunks)}"
        )
    ]
    for index, chunk in enumerate(chunks, 1):
        messages.append(f"原始消息分片 {index}/{len(chunks)}：\n{chunk}")
    messages.append(
        "以上原始消息已经发送完毕。现在请根据全部分片生成最终 JSON。"
        "只返回 JSON 对象，不要 Markdown，不要代码块。"
        "质量要求：3-5 个自然段，每段 160-320 个中文字符；每段至少包含两个具体事实颗粒，不能写空泛综述，也不能强行拔高判断。"
    )
    return messages


def generate_openclaw_digest(
    *,
    config: dict[str, Any],
    phase: str,
    phase_label: str,
    window: dict[str, Any],
    all_items: list[dict[str, Any]],
    run_slug: str,
) -> dict[str, Any]:
    summary_config = config.get("openclaw_summary") or {}
    if not summary_config.get("enabled", False):
        return {"enabled": False, "attempted": False}
    if phase == "smoke" and not summary_config.get("summarize_smoke", False):
        return {"enabled": True, "attempted": False, "reason": "smoke summary disabled"}
    if not all_items:
        return {"enabled": True, "attempted": False, "reason": "no items"}

    openclaw_bin = Path(config.get("openclaw_bin") or "openclaw").expanduser()
    prompt = build_openclaw_summary_prompt(
        phase_label=phase_label,
        window=window,
        all_items=all_items,
        max_item_chars=int(summary_config.get("max_item_chars") or 0),
    )
    started = now_local().isoformat(timespec="seconds")
    retries = int(summary_config.get("retries") or 3)
    timeout = int(summary_config.get("timeout") or 300)
    max_message_chars = int(summary_config.get("max_message_chars") or 60000)
    last_error = ""
    for attempt in range(1, retries + 1):
        session_id = f"market-immersion-summary-{run_slug}-{attempt}"
        base_cmd = [
            str(openclaw_bin),
            "agent",
            "--local",
            "--agent",
            str(summary_config.get("agent") or "main"),
            "--session-id",
            session_id,
            "--json",
            "--thinking",
            str(summary_config.get("thinking") or "low"),
            "--timeout",
            str(timeout),
        ]
        if len(prompt) <= max_message_chars:
            messages = [prompt]
        else:
            messages = build_openclaw_chunked_messages(
                phase_label=phase_label,
                window=window,
                all_items=all_items,
                max_item_chars=int(summary_config.get("max_item_chars") or 0),
                chunk_chars=max_message_chars,
            )
        completed = None
        for message in messages:
            completed = subprocess.run(
                [*base_cmd, "--message", message],
                text=True,
                capture_output=True,
                timeout=timeout + 30,
                check=False,
            )
            if completed.returncode != 0:
                break
        if completed is None:
            last_error = "OpenClaw summary produced no turn"
            time.sleep(5)
            continue
        if completed.returncode != 0:
            last_error = completed.stderr[-2000:] or completed.stdout[-2000:]
            time.sleep(5)
            continue
        try:
            data = extract_json_object(completed.stdout)
            text = "\n".join(
                payload.get("text", "")
                for payload in data.get("payloads", [])
                if isinstance(payload, dict)
            )
            digest = extract_json_object(text)
            summary_paragraphs = normalize_summary_paragraphs(digest)
            quality_warnings = summary_quality_warnings(summary_paragraphs, all_items)
            if quality_warnings and attempt < retries:
                last_error = "low quality summary: " + "; ".join(quality_warnings)
                time.sleep(5)
                continue
            return {
                "enabled": True,
                "attempted": True,
                "started_at": started,
                "attempts": attempt,
                "summary_paragraphs": summary_paragraphs,
                "quality_warnings": quality_warnings,
                "sections": digest.get("sections") or {},
                "observation": digest.get("observation") or {},
                "usage": (data.get("meta") or {}).get("agentMeta", {}).get("usage"),
            }
        except Exception as exc:  # noqa: BLE001 - retry malformed model output
            last_error = str(exc)
            time.sleep(5)
    return {
        "enabled": True,
        "attempted": True,
        "started_at": started,
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


def summary_quality_warnings(paragraphs: list[str], all_items: list[dict[str, Any]]) -> list[str]:
    if not paragraphs:
        return ["empty summary"]

    text = "".join(paragraphs)
    warnings: list[str] = []
    if len(all_items) >= 8 and len(text) < 480:
        warnings.append("summary too short for item volume")
    if len(all_items) >= 8 and not re.search(r"\d", text):
        warnings.append("summary has no numeric detail")

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
    lines.append("## 原始消息流")
    lines.append("")
    if not items:
        lines.append("- 本轮暂无可解析原始消息。")
        lines.append("")
        return
    for item in items:
        title = " ".join(str(item.get("title") or "[无标题]").split())
        content = " ".join(str(item.get("content") or "").split())
        body = display_body_without_title(title=title, content=content)
        meta = raw_message_meta_line(item)
        lines.append(f"### {item['serial']}. {title}")
        lines.append("")
        if body:
            lines.append(body)
            lines.append("")
        if meta:
            lines.append(f"{meta}")
            lines.append("")
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
) -> None:
    lines: list[str] = []

    append_summary_digest(lines, openclaw_digest, all_items)
    append_raw_message_flow(lines, all_items)
    path.write_text("\n".join(lines), encoding="utf-8")


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
        raise RuntimeError(f"Notion API {exc.code}: {details}") from exc


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
        notion_request(
            method="PATCH",
            url=f"https://api.notion.com/v1/blocks/{child_id}",
            token=token,
            timeout=timeout,
            payload={"archived": True},
        )
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
    return f"{date_label}{names.get(phase, phase_label)}"


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
    publication_key = f"{report_path.stem[:8]}:{phase}"
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
    if isinstance(existing_publication, dict) and existing_publication.get("page_id"):
        page_id = str(existing_publication.get("page_id"))
        try:
            replace_notion_page_children(page_id, token, blocks, timeout)
            publication_state[publication_key] = {
                **existing_publication,
                "title": title,
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
                "url": existing_publication.get("url"),
                "title": title,
                "block_count": len(blocks),
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
        publication_state[publication_key] = {
            "page_id": existing_page["id"],
            "url": existing_page.get("url"),
            "title": title,
            "published_at": now_local().isoformat(timespec="seconds"),
            "report_path": str(report_path),
            "discovered_from_notion": True,
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
            "skipped_duplicate": True,
            "reason": "same title already exists under Notion parent",
            "publication_key": publication_key,
            "page_id": existing_page["id"],
            "url": existing_page.get("url"),
            "title": title,
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
) -> dict[str, Any]:
    telegram = config.get("telegram") or {}
    if not telegram.get("enabled"):
        return {"enabled": False, "attempted": False}
    if phase == "smoke" and not telegram.get("send_smoke", False):
        return {"enabled": True, "attempted": False, "reason": "smoke delivery disabled"}

    target = str(telegram.get("target") or "").strip()
    if not target:
        return {"enabled": True, "attempted": False, "reason": "missing telegram target"}

    openclaw_bin = Path(config.get("openclaw_bin") or "openclaw").expanduser()
    title = report_title_for_phase(report_path=report_path, phase=phase, phase_label=phase_label)
    message = (
        f"每日快讯简报：{title}\n"
        f"{'Notion：' + notion_url if notion_url else 'Markdown 简报已生成。'}\n"
        f"manifest: {manifest_path}"
    )
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
        }
    except Exception as exc:  # noqa: BLE001 - delivery is best effort
        return {
            "enabled": True,
            "attempted": True,
            "started_at": started,
            "exception": str(exc),
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/market_immersion_config.json")
    parser.add_argument("--phase", default="morning")
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--dry-run", action="store_true")
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

    started = now_local()
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
    day = started.strftime("%Y-%m-%d")
    date_slug = started.strftime("%Y%m%d")
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

    entries: list[dict[str, Any]] = []
    feed_entry = collect_eastmoney_feed_entry(
        config=config,
        output_dir=skill_output_dir,
        window_start=window_start,
        window_end=window_end,
    )
    if feed_entry:
        entries.append(feed_entry)

    mx_supplement = config.get("mx_search_supplement") or {}
    run_mx_queries = bool(mx_supplement.get("enabled", False)) or (
        args.phase == "smoke" and bool(mx_supplement.get("enabled_for_smoke", True))
    )
    for query_def in (run_def["queries"] if run_mx_queries else []):
        query_id = query_def["id"]
        skill = query_def["skill"]
        base_query = query_def["query"]
        query = f"{window_prefix}{base_query}" if window_prefix else base_query
        entry_slug = safe_name(f"{attempt_slug}_{query_id}")
        stdout_path = stdout_dir / f"{entry_slug}.stdout.txt"
        stderr_path = stdout_dir / f"{entry_slug}.stderr.txt"

        before_files = {
            p: p.stat().st_mtime_ns
            for p in skill_output_dir.glob("*")
            if p.is_file()
        }
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
                output_dir=skill_output_dir,
                env=env,
                timeout=args.timeout,
            )
        duration_ms = int((time.time() - t0) * 1000)
        stdout_path.write_text(completed.stdout or "", encoding="utf-8")
        stderr_path.write_text(completed.stderr or "", encoding="utf-8")

        after_files = [p for p in skill_output_dir.glob("*") if p.is_file()]
        raw_files = sorted(
            str(p)
            for p in after_files
            if p.suffix == ".json" and before_files.get(p) != p.stat().st_mtime_ns
        )
        api_ok, api_messages = inspect_api_files(raw_files)
        classification = classify_items(
            raw_files=raw_files,
            window_start=window_start,
            window_end=window_end,
        )
        entries.append(
            {
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
        )

    all_items, serial_by_item = build_report_items(entries)
    openclaw_digest = generate_openclaw_digest(
        config=config,
        phase=args.phase,
        phase_label=run_def["label"],
        window=window,
        all_items=all_items,
        run_slug=run_slug,
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
        "entries": entries,
        "openclaw_summary": openclaw_digest,
    }
    manifest_path = daily_dir / f"{run_slug}.manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

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
        print(f"openclaw_summary_failed={openclaw_digest.get('error') or 'empty summary'}", file=sys.stderr)
        print(f"manifest={manifest_path}")
        return 4

    report_path = daily_dir / f"{run_slug}.md"
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
    )

    latest_path = output_root / "latest.md"
    latest_path.parent.mkdir(parents=True, exist_ok=True)
    latest_path.write_text(report_path.read_text(encoding="utf-8"), encoding="utf-8")

    failures = [e for e in entries if e["returncode"] != 0]
    api_failures = [e for e in entries if not e["api_ok"]]
    if failures or api_failures:
        manifest["validation"] = {
            "failures": [e["id"] for e in failures],
            "api_failures": [e["id"] for e in api_failures],
            "published": False,
            "reason": "collection failed before publication",
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"report={report_path}")
        print(f"manifest={manifest_path}")
        print(f"entries={len(entries)}")
        if failures:
            print(f"failures={len(failures)}", file=sys.stderr)
            return 2
        print(f"api_failures={len(api_failures)}", file=sys.stderr)
        return 3

    notion_delivery = publish_notion_page(
        config=config,
        env=env,
        phase=args.phase,
        phase_label=run_def["label"],
        report_path=report_path,
    )
    delivery = deliver_report(
        config=config,
        phase=args.phase,
        phase_label=run_def["label"],
        report_path=report_path,
        manifest_path=manifest_path,
        notion_url=notion_delivery.get("url"),
    )
    manifest["notion"] = notion_delivery
    manifest["delivery"] = delivery
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"report={report_path}")
    print(f"manifest={manifest_path}")
    print(f"entries={len(entries)}")
    if delivery.get("attempted"):
        print(f"delivery_returncode={delivery.get('returncode', 'exception')}")
    notion_config = config.get("notion") or {}
    if args.phase != "smoke" and notion_config.get("enabled"):
        if not notion_delivery.get("attempted") or notion_delivery.get("error"):
            print(
                f"notion_publish_failed={notion_delivery.get('error') or notion_delivery.get('reason') or 'not attempted'}",
                file=sys.stderr,
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
