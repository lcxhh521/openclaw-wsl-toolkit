# External reference scan guidance

Updated: 2026-05-21 13:31 CST

## Intent

When OpenClaw Agent Room / runtime work hits a non-obvious design or implementation problem, agents may look at public code-hosting projects for usable ideas.

GitHub is only one source. Public GitLab, Codeberg, SourceHut, Bitbucket, Gitee, Forgejo/Gitea instances, project docs, and package registries may also be used when they are relevant and accessible.

This is not a rule to browse for every small bug. It is a first-principles fallback: if local evidence is insufficient, similar open-source systems may already contain patterns for queueing, leases, projection, agent orchestration, UI suppression, resumable tasks, gateway backpressure, or evaluation.

## Where to scan

Prefer public, source-adjacent material:

- GitHub repositories, issues, discussions, PRs, and docs;
- GitLab public projects, issues, merge requests, and docs;
- Codeberg / Forgejo / Gitea public repos;
- SourceHut public repos and mailing-list patches;
- Bitbucket public repos;
- Gitee or other regional public mirrors when they are authoritative enough for the topic;
- language/package registries that link back to source, e.g. npm, PyPI, crates.io, Go package docs;
- official project docs when they describe the implementation pattern clearly.

Use the source closest to the implementation. Prefer repo code/docs over blog summaries when possible.

## When to scan

Use a short read-only reference scan when:

- the issue is a design/runtime pattern, not just a typo;
- local code inspection does not make the tradeoff clear;
- we are deciding between multiple architectures;
- there is likely prior art in agent frameworks, CLIs, workflow engines, message relays, or task schedulers;
- the scan can produce concrete implementation ideas within a bounded time box.

Skip it when:

- the local root cause is already clear and the patch is obvious;
- scanning would delay an urgent low-risk fix;
- the only available references are stale, irrelevant, or vendor-marketing prose;
- network/tool policy does not allow it.

## Boundaries

- Public repos/docs only.
- No private repositories.
- No secrets, tokens, OAuth state, or credential files.
- No copying code blindly; translate patterns to OpenClaw constraints.
- Preserve source URLs/paths and the reason a pattern is or is not applicable.
- Keep research artifacts local unless Alex asks for details.
- Prefer a concise evidence map over a long literature review.

## Output shape

A useful scan should produce:

1. problem being compared;
2. 3-6 candidate projects/files/docs;
3. specific pattern observed;
4. how it maps to OpenClaw;
5. what not to borrow;
6. proposed next patch or experiment.

## Current application

For Agent Room visible-output issues, useful reference areas include:

- multi-agent frameworks' supervisor/message routing patterns;
- workflow engines' task event vs user notification split;
- chatops bots' summarization and notification throttling;
- coding agents' run ledger / artifact / acceptance gate separation.
