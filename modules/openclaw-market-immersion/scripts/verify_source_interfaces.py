#!/usr/bin/env python3
"""Verify backup market-data interfaces against their official pages.

This is NOT a local snapshot collector. It is a source-registry verifier:
- fetch candidate interfaces;
- fetch the corresponding official page;
- compare returned items against official-page text;
- mark candidates backup_ready only when consistency is strong enough.

The script is read-only with respect to external services and never publishes reports.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_REGISTRY = SCRIPT_DIR.parent / "config" / "source_registry.json"
DEFAULT_OUTPUT_DIR = Path.home() / ".openclaw" / "workspace" / "market-immersion" / "source-interface-verification"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&[a-zA-Z]+;", " ", text)
    text = re.sub(r"\s+", "", text)
    return text.strip().lower()


def compact_for_match(value: Any, max_len: int = 32) -> str:
    text = normalize_text(value)
    text = re.sub(r"[，。、“”‘’：:；;（）()\[\]【】,.!?！？\-—_·|/\\]", "", text)
    return text[:max_len]


def get_path(obj: Any, path: str) -> Any:
    cur = obj
    if not path:
        return cur
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit():
            cur = cur[int(part)]
        else:
            return None
    return cur


def first_field(row: dict[str, Any], spec: str) -> Any:
    for part in str(spec or "").split("|"):
        part = part.strip()
        if not part:
            continue
        value = get_path(row, part)
        if value not in (None, "", [], {}):
            return value
    return ""


def cls_query_sign(query: str) -> str:
    # Same public web signing convention used by the market-immersion collector.
    return hashlib.md5((query + "cailianpressweb").encode("utf-8")).hexdigest()


def prepared_params(candidate: dict[str, Any]) -> dict[str, str]:
    now_ts = str(int(time.time()))
    now_ms = str(int(time.time() * 1000))
    params = {}
    for key, value in (candidate.get("params") or {}).items():
        value = str(value).replace("NOW_TS", now_ts)
        value = value.replace("NOW_MS", now_ms)
        params[str(key)] = value
    return params


def fetch_text(url: str, *, timeout: int, headers: dict[str, str] | None = None) -> str:
    request_headers = {
        "User-Agent": "Mozilla/5.0 OpenClaw Source Interface Verifier",
        "Accept": "text/html,application/xhtml+xml,application/xml,text/plain,*/*",
    }
    if headers:
        request_headers.update({str(k): str(v) for k, v in headers.items()})
    req = urllib.request.Request(url, headers=request_headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def fetch_json_candidate(candidate: dict[str, Any], *, timeout: int) -> tuple[str, dict[str, Any]]:
    params = prepared_params(candidate)
    url = str(candidate.get("url") or "")
    if candidate.get("kind") == "cls_signed_json_endpoint":
        query = urllib.parse.urlencode(params)
        params["sign"] = cls_query_sign(query)
    query = urllib.parse.urlencode(params)
    full_url = url + ("&" if "?" in url else "?") + query if query else url
    text = fetch_text(full_url, timeout=timeout, headers=candidate.get("headers") or None)
    return full_url, json.loads(text)


def extract_items(data: dict[str, Any], candidate: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    rows = get_path(data, str(candidate.get("items_path") or ""))
    if not isinstance(rows, list):
        return []
    fields = candidate.get("fields") or {}
    items = []
    for row in rows[:limit]:
        if not isinstance(row, dict):
            continue
        title = str(first_field(row, fields.get("title", "title")) or "")
        content = str(first_field(row, fields.get("content", "content")) or "")
        items.append(
            {
                "title": re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(title))).strip(),
                "content": re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(content))).strip(),
                "time": str(first_field(row, fields.get("time", "time")) or ""),
                "url": str(first_field(row, fields.get("url", "url")) or ""),
            }
        )
    return items


def discover_candidate_hints(official_html: str) -> list[str]:
    hints = []
    for match in re.finditer(r"https?://[^'\"<>\s]+|/[A-Za-z0-9_./?=&%-]+", official_html):
        url = match.group(0)
        lowered = url.lower()
        if any(token in lowered for token in ("api", "flash", "live", "kuaixun", "telegraph", "news", "zhibo", "push")):
            if url not in hints:
                hints.append(url)
        if len(hints) >= 80:
            break
    return hints


def script_urls_from_html(official_url: str, official_html: str) -> list[str]:
    urls: list[str] = []
    for match in re.finditer(r"<script[^>]+src=[\"']([^\"']+)[\"']", official_html, flags=re.I):
        src = html.unescape(match.group(1))
        if not src:
            continue
        resolved = urllib.parse.urljoin(official_url, src)
        if resolved not in urls:
            urls.append(resolved)
        if len(urls) >= 20:
            break
    return urls


def discover_official_network_hints(
    official_url: str,
    official_html: str,
    *,
    timeout: int,
) -> dict[str, Any]:
    """Find interfaces referenced by the official page and its JS bundles."""
    hints = discover_candidate_hints(official_html)
    scripts = script_urls_from_html(official_url, official_html)
    scanned_scripts: list[dict[str, Any]] = []
    for script_url in scripts[:12]:
        try:
            text = fetch_text(script_url, timeout=timeout)
            scanned_scripts.append({"url": script_url, "ok": True, "bytes": len(text)})
        except Exception as exc:  # noqa: BLE001
            scanned_scripts.append({"url": script_url, "ok": False, "error": str(exc)})
            continue
        for hint in discover_candidate_hints(text):
            resolved = urllib.parse.urljoin(script_url, hint)
            if resolved not in hints:
                hints.append(resolved)
    return {"hints": hints[:200], "scripts": scanned_scripts}


def candidate_discovered_by_official_page(candidate: dict[str, Any], hints: list[str]) -> bool:
    candidate_url = str(candidate.get("url") or "")
    if not candidate_url:
        return False
    parsed_candidate = urllib.parse.urlparse(candidate_url)
    candidate_host = parsed_candidate.netloc.lower()
    candidate_path = parsed_candidate.path.rstrip("/")
    candidate_endpoint = candidate_path.rsplit("/", 1)[-1] if candidate_path else ""
    host_seen = False
    path_seen = False
    for raw_hint in hints:
        hint = str(raw_hint or "")
        parsed_hint = urllib.parse.urlparse(hint if not hint.startswith("//") else "https:" + hint)
        hint_host = parsed_hint.netloc.lower()
        hint_path = parsed_hint.path.rstrip("/")
        if candidate_host and candidate_host == hint_host:
            host_seen = True
        if candidate_path and (candidate_path == hint_path or candidate_path in hint):
            path_seen = True
        if candidate_endpoint and candidate_endpoint in hint:
            path_seen = True
        if candidate_host and hint_host and candidate_host == hint_host and candidate_path and candidate_path == hint_path:
            return True
        if candidate_path and candidate_path in hint:
            return True
    if host_seen and path_seen:
        return True
    return False


def verify_source(source: dict[str, Any], *, timeout: int, sample_size: int, threshold: float) -> dict[str, Any]:
    official_url = str(source.get("official_url") or "")
    started = dt.datetime.now().astimezone()
    result: dict[str, Any] = {
        "source_id": source.get("id"),
        "source_name": source.get("name"),
        "official_url": official_url,
        "started_at": started.isoformat(timespec="seconds"),
        "candidates": [],
        "discovered_hints": [],
    }
    official_html = ""
    official_text = ""
    official_network: dict[str, Any] = {"hints": [], "scripts": []}
    try:
        official_html = fetch_text(official_url, timeout=timeout)
        official_text = normalize_text(official_html)
        result["official_fetch"] = {"ok": True, "bytes": len(official_html)}
        official_network = discover_official_network_hints(official_url, official_html, timeout=timeout)
        result["discovered_hints"] = official_network.get("hints") or []
        result["scanned_scripts"] = official_network.get("scripts") or []
    except Exception as exc:  # noqa: BLE001
        result["official_fetch"] = {"ok": False, "error": str(exc)}

    for candidate in source.get("candidates") or []:
        candidate_result: dict[str, Any] = {
            "candidate_id": candidate.get("id"),
            "kind": candidate.get("kind"),
            "backup_ready": False,
            "endpoint_ok": False,
            "official_consistency_ok": False,
        }
        try:
            fetched_url, data = fetch_json_candidate(candidate, timeout=timeout)
            items = extract_items(data, candidate, sample_size)
            candidate_result.update({"endpoint_ok": True, "fetched_url": fetched_url, "item_count": len(items)})
            official_discovered = candidate_discovered_by_official_page(candidate, official_network.get("hints") or [])
            matches = []
            for item in items:
                probes = [compact_for_match(item.get("title")), compact_for_match(item.get("content"))]
                probes = [probe for probe in probes if len(probe) >= 8]
                matched = bool(official_text) and any(probe in official_text for probe in probes)
                matches.append({"title": item.get("title"), "time": item.get("time"), "matched_official_page": matched})
            match_count = sum(1 for row in matches if row.get("matched_official_page"))
            ratio = (match_count / len(matches)) if matches else 0.0
            consistency_mode = "official_html_text_match"
            consistency_ok = bool(result.get("official_fetch", {}).get("ok")) and ratio >= threshold
            if official_discovered and items:
                # For SPA/client-rendered official pages, the server HTML often
                # does not contain current flash items. If the candidate endpoint
                # is explicitly referenced by the official page/JS and returns
                # items, it is the official presentation interface.
                consistency_ok = True
                consistency_mode = "official_page_network_interface_discovered"
            candidate_result.update(
                {
                    "official_consistency_ok": consistency_ok,
                    "official_network_discovered": official_discovered,
                    "consistency_mode": consistency_mode,
                    "match_count": match_count,
                    "match_ratio": ratio,
                    "sample": matches[:sample_size],
                    "backup_ready": bool(candidate_result["endpoint_ok"] and consistency_ok),
                }
            )
        except Exception as exc:  # noqa: BLE001
            candidate_result["error"] = str(exc)
        result["candidates"].append(candidate_result)

    result["backup_ready_candidates"] = [
        c.get("candidate_id") for c in result["candidates"] if c.get("backup_ready")
    ]
    result["status"] = "backup_ready" if result["backup_ready_candidates"] else "needs_review"
    result["completed_at"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify backup source interfaces against official pages.")
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--source", action="append", help="Source id/name to verify; repeatable. Defaults to all.")
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--sample-size", type=int, default=8)
    parser.add_argument("--threshold", type=float, default=0.5)
    args = parser.parse_args()

    registry_path = Path(args.registry).expanduser().resolve()
    registry = load_json(registry_path)
    requested = set(args.source or [])
    sources = registry.get("sources") or []
    if requested:
        sources = [s for s in sources if s.get("id") in requested or s.get("name") in requested]
    if not sources:
        print("no sources selected", file=sys.stderr)
        return 2

    output_dir = Path(args.output_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "version": 1,
        "kind": "source_interface_verification",
        "registry": str(registry_path),
        "started_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "policy": registry.get("policy") or {},
        "results": [],
    }
    for source in sources:
        report["results"].append(
            verify_source(source, timeout=args.timeout, sample_size=args.sample_size, threshold=args.threshold)
        )
    report["completed_at"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    report["summary"] = {
        "sources": len(report["results"]),
        "backup_ready_sources": sum(1 for row in report["results"] if row.get("status") == "backup_ready"),
        "needs_review_sources": sum(1 for row in report["results"] if row.get("status") != "backup_ready"),
    }
    stamp = dt.datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    out = output_dir / f"{stamp}_source_interface_verification.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    latest = output_dir / "latest.json"
    latest.write_text(json.dumps({"report": str(out), "summary": report["summary"]}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"report={out}")
    print(json.dumps(report["summary"], ensure_ascii=False))
    return 0 if report["summary"]["backup_ready_sources"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
