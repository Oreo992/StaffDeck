from __future__ import annotations

from dataclasses import dataclass

from app.db.models import ChatSession, UIConfig


@dataclass(frozen=True)
class HarnessV2RouteDecision:
    selected: bool
    reason: str


def select_harness_v2_canary(
    ui_config: UIConfig | None,
    chat_session: ChatSession,
    *,
    agent_id: str | None,
    is_new_session: bool,
) -> HarnessV2RouteDecision:
    """Select only allowlisted new Legacy sessions; never alter persisted mode."""

    if ui_config is None or not ui_config.harness_v2_enabled:
        return HarnessV2RouteDecision(False, "harness_v2_disabled")
    if chat_session.runtime_mode != "legacy":
        return HarnessV2RouteDecision(False, "runtime_mode_not_legacy")
    if not is_new_session:
        return HarnessV2RouteDecision(False, "existing_session_compatibility")
    normalized_agent_id = str(agent_id or chat_session.agent_id or "").strip()
    allowlist = {
        str(item).strip()
        for item in (ui_config.harness_v2_agent_allowlist_json or [])
        if str(item).strip()
    }
    if not normalized_agent_id or normalized_agent_id not in allowlist:
        return HarnessV2RouteDecision(False, "agent_not_allowlisted")
    return HarnessV2RouteDecision(True, "allowlisted_new_legacy_session")


__all__ = ["HarnessV2RouteDecision", "select_harness_v2_canary"]
