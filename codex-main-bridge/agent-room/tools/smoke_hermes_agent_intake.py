#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
ARTIFACT = ROOT / "agent-room" / "artifacts" / "hermes-agent-framework-intake-20260529"
INTAKE = ARTIFACT / "intake.json"
README = ARTIFACT / "README.md"
FIXTURE_MAP = ARTIFACT / "openclaw-to-hermes-fixture-map.md"
FALLBACK_UPTAKE = ARTIFACT / "claude-fallback-uptake-decision-20260529.md"


def check(name: str, condition: bool, failures: list[str]) -> None:
    if not condition:
        failures.append(name)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    failures: list[str] = []

    check("intake json exists", INTAKE.exists(), failures)
    check("readme exists", README.exists(), failures)
    check("fixture map exists", FIXTURE_MAP.exists(), failures)
    check("fallback uptake decision exists", FALLBACK_UPTAKE.exists(), failures)
    if failures:
        print(json.dumps({"ok": False, "failures": failures}, indent=2))
        return 1

    intake = read_json(INTAKE)
    readme = README.read_text(encoding="utf-8")
    fixture_map = FIXTURE_MAP.read_text(encoding="utf-8")
    fallback_uptake = FALLBACK_UPTAKE.read_text(encoding="utf-8")
    sources = intake.get("source_urls") if isinstance(intake.get("source_urls"), list) else []
    hypotheses = intake.get("openclaw_fit_hypotheses") if isinstance(intake.get("openclaw_fit_hypotheses"), list) else []
    non_goals = intake.get("non_goals") if isinstance(intake.get("non_goals"), list) else []
    unknowns = intake.get("unknowns") if isinstance(intake.get("unknowns"), list) else []
    next_items = intake.get("recommended_next_work_items") if isinstance(intake.get("recommended_next_work_items"), list) else []
    default_scope = intake.get("default_scope") if isinstance(intake.get("default_scope"), dict) else {}
    peer_uptake = intake.get("peer_uptake") if isinstance(intake.get("peer_uptake"), dict) else {}

    axes = {str(item.get("axis") or "") for item in hypotheses if isinstance(item, dict)}
    owners = {str(item.get("owner") or "") for item in next_items if isinstance(item, dict)}
    non_goals_text = "\n".join(str(item) for item in non_goals).lower()
    fixture_map_lower = fixture_map.lower()
    fallback_uptake_lower = fallback_uptake.lower()

    check("schema is hermes intake v0", intake.get("schema") == "openclaw.agent_room.hermes_agent_framework_intake.v0", failures)
    check("topic is hermes-agent-framework", intake.get("topic") == "hermes-agent-framework", failures)
    check("peer claim is captured", "external fallback" in str(peer_uptake.get("peer_claim") or "").lower(), failures)
    check("default candidate is nous hermes agent", default_scope.get("candidate") == "NousResearch/hermes-agent", failures)
    check("candidate ambiguity is explicit", bool(default_scope.get("ambiguity")), failures)
    check("official github source recorded", "https://github.com/NousResearch/hermes-agent" in sources, failures)
    check("alternate hermes framework source recorded", "https://hermesforge.dev/framework" in sources, failures)
    check("four fit axes present", {"runtime_profile_isolation", "persistent_memory_and_skills", "messaging_gateway", "migration_path"}.issubset(axes), failures)
    check("no live install goal", "do not install hermes" in non_goals_text, failures)
    check("no secrets migration goal", "do not migrate secrets" in non_goals_text, failures)
    check("existing workflows protected", "translation agent" in non_goals_text and "market workflows" in non_goals_text, failures)
    check("unknowns remain explicit", len(unknowns) >= 3, failures)
    check("tri-agent followup owners present", {"codex", "claude-code", "openclaw-main"}.issubset(owners), failures)
    check("readme carries sources", "https://github.com/NousResearch/hermes-agent" in readme and "https://hermesforge.dev/framework" in readme, failures)
    check("readme states no install boundary", "no install" in readme.lower() and "no migration" in readme.lower(), failures)
    check("fixture map carries candidate", "Default comparison target: `NousResearch/hermes-agent`" in fixture_map, failures)
    check("fixture map references task envelope", "`codex-main-bridge/agent-room/tools/telegram_agent_reply.py`" in fixture_map, failures)
    check("fixture map references runner", "`codex-main-bridge/agent-room/tools/agent_room_resident_bridge.py`" in fixture_map, failures)
    check("fixture map references ledger", "`codex-main-bridge/agent-room/tools/collaboration_ledger.py`" in fixture_map, failures)
    check("fixture map references telegram projection", "`codex-main-bridge/agent-room/tools/telegram_agent_bridge.py`" in fixture_map, failures)
    check("fixture map protects translation agent", "`openclaw-telegram-wsl-setup/tools/translation-agent/translation_acceptance_gate.py`" in fixture_map, failures)
    check("fixture map protects market workflow", "`modules/openclaw-market-immersion/scripts/market_immersion.py`" in fixture_map, failures)
    check("fixture map protects dispatcher", "`notification-dispatcher/notification_dispatcher_v0.py`" in fixture_map, failures)
    check("fixture map keeps no-send boundary", "do not send external messages from a fixture run" in fixture_map_lower, failures)
    check("fixture map rejects secrets migration", "reject if: the profile allows send, secret read" in fixture_map_lower, failures)
    check("fixture map has four dry-run probes", all(probe in fixture_map for probe in (
        "`runtime_profile_isolation`",
        "`skill_memory_lifecycle`",
        "`gateway_projection`",
        "`migration_preview`",
    )), failures)
    check("fixture map defines peer review target", "identify one fixture probe that should be promoted into a smoke test" in fixture_map, failures)
    check("readme links fallback uptake decision", "claude-fallback-uptake-decision-20260529.md" in readme, failures)
    check("fallback blocker is accepted", "accept the fallback limitation as a material blocker" in fallback_uptake_lower, failures)
    check("fallback keeps degraded quorum moving", "degraded-quorum mode" in fallback_uptake_lower, failures)
    check("fallback source check records official docs", all(url in fallback_uptake for url in (
        "https://github.com/NousResearch/hermes-agent",
        "https://hermes-agent.nousresearch.com/docs/",
        "https://hermes-agent.nousresearch.com/docs/guides/migrate-from-openclaw/",
    )), failures)
    check("fallback protects no install secrets send", all(term in fallback_uptake_lower for term in (
        "no install",
        "no secret",
        "no telegram",
        "no live migration",
    )), failures)
    check("fallback defines tool-enabled review target", "tool-enabled claude code review target" in fallback_uptake_lower, failures)
    check("fallback requires smoke promotion option", "promote one fixture probe into a smoke test" in fallback_uptake_lower, failures)

    result = {
        "ok": not failures,
        "artifact": str(ARTIFACT),
        "hypothesis_axes": sorted(axes),
        "next_item_owners": sorted(owners),
        "failures": failures,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
