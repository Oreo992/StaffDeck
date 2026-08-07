from __future__ import annotations

from app.core.harness_sop_bridge import (
    evidence_ledger_from_task_result,
    structured_output_from_task_result,
)
from app.harness.task_request import TaskExecutionResult
from app.runtime.contracts import ExecutionSegment, SopAuditOutcome
from app.runtime.sop_supervisor import SopSupervisor


def _segment() -> ExecutionSegment:
    return ExecutionSegment(
        skill_id="research",
        start_step_id="research",
        node_ids=["research"],
        nodes=[
            {
                "node_id": "research",
                "expected_user_info": ["asin"],
                "allowed_actions": ["call_tool:catalog.lookup"],
            }
        ],
        allowed_tool_names=["catalog.lookup"],
        boundary="checkpoint",
    )


def test_bridge_builds_supervisor_input_from_objective_capability_results() -> None:
    result = TaskExecutionResult(
        task_frame_id="task-1",
        status="completed",
        reply_fragment="完成",
        slot_updates={"asin": "A1"},
        completed_step_ids=["research"],
        next_step_id="reply",
        citations=[{"document_id": "doc-1", "chunk_id": "chunk-1"}],
        evidence_results=[{"evidence_refs": ["catalog.lookup:1"]}],
        capability_results=[
            {
                "tool_name": "catalog.lookup",
                "arguments": {"asin": "A1"},
                "success": True,
                "data": {"price": 19.9},
            },
            {
                "tool_name": "send_file",
                "arguments": {"path": "report.html"},
                "success": False,
                "error": {"code": "APPROVAL_REQUIRED"},
            },
        ],
        artifacts=[{"artifact_id": "artifact-1"}],
    )

    output = structured_output_from_task_result(result)
    ledger = evidence_ledger_from_task_result(
        result,
        approved_capability_names={"catalog.lookup"},
    )

    assert output.reply == "完成"
    assert output.completed_step_ids == ["research"]
    assert output.evidence_refs == ["catalog.lookup:1"]
    assert output.next_step_id == "reply"
    assert ledger.has_successful_tool("catalog.lookup") is True
    assert ledger.tool_results[0]["arguments"] == {"asin": "A1"}
    assert ledger.approvals == {"catalog.lookup"}
    assert ledger.knowledge_refs == [{"document_id": "doc-1", "chunk_id": "chunk-1"}]
    assert ledger.artifacts == [{"artifact_id": "artifact-1"}]


def test_bridge_marks_awaiting_user_without_treating_model_claim_as_evidence() -> None:
    result = TaskExecutionResult(
        task_frame_id="task-1",
        status="awaiting_user",
        reply_fragment="请确认",
        completed_step_ids=["claimed_without_evidence"],
    )

    output = structured_output_from_task_result(result)
    ledger = evidence_ledger_from_task_result(result)

    assert output.needs_user_input is True
    assert output.user_question == "请确认"
    assert ledger.tool_results == []
    assert ledger.knowledge_refs == []


def test_supervisor_rejects_completed_claim_when_gateway_evidence_is_missing() -> None:
    result = TaskExecutionResult(
        task_frame_id="task-1",
        status="completed",
        slot_updates={"asin": "A1"},
        completed_step_ids=["research"],
        reply_fragment="我已经查询完成",
    )

    audit = SopSupervisor().audit(
        _segment(),
        {"required_info": ["asin"], "nodes": _segment().nodes},
        {},
        structured_output_from_task_result(result),
        evidence_ledger_from_task_result(result),
        attempt=0,
        max_repairs=2,
    )

    assert audit.outcome == SopAuditOutcome.REPAIR
    assert audit.missing_evidence == ["tool:catalog.lookup"]
