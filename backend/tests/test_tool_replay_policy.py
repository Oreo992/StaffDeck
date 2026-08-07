from app.core.tool_replay_policy import (
    TOOL_CALL_HISTORY_SLOT,
    TOOL_RESULTS_SLOT,
    ToolReplayPolicy,
)
from app.tools.tool_schema import ToolCall, ToolError, ToolResult


def test_record_result_deduplicates_history_but_preserves_all_results() -> None:
    call = ToolCall(name="orders.create", arguments={"order_id": "O-1"})
    success = ToolResult(tool_name=call.name, success=True, data={"id": "O-1"})

    first = ToolReplayPolicy.record_result({}, call, success)
    second = ToolReplayPolicy.record_result(first, call, success)

    assert second[TOOL_CALL_HISTORY_SLOT] == [
        {"tool_name": "orders.create", "arguments": {"order_id": "O-1"}}
    ]
    assert len(second[TOOL_RESULTS_SLOT]) == 2


def test_record_result_preserves_structured_error() -> None:
    call = ToolCall(name="orders.create", arguments={})
    result = ToolResult(
        tool_name=call.name,
        success=False,
        error=ToolError(code="FAILED", message="failed"),
    )

    slots = ToolReplayPolicy.record_result({}, call, result)

    assert slots[TOOL_RESULTS_SLOT][0]["error"] == {
        "code": "FAILED",
        "message": "failed",
    }


def test_configuration_preserves_precedence_and_confirmation_fallback() -> None:
    assert ToolReplayPolicy.configuration(
        {"idempotency": {"enabled": "on", "key_fields": ["order_id", ""]}},
        {},
    ) == (True, ["order_id"])
    assert ToolReplayPolicy.configuration({"requires_confirmation": True}, {}) == (
        True,
        None,
    )
    assert ToolReplayPolicy.configuration(
        {"idempotency": False, "requires_confirmation": True},
        {},
    ) == (False, None)


def test_signature_key_arguments_and_default_method_are_deterministic() -> None:
    left = ToolReplayPolicy.signature("tool", {"b": 2, "a": 1})
    right = ToolReplayPolicy.signature("tool", {"a": 1, "b": 2})

    assert left == right
    assert ToolReplayPolicy.arguments({"a": 1, "b": 2}, ["b", "missing"]) == {"b": 2}
    assert ToolReplayPolicy.default_replay_enabled("post")
    assert not ToolReplayPolicy.default_replay_enabled("get")
