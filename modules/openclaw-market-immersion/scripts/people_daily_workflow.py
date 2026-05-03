#!/usr/bin/env python3
"""People's Daily deep-read workflow for the OpenClaw market module."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

from people_daily_deep_read import collect_issue, layout_url_for_date


NOTION_VERSION = "2022-06-28"


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


def compact(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def notion_request(
    *,
    method: str,
    url: str,
    token: str,
    payload: dict[str, Any] | None = None,
    timeout: int = 90,
) -> dict[str, Any]:
    data = None
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
    }
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def rich_text(text: str, href: str | None = None) -> list[dict[str, Any]]:
    value: dict[str, Any] = {"type": "text", "text": {"content": str(text or "")[:2000]}}
    if href:
        value["text"]["link"] = {"url": href}
    return [value]


def block(block_type: str, text: str = "", href: str | None = None) -> dict[str, Any]:
    if block_type == "child_page":
        return {"object": "block", "type": "child_page", "child_page": {"title": text[:180]}}
    if block_type == "to_do":
        return {"object": "block", "type": "to_do", "to_do": {"rich_text": rich_text(text, href), "checked": False}}
    if block_type.startswith("heading_"):
        return {"object": "block", "type": block_type, block_type: {"rich_text": rich_text(text, href)}}
    return {"object": "block", "type": block_type, block_type: {"rich_text": rich_text(text, href)}}


def chunked(values: list[dict[str, Any]], size: int = 90) -> list[list[dict[str, Any]]]:
    return [values[i : i + size] for i in range(0, len(values), size)]


def format_issue_title(day: dt.date) -> str:
    return day.strftime("%Y年%m月%d日")


def issue_date_from_manifest(manifest: dict[str, Any]) -> dt.date:
    return dt.date.fromisoformat(manifest["issue"]["date"])


def page_label_for(article: dict[str, Any]) -> str:
    return str(article.get("page_label") or f"第{article.get('page_no') or ''}版")


def article_page_title(article: dict[str, Any], serial: int) -> str:
    return f"{page_label_for(article)} {serial:02d} {article.get('title') or '未命名'}"


def is_editorial_metadata(article: dict[str, Any]) -> bool:
    title = str(article.get("title") or "")
    if title.startswith("本版责编") or "责编：" in title or "版式设计" in title:
        return True
    return int(article.get("char_count") or 0) <= 40 and ("责编" in title or "邮箱" in title)


def detailed_articles(manifest: dict[str, Any], max_page_no: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for article in manifest.get("articles") or []:
        try:
            page_no = int(str(article.get("page_no") or "99"))
        except ValueError:
            page_no = 99
        if page_no <= max_page_no and not is_editorial_metadata(article):
            result.append(article)
    return result


def short_excerpt(paragraph: str, limit: int = 24) -> str:
    text = compact(paragraph)
    if len(text) <= limit:
        return text
    return text[:limit].rstrip("，。；、 ") + "..."


def number_tokens(text: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"\d+(?:\.\d+)?%?|\d+/\d+", text)))[:10]


def entity_terms(text: str) -> list[str]:
    patterns = [
        r"[\u4e00-\u9fff]{2,12}(?:省|市|县|区|镇|村|大学|学院|中心|集团|公司|会议|工程|项目|园区|合作区)",
        r"(?:中国|美国|日本|东盟|非洲|匈牙利|斯威士兰|伊朗|南京|东京|上海|北京|雄安|骆驼湾|高黎贡山|可可西里)",
    ]
    values: list[str] = []
    for pattern in patterns:
        values.extend(re.findall(pattern, text))
    clean: list[str] = []
    for value in values:
        value = compact(value)
        if 2 <= len(value) <= 16 and value not in clean:
            clean.append(value)
    return clean[:10]


def paragraph_role(paragraph: str, index: int, total: int) -> str:
    text = compact(paragraph)
    if index == 1:
        return "开篇定调"
    if index == total:
        return "收束落点"
    if "？" in text:
        return "提出问题"
    if number_tokens(text):
        return "事实或数据支撑"
    if any(k in text for k in ("表示", "指出", "强调", "介绍", "认为", "说")):
        return "引述主体观点"
    if any(k in text for k in ("同时", "此外", "再看", "另一方面")):
        return "展开层次"
    return "推进论证"


def deterministic_article_analysis(article: dict[str, Any]) -> dict[str, Any]:
    paragraphs = [compact(p) for p in article.get("paragraphs") or [] if compact(p)]
    body = "\n".join(paragraphs)
    nums = "、".join(number_tokens(body)) or "未明显依赖数字"
    ents = "、".join(entity_terms(body)) or "以主题性表达为主"
    paragraph_notes = []
    for index, paragraph in enumerate(paragraphs, 1):
        role = paragraph_role(paragraph, index, len(paragraphs))
        paragraph_notes.append(
            {
                "excerpt": short_excerpt(paragraph),
                "analysis": (
                    f"这一段承担“{role}”功能。它与标题《{article.get('title') or ''}》的关系，"
                    "在于把材料从事实描述推进到主题判断；阅读时应注意段内出现的主体、数字和场景如何支撑全文论点。"
                ),
            }
        )
    full = [
        f"核心命题：这篇文章不是孤立材料，而是在{page_label_for(article)}中承担特定主题功能。",
        f"论证方式：文章通过开篇定调、事实铺陈、主体引述和收束判断形成链条；关键数字包括：{nums}。",
        f"现实指向：文章涉及的关键对象包括：{ents}。这些对象提示它与当天其他版面之间的政策或叙事关联。",
        "深度判断：阅读时不要只抓标题，要看文章选择哪些事实作为证据、如何安排段落顺序、最终把读者引向哪一种政策理解或价值判断。",
    ]
    return {"paragraph_notes": paragraph_notes, "full_analysis": full, "source": "deterministic"}


DEFAULT_ANALYSIS_INSTRUCTIONS = """请填写你自己的《人民日报》/政策文本解读 prompt。

