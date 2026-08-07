"""Compatibility conversions between the pilot runtime and Harness v2 contracts."""

from __future__ import annotations

from app.harness.contracts import HarnessToolSpec
from app.runtime.contracts import HarnessTool, ToolEffectLevel

_TO_V2_EFFECT = {
    ToolEffectLevel.READ: "read",
    ToolEffectLevel.WRITE: "write",
    ToolEffectLevel.DESTRUCTIVE: "delete",
}
_FROM_V2_EFFECT = {
    "read": ToolEffectLevel.READ,
    "write": ToolEffectLevel.WRITE,
    "delete": ToolEffectLevel.DESTRUCTIVE,
}


def to_v2_tool_spec(tool: HarnessTool) -> HarnessToolSpec:
    """Project an existing runtime tool into the upstream v0.3 contract."""

    return HarnessToolSpec(
        name=tool.name,
        description=tool.description,
        input_schema=tool.input_schema,
        side_effect=_TO_V2_EFFECT[tool.effect_level],
    )


def from_v2_tool_spec(tool: HarnessToolSpec) -> HarnessTool:
    """Project a v0.3 tool back without weakening its side-effect classification."""

    return HarnessTool(
        name=tool.name,
        description=tool.description,
        input_schema=dict(tool.input_schema),
        effect_level=_FROM_V2_EFFECT[tool.side_effect],
    )


__all__ = ["from_v2_tool_spec", "to_v2_tool_spec"]
