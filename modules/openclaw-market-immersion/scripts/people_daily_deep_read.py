#!/usr/bin/env python3
"""People's Daily issue collector and side-by-side deep-read renderer.

The electronic issue exposes both a faithful page PDF and per-article HTML.
This script keeps those two layers separate:

- PDF files preserve the original newspaper layout.
- Article HTML provides ordered, complete text for analysis and comparison.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WORKSPACE_ROOT / "scripts"))
from direct_provider_openclaw_compat import run_openclaw_model_call  # noqa: E402


USER_AGENT = "Mozilla/5.0 OpenClaw People Daily Deep Read"
DEFAULT_BASE = "https://paper.people.com.cn/rmrb/pc"


def fetch_bytes(url: str, timeout: int = 30) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/pdf,*/*",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def fetch_text(url: str, timeout: int = 30) -> str:
    data = fetch_bytes(url, timeout=timeout)
    return data.decode("utf-8", errors="replace")


def clean_text(value: str) -> str:
    value = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", "", value)
    value = re.sub(r"(?is)<br\s*/?>", "\n", value)
    value = re.sub(r"(?is)</p\s*>", "\n", value)
    value = re.sub(r"(?is)<.*?>", "", value)
    value = html.unescape(value)
    value = value.replace("\u3000", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n\s*\n+", "\n", value)
    return value.strip()


def strip_tags(value: str) -> str:
    return clean_text(value).replace("\n", " ").strip()


def first_match(pattern: str, text: str, default: str = "", flags: int = re.I | re.S) -> str:
    match = re.search(pattern, text, flags)
    if not match:
        return default
    return strip_tags(match.group(1))


def unique_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def issue_date_from_layout_url(url: str) -> dt.date:
    match = re.search(r"/layout/(\d{6})/(\d{2})/node_\d+\.html", url)
    if not match:
        raise ValueError(f"Cannot infer issue date from URL: {url}")
    return dt.datetime.strptime(match.group(1) + match.group(2), "%Y%m%d").date()


def layout_url_for_date(day: dt.date, base_url: str = DEFAULT_BASE, page_no: int = 1) -> str:
    return f"{base_url.rstrip('/')}/layout/{day:%Y%m}/{day:%d}/node_{page_no:02d}.html"


def article_number_from_url(url: str) -> str:
    match = re.search(r"content_(\d+)\.html", url)
    return match.group(1) if match else ""


def parse_layout_page(url: str, html_text: str) -> dict[str, Any]:
    page_label = first_match(r'<p[^>]+class=["\']left\s+ban["\'][^>]*>(.*?)</p>', html_text)
    if not page_label:
        page_label = first_match(r'>(\d{2}[^<]*?版[^<]*?)</a>', html_text)
    page_no_match = re.search(r"node_(\d+)\.html", url)
    page_no = page_no_match.group(1) if page_no_match else ""

    pdf_links = re.findall(r'href=["\']([^"\']+\.pdf)["\']', html_text, flags=re.I)
    image_links = re.findall(r'<img[^>]+src=["\']([^"\']+jpg(?:\.\d+)?)["\']', html_text, flags=re.I)
    node_links = re.findall(r'href=["\']([^"\']*node_\d+\.html)["\']', html_text, flags=re.I)
    article_links = re.findall(r'href=["\']([^"\']*content_\d+\.html)["\']', html_text, flags=re.I)

    article_titles: dict[str, str] = {}
    for href, title_html in re.findall(
        r'<a\b[^>]+href=["\']([^"\']*content_\d+\.html)["\'][^>]*>(.*?)</a>',
        html_text,
        flags=re.I | re.S,
    ):
        article_url = urllib.parse.urljoin(url, href)
        title = strip_tags(title_html)
        if title and article_url not in article_titles:
            article_titles[article_url] = title

    return {
        "page_no": page_no,
        "page_label": page_label or f"{page_no}版",
        "url": url,
        "pdf_url": urllib.parse.urljoin(url, pdf_links[0]) if pdf_links else "",
        "image_url": urllib.parse.urljoin(url, image_links[0]) if image_links else "",
        "node_urls": unique_keep_order(
            [
                absolute
                for href in node_links
                for absolute in [urllib.parse.urljoin(url, href)]
                if "/rmrb/pc/layout/" in absolute
            ]
        ),
        "article_urls": unique_keep_order([urllib.parse.urljoin(url, href) for href in article_links]),
        "article_titles": article_titles,
    }


def parse_article(url: str, html_text: str, fallback_title: str = "") -> dict[str, Any]:
    title = first_match(r"<h1[^>]*>(.*?)</h1>", html_text) or fallback_title
    subtitle = first_match(r"<h2[^>]*>(.*?)</h2>", html_text)
    introtitle = first_match(r"<h3[^>]*>(.*?)</h3>", html_text)
    meta = first_match(r'<p[^>]+class=["\']sec["\'][^>]*>(.*?)</p>', html_text)
    content_match = re.search(
        r"<!--enpcontent-->(.*?)<!--/enpcontent-->",
        html_text,
        flags=re.I | re.S,
    )
    if content_match:
        content_html = content_match.group(1)
    else:
        content_html = first_match(r'<div[^>]+id=["\']ozoom["\'][^>]*>(.*?)</div>', html_text)

    paragraphs = [
        clean_text(paragraph)
        for paragraph in re.findall(r"<p[^>]*>(.*?)</p>", content_html, flags=re.I | re.S)
    ]
    paragraphs = [p for p in paragraphs if p]
    if not paragraphs and content_html:
        paragraphs = [p for p in clean_text(content_html).splitlines() if p.strip()]

    article_images: list[dict[str, str]] = []
    for img_tag in re.findall(r"<img\b(?:\"[^\"]*\"|'[^']*'|[^>])*>", content_html + "\n" + html_text, flags=re.I | re.S):
        if "picture-illustrating" not in img_tag and "data-original-title" not in img_tag:
            continue
        src_match = re.search(r"\bsrc=[\"']([^\"']+)[\"']", img_tag, flags=re.I)
        if not src_match:
            continue
        caption_match = re.search(r"\bdata-original-title=[\"']([^\"']*)[\"']", img_tag, flags=re.I | re.S)
        caption = clean_text(caption_match.group(1)) if caption_match else ""
        article_images.append(
            {
                "url": urllib.parse.urljoin(url, src_match.group(1)),
                "caption": caption,
            }
        )

    page_label = ""
    page_match = re.search(r"第\s*(&nbsp;|\s)*(\d{1,2})\s*(&nbsp;|\s)*版", meta)
    if page_match:
        page_label = f"{int(page_match.group(2)):02d}版"
    elif "版" in meta:
        page_label = meta

    return {
        "id": article_number_from_url(url),
        "url": url,
        "title": title,
        "subtitle": subtitle,
        "introtitle": introtitle,
        "meta": meta,
        "page_label": page_label,
        "paragraphs": paragraphs,
        "article_images": article_images,
        "char_count": sum(len(p) for p in paragraphs),
    }


def concise_analysis(article: dict[str, Any]) -> str:
    """A deterministic fallback, used when OpenClaw analysis is disabled."""
    paragraphs = article.get("paragraphs") or []
    text = "\n".join(paragraphs)
    numbers = unique_keep_order(re.findall(r"\d+(?:\.\d+)?%?|\d+/\d+", text))[:8]
    names = unique_keep_order(
        re.findall(r"[\u4e00-\u9fff]{2,12}(?:省|市|县|区|镇|村|集团|公司|学院|大学|中心|会议|工程|规划|战略)", text)
    )[:8]
    first = paragraphs[0] if paragraphs else ""
    second = paragraphs[1] if len(paragraphs) > 1 else ""
    lines = [
        "这篇文章的解析还未调用 OpenClaw 深度生成，下面是基于正文自动抽取的阅读骨架。",
    ]
    if first:
        lines.append(f"开篇落点：{first}")
    if second:
        lines.append(f"展开方式：{second}")
    if numbers:
        lines.append("数字细节：" + "、".join(numbers))
    if names:
        lines.append("关键实体：" + "、".join(names))
    lines.append("建议正式运行时使用 --analysis openclaw 生成论证链条、政策语义和边际信息解析。")
    return "\n\n".join(lines)


def build_openclaw_prompt(article: dict[str, Any], issue_date: str, page_label: str) -> str:
    source_text = "\n".join(article.get("paragraphs") or [])
    return f"""你是中文报刊深读分析助手。请把《人民日报》当作公开政治/政策信号系统来读，基于以下原文做深度解析。

