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
    _select_managed_agent,
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


def test_duplicate_migration_source_prefers_the_record_with_the_desired_name() -> None:
    candidates = [
        {"id": "renamed", "name": "QQQ"},
        {"id": "canonical", "name": "小辰"},
    ]

    assert _select_managed_agent(candidates, "小辰")["id"] == "canonical"
    assert _select_managed_agent(candidates, "不存在")["id"] == "renamed"


def test_all_curated_sops_are_valid_staffdeck_graphs() -> None:
    assert set(SOP_TEMPLATES) == {
        "orange-pm",
        "cc-amz",
        "cc-copy",
        "cc-ads",
        "cc-cs",
        "cc-art",
        "researcher",
        "crossborder-trend-researcher",
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
        "crossborder-trend-researcher": ["parent_category"],
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


def test_amazon_research_lets_claude_choose_dimensions_inside_one_supervised_step() -> None:
    content = SOP_TEMPLATES["cc-amz"]
    nodes = {node["node_id"]: node for node in content["nodes"]}
    round_tripped = SkillCard.model_validate(content).model_dump(mode="json")
    round_trip_nodes = {node["node_id"]: node for node in round_tripped["nodes"]}

    assert [node["node_id"] for node in content["nodes"]] == [
        "collect_scope",
        "adaptive_research",
        "evidence_gate",
        "reply",
    ]
    assert "research_depth" not in content["slot_filling_policy"]["optional_defaults"]
    assert all("predicate_json" not in edge for edge in content["edges"])
    assert nodes["adaptive_research"]["metadata"]["evidence_policy"] == {
        "min_successful_tools": 1
    }
    assert round_trip_nodes["adaptive_research"]["metadata"]["evidence_policy"] == {
        "min_successful_tools": 1
    }
    assert "自主判断任务规模" in nodes["adaptive_research"]["instruction"]
    assert "自主选择" in nodes["adaptive_research"]["instruction"]
    serialized = json.dumps(content, ensure_ascii=False)
    assert "research_l1" not in serialized
    assert "research_l2" not in serialized
    assert "research_l3" not in serialized
    for required_tool in (
        "at_sellersprite.keepa_info",
        "at_sorftime.category_report",
        "at_sorftime.ali1688_product_search",
        "at_sorftime.tiktok_product_trend",
        "at_sorftime.walmart_product_trend_by_product_id",
    ):
        assert required_tool in serialized


def test_crossborder_trend_research_sop_covers_the_full_evidence_workflow() -> None:
    content = SOP_TEMPLATES["crossborder-trend-researcher"]
    node_ids = [node["node_id"] for node in content["nodes"]]

    assert node_ids == [
        "collect_scope",
        "channel_roadmap",
        "candidate_pool",
        "keyword_map",
        "collect_evidence",
        "long_term_trend",
        "supply_demand",
        "cross_channel",
        "score_decide",
        "deliver_report",
    ]
    assert content["slot_filling_policy"]["optional_defaults"]["target_market"] == "Amazon 美国站"
    serialized = json.dumps(content, ensure_ascii=False)
    for required_phrase in ("10—30", "双语", "完整历史年份", "供需", "跨渠道", "HTML", "XLSX", "公网链接"):
        assert required_phrase in serialized


@pytest.mark.skipif(not REAL_SOURCE.exists(), reason="Agent Team source checkout is not available")
def test_crossborder_trend_researcher_has_dedicated_skill_and_read_only_data_tools() -> None:
    manifest = compile_manifest(REAL_SOURCE, "demo-ecom")
    agent = next(
        item for item in manifest["agents"] if item["source_id"] == "crossborder-trend-researcher"
    )
    skill = next(
        item
        for item in manifest["general_skills"]
        if item["slug"] == "agent-team-discover-crossborder-market-trends"
    )

    assert skill["status"] == "published"
    assert "agent-team-discover-crossborder-market-trends" in agent["general_skill_slugs"]
    assert SOP_TEMPLATES["crossborder-trend-researcher"]["skill_id"] in agent["sop_skill_ids"]
    assert {
        "at_sellersprite.google_trend",
        "at_sellersprite.keyword_research",
        "at_sellersprite.market_research",
        "at_sorftime.category_report",
        "at_sorftime.keyword_trend",
        "at_sorftime.product_search",
        "at_sorftime.tiktok_product_trend",
        "at_sorftime.walmart_product_trend_by_product_id",
        "at_sorftime.ali1688_product_search",
    }.issubset(agent["tool_names"])


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
        "agents": 8,
        "general_skills": 30,
        "published_general_skills": 17,
        "sops": 8,
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
