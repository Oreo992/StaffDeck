from __future__ import annotations

import json
from pathlib import Path

from app.qqq_claude_alignment import (
    QQQ_PERSONA_PROMPT,
    TARGET_GENERAL_SKILLS,
    TARGET_SOP_ID,
    TARGET_TOOL_NAMES,
    _metadata,
    _target_sop_content,
    _ui_config_payload,
    apply_alignment,
)


class _FakeApi:
    tenant_id = "tenant_demo"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.payloads: list[tuple[str, str, object]] = []
        self.general_skills: list[dict[str, object]] = []
        self.tools = [
            {
                "id": f"tool_{index}",
                "tenant_id": self.tenant_id,
                "name": name,
                "url": f"mcp://data/{index}",
                "effect_level": "read",
                "enabled": True,
            }
            for index, name in enumerate(TARGET_TOOL_NAMES)
        ]

    def query_path(self, path: str, **params: str) -> str:
        return f"{path}?tenant_id={params['tenant_id']}"

    def request(self, method: str, path: str, payload=None):  # noqa: ANN001, ANN201
        self.calls.append((method, path))
        self.payloads.append((method, path, payload))
        if method == "GET" and path.startswith("/api/enterprise/agents?"):
            return [{"id": "agent_qqq", "name": "QQQ · Claude", "metadata": {}}]
        if method == "GET" and path.startswith("/api/enterprise/tools?"):
            return self.tools
        if method == "GET" and path.startswith("/api/enterprise/skills?"):
            return [{"id": "skill_research", "skill_id": TARGET_SOP_ID}]
        if method == "GET" and path.startswith("/api/enterprise/general-skills?"):
            return self.general_skills
        if method == "GET" and path.startswith("/api/enterprise/agents/agent_qqq/resources?"):
            return [
                {"resource_type": "skill", "resource_id": "skill_research", "status": "active"}
            ]
        if method == "GET" and path.startswith("/api/enterprise/ui-config?"):
            return {
                "tenant_id": self.tenant_id,
                "claude_runtime_enabled": True,
                "claude_model_config_id": "model_claude",
                "claude_skill_allowlist": [],
            }
        if method == "PUT" and path == "/api/enterprise/agents/agent_qqq":
            return {"id": "agent_qqq", **payload}
        if method == "PUT" and path == f"/api/enterprise/skills/{TARGET_SOP_ID}":
            return {"id": "skill_research", "skill_id": TARGET_SOP_ID, **payload}
        if method == "POST" and path == "/api/enterprise/general-skills/import":
            slug = payload["slug"]
            existing = next(
                (row for row in self.general_skills if row["slug"] == slug), None
            )
            row = {
                "id": existing["id"] if existing else f"genskill_{slug}",
                "skill_markdown": payload["markdown"],
                **payload,
            }
            if existing:
                existing.update(row)
            else:
                self.general_skills.append(row)
            return row
        if method == "POST" and path.endswith("sync-from-overall?tenant_id=tenant_demo"):
            return {"status": "synced", "head_version": "1.1.0"}
        if method == "PUT":
            return payload
        raise AssertionError(f"Unexpected API call: {method} {path}")


def test_qqq_persona_is_clean_and_selection_first() -> None:
    assert "Amazon 运营与跨境选品 Agent" in QQQ_PERSONA_PROMPT
    assert "PPC 广告分析是次级能力" in QQQ_PERSONA_PROMPT
    assert "L1" in QQQ_PERSONA_PROMPT
    assert "内部工作量参考" in QQQ_PERSONA_PROMPT
    assert "不要求用户选择" in QQQ_PERSONA_PROMPT
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
    assert "adaptive_research" in node_ids
    assert {"research_l1", "research_l2", "research_l3"}.isdisjoint(node_ids)
    assert "research_depth" not in content["slot_filling_policy"]["optional_defaults"]
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


def test_alignment_syncs_the_agent_private_sop_branch() -> None:
    api = _FakeApi()

    result = apply_alignment(api)  # type: ignore[arg-type]

    expected_path = (
        f"/api/enterprise/agents/agent_qqq/skills/{TARGET_SOP_ID}/"
        "sync-from-overall?tenant_id=tenant_demo"
    )
    assert ("POST", expected_path) in api.calls
    assert result["branch_head_version"] == "1.1.0"


def test_alignment_migrates_and_binds_cc_amz_general_skills_idempotently() -> None:
    api = _FakeApi()

    first = apply_alignment(api)  # type: ignore[arg-type]
    second = apply_alignment(api)  # type: ignore[arg-type]

    expected_slugs = {item["slug"] for item in TARGET_GENERAL_SKILLS}
    assert {row["slug"] for row in api.general_skills} == expected_slugs
    assert len(api.general_skills) == 3
    assert first["bound_general_skills"] == 3
    assert second["bound_general_skills"] == 3
    update_payloads = [
        payload
        for method, path, payload in api.payloads
        if method == "PUT" and path == "/api/enterprise/agents/agent_qqq/resources"
    ]
    assert update_payloads
    bound_slugs = {
        row["resource_id"].removeprefix("genskill_")
        for row in update_payloads[-1]["resources"]
        if row["resource_type"] == "general_skill"
    }
    assert bound_slugs == expected_slugs
    update_imports = [
        payload
        for method, path, payload in api.payloads
        if method == "POST"
        and path == "/api/enterprise/general-skills/import"
        and payload.get("original_slug")
    ]
    assert {payload["original_slug"] for payload in update_imports} == expected_slugs


def test_migrated_skill_markdown_is_runtime_clean_and_progressively_loaded() -> None:
    by_slug = {item["slug"]: item for item in TARGET_GENERAL_SKILLS}

    assert set(by_slug) == {"sellersprite", "sorftime", "deep-analysis"}
    for item in by_slug.values():
        markdown = item["markdown"]
        assert "~/.claude" not in markdown
        assert "/opt/cc-base" not in markdown
        assert "Bash" not in markdown
        assert "让用户选择 L1" not in markdown
        assert "不得编造" in markdown
    assert "at_sellersprite.asin_detail" in by_slug["sellersprite"]["markdown"]
    assert "at_sorftime.product_search" in by_slug["sorftime"]["markdown"]
    assert "完整" in by_slug["deep-analysis"]["description"]


def test_skill_eval_set_covers_positive_negative_and_composed_routing() -> None:
    fixture = Path(__file__).with_name("fixtures") / "qqq_claude_skill_evals.json"
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    expected = [set(item["expected_skill_loads"]) for item in payload["evals"]]

    assert len(expected) >= 8
    assert set() in expected
    assert {"sellersprite"} in expected
    assert {"sorftime"} in expected
    assert {"sellersprite", "sorftime"} in expected
    assert {"deep-analysis", "sellersprite"} in expected
