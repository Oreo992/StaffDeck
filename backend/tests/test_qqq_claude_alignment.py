from __future__ import annotations

from app.qqq_claude_alignment import (
    QQQ_PERSONA_PROMPT,
    TARGET_SOP_ID,
    TARGET_TOOL_NAMES,
    _metadata,
    _target_sop_content,
    _ui_config_payload,
)


def test_qqq_persona_is_clean_and_selection_first() -> None:
    assert "Amazon 运营与跨境选品 Agent" in QQQ_PERSONA_PROMPT
    assert "PPC 广告分析是次级能力" in QQQ_PERSONA_PROMPT
    assert "L1" in QQQ_PERSONA_PROMPT
    assert "EvidencePack" in QQQ_PERSONA_PROMPT
    assert "StaffDeck" not in QQQ_PERSONA_PROMPT
    assert "小卓" not in QQQ_PERSONA_PROMPT


def test_qqq_reuses_full_cc_amz_tool_profile_and_adaptive_sop() -> None:
    content = _target_sop_content()
    node_ids = {node["node_id"] for node in content["nodes"]}
    serialized_actions = {
        action
        for node in content["nodes"]
        for action in node.get("allowed_actions", [])
        if action.startswith("call_tool:")
    }

    assert len(TARGET_TOOL_NAMES) == 35
    assert {"research_l1", "research_l2", "research_l3"}.issubset(node_ids)
    assert {f"call_tool:{name}" for name in TARGET_TOOL_NAMES} == serialized_actions


def test_alignment_preserves_ownership_and_enables_research_runtime() -> None:
    metadata = _metadata({"owner_user_id": "admin", "custom": "keep"})
    ui_payload = _ui_config_payload(
        {
            "tenant_id": "tenant_demo",
            "claude_runtime_enabled": True,
            "claude_model_config_id": "model_claude",
            "claude_skill_allowlist": ["ads_sop"],
            "agent_loop_max_actions": 6,
        }
    )

    assert metadata["owner_user_id"] == "admin"
    assert metadata["custom"] == "keep"
    assert metadata["default_runtime_mode"] == "claude_supervised"
    assert ui_payload["agent_loop_max_actions"] == 8
    assert ui_payload["claude_skill_allowlist"] == ["ads_sop", TARGET_SOP_ID]
