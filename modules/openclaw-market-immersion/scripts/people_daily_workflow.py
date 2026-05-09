#!/usr/bin/env python3
"""People's Daily deep-read workflow for the OpenClaw market module."""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import hashlib
import json
import os
import random
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WORKSPACE_ROOT / "scripts"))
import background_tasks  # noqa: E402

from people_daily_deep_read import collect_issue, issue_date_from_layout_url, layout_url_for_date


NOTION_VERSION = "2022-06-28"
NOTION_RETRYABLE_HTTP = {429, 500, 502, 503, 504}
NOTION_MAX_ATTEMPTS = 6


def run_process_group(
    cmd: list[str],
    *,
    timeout: int,
    text: bool = True,
    capture_output: bool = True,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run a command in its own process group and kill descendants on timeout."""
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


def write_workflow_checkpoint(output_root: Path, issue_key: str, stage: str, payload: dict[str, Any]) -> None:
    checkpoint_dir = output_root / issue_key / "checkpoints"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    checkpoint = {
        "stage": stage,
        "issue": issue_key,
        "written_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        **payload,
    }
    checkpoint_path = checkpoint_dir / f"{stage}.json"
    checkpoint_path.write_text(
        json.dumps(checkpoint, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    task_id = os.environ.get("OPENCLAW_BACKGROUND_TASK_ID")
    if task_id:
        try:
            background_tasks.update_task(
                task_id,
                checkpoint_path=str(checkpoint_path),
                metadata={**(background_tasks.load_task(task_id).get("metadata") or {}), "last_stage": stage},
                event=f"checkpoint:{stage}",
            )
        except Exception:
            pass


def people_daily_dag_spec() -> dict[str, Any]:
    return {
        "mode": "background_dag",
        "nodes": [
            {"name": "collect", "parallel": False},
            {"name": "validate", "depends_on": ["collect"]},
            {"name": "analyze", "depends_on": ["validate"], "checkpoint": "per_article_cache"},
            {"name": "publish", "depends_on": ["analyze"], "notion": "serial_idempotent_write_or_repair"},
            {"name": "notify", "depends_on": ["publish"], "telegram": "final_link_or_short_failure_only"},
        ],
    }


def article_cache_key(article: dict[str, Any], index: int) -> str:
    raw = compact(article.get("id") or article.get("url") or article.get("title") or str(index))
    return re.sub(r"[^0-9A-Za-z_-]+", "_", raw)[-80:] or f"article_{index:02d}"


def load_cached_analysis(output_root: Path, issue_key: str, article: dict[str, Any], index: int) -> dict[str, Any] | None:
    path = output_root / issue_key / "analysis-cache" / f"{index:02d}_{article_cache_key(article, index)}.json"
    if not path.exists():
        return None
    try:
        return load_json(path)
    except Exception:
        return None


def save_cached_analysis(output_root: Path, issue_key: str, article: dict[str, Any], index: int, analysis: dict[str, Any]) -> Path:
    cache_dir = output_root / issue_key / "analysis-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{index:02d}_{article_cache_key(article, index)}.json"
    payload = {
        "issue": issue_key,
        "index": index,
        "url": article.get("url"),
        "title": article.get("title"),
        "written_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "analysis": analysis,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def overview_cache_path(output_root: Path, issue_key: str) -> Path:
    return output_root / issue_key / "overview-cache" / "issue_overview.json"


def load_cached_overview(output_root: Path, issue_key: str) -> dict[str, Any] | None:
    path = overview_cache_path(output_root, issue_key)
    if not path.exists():
        return None
    try:
        return load_json(path)
    except Exception:
        return None


def save_cached_overview(output_root: Path, issue_key: str, overview: dict[str, Any]) -> Path:
    path = overview_cache_path(output_root, issue_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "issue": issue_key,
        "written_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "overview": overview,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def analyze_articles_with_cache(
    *,
    output_root: Path,
    issue_key: str,
    articles: list[dict[str, Any]],
    settings: dict[str, Any],
    reuse_cache: bool = True,
    label: str = "analyze",
) -> dict[str, dict[str, Any]]:
    analysis_by_url: dict[str, dict[str, Any]] = {}
    expected_meta = analysis_prompt_metadata(settings)
    completed = 0
    reused = 0
    failed: list[dict[str, Any]] = []
    pending: list[tuple[int, dict[str, Any]]] = []
    for idx, article in enumerate(articles, 1):
        cached = load_cached_analysis(output_root, issue_key, article, idx) if reuse_cache else None
        if cached and isinstance(cached.get("analysis"), dict):
            analysis = cached["analysis"]
            try:
                if analysis.get("prompt_id") != expected_meta.get("prompt_id"):
                    raise RuntimeError("prompt_id mismatch")
                if analysis.get("prompt_sha256") != expected_meta.get("prompt_sha256"):
                    raise RuntimeError("prompt_sha256 mismatch")
                validate_analysis_payload(analysis, article, settings)
                analysis_by_url[article.get("url")] = analysis
                reused += 1
                continue
            except Exception:
                pass
        pending.append((idx, article))

    def run_one(item: tuple[int, dict[str, Any]]) -> tuple[int, dict[str, Any], dict[str, Any] | None, str]:
        idx, article = item
        try:
            print(f"{label} {idx}/{len(articles)} {article.get('title')}")
            analysis = analyze_article(article, settings, issue_key.replace("-", ""))
            save_cached_analysis(output_root, issue_key, article, idx, analysis)
            return idx, article, analysis, ""
        except Exception as exc:  # noqa: BLE001
            return idx, article, None, str(exc)

    parallelism = max(1, int(settings.get("parallelism") or settings.get("concurrency") or 1))
    if pending and parallelism > 1:
        print(f"{label} parallelism={parallelism} pending={len(pending)}")
        with concurrent.futures.ThreadPoolExecutor(max_workers=parallelism) as executor:
            future_map = {executor.submit(run_one, item): item for item in pending}
            for future in concurrent.futures.as_completed(future_map):
                idx, article, analysis, error = future.result()
                if analysis is not None:
                    analysis_by_url[article.get("url")] = analysis
                else:
                    failed.append({"index": idx, "title": article.get("title"), "url": article.get("url"), "error": error})
                completed = len(analysis_by_url)
                write_workflow_checkpoint(
                    output_root,
                    issue_key,
                    "analyze",
                    {"status": "failed" if failed else "running", "completed": completed, "total": len(articles), "reused": reused, "parallelism": parallelism, "failed": failed[-5:]},
                )
    else:
        for item in pending:
            idx, article, analysis, error = run_one(item)
            if analysis is not None:
                analysis_by_url[article.get("url")] = analysis
            else:
                failed.append({"index": idx, "title": article.get("title"), "url": article.get("url"), "error": error})
            completed = len(analysis_by_url)
            write_workflow_checkpoint(
                output_root,
                issue_key,
                "analyze",
                {"status": "failed" if failed else "running", "completed": completed, "total": len(articles), "reused": reused, "parallelism": parallelism, "failed": failed[-5:]},
            )
    if failed:
        write_workflow_checkpoint(output_root, issue_key, "analyze", {"status": "failed", "completed": completed, "total": len(articles), "reused": reused, "failed": failed})
        raise RuntimeError(f"People's Daily analysis failed for {len(failed)} article(s)")
    write_workflow_checkpoint(output_root, issue_key, "analyze", {"status": "done", "completed": completed, "total": len(articles), "reused": reused})
    return analysis_by_url


def compact(text: Any) -> str:
    value = str(text or "")
    # People's Daily HTML can contain zero-width/invisible format characters
    # (for example U+200B). Notion may drop them from page titles, so title
    # matching must normalize them away before comparing local and Notion text.
    value = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", value)
    return re.sub(r"\s+", " ", value).strip()


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
    last_error = ""
    for attempt in range(1, NOTION_MAX_ATTEMPTS + 1):
        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:2000]
            last_error = f"HTTP {exc.code} {exc.reason}: {body}"
            if exc.code not in NOTION_RETRYABLE_HTTP or attempt >= NOTION_MAX_ATTEMPTS:
                raise RuntimeError(f"Notion API {method} {url} failed: {last_error}") from exc
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            try:
                delay = float(retry_after) if retry_after else 0.0
            except ValueError:
                delay = 0.0
            if delay <= 0:
                delay = min(2 ** (attempt - 1), 20) + random.uniform(0.1, 0.9)
            print(f"Notion transient error; retrying {method} in {delay:.1f}s (attempt {attempt}/{NOTION_MAX_ATTEMPTS}): {last_error[:240]}")
            time.sleep(delay)
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = str(exc)
            if attempt >= NOTION_MAX_ATTEMPTS:
                raise RuntimeError(f"Notion API {method} {url} failed after retries: {last_error}") from exc
            delay = min(2 ** (attempt - 1), 20) + random.uniform(0.1, 0.9)
            print(f"Notion transport error; retrying {method} in {delay:.1f}s (attempt {attempt}/{NOTION_MAX_ATTEMPTS}): {last_error[:240]}")
            time.sleep(delay)
    raise RuntimeError(f"Notion API {method} {url} failed after retries: {last_error}")


def rich_text(text: str, href: str | None = None, color: str | None = None) -> list[dict[str, Any]]:
    value: dict[str, Any] = {"type": "text", "text": {"content": str(text or "")[:2000]}}
    if color:
        value["annotations"] = {"color": color}
    if href:
        value["text"]["link"] = {"url": href}
    return [value]


def block(block_type: str, text: str = "", href: str | None = None, color: str | None = None) -> dict[str, Any]:
    if block_type == "child_page":
        # Notion API does not accept child_page blocks via append-children.
        # Keep this as an internal placeholder; the publisher will create a
        # real child page at this exact stream position with POST /v1/pages.
        return {"__child_page_title": text[:180]}
    if block_type == "callout":
        return {"object": "block", "type": "callout", "callout": {"rich_text": rich_text(text, href, color), "icon": {"type": "emoji", "emoji": "🧭"}}}
    if block_type == "to_do":
        return {"object": "block", "type": "to_do", "to_do": {"rich_text": rich_text(text, href, color), "checked": False}}
    if block_type.startswith("heading_"):
        return {"object": "block", "type": block_type, block_type: {"rich_text": rich_text(text, href, color)}}
    return {"object": "block", "type": block_type, block_type: {"rich_text": rich_text(text, href, color)}}


def chunked(values: list[dict[str, Any]], size: int = 90) -> list[list[dict[str, Any]]]:
    return [values[i : i + size] for i in range(0, len(values), size)]


def format_issue_title(day: dt.date) -> str:
    return day.strftime("%Y年%m月%d日 人民日报深读")


def issue_date_from_manifest(manifest: dict[str, Any]) -> dt.date:
    return dt.date.fromisoformat(manifest["issue"]["date"])


def page_label_for(article: dict[str, Any]) -> str:
    return str(article.get("page_label") or f"第{article.get('page_no') or ''}版")


def article_page_title(article: dict[str, Any], serial: int) -> str:
    return f"{page_label_for(article)} {serial:02d} {article.get('title') or '未命名'}"


def author_from_meta(meta: str) -> str:
    value = compact(meta)
    value = re.sub(r"人民日报\s*\(.*?\)\s*-->", "", value)
    value = re.sub(r"《人民日报》.*$", "", value).strip()
    return compact(value)


def is_editorial_metadata(article: dict[str, Any]) -> bool:
    title = str(article.get("title") or "")
    if title.startswith("本版责编") or "责编：" in title or "版式设计" in title:
        return True
    return int(article.get("char_count") or 0) <= 40 and ("责编" in title or "邮箱" in title)


def is_news_page_label(label: Any) -> bool:
    """Return True for People's Daily pages that should enter deep-read output."""
    text = compact(label)
    return bool(text) and "要闻" in text


def news_pages(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    return [page for page in manifest.get("pages") or [] if is_news_page_label(page.get("page_label"))]


def news_page_numbers(manifest: dict[str, Any]) -> set[str]:
    return {str(page.get("page_no") or "") for page in news_pages(manifest)}


def detailed_articles(manifest: dict[str, Any], max_page_no: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    allowed_page_numbers = news_page_numbers(manifest)
    for article in manifest.get("articles") or []:
        try:
            page_no = int(str(article.get("page_no") or "99"))
        except ValueError:
            page_no = 99
        page_number = str(article.get("page_no") or "")
        keep_page = page_number in allowed_page_numbers if allowed_page_numbers else page_no <= max_page_no
        if keep_page and not is_editorial_metadata(article):
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
    if not template_path:
        raise RuntimeError("People's Daily formal analysis requires analysis.prompt_template_path")
    path = Path(template_path).expanduser()
    if not path.exists():
        raise RuntimeError(f"People's Daily analysis prompt template not found: {path}")
    text = path.read_text(encoding="utf-8")
    required_prompt_id = str(settings.get("required_prompt_id") or "").strip()
    if required_prompt_id and required_prompt_id not in text:
        raise RuntimeError(f"People's Daily prompt id mismatch: required {required_prompt_id}")
    return text


def prompt_metadata(settings: dict[str, Any]) -> dict[str, Any]:
    path = Path(str(settings.get("prompt_template_path") or "")).expanduser()
    text = path.read_text(encoding="utf-8")
    match = re.search(r"prompt_id:\s*([^\n]+)", text)
    prompt_id = compact(match.group(1)) if match else str(settings.get("required_prompt_id") or "")
    return {
        "prompt_id": prompt_id,
        "prompt_path": str(path),
        "prompt_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }


def analysis_prompt_metadata(settings: dict[str, Any]) -> dict[str, Any]:
    full_settings = settings.get("full_analysis") or {}
    structured_settings = settings.get("structured_groups") or {}
    full_path = str(full_settings.get("prompt_template_path") or "").strip()
    structured_path = str(structured_settings.get("prompt_template_path") or "").strip()
    if full_path and structured_path:
        full_text = Path(full_path).expanduser().read_text(encoding="utf-8")
        structured_text = Path(structured_path).expanduser().read_text(encoding="utf-8")
        prompt_id = str(settings.get("required_prompt_id") or "people_daily_article_split_v1").strip()
        return {
            "prompt_id": prompt_id,
            "prompt_path": f"{Path(full_path).expanduser()}|{Path(structured_path).expanduser()}",
            "prompt_sha256": hashlib.sha256((full_text + "\n---STRUCTURED---\n" + structured_text).encode("utf-8")).hexdigest(),
        }
    return prompt_metadata(settings)


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


def build_article_prompt(article: dict[str, Any], settings: dict[str, Any], extra_context: str = "") -> str:
    paragraphs = [compact(p) for p in article.get("paragraphs") or [] if compact(p)]
    numbered = "\n".join(f"{i}. {p}" for i, p in enumerate(paragraphs, 1))
    instructions = load_prompt_template(settings)
    context = f"\n{extra_context.strip()}\n" if extra_context.strip() else ""
    return f"""{instructions}{context}

标题：{article.get("title") or ""}
版面：{page_label_for(article)}
官方原文：{article.get("url") or ""}

原文分段：
{numbered}
"""


def build_combined_article_prompt(article: dict[str, Any], settings: dict[str, Any]) -> str:
    """Build one model call from the two source prompts.

    The full-analysis and structured-groups prompt files remain the source of
    truth. This wrapper only combines execution and defines the single merged
    JSON contract so the two tasks do not drift into a hand-written third prompt.
    """
    full_settings = settings.get("full_analysis") or {}
    structured_settings = settings.get("structured_groups") or {}
    full_prompt = load_prompt_template(full_settings)
    structured_prompt = load_prompt_template(structured_settings)
    paragraphs = [compact(p) for p in article.get("paragraphs") or [] if compact(p)]
    numbered = "\n".join(f"{i}. {p}" for i, p in enumerate(paragraphs, 1))
    required_prompt_id = str(settings.get("required_prompt_id") or "people_daily_article_combined_v1_2026-05-06")
    return f"""请一次性完成两个相互独立但相关的任务：

任务A：生成全文深度解读 full_analysis。
任务B：生成结构化原文与解析 structured_groups。

重要原则：
- 任务A和任务B的内容质量要求分别以下方两个源 prompt 为准；不要把两类任务混写。
- 任务A不要写成逐段解析；任务B不要写成全文深度解读。
- 下方两个源 prompt 中各自的 prompt_id/JSON 输出格式用于标识其原始任务边界；本次合并调用最终只输出本文末尾指定的合并 JSON。
- 内容质量要求由两个源 prompt 自检；自动质量门只检查结构完整、段落覆盖和可追溯性。

【任务A源 prompt：全文深度解读】
{full_prompt}

【任务B源 prompt：结构化原文与解析】
{structured_prompt}

最终输出必须是一个 JSON，不要输出 Markdown。JSON 结构必须是：
{{
  "prompt_id": "{required_prompt_id}",
  "full_analysis": ["连贯的全文深度解读；段数服务于判断质量"],
  "signal_analysis": ["可选：信号/语境分析"],
  "policy_chain": ["可选：政策链路或观察点"],
  "follow_up": ["可选：后续跟踪事项"],
  "structured_groups": [
    {{
      "title": "结构组标题，要概括共同含义",
      "paragraph_indices": [1, 2],
      "analysis": "这一组为什么要放在一起读，它共同形成了什么含义"
    }}
  ]
}}

结构性硬要求：
- prompt_id 必须原样返回 {required_prompt_id}。
- full_analysis 至少包含一段非空全文判断。
- structured_groups 必须覆盖全部原文段落编号，不能漏段，不能重复。
- paragraph_indices 只能使用输入中的段落编号。
- 每个结构组必须包含 title、paragraph_indices、analysis。
- 所有判断必须能回到原文证据，不能编造外部事实。

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
    def valid(payload: Any) -> dict[str, Any] | None:
        if isinstance(payload, dict) and (
            isinstance(payload.get("structured_groups"), list)
            or isinstance(payload.get("paragraph_notes"), list)
        ):
            return payload
        return None
    try:
        payload = json.loads(text)
        direct = valid(payload)
        if direct:
            return direct
        if isinstance(payload, dict) and isinstance(payload.get("payloads"), list):
            inner_text = "\n".join(
                str(item.get("text") or "")
                for item in payload.get("payloads") or []
                if isinstance(item, dict)
            ).strip()
            if inner_text:
                inner = parse_openclaw_output(inner_text)
                if inner:
                    return inner
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, flags=re.S)
    if match:
        try:
            payload = json.loads(match.group(0))
            direct = valid(payload)
            if direct:
                return direct
        except json.JSONDecodeError:
            return None
    return None


def parse_openclaw_json(stdout: str) -> dict[str, Any] | None:
    text = stdout.strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
        if isinstance(payload, dict):
            if isinstance(payload.get("payloads"), list):
                inner_text = "\n".join(
                    str(item.get("text") or "")
                    for item in payload.get("payloads") or []
                    if isinstance(item, dict)
                ).strip()
                if inner_text:
                    inner = parse_openclaw_json(inner_text)
                    if inner:
                        return inner
            return payload
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, flags=re.S)
    if match:
        try:
            payload = json.loads(match.group(0))
            return payload if isinstance(payload, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def validate_analysis_payload(payload: dict[str, Any], article: dict[str, Any], settings: dict[str, Any]) -> None:
    required_prompt_id = str(settings.get("required_prompt_id") or "").strip()
    if required_prompt_id and payload.get("prompt_id") != required_prompt_id:
        raise RuntimeError(f"analysis prompt_id mismatch: {payload.get('prompt_id')} != {required_prompt_id}")
    groups = payload.get("structured_groups") or []
    if not isinstance(groups, list) or not groups:
        raise RuntimeError("analysis missing structured_groups")
    paragraphs = [compact(p) for p in article.get("paragraphs") or [] if compact(p)]
    expected = list(range(1, len(paragraphs) + 1))
    seen: list[int] = []
    for group in groups:
        if not isinstance(group, dict):
            raise RuntimeError("structured_groups contains non-object group")
        title = compact(group.get("title"))
        analysis = compact(group.get("analysis"))
        indices = group.get("paragraph_indices") or []
        if not title or not analysis or not isinstance(indices, list):
            raise RuntimeError("structured group missing title/analysis/paragraph_indices")
        for value in indices:
            if not isinstance(value, int):
                raise RuntimeError("paragraph_indices must be integers")
            seen.append(value)
    if sorted(seen) != expected:
        raise RuntimeError(f"structured group paragraph coverage mismatch: got {sorted(seen)}, expected {expected}")
    full = payload.get("full_analysis") or []
    if not isinstance(full, list) or not [x for x in full if compact(x)]:
        raise RuntimeError("full_analysis must contain at least one non-empty paragraph")
    # Do not turn style preferences into hard failures. Paragraph count, group
    # count, repeated contrast phrasing, and legacy template wording are quality
    # review signals, not structural validity errors. The hard gate should only
    # reject payloads that cannot be safely rendered or traced back to the source
    # paragraphs.


def validate_overview_payload(payload: dict[str, Any], settings: dict[str, Any]) -> None:
    required_prompt_id = str(settings.get("required_prompt_id") or "").strip()
    if required_prompt_id and payload.get("prompt_id") != required_prompt_id:
        raise RuntimeError(f"overview prompt_id mismatch: {payload.get('prompt_id')} != {required_prompt_id}")
    overview = payload.get("overview") or []
    if not isinstance(overview, list):
        raise RuntimeError("overview must be a list of paragraphs")
    paras = [compact(line) for line in overview if compact(line)]
    # 3-5 paragraphs is the normal target, not a doctrine. Keep only a broad
    # guardrail so the automation rejects obviously broken output without forcing
    # the model to pad or compress a naturally coherent overview.
    if not (2 <= len(paras) <= 7):
        raise RuntimeError("overview must contain 2-7 non-empty paragraphs")
    banned = ["###", "版面边界", "非要闻不纳入", "本页只保留要闻版面"]
    text = "\n".join(paras)
    hits = [phrase for phrase in banned if phrase in text]
    if hits:
        raise RuntimeError("overview contains banned wording: " + "、".join(hits))
    if any(re.match(r"^\s*(?:[-*•]|\d+[.、])\s+", para) for para in paras):
        raise RuntimeError("overview should be connected prose, not bullet/list items")


def validate_cached_analysis_quality(
    *,
    output_root: Path,
    issue_key: str,
    articles: list[dict[str, Any]],
    settings: dict[str, Any],
) -> dict[str, Any]:
    expected_meta = analysis_prompt_metadata(settings)
    failures: list[dict[str, Any]] = []
    ok = 0
    for idx, article in enumerate(articles, 1):
        cached = load_cached_analysis(output_root, issue_key, article, idx)
        if not cached:
            failures.append({"index": idx, "title": article.get("title"), "error": "missing analysis cache"})
            continue
        analysis = cached.get("analysis") if isinstance(cached.get("analysis"), dict) else None
        if not analysis:
            failures.append({"index": idx, "title": article.get("title"), "error": "cache missing analysis object"})
            continue
        try:
            if analysis.get("source") != "openclaw":
                raise RuntimeError(f"analysis source is not openclaw: {analysis.get('source')}")
            if analysis.get("prompt_id") != expected_meta.get("prompt_id"):
                raise RuntimeError("prompt_id mismatch")
            if analysis.get("prompt_sha256") != expected_meta.get("prompt_sha256"):
                raise RuntimeError("prompt_sha256 mismatch")
            validate_analysis_payload(analysis, article, settings)
            ok += 1
        except Exception as exc:  # noqa: BLE001
            failures.append({"index": idx, "title": article.get("title"), "error": str(exc)})
    result = {
        "status": "failed" if failures else "passed",
        "passed": ok,
        "total": len(articles),
        "failed": failures,
        "prompt_id": expected_meta.get("prompt_id"),
        "prompt_sha256": expected_meta.get("prompt_sha256"),
    }
    write_workflow_checkpoint(output_root, issue_key, "quality_gate", result)
    if failures:
        raise RuntimeError(f"People's Daily quality gate failed: {len(failures)}/{len(articles)} article(s)")
    return result


def validate_full_analysis_payload(payload: dict[str, Any], settings: dict[str, Any]) -> None:
    required_prompt_id = str(settings.get("required_prompt_id") or "").strip()
    if required_prompt_id and payload.get("prompt_id") != required_prompt_id:
        raise RuntimeError(f"full_analysis prompt_id mismatch: {payload.get('prompt_id')} != {required_prompt_id}")
    full = payload.get("full_analysis") or []
    if not isinstance(full, list) or not [line for line in full if compact(line)]:
        raise RuntimeError("full_analysis payload missing non-empty full_analysis")


def validate_structured_groups_payload(payload: dict[str, Any], article: dict[str, Any], settings: dict[str, Any]) -> None:
    required_prompt_id = str(settings.get("required_prompt_id") or "").strip()
    if required_prompt_id and payload.get("prompt_id") != required_prompt_id:
        raise RuntimeError(f"structured_groups prompt_id mismatch: {payload.get('prompt_id')} != {required_prompt_id}")
    groups = payload.get("structured_groups") or []
    if not isinstance(groups, list) or not groups:
        raise RuntimeError("structured_groups payload missing structured_groups")
    combined = {"prompt_id": "", "structured_groups": groups, "full_analysis": ["placeholder"]}
    validate_analysis_payload(combined, article, {"required_prompt_id": ""})


def run_openclaw_json_prompt(
    *,
    prompt: str,
    openclaw_bin: Path,
    agent: str,
    model: str,
    thinking: str,
    timeout: int,
    session_id: str,
) -> dict[str, Any]:
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
        "--model",
        model,
        "--message",
        prompt,
    ]
    completed = run_process_group(
        cmd,
        text=True,
        capture_output=True,
        timeout=timeout + 30,
        check=False,
    )
    payload = parse_openclaw_json(completed.stdout)
    if payload and completed.returncode == 0:
        return payload
    raise RuntimeError(
        "OpenClaw JSON prompt failed. "
        f"returncode={completed.returncode}; stderr={completed.stderr[-1200:]}; stdout={completed.stdout[-1200:]}"
    )


def openclaw_article_analysis(
    *,
    article: dict[str, Any],
    openclaw_bin: Path,
    agent: str,
    model: str,
    thinking: str,
    timeout: int,
    session_prefix: str,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    settings = settings or {}
    split_full = settings.get("full_analysis") or {}
    split_structured = settings.get("structured_groups") or {}
    if settings.get("combined_call", False) and split_full.get("prompt_template_path") and split_structured.get("prompt_template_path"):
        metadata = analysis_prompt_metadata(settings)
        prompt = build_combined_article_prompt(article, settings)
        payload = run_openclaw_json_prompt(
            prompt=prompt,
            openclaw_bin=openclaw_bin,
            agent=agent,
            model=model,
            thinking=thinking,
            timeout=timeout,
            session_id=f"{session_prefix}-{article.get('id') or int(time.time())}-combined",
        )
        validate_analysis_payload(payload, article, settings)
        payload.update(metadata)
        payload["source"] = "openclaw"
        payload["mode"] = "combined_from_source_prompts"
        return payload
    if split_full.get("prompt_template_path") and split_structured.get("prompt_template_path"):
        metadata = analysis_prompt_metadata(settings)
        full_prompt = build_article_prompt(article, split_full)
        full_payload = run_openclaw_json_prompt(
            prompt=full_prompt,
            openclaw_bin=openclaw_bin,
            agent=agent,
            model=model,
            thinking=thinking,
            timeout=timeout,
            session_id=f"{session_prefix}-{article.get('id') or int(time.time())}-full",
        )
        validate_full_analysis_payload(full_payload, split_full)
        full_analysis = [compact(line) for line in full_payload.get("full_analysis") or [] if compact(line)]
        structured_prompt = build_article_prompt(
            article,
            split_structured,
            extra_context="全文深度解读（供结构化分组时把握全文视角，不要机械照抄）：\n" + "\n".join(full_analysis),
        )
        structured_payload = run_openclaw_json_prompt(
            prompt=structured_prompt,
            openclaw_bin=openclaw_bin,
            agent=agent,
            model=model,
            thinking=thinking,
            timeout=timeout,
            session_id=f"{session_prefix}-{article.get('id') or int(time.time())}-structured",
        )
        validate_structured_groups_payload(structured_payload, article, split_structured)
        payload = {
            "prompt_id": metadata.get("prompt_id"),
            "full_analysis": full_analysis,
            "structured_groups": structured_payload.get("structured_groups") or [],
            "signal_analysis": full_payload.get("signal_analysis") or [],
            "policy_chain": full_payload.get("policy_chain") or [],
            "follow_up": full_payload.get("follow_up") or [],
            **metadata,
            "source": "openclaw",
            "mode": "split_full_then_structured",
        }
        validate_analysis_payload(payload, article, settings)
        return payload
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
        "--model",
        model,
        "--message",
        prompt,
    ]
    completed = run_process_group(
        cmd,
        text=True,
        capture_output=True,
        timeout=timeout + 30,
        check=False,
    )
    payload = parse_openclaw_output(completed.stdout)
    if payload and completed.returncode == 0:
        validate_analysis_payload(payload, article, settings or {})
        payload.update(analysis_prompt_metadata(settings or {}))
        payload["source"] = "openclaw"
        payload["returncode"] = completed.returncode
        return payload
    if not (settings or {}).get("allow_deterministic_fallback", False):
        raise RuntimeError(
            "OpenClaw People's Daily analysis failed; deterministic fallback is disabled. "
            f"returncode={completed.returncode}; stderr={completed.stderr[-1200:]}; stdout={completed.stdout[-1200:]}"
        )
    fallback = deterministic_article_analysis(article)
    fallback["source"] = "deterministic_after_openclaw_failure"
    fallback["openclaw_returncode"] = completed.returncode
    fallback["openclaw_stderr"] = completed.stderr[-1200:]
    return fallback


def build_overview_prompt(
    *,
    manifest: dict[str, Any],
    detailed: list[dict[str, Any]],
    analysis_by_url: dict[str, dict[str, Any]],
    settings: dict[str, Any],
) -> str:
    instructions = load_prompt_template(settings)
    issue_date = compact((manifest.get("issue") or {}).get("date"))
    article_lines: list[str] = []
    for idx, article in enumerate(detailed, 1):
        analysis = analysis_by_url.get(article.get("url")) or {}
        full = [compact(line) for line in analysis.get("full_analysis") or [] if compact(line)]
        article_lines.append(
            "\n".join(
                [
                    f"{idx}. 标题：{article.get('title') or ''}",
                    f"版面：{page_label_for(article)}",
                    f"官方原文：{article.get('url') or ''}",
                    "整篇深度解读：" + (" / ".join(full) if full else "（缺失）"),
                ]
            )
        )
    return f"""{instructions}

日期：{issue_date}

当天要闻文章与整篇深度解读：
{chr(10).join(article_lines)}
"""


def openclaw_issue_overview(
    *,
    manifest: dict[str, Any],
    detailed: list[dict[str, Any]],
    analysis_by_url: dict[str, dict[str, Any]],
    openclaw_bin: Path,
    agent: str,
    model: str,
    thinking: str,
    timeout: int,
    session_prefix: str,
    settings: dict[str, Any],
) -> dict[str, Any]:
    prompt = build_overview_prompt(manifest=manifest, detailed=detailed, analysis_by_url=analysis_by_url, settings=settings)
    session_id = f"{session_prefix}-overview"
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
        "--model",
        model,
        "--message",
        prompt,
    ]
    completed = run_process_group(
        cmd,
        text=True,
        capture_output=True,
        timeout=timeout + 30,
        check=False,
    )
    payload = parse_openclaw_json(completed.stdout)
    if payload and completed.returncode == 0:
        validate_overview_payload(payload, settings)
        payload.update(prompt_metadata(settings))
        payload["source"] = "openclaw"
        payload["returncode"] = completed.returncode
        return payload
    if not settings.get("allow_deterministic_fallback", False):
        raise RuntimeError(
            "OpenClaw People's Daily overview failed; deterministic fallback is disabled. "
            f"returncode={completed.returncode}; stderr={completed.stderr[-1200:]}; stdout={completed.stdout[-1200:]}"
        )
    fallback = {
        "overview": build_daily_overview_lines(detailed, analysis_by_url),
        "source": "deterministic_after_openclaw_failure",
        "openclaw_returncode": completed.returncode,
        "openclaw_stderr": completed.stderr[-1200:],
    }
    return fallback


def analyze_article(article: dict[str, Any], settings: dict[str, Any], issue_key: str) -> dict[str, Any]:
    if not settings.get("enabled", True):
        if not settings.get("allow_deterministic_fallback", False):
            raise RuntimeError("People's Daily analysis disabled but deterministic fallback is not allowed")
        return deterministic_article_analysis(article)
    if str(settings.get("mode") or "openclaw") != "openclaw":
        if not settings.get("allow_deterministic_fallback", False):
            raise RuntimeError("People's Daily analysis mode is not openclaw and deterministic fallback is not allowed")
        return deterministic_article_analysis(article)
    return openclaw_article_analysis(
        article=article,
        openclaw_bin=Path(settings.get("openclaw_bin") or "openclaw").expanduser(),
        agent=str(settings.get("agent") or "daily-writer"),
        model=str(settings.get("model") or "openai-codex/gpt-5.5"),
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


def append_original_text_blocks(blocks: list[dict[str, Any]], text: str) -> None:
    value = compact(text)
    if not value:
        return
    max_len = 1900
    for start in range(0, len(value), max_len):
        blocks.append(block("paragraph", value[start : start + max_len], color="gray"))


def build_article_page_blocks(article: dict[str, Any], analysis: dict[str, Any]) -> list[dict[str, Any]]:
    blocks = [
        block("callout", "浅色为原文，正常色为解析；不写‘原文’标签；不机械逐自然段；按文章结构组解析。"),
        block("heading_1", "基本信息"),
        block("bulleted_list_item", f"版面：{page_label_for(article)}"),
        block("bulleted_list_item", "官方原文", str(article.get("url") or "")),
        block("bulleted_list_item", f"正文规模：约 {article.get('char_count') or 0} 字"),
        block("heading_1", "结构化原文与解析"),
    ]
    paragraphs = [compact(p) for p in article.get("paragraphs") or [] if compact(p)]
    groups = analysis.get("structured_groups") or []
    if groups:
        for group in groups:
            blocks.append(block("heading_3", compact(group.get("title") or "结构组")))
            for index in group.get("paragraph_indices") or []:
                if isinstance(index, int) and 1 <= index <= len(paragraphs):
                    append_original_text_blocks(blocks, paragraphs[index - 1])
            append_text_blocks(blocks, "paragraph", "解析：", compact(group.get("analysis") or ""))
    else:
        notes = list(analysis.get("paragraph_notes") or [])
        for index, paragraph in enumerate(paragraphs, 1):
            note = notes[index - 1] if index - 1 < len(notes) and isinstance(notes[index - 1], dict) else {}
            explanation = compact(note.get("analysis") or "")
            if not explanation:
                explanation = deterministic_article_analysis({"title": article.get("title"), "paragraphs": [paragraph]})[
                    "paragraph_notes"
                ][0]["analysis"]
            append_text_blocks(blocks, "quote", "原文：", paragraph)
            append_text_blocks(blocks, "paragraph", "解析：", explanation)
    return blocks


def build_daily_overview_lines(detailed: list[dict[str, Any]], analysis_by_url: dict[str, dict[str, Any]]) -> list[str]:
    """Build a compact date-page overview from article-level analyses.

    Private deployments can replace this with a richer prompt-generated overview;
    this deterministic version keeps the public workflow usable without embedding
    user-specific reading strategies in the repository.
    """
    if not detailed:
        return ["今天未识别到可进入深读流程的要闻文章；请检查人民日报版面标签或抓取结果。"]
    by_page: dict[str, list[dict[str, Any]]] = {}
    for article in detailed:
        by_page.setdefault(page_label_for(article), []).append(article)
    first_titles = "、".join((a.get("title") or "未命名") for a in detailed[:3])
    lines = [
        f"今天的要闻深读共保留 {len(detailed)} 篇文章。入口判断应先看头版与其他要闻版面的组合关系，而不是把每篇文章平均摘要；头几篇文章包括：{first_titles}。",
        "阅读时应区分不同文章承担的功能：有的负责定调，有的负责把定调落到经济、科技、治理、民生、国际叙事或执行场景。真正有价值的是看这些文章如何共同形成当天的政策信号和评价口径。",
    ]
    page_summary = "；".join(f"{label} {len(items)} 篇" for label, items in by_page.items())
    lines.append(f"版面覆盖为：{page_summary}。后续复核时，重点检查每篇的整篇深度解读和子页结构化原文与解析是否完整、是否仍能回到原文证据。")

    analysis_fragments: list[str] = []
    for article in detailed:
        analysis = analysis_by_url.get(article.get("url")) or {}
        for line in analysis.get("full_analysis") or []:
            text = compact(line)
            if text:
                analysis_fragments.append(text)
                break
        if len(analysis_fragments) >= 2:
            break
    if analysis_fragments:
        lines.append("从逐篇深读看，当前最先浮出的线索是：" + "；".join(analysis_fragments)[:900])
    return lines[:5]


def issue_overview_with_cache(
    *,
    output_root: Path,
    issue_key: str,
    manifest: dict[str, Any],
    detailed: list[dict[str, Any]],
    analysis_by_url: dict[str, dict[str, Any]],
    settings: dict[str, Any],
    reuse_cache: bool = True,
) -> list[str]:
    overview_settings = dict(settings.get("overview") or {})
    if not overview_settings.get("enabled", True):
        return build_daily_overview_lines(detailed, analysis_by_url)
    overview_settings.setdefault("openclaw_bin", settings.get("openclaw_bin") or "openclaw")
    overview_settings.setdefault("agent", settings.get("agent") or "daily-writer")
    overview_settings.setdefault("model", settings.get("model") or "openai-codex/gpt-5.5")
    overview_settings.setdefault("thinking", settings.get("thinking") or "medium")
    overview_settings.setdefault("timeout", settings.get("timeout") or 300)
    if not overview_settings.get("prompt_template_path"):
        if overview_settings.get("allow_deterministic_fallback", False):
            return build_daily_overview_lines(detailed, analysis_by_url)
        raise RuntimeError("People's Daily overview requires analysis.overview.prompt_template_path")

    if reuse_cache:
        cached = load_cached_overview(output_root, issue_key)
        payload = cached.get("overview") if isinstance(cached, dict) else None
        if isinstance(payload, dict):
            try:
                if payload.get("source") == "openclaw":
                    expected = prompt_metadata(overview_settings)
                    if payload.get("prompt_id") != expected.get("prompt_id") or payload.get("prompt_sha256") != expected.get("prompt_sha256"):
                        raise RuntimeError("overview cache prompt metadata mismatch")
                validate_overview_payload(payload, overview_settings)
                return [compact(line) for line in payload.get("overview") or [] if compact(line)]
            except Exception:
                pass

    print("overview analyze 1/1 今日总览")
    payload = openclaw_issue_overview(
        manifest=manifest,
        detailed=detailed,
        analysis_by_url=analysis_by_url,
        openclaw_bin=Path(overview_settings.get("openclaw_bin") or "openclaw").expanduser(),
        agent=str(overview_settings.get("agent") or "daily-writer"),
        model=str(overview_settings.get("model") or "openai-codex/gpt-5.5"),
        thinking=str(overview_settings.get("thinking") or "medium"),
        timeout=int(overview_settings.get("timeout") or 300),
        session_prefix=f"pd-{issue_key.replace('-', '')}",
        settings=overview_settings,
    )
    save_cached_overview(output_root, issue_key, payload)
    write_workflow_checkpoint(
        output_root,
        issue_key,
        "overview",
        {"status": "done", "source": payload.get("source"), "paragraphs": len(payload.get("overview") or [])},
    )
    return [compact(line) for line in payload.get("overview") or [] if compact(line)]


def build_date_page_blocks(
    *,
    manifest: dict[str, Any],
    detailed: list[dict[str, Any]],
    detailed_title_by_url: dict[str, str],
    analysis_by_url: dict[str, dict[str, Any]] | None = None,
    overview_lines: list[str] | None = None,
) -> list[dict[str, Any]]:
    analysis_by_url = analysis_by_url or {}
    blocks: list[dict[str, Any]] = [
        block("heading_1", "今日总览"),
    ]
    for line in overview_lines or build_daily_overview_lines(detailed, analysis_by_url):
        blocks.append(block("paragraph", line))

    blocks.append(block("heading_1", "全日PDF"))
    selected_pages = news_pages(manifest) or list(manifest.get("pages") or [])
    for page in selected_pages:
        label = page.get("page_label") or page.get("page_no") or ""
        blocks.append(block("bulleted_list_item", str(label), page.get("pdf_url") or None))

    detailed_urls = {article.get("url") for article in detailed}
    articles_by_page: dict[str, list[dict[str, Any]]] = {}
    for article in detailed:
        if is_editorial_metadata(article):
            continue
        articles_by_page.setdefault(str(article.get("page_no") or ""), []).append(article)

    for page in selected_pages:
        page_no = str(page.get("page_no") or "")
        label = page.get("page_label") or f"第{page_no}版"
        if not articles_by_page.get(page_no):
            continue
        blocks.append(block("heading_1", label))
        for article in articles_by_page.get(page_no, []):
            title = article.get("title") or "未命名"
            child_title = detailed_title_by_url.get(article.get("url"), "")
            match = re.search(r"\s(\d{2})\s", child_title)
            serial_prefix = f"{match.group(1)} " if match else ""
            blocks.append(block("heading_2", f"{serial_prefix}{title}"))
            subtitle = compact(article.get("subtitle") or "")
            if subtitle:
                blocks.append(block("paragraph", subtitle))
            author = author_from_meta(str(article.get("meta") or ""))
            if author:
                blocks.append(block("paragraph", author))
            blocks.append(block("paragraph", "正文：官方原文", article.get("url") or None))
            for image in article.get("article_images") or []:
                image_url = image.get("url")
                if image_url:
                    blocks.append({
                        "object": "block",
                        "type": "image",
                        "image": {
                            "type": "external",
                            "external": {"url": image_url},
                            "caption": rich_text(compact(image.get("caption") or "人民日报配图")),
                        },
                    })
            analysis = analysis_by_url.get(article.get("url")) or {}
            blocks.append(block("heading_3", "整篇深度解读"))
            for line in analysis.get("full_analysis") or deterministic_article_analysis(article)["full_analysis"]:
                append_text_blocks(blocks, "paragraph", "", compact(line))
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
    if any("__child_page_title" in block_value for block_value in blocks):
        raise RuntimeError("append_children received child_page placeholders; use append_children_with_child_pages")
    for part in chunked(blocks):
        notion_request(
            method="PATCH",
            url=f"https://api.notion.com/v1/blocks/{page_id}/children",
            token=token,
            payload={"children": part},
            timeout=timeout,
        )


def create_child_page(
    *,
    parent_page_id: str,
    token: str,
    title: str,
    blocks: list[dict[str, Any]] | None = None,
    timeout: int,
) -> dict[str, Any]:
    page = notion_request(
        method="POST",
        url="https://api.notion.com/v1/pages",
        token=token,
        payload={
            "parent": {"page_id": parent_page_id},
            "properties": {"title": {"title": rich_text(title)}},
        },
        timeout=timeout,
    )
    if blocks:
        append_children(page["id"], token, blocks, timeout)
    return page


def append_children_with_child_pages(
    page_id: str,
    token: str,
    blocks: list[dict[str, Any]],
    timeout: int,
) -> int:
    """Append blocks while creating child pages at placeholder positions."""
    pending: list[dict[str, Any]] = []
    created = 0
    for block_value in blocks:
        child_title = block_value.get("__child_page_title")
        if child_title:
            if pending:
                append_children(page_id, token, pending, timeout)
                pending = []
            create_child_page(parent_page_id=page_id, token=token, title=str(child_title), timeout=timeout)
            created += 1
            continue
        pending.append(block_value)
    if pending:
        append_children(page_id, token, pending, timeout)
    return created


def clear_children(page_id: str, token: str, timeout: int) -> None:
    for child in list_page_children(page_id, token, timeout):
        child_id = child.get("id")
        if not child_id:
            continue
        if child.get("type") == "child_page":
            notion_request(
                method="PATCH",
                url=f"https://api.notion.com/v1/pages/{child_id}",
                token=token,
                payload={"archived": True},
                timeout=timeout,
            )
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
    page = notion_request(
        method="POST",
        url="https://api.notion.com/v1/pages",
        token=token,
        payload={
            "parent": {"page_id": parent_page_id},
            "properties": {"title": {"title": [{"type": "text", "text": {"content": title}}]}},
        },
        timeout=timeout,
    )
    append_children_with_child_pages(page["id"], token, blocks, timeout)
    return page


def update_page_title(page_id: str, token: str, title: str, timeout: int) -> None:
    notion_request(
        method="PATCH",
        url=f"https://api.notion.com/v1/pages/{page_id}",
        token=token,
        payload={"properties": {"title": {"title": rich_text(title)}}},
        timeout=timeout,
    )


def fill_article_pages(
    *,
    date_page_id: str,
    token: str,
    detailed: list[dict[str, Any]],
    title_by_url: dict[str, str],
    analysis_by_url: dict[str, dict[str, Any]],
    timeout: int,
    replace_existing: bool = False,
) -> list[dict[str, Any]]:
    children = list_page_children(date_page_id, token, timeout)
    child_pages = {
        compact((child.get("child_page") or {}).get("title")): child
        for child in children
        if child.get("type") == "child_page"
    }
    filled: list[dict[str, Any]] = []
    for article in detailed:
        title = title_by_url[article.get("url")]
        child = child_pages.get(compact(title))
        if not child:
            child = create_child_page(parent_page_id=date_page_id, token=token, title=title, timeout=timeout)
            child_pages[compact(title)] = child
        elif replace_existing:
            clear_children(child["id"], token, timeout)
        blocks = build_article_page_blocks(article, analysis_by_url[article.get("url")] or {})
        append_children(child["id"], token, blocks, timeout)
        filled.append({"title": title, "id": child["id"]})
    return filled


def notion_block_text(block_value: dict[str, Any]) -> str:
    block_type = str(block_value.get("type") or "")
    payload = block_value.get(block_type) or {}
    if block_type == "child_page":
        return str(payload.get("title") or "")
    rich_text = payload.get("rich_text") or []
    return "".join(str(part.get("plain_text") or ((part.get("text") or {}).get("content") or "")) for part in rich_text)


def article_child_has_content(child_id: str, token: str, timeout: int) -> bool:
    children = list_page_children(child_id, token, timeout)
    headings = {compact(notion_block_text(child)) for child in children if str(child.get("type") or "").startswith("heading_")}
    return "结构化原文与解析" in headings and len(children) >= 6


def inspect_article_children(
    *,
    date_page_id: str,
    token: str,
    detailed: list[dict[str, Any]],
    title_by_url: dict[str, str],
    timeout: int,
) -> dict[str, Any]:
    children = list_page_children(date_page_id, token, timeout)
    child_pages = {
        compact((child.get("child_page") or {}).get("title")): child
        for child in children
        if child.get("type") == "child_page"
    }
    missing: list[dict[str, Any]] = []
    empty: list[dict[str, Any]] = []
    complete = 0
    for article in detailed:
        title = title_by_url[article.get("url")]
        child = child_pages.get(compact(title))
        if not child:
            missing.append(article)
            continue
        if article_child_has_content(child["id"], token, timeout):
            complete += 1
        else:
            empty.append(article)
    return {
        "complete": complete == len(detailed),
        "complete_count": complete,
        "expected": len(detailed),
        "missing": missing,
        "empty": empty,
    }


def append_missing_article_entries(
    *,
    date_page_id: str,
    token: str,
    articles: list[dict[str, Any]],
    title_by_url: dict[str, str],
    timeout: int,
) -> None:
    # Notion rejects child_page blocks in append-children payloads. Missing
    # article pages are created with POST /v1/pages inside fill_article_pages().
    return


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


def find_existing_publication(output_root: Path, issue_key: str) -> dict[str, Any]:
    state = load_publication_state(publication_state_path(output_root))
    publication = state.get(issue_key) or {}
    if publication.get("url"):
        return publication
    issue_dir = output_root / issue_key
    for path in sorted(issue_dir.glob("*.state.json"), reverse=True):
        try:
            payload = load_json(path)
        except Exception:
            continue
        notion = payload.get("notion") or {}
        if notion.get("url"):
            return {
                "page_id": notion.get("page_id") or notion.get("id"),
                "url": notion.get("url"),
                "status": "complete" if notion.get("child_pages_filled") else notion.get("status"),
                "source": str(path),
                "detailed_pages": notion.get("child_pages_filled"),
            }
    return {}


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


def resolve_people_daily_parent_page_id(config: dict[str, Any], notion_config: dict[str, Any], env: dict[str, str]) -> str:
    """Resolve the Notion parent page for People's Daily publishing.

    Local configs can keep page IDs out of the public repo by using env vars.
    Resolution order is explicit People's Daily config, People's Daily env var,
    global Notion config, then global Notion env var.
    """
    global_notion = config.get("notion") or {}
    explicit = str(notion_config.get("people_daily_page_id") or "").strip()
    if explicit:
        return explicit
    for key in (
        notion_config.get("people_daily_page_id_env"),
        notion_config.get("parent_page_id_env"),
        global_notion.get("parent_page_id_env"),
    ):
        key = str(key or "").strip()
        if key and env.get(key, "").strip():
            return env[key].strip()
    fallback = str(global_notion.get("parent_page_id") or "").strip()
    if fallback:
        return fallback
    return ""


def publish_to_notion(
    *,
    config: dict[str, Any],
    manifest: dict[str, Any],
    force: bool,
    dry_run: bool,
    skip_analysis: bool = False,
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

    max_page_no = int((pd_config.get("analysis") or {}).get("detailed_max_page_no") or 4)
    detailed = detailed_articles(manifest, max_page_no)
    title_by_url = {article.get("url"): article_page_title(article, idx) for idx, article in enumerate(detailed, 1)}
    if dry_run:
        if skip_analysis:
            publication = find_existing_publication(output_root, issue_key)
            if publication.get("url"):
                return {
                    "enabled": True,
                    "attempted": False,
                    "dry_run": True,
                    "skipped_duplicate": True,
                    "url": publication.get("url"),
                    "source": publication.get("source"),
                    "detailed_pages": publication.get("detailed_pages"),
                }
        analysis_settings = dict(pd_config.get("analysis") or {})
        analysis_settings.setdefault("openclaw_bin", config.get("openclaw_bin") or "openclaw")
        try:
            quality_gate = validate_cached_analysis_quality(
                output_root=output_root,
                issue_key=issue_key,
                articles=detailed,
                settings=analysis_settings,
            )
        except Exception as exc:  # noqa: BLE001
            quality_gate = {"status": "failed", "error": str(exc)}
        cached_overview = load_cached_overview(output_root, issue_key)
        overview_payload = cached_overview.get("overview") if isinstance(cached_overview, dict) else None
        overview_lines = []
        if isinstance(overview_payload, dict):
            overview_lines = [compact(line) for line in overview_payload.get("overview") or [] if compact(line)]
        blocks = build_date_page_blocks(
            manifest=manifest,
            detailed=detailed,
            detailed_title_by_url=title_by_url,
            overview_lines=overview_lines or None,
        )
        return {
            "enabled": True,
            "attempted": False,
            "dry_run": True,
            "date_title": format_issue_title(issue_date),
            "date_blocks": len(blocks),
            "detailed_pages": len(detailed),
            "quality_gate": quality_gate,
            "overview_cached": bool(overview_lines),
        }

    env = os.environ.copy()
    env.update(load_env_file(Path(notion_config.get("secrets_env") or (config.get("notion") or {}).get("secrets_env") or "").expanduser()))
    token = env.get(str(notion_config.get("token_env") or "NOTION_TOKEN"), "").strip()
    parent_page_id = resolve_people_daily_parent_page_id(config, notion_config, env)
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

    candidate_page_id = ""
    candidate_url = ""
    candidate_source = ""
    if not force and existing and existing.get("page_id"):
        candidate_page_id = str(existing.get("page_id") or "")
        candidate_url = str(existing.get("url") or "")
        candidate_source = "local_publication_state"
    if not force and notion_existing and notion_existing.get("id"):
        candidate_page_id = str(notion_existing.get("id") or "")
        candidate_url = str(notion_existing.get("url") or candidate_url)
        candidate_source = "notion_title_check"
    if candidate_page_id and not candidate_url:
        try:
            candidate_url = str(notion_request(method="GET", url=f"https://api.notion.com/v1/pages/{candidate_page_id}", token=token, timeout=timeout).get("url") or "")
        except Exception:
            candidate_url = ""

    if candidate_page_id:
        status = inspect_article_children(
            date_page_id=candidate_page_id,
            token=token,
            detailed=detailed,
            title_by_url=title_by_url,
            timeout=timeout,
        )
        if status["complete"]:
            state[issue_key] = {
                "page_id": candidate_page_id,
                "url": candidate_url,
                "published_at": existing.get("published_at") if isinstance(existing, dict) else "",
                "last_checked_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                "detailed_pages": status["complete_count"],
                "status": "complete",
                "source": candidate_source,
            }
            save_publication_state(state_path, state)
            return {
                "enabled": True,
                "attempted": False,
                "skipped_duplicate": True,
                "page_id": candidate_page_id,
                "url": candidate_url,
                "source": candidate_source,
                "detailed_pages": status["complete_count"],
            }

        repair_targets = list(status["missing"] or []) + list(status["empty"] or [])
        append_missing_article_entries(
            date_page_id=candidate_page_id,
            token=token,
            articles=list(status["missing"] or []),
            title_by_url=title_by_url,
            timeout=timeout,
        )
        if skip_analysis:
            raise RuntimeError("publish-only resume found partial Notion page; refusing repair without analysis")
        analysis_settings = dict(pd_config.get("analysis") or {})
        analysis_settings.setdefault("openclaw_bin", config.get("openclaw_bin") or "openclaw")
        analysis_by_url = analyze_articles_with_cache(
            output_root=output_root,
            issue_key=issue_key,
            articles=repair_targets,
            settings=analysis_settings,
            reuse_cache=True,
            label="repair analyze",
        )
        filled = fill_article_pages(
            date_page_id=candidate_page_id,
            token=token,
            detailed=repair_targets,
            title_by_url=title_by_url,
            analysis_by_url=analysis_by_url,
            timeout=timeout,
            replace_existing=True,
        )
        repaired_status = inspect_article_children(
            date_page_id=candidate_page_id,
            token=token,
            detailed=detailed,
            title_by_url=title_by_url,
            timeout=timeout,
        )
        state[issue_key] = {
            "page_id": candidate_page_id,
            "url": candidate_url,
            "last_repaired_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "detailed_pages": repaired_status["complete_count"],
            "expected_detailed_pages": repaired_status["expected"],
            "status": "complete" if repaired_status["complete"] else "partial",
            "source": candidate_source,
        }
        save_publication_state(state_path, state)
        if not repaired_status["complete"]:
            raise RuntimeError(
                f"Notion page is still partial after repair: {repaired_status['complete_count']}/{repaired_status['expected']} article pages complete"
            )
        return {
            "enabled": True,
            "attempted": True,
            "repaired_existing": True,
            "page_id": candidate_page_id,
            "url": candidate_url,
            "detailed_pages": repaired_status["complete_count"],
            "filled_now": len(filled),
        }

    if skip_analysis:
        raise RuntimeError("publish-only resume found no existing Notion page; refusing to rebuild without analysis")

    analysis_settings = dict(pd_config.get("analysis") or {})
    analysis_settings.setdefault("openclaw_bin", config.get("openclaw_bin") or "openclaw")
    analysis_by_url = analyze_articles_with_cache(
        output_root=output_root,
        issue_key=issue_key,
        articles=detailed,
        settings=analysis_settings,
        reuse_cache=True,
        label="analyze",
    )
    validate_cached_analysis_quality(
        output_root=output_root,
        issue_key=issue_key,
        articles=detailed,
        settings=analysis_settings,
    )
    overview_lines = issue_overview_with_cache(
        output_root=output_root,
        issue_key=issue_key,
        manifest=manifest,
        detailed=detailed,
        analysis_by_url=analysis_by_url,
        settings=analysis_settings,
        reuse_cache=True,
    )

    blocks = build_date_page_blocks(
        manifest=manifest,
        detailed=detailed,
        detailed_title_by_url=title_by_url,
        analysis_by_url=analysis_by_url,
        overview_lines=overview_lines,
    )

    existing_page_id = ""
    existing_url = ""
    if force and existing and existing.get("page_id"):
        existing_page_id = str(existing.get("page_id") or "")
        existing_url = str(existing.get("url") or "")
    elif force and notion_existing and notion_existing.get("id"):
        existing_page_id = str(notion_existing.get("id") or "")
        existing_url = str(notion_existing.get("url") or "")

    if existing_page_id:
        update_page_title(existing_page_id, token, title, timeout)
        clear_children(existing_page_id, token, timeout)
        append_children_with_child_pages(existing_page_id, token, blocks, timeout)
        page = {"id": existing_page_id, "url": existing_url}
    else:
        page = create_date_page(
            parent_page_id=parent_page_id,
            token=token,
            title=title,
            blocks=blocks,
            timeout=timeout,
        )

    state[issue_key] = {
        "page_id": page["id"],
        "url": page.get("url"),
        "started_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "detailed_pages": 0,
        "expected_detailed_pages": len(detailed),
        "status": "partial",
        "updated_existing": bool(existing_page_id),
    }
    save_publication_state(state_path, state)

    filled = fill_article_pages(
        date_page_id=page["id"],
        token=token,
        detailed=detailed,
        title_by_url=title_by_url,
        analysis_by_url=analysis_by_url,
        timeout=timeout,
    )
    final_status = inspect_article_children(
        date_page_id=page["id"],
        token=token,
        detailed=detailed,
        title_by_url=title_by_url,
        timeout=timeout,
    )
    state[issue_key] = {
        "page_id": page["id"],
        "url": page.get("url"),
        "published_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "detailed_pages": final_status["complete_count"],
        "expected_detailed_pages": final_status["expected"],
        "status": "complete" if final_status["complete"] else "partial",
        "updated_existing": bool(existing_page_id),
    }
    save_publication_state(state_path, state)
    if not final_status["complete"]:
        raise RuntimeError(
            f"Notion publish incomplete: {final_status['complete_count']}/{final_status['expected']} article pages complete"
        )
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
    output_root = Path(pd_config.get("output_dir") or "~/.openclaw/workspace/people-daily-deep-read").expanduser()
    issue_date = issue_date_from_layout_url(start_url)
    existing_manifest = output_root / issue_date.strftime("%Y-%m-%d") / "manifest.json"
    if existing_manifest.exists() and not args.force:
        manifest = load_json(existing_manifest)
        manifest.setdefault("workflow", {})["resumed_from_manifest"] = str(existing_manifest)
        return manifest
    manifest = collect_issue(
        start_url=start_url,
        output_root=output_root,
        max_pages=int(args.max_pages or pd_config.get("max_pages") or 99),
        delay_seconds=float(args.delay if args.delay is not None else pd_config.get("crawl_delay_seconds", 120)),
        download_pdfs=not args.no_pdf,
        analysis="none",
        openclaw_bin=str(config.get("openclaw_bin") or "openclaw"),
        timeout=int(pd_config.get("timeout") or 120),
    )
    return manifest


def validate_manifest(manifest: dict[str, Any], *, max_page_no: int, require_news: bool = True) -> None:
    issue = manifest.get("issue") or {}
    issue_date = compact(issue.get("date"))
    if not issue_date:
        raise RuntimeError("People's Daily manifest is missing issue.date")
    pages = manifest.get("pages") or []
    articles = manifest.get("articles") or []
    if not pages:
        raise RuntimeError("People's Daily crawl produced zero pages; likely network/page availability failure")
    if not articles:
        raise RuntimeError("People's Daily crawl produced zero articles; likely network/page availability failure")
    if require_news:
        selected_pages = news_pages(manifest)
        selected_articles = detailed_articles(manifest, max_page_no)
        if not selected_pages:
            raise RuntimeError("People's Daily crawl found no 要闻 pages; do not publish partial/invalid output")
        if not selected_articles:
            raise RuntimeError("People's Daily crawl found no 要闻 articles; do not publish partial/invalid output")



def telegram_bot_token_from_config(path: Path) -> str:
    try:
        config = json.loads(path.expanduser().read_text(encoding="utf-8"))
    except Exception:
        return ""
    return str(((config.get("channels") or {}).get("telegram") or {}).get("botToken") or "").strip()


def send_telegram_bot_api(*, bot_token: str, chat_id: str, text: str, timeout: int, attempts: int = 4) -> dict[str, Any]:
    body = urllib.parse.urlencode(
        {
            "chat_id": chat_id.removeprefix("telegram:"),
            "text": text,
            "disable_web_page_preview": "false",
        }
    ).encode("utf-8")
    payload: dict[str, Any] = {}
    used_attempts = 0
    for attempt in range(max(1, attempts)):
        used_attempts = attempt + 1
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            data=body,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            break
        except (urllib.error.URLError, TimeoutError, socket.timeout):
            if attempt + 1 >= max(1, attempts):
                raise
            time.sleep(min(2**attempt, 10))
    return {
        "enabled": True,
        "attempted": True,
        "provider": "telegram_bot_api",
        "returncode": 0 if payload.get("ok") else 1,
        "message_id": ((payload.get("result") or {}).get("message_id")),
        "attempts": used_attempts,
    }


def deliver_people_daily_link(*, config: dict[str, Any], manifest: dict[str, Any], publication: dict[str, Any]) -> dict[str, Any]:
    pd_config = config.get("people_daily_deep_read") or {}
    telegram = (pd_config.get("telegram") or config.get("telegram") or {})
    if not telegram.get("enabled"):
        return {"enabled": False, "attempted": False}
    url = str(publication.get("url") or "").strip()
    if not url:
        return {"enabled": True, "attempted": False, "reason": "missing Notion url"}
    target = str(telegram.get("target") or "").strip()
    if not target:
        return {"enabled": True, "attempted": False, "reason": "missing telegram target"}

    issue_date = issue_date_from_manifest(manifest)
    title = format_issue_title(issue_date)
    message = f"人民日报深读完成：{title}\nNotion：{url}"
    timeout = int(telegram.get("timeout") or 120)
    method = str(telegram.get("delivery_method") or "").strip().lower()
    if str(telegram.get("channel") or "telegram") == "telegram" and method == "bot_api":
        token = telegram_bot_token_from_config(Path(telegram.get("openclaw_config_path") or "~/.openclaw/openclaw.json"))
        if not token:
            return {"enabled": True, "attempted": False, "reason": "missing channels.telegram.botToken"}
        try:
            return send_telegram_bot_api(
                bot_token=token,
                chat_id=target,
                text=message,
                timeout=timeout,
                attempts=int(telegram.get("retry_attempts") or 4),
            )
        except Exception as exc:  # noqa: BLE001
            return {"enabled": True, "attempted": True, "provider": "telegram_bot_api", "exception": str(exc)}

    openclaw_bin = Path(config.get("openclaw_bin") or "openclaw").expanduser()
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
    account = str(telegram.get("account") or "").strip()
    if account:
        cmd.extend(["--account", account])
    try:
        completed = run_process_group(cmd, text=True, capture_output=True, timeout=timeout, check=False)
        return {
            "enabled": True,
            "attempted": True,
            "provider": "openclaw_cli",
            "returncode": completed.returncode,
            "stdout": completed.stdout[-1200:],
            "stderr": completed.stderr[-1200:],
        }
    except Exception as exc:  # noqa: BLE001
        return {"enabled": True, "attempted": True, "provider": "openclaw_cli", "exception": str(exc)}


def deliver_people_daily_status(
    *,
    config: dict[str, Any],
    manifest: dict[str, Any],
    status: str,
    reason: str = "",
    publication: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pd_config = config.get("people_daily_deep_read") or {}
    telegram = (pd_config.get("telegram") or config.get("telegram") or {})
    if not telegram.get("enabled"):
        return {"enabled": False, "attempted": False}
    target = str(telegram.get("target") or "").strip()
    if not target:
        return {"enabled": True, "attempted": False, "reason": "missing telegram target"}
    title = format_issue_title(issue_date_from_manifest(manifest))
    labels = {"complete": "完成", "partial": "部分完成", "failed": "失败"}
    lines = [f"人民日报深读{labels.get(status, status)}：{title}"]
    url = str((publication or {}).get("url") or "").strip()
    if url:
        lines.append(f"Notion：{url}")
    if reason:
        lines.append(f"状态：{reason[:260]}")
    message = "\n".join(lines)
    timeout = int(telegram.get("timeout") or 120)
    method = str(telegram.get("delivery_method") or "").strip().lower()
    if str(telegram.get("channel") or "telegram") == "telegram" and method == "bot_api":
        token = telegram_bot_token_from_config(Path(telegram.get("openclaw_config_path") or "~/.openclaw/openclaw.json"))
        if not token:
            return {"enabled": True, "attempted": False, "reason": "missing channels.telegram.botToken"}
        try:
            return send_telegram_bot_api(
                bot_token=token,
                chat_id=target,
                text=message,
                timeout=timeout,
                attempts=int(telegram.get("retry_attempts") or 4),
            )
        except Exception as exc:  # noqa: BLE001
            return {"enabled": True, "attempted": True, "provider": "telegram_bot_api", "exception": str(exc)}
    openclaw_bin = Path(config.get("openclaw_bin") or "openclaw").expanduser()
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
    account = str(telegram.get("account") or "").strip()
    if account:
        cmd.extend(["--account", account])
    try:
        completed = run_process_group(cmd, text=True, capture_output=True, timeout=timeout, check=False)
        return {"enabled": True, "attempted": True, "provider": "openclaw_cli", "returncode": completed.returncode, "stdout": completed.stdout[-1200:], "stderr": completed.stderr[-1200:]}
    except Exception as exc:  # noqa: BLE001
        return {"enabled": True, "attempted": True, "provider": "openclaw_cli", "exception": str(exc)}

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
    parser.add_argument("--no-validate", action="store_true", help="Skip manifest sanity checks")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--analysis-only",
        action="store_true",
        help="Run/reuse article analysis cache and stop before Notion publish/Telegram notify.",
    )
    parser.add_argument(
        "--from-stage",
        choices=["collect", "validate", "analyze", "publish", "notify"],
        default="",
        help="Resume workflow from a stage. publish/notify reuse the existing manifest and skip article analysis reruns.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    script_dir = Path(__file__).resolve().parent
    config_path = (script_dir / args.config).resolve()
    config = load_json(config_path)
    manifest = collect_or_load_manifest(args, config)
    pd_config = config.get("people_daily_deep_read") or {}
    output_root = Path(pd_config.get("output_dir") or "~/.openclaw/workspace/people-daily-deep-read").expanduser()
    issue_key = issue_date_from_manifest(manifest).strftime("%Y-%m-%d")
    report_path = output_root / issue_key / f"{issue_key.replace('-', '')}_people_daily_deep_read.md"
    checkpoint_dir = output_root / issue_key / "checkpoints"
    task = background_tasks.create_or_resume_task(
        task_type="people_daily_deep_read",
        task_id=f"people_daily_deep_read-{issue_key}",
        key=issue_key,
        requested_by=os.environ.get("OPENCLAW_REQUESTED_BY", "systemd/supervisor"),
        input_summary=f"生成{issue_key}人民日报深读，完成标准：本地深读、Notion 发布、Telegram Notion 链接通知。",
        success_criteria=[
            str(report_path),
            str(output_root / issue_key / "manifest.json"),
            str(checkpoint_dir / "publish.json"),
            str(checkpoint_dir / "notify.json"),
        ],
        retry_policy={"mode": "supervisor", "max_immediate_retries": 0, "retry_delay_minutes": 20},
        review_required=False,
        metadata={"issue": issue_key},
    )
    background_task_id = str(task["task_id"])
    os.environ["OPENCLAW_BACKGROUND_TASK_ID"] = background_task_id
    background_tasks.add_artifacts(background_task_id, [report_path, output_root / issue_key / "manifest.json", checkpoint_dir])
    write_workflow_checkpoint(output_root, issue_key, "dag", {"status": "defined", "dag": people_daily_dag_spec()})
    write_workflow_checkpoint(
        output_root,
        issue_key,
        "collect",
        {
            "status": "done",
            "pages": len(manifest.get("pages") or []),
            "articles": len(manifest.get("articles") or []),
            "resumed_from_manifest": (manifest.get("workflow") or {}).get("resumed_from_manifest"),
        },
    )
    if not args.no_validate:
        max_page_no = int((pd_config.get("analysis") or {}).get("detailed_max_page_no") or 4)
        try:
            validate_manifest(manifest, max_page_no=max_page_no)
            write_workflow_checkpoint(output_root, issue_key, "validate", {"status": "done", "max_page_no": max_page_no})
        except Exception as exc:  # noqa: BLE001
            write_workflow_checkpoint(output_root, issue_key, "validate", {"status": "failed", "error": str(exc), "max_page_no": max_page_no})
            background_tasks.fail_task(
                background_task_id,
                error_kind="validation_failed",
                error_summary=str(exc),
                checkpoint_path=checkpoint_dir / "validate.json",
                artifacts=[output_root / issue_key / "manifest.json"],
                needs_review=False,
            )
            raise
    print(f"issue={manifest['issue']['date']}")
    print(f"pages={len(manifest.get('pages') or [])}")
    print(f"articles={len(manifest.get('articles') or [])}")
    if args.analysis_only or args.from_stage == "analyze":
        max_page_no = int((pd_config.get("analysis") or {}).get("detailed_max_page_no") or 4)
        detailed = detailed_articles(manifest, max_page_no)
        analysis_settings = dict(pd_config.get("analysis") or {})
        analysis_settings.setdefault("openclaw_bin", config.get("openclaw_bin") or "openclaw")
        if args.dry_run:
            cached = sum(1 for idx, article in enumerate(detailed, 1) if load_cached_analysis(output_root, issue_key, article, idx))
            write_workflow_checkpoint(output_root, issue_key, "analyze", {"status": "dry_run", "cached": cached, "total": len(detailed)})
            print(json.dumps({"analysis": {"dry_run": True, "cached": cached, "total": len(detailed)}}, ensure_ascii=False, indent=2))
            return 0
        analysis_by_url = analyze_articles_with_cache(
            output_root=output_root,
            issue_key=issue_key,
            articles=detailed,
            settings=analysis_settings,
            reuse_cache=True,
            label="analyze",
        )
        validate_cached_analysis_quality(
            output_root=output_root,
            issue_key=issue_key,
            articles=detailed,
            settings=analysis_settings,
        )
        overview_lines = issue_overview_with_cache(
            output_root=output_root,
            issue_key=issue_key,
            manifest=manifest,
            detailed=detailed,
            analysis_by_url=analysis_by_url,
            settings=analysis_settings,
            reuse_cache=True,
        )
        print(json.dumps({"analysis": {"completed": len(analysis_by_url), "total": len(detailed)}, "overview": {"paragraphs": len(overview_lines)}}, ensure_ascii=False, indent=2))
        return 0
    if args.no_publish:
        write_workflow_checkpoint(output_root, issue_key, "publish", {"status": "skipped", "reason": "--no-publish"})
        return 0
    if args.from_stage == "notify":
        publication = find_existing_publication(output_root, issue_key)
        if not publication.get("url"):
            write_workflow_checkpoint(output_root, issue_key, "notify", {"status": "failed", "attempted": False, "reason": "missing existing Notion publication"})
            print(json.dumps({"telegram": {"enabled": True, "attempted": False, "reason": "missing existing Notion publication"}}, ensure_ascii=False, indent=2))
            return 7
        if args.dry_run:
            delivery = {"enabled": True, "attempted": False, "reason": "dry-run delivery disabled"}
        else:
            delivery = deliver_people_daily_status(
                config=config,
                manifest=manifest,
                publication=publication,
                status="complete",
            )
        write_workflow_checkpoint(
            output_root,
            issue_key,
            "notify",
            {"status": "failed" if delivery.get("exception") or int(delivery.get("returncode") or 0) != 0 else "done", "attempted": bool(delivery.get("attempted")), "returncode": delivery.get("returncode"), "exception": delivery.get("exception"), "notion_url": publication.get("url"), "formal": True, "requires_formal_retry": False},
        )
        print(json.dumps({"telegram": delivery, "publication": publication}, ensure_ascii=False, indent=2))
        if delivery.get("enabled") and delivery.get("attempted") and (delivery.get("exception") or int(delivery.get("returncode") or 0) != 0):
            return 7
        return 0
    try:
        result = publish_to_notion(
            config=config,
            manifest=manifest,
            force=args.force,
            dry_run=args.dry_run,
            skip_analysis=args.from_stage == "publish",
        )
    except Exception as exc:  # noqa: BLE001 - let systemd retry the publish stage; do not push non-formal output
        write_workflow_checkpoint(output_root, issue_key, "publish", {"status": "failed", "error": str(exc)})
        delivery = {"enabled": True, "attempted": False, "reason": "publish failed; non-formal Telegram delivery blocked"}
        write_workflow_checkpoint(
            output_root,
            issue_key,
            "notify",
            {"status": "blocked", "attempted": False, "exception": None, "returncode": None, "formal": False, "requires_formal_retry": True, "reason": delivery.get("reason")},
        )
        print(json.dumps({"notion": {"enabled": True, "attempted": True, "error": str(exc)}, "telegram": delivery}, ensure_ascii=False, indent=2))
        error_path = background_tasks.write_error(
            background_task_id,
            error_kind="notion_publish_failed",
            error_summary=str(exc),
            details={"checkpoint": str(checkpoint_dir / "publish.json")},
        )
        background_tasks.fail_task(
            background_task_id,
            error_kind="notion_publish_failed",
            error_summary=str(exc),
            checkpoint_path=checkpoint_dir / "publish.json",
            artifacts=[report_path, output_root / issue_key / "manifest.json", error_path],
            needs_review=False,
        )
        return 6
    write_workflow_checkpoint(
        output_root,
        issue_key,
        "publish",
        {"status": "dry_run" if result.get("dry_run") else "done" if result.get("url") else "partial", "url": result.get("url"), "attempted": bool(result.get("attempted")), "reason": result.get("reason")},
    )
    if args.dry_run:
        delivery = {"enabled": True, "attempted": False, "reason": "dry-run delivery disabled"}
    elif not result.get("url"):
        delivery = {"enabled": True, "attempted": False, "reason": "missing formal Notion url; Telegram delivery blocked"}
    else:
        delivery = deliver_people_daily_status(
            config=config,
            manifest=manifest,
            publication=result,
            status="complete" if result.get("url") else "partial",
            reason="" if result.get("url") else str(result.get("reason") or "missing Notion url"),
        )
    write_workflow_checkpoint(
        output_root,
        issue_key,
        "notify",
        {"status": "failed" if delivery.get("exception") or int(delivery.get("returncode") or 0) != 0 else "done", "attempted": bool(delivery.get("attempted")), "returncode": delivery.get("returncode"), "exception": delivery.get("exception"), "notion_url": result.get("url"), "formal": bool(result.get("url")), "requires_formal_retry": not bool(result.get("url"))},
    )
    print(json.dumps({"notion": result, "telegram": delivery}, ensure_ascii=False, indent=2))
    if result.get("enabled") and not result.get("attempted") and not result.get("dry_run") and not result.get("skipped_duplicate"):
        return 2
    if delivery.get("enabled") and delivery.get("attempted") and (delivery.get("exception") or int(delivery.get("returncode") or 0) != 0):
        background_tasks.fail_task(
            background_task_id,
            error_kind="telegram_delivery_failed",
            error_summary="人民日报深读 Telegram Notion 链接通知失败，前序产物已生成。",
            checkpoint_path=checkpoint_dir / "notify.json",
            artifacts=[report_path, output_root / issue_key / "manifest.json"],
            needs_review=False,
        )
        return 7
    background_tasks.finish_task(
        background_task_id,
        artifacts=[report_path, output_root / issue_key / "manifest.json", checkpoint_dir / "publish.json", checkpoint_dir / "notify.json"],
        summary=f"{issue_key} 人民日报深读完成；Notion={result.get('url') or ''}",
        main_review_required=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