公开仓库只提供工作流、Notion 结构和 JSON 输出契约；具体解读方法属于用户自己的策略资产，不应由仓库内置。

输出必须是 JSON，不要输出 Markdown，不要复制原文全文。JSON 结构：
{
  "paragraph_notes": [
    {"excerpt": "该段不超过24个汉字的段首定位短摘", "analysis": "这一段的解读"}
  ],
  "signal_analysis": ["可选：信号/语境分析"],
  "policy_chain": ["可选：政策链路或观察点"],
  "follow_up": ["可选：后续跟踪事项"],
  "full_analysis": ["全文深度解读"]
}

最低要求：paragraph_notes 数量必须和原文段落数量一致；不要空泛套话；所有判断应能回到原文证据。
"""


def load_prompt_template(settings: dict[str, Any]) -> str:
    template_path = str(settings.get("prompt_template_path") or "").strip()
    if template_path:
        path = Path(template_path).expanduser()
        if not path.exists():
            raise RuntimeError(f"People's Daily analysis prompt template not found: {path}")
        return path.read_text(encoding="utf-8")
    return str(settings.get("prompt_template") or DEFAULT_ANALYSIS_INSTRUCTIONS)


def build_openclaw_prompt(article: dict[str, Any], settings: dict[str, Any] | None = None) -> str:
    paragraphs = [compact(p) for p in article.get("paragraphs") or [] if compact(p)]
    numbered = "\n".join(f"{i}. {p}" for i, p in enumerate(paragraphs, 1))
    instructions = load_prompt_template(settings or {})
    return f"""{instructions}

标题：{article.get("title") or ""}
版面：{page_label_for(article)}
官方原文：{article.get("url") or ""}

