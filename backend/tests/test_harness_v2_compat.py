from __future__ import annotations

from pathlib import Path

import pytest

from app.harness.contracts import HarnessLimits, HarnessToolSpec
from app.harness.errors import HarnessExecutionError
from app.harness.execution_context import SANDBOX_WORKSPACE, SandboxExecutionContext
from app.runtime.contracts import HarnessTool, ToolEffectLevel
from app.runtime.v2_compat import from_v2_tool_spec, to_v2_tool_spec


def test_v2_contracts_validate_limits_without_database_dependencies() -> None:
    limits = HarnessLimits(max_file_bytes=128, max_workspace_bytes=256)

    assert limits.max_file_bytes == 128
    with pytest.raises(ValueError, match="positive integer"):
        HarnessLimits(max_entries=0)
    with pytest.raises(ValueError, match="cannot exceed"):
        HarnessLimits(max_file_bytes=257, max_workspace_bytes=256)


@pytest.mark.parametrize(
    ("runtime_effect", "v2_effect"),
    [
        (ToolEffectLevel.READ, "read"),
        (ToolEffectLevel.WRITE, "write"),
        (ToolEffectLevel.DESTRUCTIVE, "delete"),
    ],
)
def test_runtime_tool_contract_round_trips_without_effect_downgrade(
    runtime_effect: ToolEffectLevel,
    v2_effect: str,
) -> None:
    runtime_tool = HarnessTool(
        name="inventory.lookup",
        description="查询库存",
        input_schema={"type": "object", "properties": {"asin": {"type": "string"}}},
        effect_level=runtime_effect,
    )

    v2_tool = to_v2_tool_spec(runtime_tool)
    restored = from_v2_tool_spec(v2_tool)

    assert isinstance(v2_tool, HarnessToolSpec)
    assert v2_tool.side_effect == v2_effect
    assert restored == runtime_tool


def test_sandbox_execution_context_maps_only_workspace_paths(tmp_path: Path) -> None:
    workspace = tmp_path / "task"
    workspace.mkdir()
    nested = workspace / "reports"
    nested.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    context = SandboxExecutionContext.create(workspace, "bubblewrap")

    assert context.process_path(nested / "report.html") == (
        f"{SANDBOX_WORKSPACE}/reports/report.html"
    )
    assert context.process_path(outside / "secret.txt") == str(outside / "secret.txt")
    with pytest.raises(HarnessExecutionError) as exc_info:
        context.host_cwd(outside)
    assert exc_info.value.error.code == "INVALID_WORKSPACE_CWD"
