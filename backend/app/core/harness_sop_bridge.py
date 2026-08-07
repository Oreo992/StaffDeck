from __future__ import annotations

from typing import Any

from app.harness.task_request import TaskExecutionResult
from app.runtime.contracts import HarnessStructuredOutput
from app.runtime.sop_supervisor import EvidenceLedger


def structured_output_from_task_result(
    result: TaskExecutionResult,
) -> HarnessStructuredOutput:
    """Convert candidate model output without granting it Graph authority."""

    evidence_refs: list[str] = []
    for evidence in result.evidence_results:
        if not isinstance(evidence, dict):
            continue
        refs = evidence.get("evidence_refs")
        if not isinstance(refs, list):
            continue
        for ref in refs:
            normalized = str(ref or "").strip()
            if normalized and normalized not in evidence_refs:
                evidence_refs.append(normalized)
    needs_user_input = result.status == "awaiting_user"
    return HarnessStructuredOutput(
        reply=result.reply_fragment,
        slot_updates=dict(result.slot_updates),
        completed_step_ids=list(result.completed_step_ids),
        evidence_refs=evidence_refs,
        needs_user_input=needs_user_input,
        user_question=result.reply_fragment if needs_user_input else None,
        next_step_id=result.next_step_id,
    )


def evidence_ledger_from_task_result(
    result: TaskExecutionResult,
    *,
    approved_capability_names: set[str] | None = None,
) -> EvidenceLedger:
    """Build audit evidence only from gateway results and persisted artifacts."""

    ledger = EvidenceLedger(
        approvals=set(approved_capability_names or set()),
        knowledge_refs=[dict(item) for item in result.citations if isinstance(item, dict)],
        artifacts=[dict(item) for item in result.artifacts if isinstance(item, dict)],
    )
    for item in result.capability_results:
        if not isinstance(item, dict):
            continue
        tool_name = str(item.get("tool_name") or "").strip()
        if not tool_name:
            continue
        arguments = item.get("arguments")
        ledger.record_tool_result(
            tool_name,
            dict(arguments) if isinstance(arguments, dict) else {},
            item.get("success") is True,
            data=_bounded_evidence_value(item.get("data")),
            error=_bounded_evidence_value(item.get("error")),
        )
    return ledger


def _bounded_evidence_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= 4:
        return "<truncated>"
    if isinstance(value, str):
        return value[:8_000]
    if isinstance(value, dict):
        return {
            str(key)[:200]: _bounded_evidence_value(item, depth=depth + 1)
            for key, item in list(value.items())[:50]
        }
    if isinstance(value, list):
        return [_bounded_evidence_value(item, depth=depth + 1) for item in value[:50]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:8_000]


__all__ = [
    "evidence_ledger_from_task_result",
    "structured_output_from_task_result",
]
