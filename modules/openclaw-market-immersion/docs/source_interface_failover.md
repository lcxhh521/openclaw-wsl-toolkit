# Source Interface Failover Design

Purpose: maintain verified backup interfaces for daily market briefs. This is not local snapshot retention.

## Two separate workflows

### 1. Low-frequency source discovery / verification

This is routine maintenance, not a publish-time action.

- Timer: `openclaw-source-interface-verification.timer`
- Frequency: twice monthly, on the 1st and 16th at 07:05 CST (roughly every 2–3 weeks)
- Output: verification reports under `market-immersion/source-interface-verification/`
- It does not publish reports and does not replace the active source.

### 2. Failure-triggered replacement

This happens only when a primary source fails during an actual daily-brief collection.

Policy:
- Try primary first.
- If primary fails, try verified backup candidates one by one.
- If a backup works, use it temporarily for that run and record the failover.
- On the next run, try the primary again first.
- If the primary has recovered, immediately fail back to the primary.
- If all verified backups fail, then start a new discovery/search process for replacement interfaces.
- Do not publish a degraded report automatically. If all recovery/failover paths fail, stop and ask Alex explicitly before any degraded publication.

## Core objects

1. Source registry: `config/source_registry.json`
   - public repository contains only a schema/example registry
   - operational interface URLs, request parameters, signatures and field mappings must stay in the installed local workspace
   - verification policy

2. Verifier: `scripts/verify_source_interfaces.py`
   - fetches the official page
   - scans official page JS/network references
   - fetches candidate interfaces
   - extracts sample items from candidate interfaces
   - compares samples against official-page presentation or official-page-discovered network interfaces
   - marks candidates `backup_ready` only when the endpoint works and consistency passes

3. Verification report
   - latest pointer: `market-immersion/source-interface-verification/latest.json`
   - contains per-source endpoint status, official fetch status, match ratio, discovered URL hints, and backup-ready candidates

## Verification standard

- A candidate interface is not a backup merely because it returns JSON.
- It must be verified against the official page or an official page-discovered interface.
- If the official page is client-rendered and the verifier cannot prove candidate consistency, status remains `needs_review`.
- Daily brief generation may use a backup only if a recent verification report marks it `backup_ready`.