要求：
1. 不要复述全文，不要写空泛评价。
2. 必须解释文章的论证结构：它先确立什么问题，再用哪些事实或概念推进，最后落到什么政策/价值判断。
3. 必须读出政治经济意义：文章承担什么政治功能，关联哪些资源配置、产业方向、财政/金融安排、公共服务投入、区域发展或企业角色。
4. 必须读出信号对象和隐含深意：主要说给谁听，透露了什么政策优先级、考核压力、治理边界、风险遮蔽或未展开的利益冲突。
5. 必须指出文中的具体细节、数字、机构、地点、时间和措辞如何支撑判断。
6. 判断要贴着原文证据，不要离开材料做阴谋论式推断。
7. 不要写固定“后续验证”尾巴；如确有必要，只在文中自然点出可观察的文件、资金、项目或数据。
8. 不要输出后台生产说明，例如原始文章数、合并数量、跨版说明、归入首次版面、后续延续至某版。
9. 输出 Markdown，结构固定为：核心判断、政治经济意义、信号对象与隐含深意、论证链条、关键细节、缺席与边界。

期号：人民日报 {issue_date} {page_label}
标题：{article.get("title") or ""}
作者/版面：{article.get("meta") or ""}
原文：
{source_text}
"""


def run_openclaw_analysis(
    *,
    openclaw_bin: str,
    article: dict[str, Any],
    issue_date: str,
    page_label: str,
    timeout: int,
) -> dict[str, Any]:
    prompt = build_openclaw_prompt(article, issue_date, page_label)
    session_id = f"people-daily-{issue_date.replace('-', '')}-{article.get('id') or 'article'}"
    cmd = [
        openclaw_bin,
        "agent",
        "--local",
        "--agent",
        "main",
        "--session-id",
        session_id,
        "--json",
        "--thinking",
        "medium",
        "--timeout",
        str(timeout),
        prompt,
    ]
    started = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    completed = run_openclaw_model_call(cmd, text=True, capture_output=True, timeout=timeout + 20, check=False)
    analysis = completed.stdout.strip()
    try:
        payload = json.loads(completed.stdout)
        if isinstance(payload, dict):
            analysis = (
                payload.get("message")
                or payload.get("content")
                or payload.get("text")
                or completed.stdout.strip()
            )
    except json.JSONDecodeError:
        pass
    return {
        "started_at": started,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "analysis": analysis.strip(),
    }


def ensure_relative(path: Path, base: Path) -> str:
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return path.as_posix()


def write_html_preview(path: Path, manifest: dict[str, Any], output_dir: Path) -> None:
    issue = manifest["issue"]
    pages = manifest["pages"]
    articles = manifest["articles"]
    first_pdf = next((p.get("pdf_file") for p in pages if p.get("pdf_file")), "")
    first_pdf_rel = ensure_relative(Path(first_pdf), output_dir) if first_pdf else ""

    nav_items = []
    for article in articles:
        nav_items.append(
            f'<a href="#article-{html.escape(article["id"] or str(len(nav_items)+1))}">'
            f'{html.escape(article.get("page_label") or "")} {html.escape(article.get("title") or "未命名")}</a>'
        )
    article_blocks = []
    for article in articles:
        article_id = html.escape(article["id"] or article.get("title") or "")
        original_paragraphs = "\n".join(
            f"<p>{html.escape(p)}</p>" for p in (article.get("paragraphs") or [])
        )
        analysis = html.escape(article.get("analysis") or "")
        source_url = html.escape(article.get("url") or "")
        page_pdf = ""
        for page in pages:
            if page.get("page_no") == article.get("page_no") and page.get("pdf_file"):
                page_pdf = ensure_relative(Path(page["pdf_file"]), output_dir)
                break
        if not page_pdf:
            page_pdf = first_pdf_rel
        article_blocks.append(
            f"""
            <section class="article" id="article-{article_id}" data-pdf="{html.escape(page_pdf)}">
              <div class="article-head">
                <div>
                  <div class="kicker">{html.escape(article.get("page_label") or "")}</div>
                  <h2>{html.escape(article.get("title") or "未命名")}</h2>
                  <p class="meta">{html.escape(article.get("meta") or "")}</p>
                </div>
                <a href="{source_url}" target="_blank" rel="noreferrer">原网页</a>
              </div>
              <div class="compare">
                <div class="pane original">
                  <h3>原文</h3>
                  {original_paragraphs}
                </div>
                <div class="pane analysis">
                  <h3>解析</h3>
                  {''.join(f'<p>{part}</p>' for part in analysis.splitlines() if part.strip())}
                </div>
              </div>
            </section>
            """
        )

    html_doc = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>人民日报 {html.escape(issue["date"])} 深读</title>
  <style>
    :root {{ color-scheme: light; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    body {{ margin: 0; color: #262626; background: #f6f6f4; }}
    header {{ position: sticky; top: 0; z-index: 10; background: #fff; border-bottom: 1px solid #ddd; padding: 14px 22px; }}
    h1 {{ margin: 0 0 4px; font-size: 22px; letter-spacing: 0; }}
    .sub {{ margin: 0; color: #666; font-size: 13px; }}
    .shell {{ display: grid; grid-template-columns: minmax(380px, 42vw) 1fr; min-height: calc(100vh - 66px); }}
    .layout {{ position: sticky; top: 66px; height: calc(100vh - 66px); background: #2d2d2d; border-right: 1px solid #ccc; }}
    iframe {{ width: 100%; height: 100%; border: 0; background: #fff; }}
    main {{ padding: 22px 26px 60px; }}
    nav {{ display: flex; gap: 8px; overflow-x: auto; padding-bottom: 14px; margin-bottom: 8px; }}
    nav a {{ flex: 0 0 auto; color: #333; background: #fff; border: 1px solid #ddd; border-radius: 6px; padding: 7px 10px; text-decoration: none; font-size: 13px; }}
    .article {{ background: #fff; border: 1px solid #ddd; border-radius: 8px; margin: 0 0 22px; }}
    .article-head {{ display: flex; justify-content: space-between; gap: 16px; padding: 18px 20px 14px; border-bottom: 1px solid #eee; }}
    .article-head a {{ color: #a30000; white-space: nowrap; font-size: 13px; text-decoration: none; margin-top: 4px; }}
    .kicker {{ color: #a30000; font-size: 13px; margin-bottom: 6px; }}
    h2 {{ margin: 0; font-size: 22px; line-height: 1.35; }}
    .meta {{ margin: 8px 0 0; color: #777; font-size: 13px; }}
    .compare {{ display: grid; grid-template-columns: 1fr 1fr; }}
    .pane {{ padding: 18px 20px 22px; min-width: 0; }}
    .pane + .pane {{ border-left: 1px solid #eee; }}
    h3 {{ margin: 0 0 12px; font-size: 15px; color: #555; }}
    p {{ font-size: 16px; line-height: 1.78; margin: 0 0 13px; }}
    .analysis p {{ color: #303030; }}
    @media (max-width: 980px) {{
      .shell {{ grid-template-columns: 1fr; }}
      .layout {{ position: relative; top: 0; height: 72vh; }}
      .compare {{ grid-template-columns: 1fr; }}
      .pane + .pane {{ border-left: 0; border-top: 1px solid #eee; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>人民日报 {html.escape(issue["date"])} 深读</h1>
    <p class="sub">左侧保留原始 PDF 版面，右侧按文章展示原文与解析。点击文章时会切换到对应版面。</p>
  </header>
  <div class="shell">
    <aside class="layout"><iframe id="pdfFrame" src="{html.escape(first_pdf_rel)}"></iframe></aside>
    <main>
      <nav>{''.join(nav_items)}</nav>
      {''.join(article_blocks)}
    </main>
  </div>
  <script>
    const frame = document.getElementById('pdfFrame');
    const observer = new IntersectionObserver(entries => {{
      for (const entry of entries) {{
        if (entry.isIntersecting) {{
          const pdf = entry.target.dataset.pdf;
          if (pdf && frame.getAttribute('src') !== pdf) frame.setAttribute('src', pdf);
        }}
      }}
    }}, {{ threshold: 0.35 }});
    document.querySelectorAll('.article').forEach(el => observer.observe(el));
  </script>
</body>
</html>
"""
    path.write_text(html_doc, encoding="utf-8")


