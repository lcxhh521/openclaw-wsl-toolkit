# People's Daily Deep-Read Workflow

This workflow keeps only the public, reusable automation shape. Local deployments should provide their own private reading prompts under `~/.openclaw/private-prompts/people_daily/`; do not commit prompt contents, Notion IDs, generated issue output, or state files.

## Purpose

Treat the People's Daily issue as a daily policy-signal reading task, not a full-newspaper archive or a generic summary. The output should help the reader understand:

- the issue-level signal for the day;
- each retained article's article-level meaning;
- the original evidence behind each interpretation.

## Scope

- Keep only pages whose page label contains `要闻`.
- Drop supplement, lifestyle/service, culture, ordinary professional pages, and editorial metadata rows.
- Keep PDF links only for retained news pages.
- Create article child pages only for retained news articles.

If an old manifest has no page labels, the workflow may fall back to `analysis.detailed_max_page_no` for compatibility, but new runs should use page labels as the authority.

## Images

- Keep image metadata collected from retained article pages when available.
- Treat picture reports as articles that need image-signal reading.
- Do not infer image content if the source did not provide image/caption data.

## Date Page Shape

The date page is the day's entry point. It should stay readable and should not contain the full structured original-text analysis.

1. Page title: `YYYY年MM月DD日 人民日报深读`.
2. `今日总览`
   - Normally 3-5 connected paragraphs.
   - It should describe the issue-level signal and article/page relationships, not list every article mechanically.
   - It is generated after article-level analysis, using all retained articles and their `full_analysis` outputs.
3. `全日PDF`
   - Only retained news-page PDFs.
4. One section per retained news page.
5. Each retained article row includes title, official source link, article-level `全文深度解读`, and the child-page entry.

The date page should not contain the full `结构化原文与解析`, long original-text quotations, backend classification labels, debug headings, or generation-process notes.

## Article Child Page Shape

Each retained article gets a child page. The child page carries close-reading material so the date page does not become too long.

1. `基本信息`
2. `结构化原文与解析`
   - Original text remains visible, rendered from source paragraphs by `paragraph_indices`.
   - Analysis is organized by structure groups, not necessarily one group per natural paragraph.
   - Each group explains the meaning/function of the grouped paragraphs and remains traceable to original evidence.

The child page does not repeat the issue overview, PDF list, or other article list.

## Article Prompt Contract

Article-level analysis is conceptually two tasks but may be executed as one model call for speed.

### Source prompts

Keep these as separate source-of-truth prompt files in the local private prompt directory:

```text
~/.openclaw/private-prompts/people_daily/article_full_analysis_v1.md
~/.openclaw/private-prompts/people_daily/article_structured_groups_v1.md
```

- The first prompt defines `full_analysis`: article-level deep reading from the whole-article perspective.
- The second prompt defines `structured_groups`: structure-group analysis for the original paragraphs.
- Content-quality requirements belong in those prompts, not in script hard gates.

### Combined execution

For production speed, the script can set `analysis.combined_call=true`. In that mode it dynamically reads the two source prompt files, embeds them unchanged into a thin wrapper, and asks the model to return one merged JSON object.

There does **not** need to be a separate committed combined-prompt file. The wrapper only preserves task boundaries and defines the merged JSON contract.

Expected merged JSON:

```json
{
  "prompt_id": "people_daily_article_combined_v1_2026-05-06",
  "full_analysis": ["article-level deep-read paragraph"],
  "signal_analysis": ["optional signal analysis"],
  "policy_chain": ["optional chain/observation item"],
  "follow_up": ["optional follow-up item"],
  "structured_groups": [
    {
      "title": "structure-group title",
      "paragraph_indices": [1, 2],
      "analysis": "why these source paragraphs should be read together"
    }
  ]
}
```

Hard validation should stay structural only:

- `prompt_id` matches the configured required prompt id.
- `full_analysis` contains at least one non-empty paragraph.
- `structured_groups` covers every input paragraph exactly once.
- `paragraph_indices` are integers and refer only to input paragraph numbers.
- Each group has `title`, `paragraph_indices`, and `analysis`.

Do not turn writing style, paragraph count, group count, template wording, or specific rhetorical patterns into hard script failures. Those are prompt/self-review and human-review concerns.

### Split fallback

If `combined_call=false`, the script may run the two source prompts as separate calls and then merge their payloads. This is slower but should preserve the same output shape and page rendering contract.

## Issue Overview Prompt Contract

The issue overview is a separate prompt from article-level analysis. It should be generated after retained articles have `full_analysis` so the overview can read the day as a whole.

Expected JSON:

```json
{
  "prompt_id": "people_daily_overview_v1_2026-05-06",
  "overview": ["connected issue-level paragraph"]
}
```

Validation should reject obviously broken overview output, but the 3-5 paragraph target is a writing norm, not a doctrine. The overview should avoid backend workflow explanations and Markdown/debug headings.

## Safe Production Flow for Structure Rewrites

For large article batches or later rewrites, use a staged flow:

1. Generate temporary drafts outside the canonical output, for example `/tmp/pd_struct_1_7.md`.
2. Use clear separators such as `===== ARTICLE <n> =====` and preserve article titles.
3. Do not mutate the canonical Markdown or Notion page during draft generation.
4. Check coverage: no missing articles, no duplicates, titles match, quote counts look plausible.
5. Merge only the target `结构化原文与解析` section.
6. Do not touch approved article-level deep reads, date overview, PDF links, or unrelated sections.
7. Patch Notion only after local verification, preferably with a conservative block-level patch helper.

## Delivery

When Telegram delivery is enabled for the workflow, the user-facing completion message should contain the Notion page link only. Local Markdown, manifest paths, HTML previews, output directories, cache files, and generated state files are internal audit artifacts; do not surface them as the completion reminder.

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

If generation logic changes, confirm the timer is calling the updated installed script, not only a preview document.

## Notion Rules

- First publication may create the date page and article child pages.
- If the date page already exists and all expected article child pages are complete, the workflow skips unless `--force` is passed.
- If a previous run left a partial date page, the next run repairs missing/empty article child pages instead of creating a duplicate date page.
- A publish is marked complete only after all expected article child pages contain both `结构化原文与解析` and `全文深度解读`; otherwise the script exits non-zero so systemd can retry.
- Small later edits should be block/section patches rather than full-page rebuilds.
- Never publish secrets, local output, generated daily Markdown/HTML, cache files, or state files to Git.
- Completion notifications should point to the Notion URL, not local files.

## Quality Gate

Before publishing or pushing workflow changes:

- Python scripts compile with `python3 -m py_compile`.
- `--dry-run --no-pdf` works for a known date or manifest.
- Generated output keeps only news pages when page labels are present.
- Article payloads satisfy the structural combined JSON contract.
- No generated daily issue output, state, secrets, `/tmp` drafts, analysis caches, overview caches, checkpoints, or `__pycache__` files are staged.
