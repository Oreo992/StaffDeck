from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from sqlmodel import Session

from app.core.harness_invocation_store import (
    HarnessInvocationConflict,
    HarnessInvocationStore,
    logical_action_key,
)
from app.core.harness_run_store import HarnessRunStore
from app.db.models import HarnessTaskFrameRecord, new_id
from app.harness.task_request import TaskExecutionResult, TaskRequirement
from app.runtime.contracts import (
    HarnessRunRequest,
    HarnessRunResult,
    HarnessRuntime,
    HarnessTool,
    ToolEffectLevel,
)

CapabilityInvoker = Callable[[str, dict[str, Any]], dict[str, Any]]
TraceSink = Callable[[str, dict[str, Any]], None]

_SYSTEM_PROMPT = """
你是一个自主任务执行 Agent。请在本次连续 Runtime 会话中完成整个 TaskRequirement，
自行决定分析顺序和能力调用；不要等待外层为每一步重新调用模型。

硬性约束：
1. 只能调用当前冻结清单中提供的能力，不得声称未实际执行的调用已经成功。
2. 能力返回失败时必须按真实结果处理；审批被拒绝或缺少审批时停止副作用操作。
3. SOP 信息是执行约束，不是要求每次对话都进入 SOP。你只能提出 slot 和下一步候选，
   不能宣告 Graph 节点已经提交，最终状态由外部证据审计器决定。
4. 需要交付或发布文件时，主动调用相应文件能力；不要只输出大段文件源码冒充交付。
5. 最终返回结构化结果，回复简洁、直接，不描述内部编排实现。
""".strip()

_BLOCKING_CAPABILITY_ERRORS = {
    "CAPABILITY_AUTHORIZATION_REVOKED",
    "INVOCATION_RECONCILIATION_REQUIRED",
    "INVOCATION_CLAIM_FAILED",
}