def write_markdown(path: Path, manifest: dict[str, Any]) -> None:
    issue = manifest["issue"]
    lines = [f"# 人民日报 {issue['date']} 深读", ""]
    lines.append("## 版面原件")
    for page in manifest["pages"]:
        label = page.get("page_label") or page.get("page_no") or ""
        pdf_url = page.get("pdf_url") or ""
        lines.append(f"- {label}：{pdf_url}")
    lines.append("")
    lines.append("## 原文与解析")
    for article in manifest["articles"]:
        lines.append(f"### {article.get('page_label') or ''} {article.get('title') or '未命名'}".strip())
        if article.get("meta"):
            lines.append(article["meta"])
        lines.append("")
        lines.append("#### 解析")
        lines.append(article.get("analysis") or "")
        lines.append("")
        lines.append("#### 原文")
        for paragraph in article.get("paragraphs") or []:
            lines.append(paragraph)
            lines.append("")
    path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def collect_issue(
    *,
    start_url: str,
    output_root: Path,
    max_pages: int,
    delay_seconds: float,
    download_pdfs: bool,
    analysis: str,
    openclaw_bin: str,
    timeout: int,
) -> dict[str, Any]:
    issue_date = issue_date_from_layout_url(start_url)
    issue_dir = output_root / issue_date.strftime("%Y-%m-%d")
    pdf_dir = issue_dir / "pdf"
    issue_dir.mkdir(parents=True, exist_ok=True)
    pdf_dir.mkdir(parents=True, exist_ok=True)

    pending = [start_url]
    visited_layouts: set[str] = set()
    pages: list[dict[str, Any]] = []
    article_urls: list[tuple[str, str, str, str]] = []

    while pending and len(visited_layouts) < max_pages:
        url = pending.pop(0)
        if url in visited_layouts:
            continue
        if visited_layouts and delay_seconds > 0:
            time.sleep(delay_seconds)
        layout_html = fetch_text(url, timeout=timeout)
        page = parse_layout_page(url, layout_html)
        visited_layouts.add(url)

        if download_pdfs and page.get("pdf_url"):
            pdf_name = f"rmrb_{issue_date:%Y%m%d}_{page.get('page_no') or len(pages)+1}.pdf"
            pdf_path = pdf_dir / pdf_name
            if not pdf_path.exists():
                if delay_seconds > 0:
                    time.sleep(delay_seconds)
                pdf_path.write_bytes(fetch_bytes(page["pdf_url"], timeout=timeout))
            page["pdf_file"] = str(pdf_path)

        pages.append(page)
        for article_url in page.get("article_urls") or []:
            title = (page.get("article_titles") or {}).get(article_url, "")
            article_urls.append((article_url, title, page.get("page_no") or "", page.get("page_label") or ""))
        for node_url in page.get("node_urls") or []:
            if node_url not in visited_layouts and node_url not in pending:
                pending.append(node_url)

    articles: list[dict[str, Any]] = []
    seen_articles: set[str] = set()
    for article_url, fallback_title, page_no, page_label in article_urls:
        if article_url in seen_articles:
            continue
        seen_articles.add(article_url)
        if delay_seconds > 0:
            time.sleep(delay_seconds)
        article_html = fetch_text(article_url, timeout=timeout)
        article = parse_article(article_url, article_html, fallback_title=fallback_title)
        article["page_no"] = page_no
        article["page_label"] = page_label or article.get("page_label") or ""
        if analysis == "openclaw":
            result = run_openclaw_analysis(
                openclaw_bin=openclaw_bin,
                article=article,
                issue_date=issue_date.isoformat(),
                page_label=article["page_label"],
                timeout=timeout,
            )
            article["analysis"] = result.get("analysis") or ""
            article["analysis_run"] = {
                "returncode": result.get("returncode"),
                "stderr": result.get("stderr"),
                "started_at": result.get("started_at"),
            }
        elif analysis == "template":
            article["analysis"] = concise_analysis(article)
        else:
            article["analysis"] = ""
        articles.append(article)

    manifest = {
        "version": 1,
        "source": "people_daily",
        "issue": {
            "date": issue_date.isoformat(),
            "start_url": start_url,
        },
        "created_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "pages": pages,
        "articles": articles,
    }
    manifest_path = issue_dir / "manifest.json"
    html_path = issue_dir / f"{issue_date:%Y%m%d}_people_daily_deep_read.html"
    markdown_path = issue_dir / f"{issue_date:%Y%m%d}_people_daily_deep_read.md"
    manifest["files"] = {
        "manifest": str(manifest_path),
        "html": str(html_path),
        "markdown": str(markdown_path),
    }
    write_html_preview(html_path, manifest, issue_dir)
    write_markdown(markdown_path, manifest)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect People's Daily issue PDFs and article text.")
    parser.add_argument("--layout-url", help="People's Daily layout URL, e.g. node_01.html")
    parser.add_argument("--date", help="Issue date in YYYY-MM-DD. Ignored when --layout-url is provided.")
    parser.add_argument("--output-dir", default=os.path.expanduser("~/.openclaw/workspace/people-daily-deep-read"))
    parser.add_argument("--max-pages", type=int, default=99)
    parser.add_argument("--delay", type=float, default=120.0, help="Delay between automated requests. Default follows robots crawl-delay.")
    parser.add_argument("--no-pdf", action="store_true", help="Do not download PDF files.")
    parser.add_argument("--analysis", choices=["none", "template", "openclaw"], default="template")
    parser.add_argument("--openclaw-bin", default=os.environ.get("OPENCLAW_BIN", "openclaw"))
    parser.add_argument("--timeout", type=int, default=120)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.layout_url:
        start_url = args.layout_url
    else:
        day = dt.date.today()
        if args.date:
            day = dt.datetime.strptime(args.date, "%Y-%m-%d").date()
        start_url = layout_url_for_date(day)
    manifest = collect_issue(
        start_url=start_url,
        output_root=Path(args.output_dir).expanduser(),
        max_pages=max(1, args.max_pages),
        delay_seconds=max(0.0, args.delay),
        download_pdfs=not args.no_pdf,
        analysis=args.analysis,
        openclaw_bin=args.openclaw_bin,
        timeout=args.timeout,
    )
    print(f"manifest={manifest['files']['manifest']}")
    print(f"html={manifest['files']['html']}")
    print(f"markdown={manifest['files']['markdown']}")
    print(f"pages={len(manifest['pages'])}")
    print(f"articles={len(manifest['articles'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
