#!/usr/bin/env python3
"""Check bilingual translation artifacts for missing EN/ZH pairing.

This gate is intentionally structural. It does not judge translation quality;
it blocks obvious candidate failures where a bilingual deliverable contains
large source-only/translation-only body blocks, Chinese text repeated in place
of source text, PDF pages dominated by one language, or source tables/charts
that were flattened into ordinary paragraph text.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
from pathlib import Path
from typing import Any


CJK_RE = re.compile(r"[\u4e00-\u9fff]")
LATIN_RE = re.compile(r"[A-Za-z]")
CJK_SENTENCE_RE = re.compile(
    r"[\u4e00-\u9fff][\u4e00-\u9fff0-9A-Za-z\u3000-\u303f\uff00-\uffef"
    r"()（）,.;:!?%\"'\-—]{24,}[\u3002\uff01\uff1f]"
)
BILINGUAL_REQUEST_RE = re.compile(
    r"bilingual|paragraph[- ]by[- ]paragraph|parallel text|"
    r"\u53cc\u8bed|\u4e2d\u82f1|\u82f1\u4e2d|\u5bf9\u7167|"
    r"\u4e2d\u6587.*\u82f1\u6587|\u82f1\u6587.*\u4e2d\u6587",
    re.I,
)
TABLE_FRAGMENT_MARKERS = (
    "QUANTUM FUND EQUITY",
    "Portfolio Structure",
    "Net Asset Value",
    "Closing",
    "% Change",
    "Investment",
    "Positions",
    "Exposure",
    "Long",
    "Short",
    "Treasury Bills",
    "U.S. T-Bonds",
    "Eurodollar",
    "Crude Oil",
    "Japanese Bonds",
    "Common Stock",
    "Currencies",
    "Futures",
    "Options",
)
TABLE_FRAGMENT_ANCHOR_MARKERS = (
    "QUANTUM FUND EQUITY",
    "Portfolio Structure",
    "Net Asset Value",
    "Closing",
    "% Change",
)
TABLEISH_LINE_RE = re.compile(
    r"(%|\$|\b\d{2,}(?:[.,]\d+)?\b|Net Asset Value|Portfolio|"
    r"Investment|Positions|Exposure|Long|Short|Closing|Futures|Options)",
    re.I,
)


def now_cst() -> str:
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(timespec="seconds")


def cjk_count(text: str) -> int:
    return len(CJK_RE.findall(text))


def latin_count(text: str) -> int:
    return len(LATIN_RE.findall(text))


def compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def clean_markdown_block(block: str) -> str:
    lines: list[str] = []
    in_fence = False
    for raw in block.splitlines():
        line = raw.strip()
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if not line or line.startswith("<!--"):
            continue
        lines.append(line)
    text = "\n".join(lines).strip()
    if not text:
        return ""
    if text.startswith(("#", "|", "---")):
        return ""
    if re.fullmatch(r"[-+*/=0-9.,:;()$% \tA-Za-z]+", text) and len(text) < 140:
        return ""
    return text


def markdown_blocks(text: str) -> list[str]:
    return [b for b in (clean_markdown_block(part) for part in re.split(r"\n\s*\n", text)) if b]


def strip_html(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value)
    return html.unescape(re.sub(r"\s+", " ", value)).strip()


def html_single_blocks(text: str) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    pattern = re.compile(r'<p class="(?:toc-)?single (en|zh)">(?P<body>.*?)</p>', re.S)
    for match in pattern.finditer(text):
        body = strip_html(match.group("body"))
        if not body:
            continue
        blocks.append({"lang": match.group(1), "text": body})
    return blocks


def single_block_summary(
    zh_hits: list[dict[str, Any]],
    en_hits: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "significant_single_zh_count": len(zh_hits),
        "significant_single_en_count": len(en_hits),
        "single_zh_examples": zh_hits[:20],
        "single_en_examples": en_hits[:20],
    }


def significant_single_blocks(
    blocks: list[dict[str, Any]],
    *,
    min_cjk_single: int,
    max_latin_in_zh: int,
    min_latin_single: int,
    max_cjk_in_en: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    zh_hits: list[dict[str, Any]] = []
    en_hits: list[dict[str, Any]] = []
    for index, block in enumerate(blocks, 1):
        text = compact(str(block["text"]))
        cjk = cjk_count(text)
        lat = latin_count(text)
        lang = block.get("lang")
        if lang == "zh" or (lang is None and cjk >= min_cjk_single and lat <= max_latin_in_zh):
            if cjk >= min_cjk_single and lat <= max_latin_in_zh:
                zh_hits.append({"index": index, "cjk_chars": cjk, "latin_chars": lat, "sample": text[:160]})
        if lang == "en" or (lang is None and lat >= min_latin_single and cjk <= max_cjk_in_en):
            if lat >= min_latin_single and cjk <= max_cjk_in_en:
                en_hits.append({"index": index, "cjk_chars": cjk, "latin_chars": lat, "sample": text[:160]})
    return zh_hits, en_hits


def markdown_language_blocks(text: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for block in markdown_blocks(text):
        body = compact(block)
        cjk = cjk_count(body)
        lat = latin_count(body)
        if cjk == 0 and lat == 0:
            continue
        lang = None
        if cjk >= 40 and lat <= 80:
            lang = "zh"
        elif lat >= 120 and cjk <= 20:
            lang = "en"
        out.append({"lang": lang, "text": block, "cjk_chars": cjk, "latin_chars": lat})
    return out


def duplicate_cjk_sentences(text: str) -> list[dict[str, Any]]:
    seen: set[str] = set()
    duplicates: dict[str, int] = {}
    for match in CJK_SENTENCE_RE.finditer(text):
        sentence = compact(match.group(0))
        if sentence in seen:
            duplicates[sentence] = duplicates.get(sentence, 1) + 1
        else:
            seen.add(sentence)
    return [
        {"count": count, "sample": sentence[:180]}
        for sentence, count in sorted(duplicates.items(), key=lambda item: (-item[1], item[0]))[:30]
    ]


def table_fragment_score(text: str) -> int:
    lowered = text.lower()
    return sum(1 for marker in TABLE_FRAGMENT_MARKERS if marker.lower() in lowered)


def table_fragment_anchor_score(text: str) -> int:
    lowered = text.lower()
    return sum(1 for marker in TABLE_FRAGMENT_ANCHOR_MARKERS if marker.lower() in lowered)


def table_fragment_line_metrics(text: str) -> dict[str, Any]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return {
            "line_count": 0,
            "tableish_line_count": 0,
            "short_line_count": 0,
            "tableish_line_ratio": 0.0,
            "short_line_ratio": 0.0,
        }
    tableish = [line for line in lines if TABLEISH_LINE_RE.search(line)]
    short = [line for line in lines if len(line) <= 55]
    return {
        "line_count": len(lines),
        "tableish_line_count": len(tableish),
        "short_line_count": len(short),
        "tableish_line_ratio": len(tableish) / len(lines),
        "short_line_ratio": len(short) / len(lines),
    }


def pdf_layout_stats(page: Any) -> tuple[int, int, float]:
    text_blocks = len(page.get_text("blocks"))
    drawings = len(page.get_drawings())
    page_area = page.rect.width * page.rect.height
    image_area = 0.0
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") != 1:
            continue
        x0, y0, x1, y1 = block["bbox"]
        image_area += max(0.0, x1 - x0) * max(0.0, y1 - y0)
    return text_blocks, drawings, image_area / page_area if page_area > 0 else 0.0


def pdf_page_checks(
    path: Path,
    *,
    skip_pages: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], str | None]:
    try:
        import fitz  # type: ignore
    except Exception as exc:  # pragma: no cover - depends on host package set
        return [], [], [], f"PyMuPDF unavailable: {exc}"

    dominant: list[dict[str, Any]] = []
    duplicated: list[dict[str, Any]] = []
    table_fragmented: list[dict[str, Any]] = []
    doc = fitz.open(path)
    for index, page in enumerate(doc, 1):
        if index <= skip_pages:
            continue
        text = page.get_text("text")
        body = compact(text)
        cjk = cjk_count(body)
        lat = latin_count(body)
        total = cjk + lat
        ratio = cjk / total if total else 0.0
        if len(body) >= 450 and cjk >= 250 and ratio >= 0.55 and lat <= 260:
            dominant.append(
                {
                    "page": index,
                    "chars": len(body),
                    "cjk_chars": cjk,
                    "latin_chars": lat,
                    "cjk_latin_ratio": round(ratio, 3),
                    "head": text[:220].replace("\n", " | "),
                }
            )
        markers = table_fragment_score(text)
        anchors = table_fragment_anchor_score(text)
        if anchors >= 1 and markers >= 4:
            text_blocks, drawings, image_area_ratio = pdf_layout_stats(page)
            line_metrics = table_fragment_line_metrics(text)
            if (
                image_area_ratio < 0.18
                and drawings < 2
                and text_blocks >= 24
                and line_metrics["line_count"] >= 20
                and line_metrics["tableish_line_ratio"] >= 0.45
            ):
                table_fragmented.append(
                    {
                        "page": index,
                        "markers": markers,
                        "anchor_markers": anchors,
                        **{
                            key: round(value, 3) if isinstance(value, float) else value
                            for key, value in line_metrics.items()
                        },
                        "text_blocks": text_blocks,
                        "drawings": drawings,
                        "image_area_ratio": round(image_area_ratio, 3),
                        "head": text[:220].replace("\n", " | "),
                    }
                )
        dups = duplicate_cjk_sentences(text)
        if cjk >= 300 and len(dups) >= 2:
            duplicated.append(
                {
                    "page": index,
                    "cjk_chars": cjk,
                    "duplicated_chinese_sentences": len(dups),
                    "head": text[:220].replace("\n", " | "),
                }
            )
    return dominant, duplicated, table_fragmented, None


def run_integrity_gate(
    run_dir: Path,
    artifacts: list[str],
    *,
    expect_bilingual: str = "auto",
    max_significant_single_zh: int = 0,
    max_significant_single_en: int = 0,
    min_cjk_single: int = 40,
    max_latin_in_zh: int = 80,
    min_latin_single: int = 120,
    max_cjk_in_en: int = 20,
    skip_pdf_pages: int = 10,
) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    corpus_bits: list[str] = []
    for name in ("user_request.md", "acceptance_plan.json", "manifest.json"):
        path = run_dir / name
        if path.exists():
            corpus_bits.append(path.read_text(encoding="utf-8", errors="ignore"))
    inferred_bilingual = bool(BILINGUAL_REQUEST_RE.search("\n".join(corpus_bits)))
    if expect_bilingual == "yes":
        bilingual_expected = True
    elif expect_bilingual == "no":
        bilingual_expected = False
    else:
        bilingual_expected = inferred_bilingual

    resolved: list[Path] = []
    for artifact in artifacts:
        path = Path(artifact)
        if not path.is_absolute():
            path = run_dir / path
        path = path.resolve()
        try:
            path.relative_to(run_dir)
        except ValueError:
            continue
        if path.exists() and path not in resolved:
            resolved.append(path)

    failures: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    artifact_reports: list[dict[str, Any]] = []

    if not bilingual_expected:
        return {
            "schema": "openclaw.translation_bilingual_integrity.v0",
            "checked_at": now_cst(),
            "run_dir": str(run_dir),
            "status": "SKIPPED",
            "reason": "bilingual request not detected",
            "artifacts_checked": [],
            "failures": [],
            "warnings": [],
        }

    for path in resolved:
        suffix = path.suffix.lower()
        report: dict[str, Any] = {
            "artifact": str(path.relative_to(run_dir)),
            "bytes": path.stat().st_size,
            "mtime": dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc).isoformat(),
        }
        if suffix in {".md", ".markdown", ".txt"}:
            text = path.read_text(encoding="utf-8", errors="ignore")
            zh_hits, en_hits = significant_single_blocks(
                markdown_language_blocks(text),
                min_cjk_single=min_cjk_single,
                max_latin_in_zh=max_latin_in_zh,
                min_latin_single=min_latin_single,
                max_cjk_in_en=max_cjk_in_en,
            )
            dups = duplicate_cjk_sentences(text)
            report.update(
                {
                    "type": "markdown_text",
                    "duplicate_chinese_sentence_count": len(dups),
                    "duplicate_chinese_examples": dups[:10],
                    "single_block_policy": "diagnostic_only_for_markdown_intermediate",
                    **single_block_summary(zh_hits, en_hits),
                }
            )
            if zh_hits or en_hits:
                warnings.append(
                    {
                        "gate": "markdown_significant_single_blocks_diagnostic",
                        "artifact": report["artifact"],
                        "single_zh_count": len(zh_hits),
                        "single_en_count": len(en_hits),
                        "examples": {"zh": zh_hits[:5], "en": en_hits[:5]},
                    }
                )
            if len(dups) >= 2:
                warnings.append(
                    {
                        "gate": "markdown_duplicated_chinese_body_text_diagnostic",
                        "artifact": report["artifact"],
                        "count": len(dups),
                        "examples": dups[:10],
                    }
                )
        elif suffix in {".html", ".htm"}:
            text = path.read_text(encoding="utf-8", errors="ignore")
            zh_hits, en_hits = significant_single_blocks(
                html_single_blocks(text),
                min_cjk_single=min_cjk_single,
                max_latin_in_zh=max_latin_in_zh,
                min_latin_single=min_latin_single,
                max_cjk_in_en=max_cjk_in_en,
            )
            report.update(
                {
                    "type": "html_renderer",
                    "single_block_policy": "diagnostic_only_for_temporary_renderer_artifact",
                    **single_block_summary(zh_hits, en_hits),
                }
            )
            if zh_hits or en_hits:
                warnings.append(
                    {
                        "gate": "html_significant_single_blocks_diagnostic",
                        "artifact": report["artifact"],
                        "single_zh_count": len(zh_hits),
                        "single_en_count": len(en_hits),
                        "examples": {"zh": zh_hits[:5], "en": en_hits[:5]},
                    }
                )
        elif suffix == ".pdf":
            dominant, duplicated, table_fragmented, warning = pdf_page_checks(path, skip_pages=skip_pdf_pages)
            if warning:
                warnings.append({"gate": "pdf_text_check_unavailable", "artifact": report["artifact"], "warning": warning})
            report.update(
                {
                    "type": "pdf",
                    "chinese_dominant_page_count": len(dominant),
                    "duplicated_chinese_page_count": len(duplicated),
                    "table_fragmented_page_count": len(table_fragmented),
                    "chinese_dominant_examples": dominant[:20],
                    "duplicated_chinese_examples": duplicated[:20],
                    "table_fragmented_examples": table_fragmented[:20],
                }
            )
            if dominant:
                failures.append(
                    {
                        "gate": "pdf_chinese_dominant_missing_bilingual_pages",
                        "artifact": report["artifact"],
                        "count": len(dominant),
                        "examples": dominant[:10],
                    }
                )
            if duplicated:
                failures.append(
                    {
                        "gate": "pdf_duplicated_chinese_body_text",
                        "artifact": report["artifact"],
                        "count": len(duplicated),
                        "examples": duplicated[:10],
                    }
                )
            if table_fragmented:
                failures.append(
                    {
                        "gate": "pdf_table_or_chart_fragmented_as_body_text",
                        "artifact": report["artifact"],
                        "count": len(table_fragmented),
                        "examples": table_fragmented[:10],
                    }
                )
        else:
            report["type"] = "skipped_unsupported_suffix"
        artifact_reports.append(report)

    status = "PASSED" if not failures else "FAILED"
    return {
        "schema": "openclaw.translation_bilingual_integrity.v0",
        "checked_at": now_cst(),
        "run_dir": str(run_dir),
        "status": status,
        "bilingual_expected": bilingual_expected,
        "thresholds": {
            "max_significant_single_zh": max_significant_single_zh,
            "max_significant_single_en": max_significant_single_en,
            "min_cjk_single": min_cjk_single,
            "max_latin_in_zh": max_latin_in_zh,
            "min_latin_single": min_latin_single,
            "max_cjk_in_en": max_cjk_in_en,
            "skip_pdf_pages": skip_pdf_pages,
            "pdf_table_fragment_min_marker_hits": 4,
            "pdf_table_fragment_min_anchor_hits": 1,
            "pdf_table_fragment_max_image_area_ratio": 0.18,
            "pdf_table_fragment_min_text_blocks": 24,
            "pdf_table_fragment_min_tableish_line_ratio": 0.45,
        },
        "artifacts_checked": artifact_reports,
        "failures": failures,
        "warnings": warnings,
        "next_action": (
            "repair source/translation pairing before candidate promotion"
            if failures
            else "bilingual structure gate cleared"
        ),
    }


def write_report_md(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Bilingual Integrity Gate",
        "",
        f"- Status: `{report['status']}`",
        f"- Bilingual expected: `{report.get('bilingual_expected', False)}`",
        f"- Failure gates: {len(report.get('failures', []))}",
        f"- Warning gates: {len(report.get('warnings', []))}",
        "",
    ]
    for artifact in report.get("artifacts_checked", []):
        lines.append(
            f"- `{artifact.get('artifact')}`: "
            f"single_zh={artifact.get('significant_single_zh_count', 'n/a')}, "
            f"single_en={artifact.get('significant_single_en_count', 'n/a')}, "
            f"dominant_pages={artifact.get('chinese_dominant_page_count', 'n/a')}, "
            f"table_fragmented_pages={artifact.get('table_fragmented_page_count', 'n/a')}"
        )
    if report.get("failures"):
        lines.extend(["", "## Failures", ""])
        for failure in report["failures"]:
            lines.append(f"- `{failure.get('gate')}` in `{failure.get('artifact')}`: count={failure.get('count')}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--artifact", action="append", default=[], help="Artifact path under run dir; repeatable")
    ap.add_argument("--expect-bilingual", choices=["auto", "yes", "no"], default="auto")
    ap.add_argument("--max-significant-single-zh", type=int, default=0)
    ap.add_argument("--max-significant-single-en", type=int, default=0)
    ap.add_argument("--min-cjk-single", type=int, default=40)
    ap.add_argument("--max-latin-in-zh", type=int, default=80)
    ap.add_argument("--min-latin-single", type=int, default=120)
    ap.add_argument("--max-cjk-in-en", type=int, default=20)
    ap.add_argument("--skip-pdf-pages", type=int, default=10)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--md-out", type=Path)
    args = ap.parse_args()

    run_dir = args.run_dir.resolve()
    artifacts = args.artifact or [
        name
        for name in ("translation.md", "build_html_if_needed.tmp.html", "alchemy_finance_bilingual.pdf")
        if (run_dir / name).exists()
    ]
    report = run_integrity_gate(
        run_dir,
        artifacts,
        expect_bilingual=args.expect_bilingual,
        max_significant_single_zh=args.max_significant_single_zh,
        max_significant_single_en=args.max_significant_single_en,
        min_cjk_single=args.min_cjk_single,
        max_latin_in_zh=args.max_latin_in_zh,
        min_latin_single=args.min_latin_single,
        max_cjk_in_en=args.max_cjk_in_en,
        skip_pdf_pages=args.skip_pdf_pages,
    )
    if args.out:
        out = args.out if args.out.is_absolute() else run_dir / args.out
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.md_out:
        md_out = args.md_out if args.md_out.is_absolute() else run_dir / args.md_out
        write_report_md(md_out, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("BILINGUAL_INTEGRITY_GATE_OK" if report["status"] in {"PASSED", "SKIPPED"} else "BILINGUAL_INTEGRITY_GATE_FAILED")
    return 0 if report["status"] in {"PASSED", "SKIPPED"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
