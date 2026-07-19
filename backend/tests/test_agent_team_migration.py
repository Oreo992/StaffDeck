from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agent_team_migration import (
    LEGACY_PATH_PATTERN,
    SOP_TEMPLATES,
    _safe_text,
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