原文分段：
{numbered}
"""


def parse_openclaw_output(stdout: str) -> dict[str, Any] | None:
    text = stdout.strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
        if isinstance(payload, dict) and isinstance(payload.get("paragraph_notes"), list):
            return payload
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, flags=re.S)
    if match:
        try:
            payload = json.loads(match.group(0))
            if isinstance(payload, dict) and isinstance(payload.get("paragraph_notes"), list):
                return payload
        except json.JSONDecodeError:
            return None
    return None


def openclaw_article_analysis(
    *,
    article: dict[str, Any],
    openclaw_bin: Path,
    agent: str,
    thinking: str,
    timeout: int,
    session_prefix: str,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    prompt = build_openclaw_prompt(article, settings or {})
    session_id = f"{session_prefix}-{article.get('id') or int(time.time())}"
    cmd = [
        str(openclaw_bin),
        "agent",
        "--local",
        "--agent",
        agent,
        "--session-id",
        session_id,
        "--json",
        "--thinking",
        thinking,
        "--timeout",
        str(timeout),
        prompt,
    ]
    completed = subprocess.run(
        cmd,
        text=True,
        capture_output=True,
        timeout=timeout + 30,
        check=False,
    )
    payload = parse_openclaw_output(completed.stdout)
    if payload:
        payload["source"] = "openclaw"
        payload["returncode"] = completed.returncode
        return payload
    fallback = deterministic_article_analysis(article)
    fallback["source"] = "deterministic_after_openclaw_failure"
    fallback["openclaw_returncode"] = completed.returncode
    fallback["openclaw_stderr"] = completed.stderr[-1200:]
    return fallback


def analyze_article(article: dict[str, Any], settings: dict[str, Any], issue_key: str) -> dict[str, Any]:
    if not settings.get("enabled", True):
        return deterministic_article_analysis(article)
    if str(settings.get("mode") or "openclaw") != "openclaw":
        return deterministic_article_analysis(article)
    return openclaw_article_analysis(
        article=article,
        openclaw_bin=Path(settings.get("openclaw_bin") or "openclaw").expanduser(),
        agent=str(settings.get("agent") or "main"),
        thinking=str(settings.get("thinking") or "medium"),
        timeout=int(settings.get("timeout") or 300),
        session_prefix=f"people-daily-{issue_key}",
        settings=settings,
    )


def append_text_blocks(blocks: list[dict[str, Any]], block_type: str, prefix: str, text: str) -> None:
    value = compact(text)
    if not value:
        return
    # Notion rich_text content is capped at 2000 chars. Keep paragraph text visible by splitting.
    max_len = max(200, 1900 - len(prefix))
    for start in range(0, len(value), max_len):
        chunk = value[start : start + max_len]
        chunk_prefix = prefix if start == 0 else "原文续："
        blocks.append(block(block_type, f"{chunk_prefix}{chunk}"))


def build_article_page_blocks(article: dict[str, Any], analysis: dict[str, Any]) -> list[dict[str, Any]]:
    blocks = [
        block("heading_1", "基本信息"),
        block("bulleted_list_item", f"版面：{page_label_for(article)}"),
        block("bulleted_list_item", "官方原文", str(article.get("url") or "")),
        block("bulleted_list_item", f"正文规模：约 {article.get('char_count') or 0} 字"),
        block("heading_1", "逐段对照解读"),
    ]
    notes = list(analysis.get("paragraph_notes") or [])
    paragraphs = [compact(p) for p in article.get("paragraphs") or [] if compact(p)]
    for index, paragraph in enumerate(paragraphs, 1):
        note = notes[index - 1] if index - 1 < len(notes) and isinstance(notes[index - 1], dict) else {}
        excerpt = compact(note.get("excerpt") or short_excerpt(paragraph))
        explanation = compact(note.get("analysis") or "")
        if not explanation:
            explanation = deterministic_article_analysis({"title": article.get("title"), "paragraphs": [paragraph]})[
                "paragraph_notes"
            ][0]["analysis"]
        append_text_blocks(blocks, "quote", "原文：", paragraph)
        append_text_blocks(blocks, "paragraph", "解析：", explanation)
    signal_lines = [compact(line) for line in analysis.get("signal_analysis") or [] if compact(line)]
    if signal_lines:
        blocks.append(block("heading_1", "政治/政策信号"))
        for line in signal_lines:
            append_text_blocks(blocks, "paragraph", "", line)
    chain_lines = [compact(line) for line in analysis.get("policy_chain") or [] if compact(line)]
    if chain_lines:
        blocks.append(block("heading_1", "政策链路与观察点"))
        for line in chain_lines:
            append_text_blocks(blocks, "bulleted_list_item", "", line)
    follow_lines = [compact(line) for line in analysis.get("follow_up") or [] if compact(line)]
    if follow_lines:
        blocks.append(block("heading_1", "后续验证清单"))
        for line in follow_lines:
            append_text_blocks(blocks, "to_do", "", line)
    blocks.append(block("heading_1", "全文深度解读"))
    for line in analysis.get("full_analysis") or deterministic_article_analysis(article)["full_analysis"]:
        append_text_blocks(blocks, "paragraph", "", compact(line))
    return blocks


def build_date_page_blocks(
    *,
    manifest: dict[str, Any],
    detailed: list[dict[str, Any]],
    detailed_title_by_url: dict[str, str],
) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = [
        block("heading_1", "全日总览"),
        block(
            "paragraph",
            "本页按人民日报电子版归档：PDF保留版面原貌，正文通过官方原文入口进入；前4版文章在各自条目下方放置逐篇深读子页。阅读重点不是摘要，而是版面信号、主体排序、政策链路、图像/版式暗示和后续验证。",
        ),
        block("heading_1", "今日版面信号观察"),
        block("bulleted_list_item", "先看版面顺序和组合：头版负责定调，后续版面常承担展开、经验推广、执行动员、国际叙事或评论功能。"),
        block("bulleted_list_item", "重点看同一主题是否跨版重复出现；重复通常意味着政策优先级、宣传动员或治理压力。"),
        block("bulleted_list_item", "图片和版式需要补读：人物、场景、设备、群众、边疆/基层/工厂/港口等视觉主体，可能承担文字之外的政治信号。"),
        block("heading_1", "全日PDF"),
    ]
    for page in manifest.get("pages") or []:
        label = page.get("page_label") or page.get("page_no") or ""
        blocks.append(block("bulleted_list_item", f"{label} PDF", page.get("pdf_url") or None))

    detailed_urls = {article.get("url") for article in detailed}
    articles_by_page: dict[str, list[dict[str, Any]]] = {}
    for article in manifest.get("articles") or []:
        if is_editorial_metadata(article):
            continue
        articles_by_page.setdefault(str(article.get("page_no") or ""), []).append(article)

    for page in manifest.get("pages") or []:
        page_no = str(page.get("page_no") or "")
        label = page.get("page_label") or f"第{page_no}版"
        blocks.append(block("heading_1", label))
        for article in articles_by_page.get(page_no, []):
            title = article.get("title") or "未命名"
            blocks.append(block("heading_2", title))
            blocks.append(block("paragraph", "正文：官方原文", article.get("url") or None))
            if article.get("url") in detailed_urls:
                blocks.append(block("child_page", detailed_title_by_url[article.get("url")]))
    return blocks


def list_page_children(page_id: str, token: str, timeout: int) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    cursor = ""
    while True:
        url = f"https://api.notion.com/v1/blocks/{page_id}/children?page_size=100"
        if cursor:
            url += "&start_cursor=" + cursor
        payload = notion_request(method="GET", url=url, token=token, timeout=timeout)
        results.extend(payload.get("results") or [])
        if not payload.get("has_more"):
            return results
        cursor = payload.get("next_cursor") or ""


def append_children(page_id: str, token: str, blocks: list[dict[str, Any]], timeout: int) -> None:
    for part in chunked(blocks):
        notion_request(
            method="PATCH",
            url=f"https://api.notion.com/v1/blocks/{page_id}/children",
            token=token,
            payload={"children": part},
            timeout=timeout,
        )


def clear_children(page_id: str, token: str, timeout: int) -> None:
    for child in list_page_children(page_id, token, timeout):
        child_id = child.get("id")
        if not child_id:
            continue
        notion_request(
            method="PATCH",
            url=f"https://api.notion.com/v1/blocks/{child_id}",
            token=token,
            payload={"archived": True},
            timeout=timeout,
        )


def create_date_page(
    *,
    parent_page_id: str,
    token: str,
    title: str,
    blocks: list[dict[str, Any]],
    timeout: int,
) -> dict[str, Any]:
    first, rest = blocks[:90], blocks[90:]
    page = notion_request(
        method="POST",
        url="https://api.notion.com/v1/pages",
        token=token,
        payload={
            "parent": {"page_id": parent_page_id},
            "properties": {"title": {"title": [{"type": "text", "text": {"content": title}}]}},
            "children": first,
        },
        timeout=timeout,
    )
    if rest:
        append_children(page["id"], token, rest, timeout)
    return page


def fill_article_pages(
    *,
    date_page_id: str,
    token: str,
    detailed: list[dict[str, Any]],
    title_by_url: dict[str, str],
    analysis_by_url: dict[str, dict[str, Any]],
    timeout: int,
) -> list[dict[str, Any]]:
    children = list_page_children(date_page_id, token, timeout)
    child_pages = {
        (child.get("child_page") or {}).get("title"): child
        for child in children
        if child.get("type") == "child_page"
    }
    filled: list[dict[str, Any]] = []
    for article in detailed:
        title = title_by_url[article.get("url")]
        child = child_pages.get(title)
        if not child:
            continue
        blocks = build_article_page_blocks(article, analysis_by_url[article.get("url")] or {})
        append_children(child["id"], token, blocks, timeout)
        filled.append({"title": title, "id": child["id"]})
    return filled


def find_existing_child_page(
    *,
    parent_page_id: str,
    token: str,
    title: str,
    timeout: int,
) -> dict[str, Any] | None:
    for child in list_page_children(parent_page_id, token, timeout):
        if child.get("type") != "child_page":
            continue
        child_page = child.get("child_page") or {}
        if compact(child_page.get("title")) == compact(title):
            return child
    return None


def publication_state_path(output_root: Path) -> Path:
    return output_root / "people_daily_publications.json"


def load_publication_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_publication_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def publish_to_notion(
    *,
    config: dict[str, Any],
    manifest: dict[str, Any],
    force: bool,
    dry_run: bool,
) -> dict[str, Any]:
    pd_config = config.get("people_daily_deep_read") or {}
    notion_config = pd_config.get("notion") or {}
    if not notion_config.get("enabled", True):
        return {"enabled": False, "attempted": False}

    issue_date = issue_date_from_manifest(manifest)
    issue_key = issue_date.isoformat()
    output_root = Path(pd_config.get("output_dir") or config.get("workspace_dir") or ".").expanduser()
    state_path = publication_state_path(output_root)
    state = load_publication_state(state_path)
    existing = state.get(issue_key)
    if existing and existing.get("page_id") and not force:
        return {"enabled": True, "attempted": False, "skipped_duplicate": True, "page_id": existing.get("page_id")}

    max_page_no = int((pd_config.get("analysis") or {}).get("detailed_max_page_no") or 4)
    detailed = detailed_articles(manifest, max_page_no)
    title_by_url = {article.get("url"): article_page_title(article, idx) for idx, article in enumerate(detailed, 1)}
    if dry_run:
        blocks = build_date_page_blocks(manifest=manifest, detailed=detailed, detailed_title_by_url=title_by_url)
        return {
            "enabled": True,
            "attempted": False,
            "dry_run": True,
            "date_title": format_issue_title(issue_date),
            "date_blocks": len(blocks),
            "detailed_pages": len(detailed),
        }

    env = os.environ.copy()
    env.update(load_env_file(Path(notion_config.get("secrets_env") or (config.get("notion") or {}).get("secrets_env") or "").expanduser()))
    token = env.get(str(notion_config.get("token_env") or "NOTION_TOKEN"), "").strip()
    parent_page_id = str(notion_config.get("people_daily_page_id") or "").strip()
    if not token:
        raise RuntimeError("Missing NOTION_TOKEN for People's Daily publishing")
    if not parent_page_id:
        raise RuntimeError("Missing people_daily_deep_read.notion.people_daily_page_id")

    timeout = int(notion_config.get("timeout") or 120)
    title = format_issue_title(issue_date)
    notion_existing = find_existing_child_page(
        parent_page_id=parent_page_id,
        token=token,
        title=title,
        timeout=timeout,
    )
    if notion_existing and notion_existing.get("id") and not force:
        state[issue_key] = {
            "page_id": notion_existing["id"],
            "url": notion_existing.get("url"),
            "published_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "discovered_from_notion": True,
        }
        save_publication_state(state_path, state)
        return {
            "enabled": True,
            "attempted": False,
            "skipped_duplicate": True,
            "page_id": notion_existing["id"],
            "source": "notion_title_check",
        }

    analysis_settings = dict(pd_config.get("analysis") or {})
    analysis_settings.setdefault("openclaw_bin", config.get("openclaw_bin") or "openclaw")
    analysis_by_url: dict[str, dict[str, Any]] = {}
    for idx, article in enumerate(detailed, 1):
        print(f"analyze {idx}/{len(detailed)} {article.get('title')}")
        analysis_by_url[article.get("url")] = analyze_article(article, analysis_settings, issue_date.strftime("%Y%m%d"))

    blocks = build_date_page_blocks(manifest=manifest, detailed=detailed, detailed_title_by_url=title_by_url)

    existing_page_id = ""
    existing_url = ""
    if force and existing and existing.get("page_id"):
        existing_page_id = str(existing.get("page_id") or "")
        existing_url = str(existing.get("url") or "")
    elif force and notion_existing and notion_existing.get("id"):
        existing_page_id = str(notion_existing.get("id") or "")
        existing_url = str(notion_existing.get("url") or "")

    if existing_page_id:
        clear_children(existing_page_id, token, timeout)
        append_children(existing_page_id, token, blocks, timeout)
        page = {"id": existing_page_id, "url": existing_url}
    else:
        page = create_date_page(
            parent_page_id=parent_page_id,
            token=token,
            title=title,
            blocks=blocks,
            timeout=timeout,
        )

    filled = fill_article_pages(
        date_page_id=page["id"],
        token=token,
        detailed=detailed,
        title_by_url=title_by_url,
        analysis_by_url=analysis_by_url,
        timeout=timeout,
    )
    state[issue_key] = {
        "page_id": page["id"],
        "url": page.get("url"),
        "published_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "detailed_pages": len(filled),
        "updated_existing": bool(existing_page_id),
    }
    save_publication_state(state_path, state)
    return {"enabled": True, "attempted": True, "page_id": page["id"], "url": page.get("url"), "detailed_pages": len(filled), "updated_existing": bool(existing_page_id)}


def collect_or_load_manifest(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    if args.manifest:
        return load_json(Path(args.manifest))

    pd_config = config.get("people_daily_deep_read") or {}
    if args.layout_url:
        start_url = args.layout_url
    else:
        day = dt.date.today()
        if args.date:
            day = dt.date.fromisoformat(args.date)
        start_url = layout_url_for_date(day, str(pd_config.get("base_url") or "https://paper.people.com.cn/rmrb/pc"))
    manifest = collect_issue(
        start_url=start_url,
        output_root=Path(pd_config.get("output_dir") or "~/.openclaw/workspace/people-daily-deep-read").expanduser(),
        max_pages=int(args.max_pages or pd_config.get("max_pages") or 99),
        delay_seconds=float(args.delay if args.delay is not None else pd_config.get("crawl_delay_seconds", 120)),
        download_pdfs=not args.no_pdf,
        analysis="none",
        openclaw_bin=str(config.get("openclaw_bin") or "openclaw"),
        timeout=int(pd_config.get("timeout") or 120),
    )
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run People's Daily deep-read workflow.")
    parser.add_argument("--config", default="../config/market_immersion_config.json")
    parser.add_argument("--date", help="Issue date YYYY-MM-DD")
    parser.add_argument("--layout-url")
    parser.add_argument("--manifest")
    parser.add_argument("--max-pages", type=int)
    parser.add_argument("--delay", type=float)
    parser.add_argument("--no-pdf", action="store_true")
    parser.add_argument("--no-publish", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    script_dir = Path(__file__).resolve().parent
    config_path = (script_dir / args.config).resolve()
    config = load_json(config_path)
    manifest = collect_or_load_manifest(args, config)
    print(f"issue={manifest['issue']['date']}")
    print(f"pages={len(manifest.get('pages') or [])}")
    print(f"articles={len(manifest.get('articles') or [])}")
    if args.no_publish:
        return 0
    result = publish_to_notion(config=config, manifest=manifest, force=args.force, dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("enabled") and not result.get("attempted") and not result.get("dry_run") and not result.get("skipped_duplicate"):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