class HarnessFrameExecutor:
    """Run one TaskRequirement once; the selected Runtime owns its autonomous loop."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.runs = HarnessRunStore(db)
        self.invocations = HarnessInvocationStore(db)

    async def execute(
        self,
        frame: HarnessTaskFrameRecord,
        *,
        lease_owner: str,
        requirement: TaskRequirement,
        runtime: HarnessRuntime,
        model: str,
        api_key: str,
        invoke_capability: CapabilityInvoker,
        resume_session_id: str | None = None,
        max_turns: int = 20,
        max_budget_usd: float | None = None,
        event_sink: TraceSink | None = None,
        environment: dict[str, str] | None = None,
        system_prompt: str | None = None,
        runtime_context: str | None = None,
    ) -> TaskExecutionResult:
        requirement_snapshot = requirement.model_dump(mode="json")
        capability_snapshot = requirement.capability_manifest.model_dump(mode="json")
        run = self.runs.start(
            frame,
            lease_owner=lease_owner,
            requirement=requirement_snapshot,
            capability_snapshot=capability_snapshot,
        )
        capability_results: list[dict[str, Any]] = []
        run_open = {"value": True}
        descriptors = {
            item.name: item
            for item in requirement.capability_manifest.available
            if item.available
        }

        def invoke(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
            if not run_open["value"]:
                return {
                    "success": False,
                    "error": {
                        "code": "HARNESS_RUN_CLOSED",
                        "message": "本次 Runtime 已结束，不能继续调用能力。",
                    },
                }
            self.runs.renew(
                run.id,
                lease_owner=lease_owner,
                attempt_no=run.attempt_no,
            )
            descriptor = descriptors.get(name)
            if descriptor is None:
                result = {
                    "success": False,
                    "error": {
                        "code": "CAPABILITY_NOT_AVAILABLE",
                        "message": "该能力不在当前 TaskFrame 的冻结清单中。",
                    },
                }
                capability_results.append(
                    _bounded_capability_result(name, arguments, result)
                )
                return result
            action_key = None
            effect_level = descriptor.effect_level
            if descriptor.kind == "general_skill" and str(
                arguments.get("operation") or ""
            ).strip().lower() == "read":
                effect_level = "read"
            if effect_level != "read":
                action_key = logical_action_key(
                    tenant_id=frame.tenant_id,
                    task_frame_id=requirement.task_frame_id,
                    step_id=frame.step_id,
                    tool_id=descriptor.capability_id,
                    tool_name=name,
                    arguments=arguments,
                )
            try:
                claim = self.invocations.claim(
                    tenant_id=frame.tenant_id,
                    session_id=frame.session_id,
                    task_id=requirement.task_frame_id,
                    run_id=run.id,
                    call_id=new_id("hcall"),
                    tool_name=name,
                    arguments=arguments,
                    logical_action_key=action_key,
                    audit_arguments=_audit_arguments(arguments),
                )
            except HarnessInvocationConflict as exc:
                result = {
                    "success": False,
                    "error": {
                        "code": "INVOCATION_RECONCILIATION_REQUIRED",
                        "message": str(exc),
                    },
                }
                capability_results.append(
                    _bounded_capability_result(name, arguments, result)
                )
                return result
            if claim.replay is not None:
                capability_results.append(
                    _bounded_capability_result(name, arguments, claim.replay)
                )
                return claim.replay
            if claim.record is None:
                result = {
                    "success": False,
                    "error": {
                        "code": "INVOCATION_CLAIM_FAILED",
                        "message": "无法建立能力调用凭据。",
                    },
                }
                capability_results.append(
                    _bounded_capability_result(name, arguments, result)
                )
                return result
            try:
                result = invoke_capability(name, dict(arguments or {}))
            except Exception as exc:  # noqa: BLE001 - gateway failures become evidence
                result = {
                    "success": False,
                    "error": {
                        "code": "CAPABILITY_EXECUTION_ERROR",
                        "message": str(exc),
                    },
                }
            self.invocations.finish(
                claim.record,
                result,
                definitely_not_sent=_failure_was_not_sent(result),
            )
            capability_results.append(
                _bounded_capability_result(name, arguments, result)
            )
            return result

        def emit(event_type: str, payload: dict[str, Any]) -> None:
            if not run_open["value"]:
                return
            self.runs.renew(
                run.id,
                lease_owner=lease_owner,
                attempt_no=run.attempt_no,
            )
            if event_sink is not None:
                event_sink(event_type, payload)

        request = HarnessRunRequest(
            run_id=run.id,
            model=model,
            api_key=api_key,
            prompt=_task_prompt(requirement_snapshot, runtime_context),
            system_prompt=_combined_system_prompt(system_prompt),
            resume_session_id=resume_session_id,
            tools=_runtime_tools(requirement),
            execute_tool=invoke,
            event_sink=emit,
            max_turns=max(1, min(int(max_turns), 100)),
            max_budget_usd=max_budget_usd,
            environment=dict(environment or {}),
        )
        try:
            emit("harness.run_bound", {"run_id": run.id})
            runtime_result = await runtime.run_segment(request)
            result = _execution_result(requirement, runtime_result, capability_results)
        except Exception as exc:  # noqa: BLE001 - runtime boundary must fail closed
            result = TaskExecutionResult(
                task_frame_id=requirement.task_frame_id,
                status="failed",
                reply_fragment="当前任务 Runtime 执行失败，请稍后重试。",
                capability_results=capability_results,
                task_summary="Runtime 执行异常。",
                error={"code": "HARNESS_RUNTIME_ERROR", "message": str(exc)},
            )
        finally:
            run_open["value"] = False
        self.runs.finish(
            run.id,
            lease_owner=lease_owner,
            attempt_no=run.attempt_no,
            status=result.status,
            action_count=result.action_count,
            result=result.model_dump(mode="json"),
        )
        return result


def _runtime_tools(requirement: TaskRequirement) -> list[HarnessTool]:
    return [
        HarnessTool(
            name=item.name,
            description=item.description,
            input_schema=item.input_schema or {"type": "object"},
            effect_level=ToolEffectLevel(item.effect_level),
        )
        for item in requirement.capability_manifest.available
        if item.available and item.kind != "internal"
    ]


def _task_prompt(
    requirement_snapshot: dict[str, Any],
    runtime_context: str | None,
) -> str:
    payload = json.dumps(requirement_snapshot, ensure_ascii=False, separators=(",", ":"))
    prefix = str(runtime_context or "").strip()
    requirement_prompt = f"执行以下 TaskRequirement。它是本次执行的完整边界：\n{payload}"
    return f"{prefix}\n\n{requirement_prompt}" if prefix else requirement_prompt


def _combined_system_prompt(system_prompt: str | None) -> str:
    prefix = str(system_prompt or "").strip()
    return f"{prefix}\n\n{_SYSTEM_PROMPT}" if prefix else _SYSTEM_PROMPT


def _execution_result(
    requirement: TaskRequirement,
    runtime_result: HarnessRunResult,
    capability_results: list[dict[str, Any]],
) -> TaskExecutionResult:
    if runtime_result.is_error:
        cancelled = runtime_result.error_code == "runtime_cancelled"
        return TaskExecutionResult(
            task_frame_id=requirement.task_frame_id,
            status="cancelled" if cancelled else "failed",
            reply_fragment=(
                "本次执行已取消。"
                if cancelled
                else "当前任务 Runtime 执行失败，请稍后重试。"
            ),
            capability_results=capability_results,
            task_summary="Runtime 已取消。" if cancelled else "Runtime 返回错误。",
            action_count=max(0, runtime_result.num_turns),
            runtime_session_id=runtime_result.session_id,
            error={
                "code": runtime_result.error_code or "HARNESS_RUNTIME_ERROR",
                "message": runtime_result.error_message or "Runtime execution failed.",
            },
        )

    approval_error = _first_capability_error(capability_results, {"APPROVAL_REQUIRED"})
    if approval_error is not None:
        return TaskExecutionResult(
            task_frame_id=requirement.task_frame_id,
            status="awaiting_user",
            reply_fragment="继续执行这项操作需要你的明确确认。",
            capability_results=capability_results,
            task_summary="能力调用正在等待审批。",
            action_count=max(0, runtime_result.num_turns),
            runtime_session_id=runtime_result.session_id,
            error=approval_error,
        )

    blocked_error = _first_capability_error(
        capability_results,
        _BLOCKING_CAPABILITY_ERRORS,
    )
    if blocked_error is not None:
        return TaskExecutionResult(
            task_frame_id=requirement.task_frame_id,
            status="blocked",
            reply_fragment="当前能力授权或调用状态已变化，需要重新规划后继续。",
            capability_results=capability_results,
            task_summary="能力调用被安全门禁阻断。",
            action_count=max(0, runtime_result.num_turns),
            runtime_session_id=runtime_result.session_id,
            error=blocked_error,
        )

    output = runtime_result.output
    status = "awaiting_user" if output.needs_user_input else "completed"
    reply = output.user_question if output.needs_user_input else output.reply
    allowed_next_steps = {
        str(item.get("next_node_id") or "").strip()
        for item in requirement.allowed_transitions
        if isinstance(item, dict)
    }
    candidate_next_step = str(output.next_step_id or "").strip() or None
    if candidate_next_step not in allowed_next_steps:
        candidate_next_step = None
    citations = _collect_dict_items(capability_results, "citations")
    artifacts = _collect_dict_items(capability_results, "artifacts")
    evidence_results = (
        [{"evidence_refs": list(output.evidence_refs)}] if output.evidence_refs else []
    )
    return TaskExecutionResult(
        task_frame_id=requirement.task_frame_id,
        status=status,
        reply_fragment=str(reply or "").strip(),
        slot_updates={
            str(key): value
            for key, value in dict(output.slot_updates or {}).items()
            if str(key).strip() and not str(key).startswith("_")
        },
        completed_step_ids=[
            str(step_id)
            for step_id in output.completed_step_ids
            if str(step_id).strip()
        ],
        next_step_id=candidate_next_step,
        citations=citations,
        evidence_results=evidence_results,
        capability_results=capability_results,
        artifacts=artifacts,
        task_summary=(
            "Runtime 已返回候选结果，等待外部审计。"
            if requirement.kind == "sop"
            else "Runtime 已完成对话任务。"
        ),
        action_count=max(0, runtime_result.num_turns),
        runtime_session_id=runtime_result.session_id,
    )


def _first_capability_error(
    results: list[dict[str, Any]],
    codes: set[str],
) -> dict[str, Any] | None:
    for result in results:
        error = result.get("error")
        if not isinstance(error, dict):
            continue
        if str(error.get("code") or "") in codes:
            return {
                "code": str(error.get("code")),
                "message": str(error.get("message") or ""),
            }
    return None


def _collect_dict_items(
    results: list[dict[str, Any]],
    key: str,
) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    for result in results:
        value = result.get(key)
        if not isinstance(value, list):
            continue
        collected.extend(item for item in value if isinstance(item, dict))
    return collected


def _bounded_capability_result(
    tool_name: str,
    arguments: dict[str, Any],
    result: dict[str, Any],
    *,
    max_chars: int = 12_000,
) -> dict[str, Any]:
    payload = {
        "tool_name": tool_name,
        "arguments": dict(arguments or {}),
        **dict(result or {}),
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    if len(serialized) <= max_chars:
        return payload
    return {
        "tool_name": tool_name,
        "success": result.get("success") is True,
        "truncated": True,
        "preview": serialized[:max_chars],
        "error": result.get("error"),
    }


def _audit_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    sensitive = re.compile(
        r"(?:content|secret|token|password|api[_-]?key|authorization|credential)",
        re.IGNORECASE,
    )
    return {
        str(key): "<redacted>" if sensitive.search(str(key)) else value
        for key, value in arguments.items()
    }


def _failure_was_not_sent(result: dict[str, Any]) -> bool:
    error = result.get("error")
    code = str(error.get("code") or "") if isinstance(error, dict) else ""
    return code in {
        "APPROVAL_REQUIRED",
        "ARTIFACT_REJECTED",
        "CAPABILITY_NOT_AVAILABLE",
        "NOT_ALLOWED",
        "SOP_ACTIVATION_REQUIRED",
    }


__all__ = ["HarnessFrameExecutor"]
