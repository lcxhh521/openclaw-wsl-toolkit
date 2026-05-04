# People's Daily Deep-Read Workflow

This workflow keeps only the public, reusable automation shape. Local deployments can provide their own analysis prompt through `people_daily_deep_read.analysis.prompt_template_path` or `prompt_template`.

## Purpose

Treat the People's Daily issue as a daily policy-signal reading task, not a full-newspaper archive or a generic summary. The daily output should help the reader understand the issue-level signal, the article-level reasoning, and the original evidence behind each interpretation.

## Scope

- Keep pages whose page label contains `要闻`.
- Drop non-news pages by default: supplement, lifestyle/service, culture, ordinary professional pages, and editorial metadata rows.
- Keep the PDF links for retained news pages.
- Create article child pages only for retained news articles.

If an old manifest has no page labels, the workflow falls back to `analysis.detailed_max_page_no` for compatibility.

## Images

- Keep image metadata collected from article pages when available.
- Treat picture reports as articles that need image-signal reading.
- Do not infer image content if the source did not provide image/caption data.

## Date Page Shape

1. `今日总览`
   - 3-5 paragraphs in the target deployment.
   - It should describe the issue-level signal and article/page relationships, not list every article mechanically.
   - The public script contains a deterministic fallback overview; private deployments may replace it with a prompt-generated overview.
2. `全日PDF`
   - Only retained news-page PDFs.
3. One section per retained news page.
4. Each article row includes title, official source link, and child-page entry.

## Article Child Page Shape

1. `基本信息`
2. `结构化原文与解析`
   - Original text should remain visible.
   - Analysis should be tied to original evidence.
3. Optional signal / policy-chain / follow-up sections when the prompt returns them.
4. `全文深度解读`

## Prompt Contract

The workflow expects the OpenClaw analysis call to return JSON:

```json
{
  "paragraph_notes": [
    {"excerpt": "short locator", "analysis": "paragraph or structure-group analysis"}
  ],
  "signal_analysis": ["optional signal analysis"],
  "policy_chain": ["optional chain/observation item"],
  "follow_up": ["optional follow-up item"],
  "full_analysis": ["article-level deep-read paragraphs"]
}
```

Minimum requirements:

- `paragraph_notes` should align with the source paragraphs unless the local prompt explicitly implements structure-group merging.
- Do not output empty praise or generic filler.
- Every judgment should be traceable to original text, page placement, source metadata, or explicitly marked follow-up hypotheses.
- Keep local/private reading strategies out of the public repository; load them from a local prompt template.

## Safe Production Flow for Structure Rewrites

For large article batches, use a staged flow:

1. Generate temporary drafts outside the canonical output, for example `/tmp/pd_struct_1_7.md`.
2. Use clear separators such as `===== ARTICLE <n> =====` and preserve article titles.
3. Do not mutate the canonical Markdown or Notion page during draft generation.
4. Check coverage: no missing articles, no duplicates, titles match, quote counts look plausible.
5. Merge only the target `结构化原文与解析` section.
6. Do not touch approved article-level deep reads, date overview, PDF links, or unrelated sections.
7. Patch Notion only after local verification, preferably with a conservative block-level patch helper.

## Automation

The module ships a user-level systemd timer:

- `openclaw-people-daily-deep-read.timer`
- `openclaw-people-daily-deep-read.service`
- Default schedule: daily at `08:30`
- `Persistent=true`, so missed runs are triggered after the user service manager is back.

Check it with:

```bash
systemctl --user list-timers openclaw-people-daily-deep-read.timer --all
systemctl --user status openclaw-people-daily-deep-read.service --no-pager
```

## Notion Rules

- First publication may create the date page and article child pages.
- If the date page already exists, the workflow skips unless `--force` is passed.
- Small later edits should be block/section patches rather than full-page rebuilds.
- Never publish secrets, local output, generated daily Markdown/HTML, or state files to Git.

## Quality Gate

Before publishing or pushing workflow changes:

- Python scripts compile with `python3 -m py_compile`.
- `--dry-run --no-pdf` works for a known date or manifest.
- Generated output keeps only news pages when page labels are present.
- No generated daily issue output, state, secrets, `/tmp` drafts, or `__pycache__` files are staged.
