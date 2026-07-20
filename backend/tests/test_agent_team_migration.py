from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agent_team_migration import (
    LEGACY_PATH_PATTERN,
    SHARED_AMAZON_RESEARCH_SKILL_ID,
    SHARED_AMAZON_RESEARCH_TOOLS,
    SOP_TEMPLATES,
    _safe_text,
    _sanitize_persona,
    compile_manifest,
)
from app.skills.skill_schema import SkillCard


REAL_SOURCE = Path("/Users/pintn/agent-team-platform/apps/cc-platform")


def test_secret_and_legacy_runtime_values_are_scrubbed() -> None:
    source = """
{"secret_key": "do-not-copy", "api_key": "also-secret"}
APP_SECRET = "hard-coded-secret"
TOKEN = os.environ.get("SERVICE_TOKEN", "hard-coded-token")
python ~/.claude/skills/example/scripts/run.py --out $TASK_DIR/out.json
mcp__studio__studio_report
"""

    result = _safe_text(source)

    assert "do-not-copy" not in result
    assert "also-secret" not in result
    assert "hard-coded-secret" not in result
    assert "hard-coded-token" not in result
    assert LEGACY_PATH_PATTERN.search(result) is None
    assert "$TASK_DIR" not in result
    assert "mcp__studio__studio_report" not in result


def test_all_curated_sops_are_valid_staffdeck_graphs() -> None:
    assert set(SOP_TEMPLATES) == {
        "orange-pm",
        "cc-amz",
        "cc-copy",
        "cc-ads",
        "cc-cs",
        "cc-art",
        "researcher",
    }
    for content in SOP_TEMPLATES.values():
        card = SkillCard.model_validate(content)
        assert card.nodes
        assert card.start_node_id == card.nodes[0].node_id
        assert card.terminal_node_ids == [card.nodes[-1].node_id]


def test_all_migrated_sops_only_block_on_minimum_identity_fields() -> None:
    expected_by_agent = {
        "orange-pm": ["goal"],
        "cc-amz": ["product_or_asin"],
        "cc-copy": ["product"],
        "cc-ads": ["asin"],
        "cc-cs": [],
        "cc-art": [],
        "researcher": ["research_question"],
    }

    for source_id, expected_fields in expected_by_agent.items():
        content = SOP_TEMPLATES[source_id]
        collect = content["nodes"][0]
        assert collect["expected_user_info"] == expected_fields
        assert content["required_info"] == sorted(expected_fields)
        assert "非关键" in collect["instruction"]
        assert "不得追问" in collect["instruction"]


def test_all_migrated_sops_default_to_first_draft_delivery() -> None:
    for content in SOP_TEMPLATES.values():
        policy = content["slot_filling_policy"]
        assert policy["ask_only_for_required_info"] is True
        assert policy["optional_info_policy"] == "assume_and_disclose"
        assert policy["direct_delivery_policy"] == "do_not_ask_optional_questions"
        assert any("先交付可用首版" in rule for rule in content["response_rules"])
        assert any("不得重复追问" in rule for rule in content["response_rules"])


def test_migrated_persona_requires_real_html_link_and_non_blocking_defaults() -> None:
    result = _sanitize_persona("原始员工说明", "cc-ads")

    assert "HTML" in result
    assert "公网链接" in result
    assert "不得声称" in result
    assert "非关键" in result
    assert "先交付可用首版" in result
    assert "不可逆" in result


@pytest.mark.skipif(not REAL_SOURCE.exists(), reason="Agent Team source checkout is not available")
def test_every_migrated_agent_can_route_to_shared_amazon_research() -> None:
    manifest = compile_manifest(REAL_SOURCE, "demo-ecom")

    for agent in manifest["agents"]:
        assert SHARED_AMAZON_RESEARCH_SKILL_ID in agent["sop_skill_ids"]
        assert set(SHARED_AMAZON_RESEARCH_TOOLS).issubset(agent["tool_names"])
        assert len(agent["sop_skill_ids"]) == (1 if agent["source_id"] == "cc-amz" else 2)


@pytest.mark.skipif(not REAL_SOURCE.exists(), reason="Agent Team source checkout is not available")
def test_real_demo_ecom_manifest_has_expected_inventory_and_is_deterministic() -> None:
    first = compile_manifest(REAL_SOURCE, "demo-ecom")
    second = compile_manifest(REAL_SOURCE, "demo-ecom")

    assert first["counts"] == {
        "agents": 7,
        "general_skills": 29,
        "published_general_skills": 16,
        "sops": 7,
        "mcp_servers": 2,
        "mcp_tools": 35,
    }
    assert first["manifest_sha256"] == second["manifest_sha256"]
    assert {agent["source_id"] for agent in first["agents"]} == set(SOP_TEMPLATES)

    serialized = json.dumps(first, ensure_ascii=False)
    for config_path in (
        REAL_SOURCE / "base/.claude/skills/sellersprite/config.json",
        REAL_SOURCE / "base/.claude/skills/sorftime/config.json",
    ):
        source_config = json.loads(config_path.read_text(encoding="utf-8"))
        for key, value in source_config.items():
            if isinstance(value, str) and any(
                marker in key.lower() for marker in ("key", "secret", "token", "password")
            ):
                assert value not in serialized
    for marker in (
        "/opt/cc-base",
        "$HOME/.claude",
        "~/.claude",
        "$TASK_DIR",
        "mcp__studio__studio_report",
    ):
        assert marker not in serialized
