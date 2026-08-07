from __future__ import annotations

from app.core.runtime_tool_manifest import capability_manifest_from_runtime_tools
from app.runtime.contracts import HarnessTool, ToolEffectLevel


def test_runtime_tool_manifest_preserves_effects_and_classifies_builtins() -> None:
    manifest = capability_manifest_from_runtime_tools(
        [
            HarnessTool(name="catalog.lookup", effect_level=ToolEffectLevel.READ),
            HarnessTool(name="knowledge_search", effect_level=ToolEffectLevel.READ),
            HarnessTool(name="send_file", effect_level=ToolEffectLevel.WRITE),
            HarnessTool(name="load_skill", effect_level=ToolEffectLevel.READ),
        ],
        namespace="claude-conversation",
    )

    by_name = {item.name: item for item in manifest.available}
    assert by_name["catalog.lookup"].kind == "tool"
    assert by_name["knowledge_search"].kind == "knowledge"
    assert by_name["send_file"].kind == "file"
    assert by_name["load_skill"].kind == "general_skill"
    assert by_name["send_file"].effect_level == "write"
    assert manifest.snapshot_revision.startswith("sha256:")


def test_runtime_tool_manifest_snapshot_is_deterministic_and_deduplicated() -> None:
    tool = HarnessTool(name="catalog.lookup", description="lookup")

    first = capability_manifest_from_runtime_tools([tool, tool], namespace="test")
    second = capability_manifest_from_runtime_tools([tool], namespace="test")

    assert len(first.available) == 1
    assert first.snapshot_revision == second.snapshot_revision
