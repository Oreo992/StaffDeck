from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlmodel import Session

from app.capabilities.contracts import CapabilityDescriptor, CapabilityManifest
from app.core.capability_manifest import (
    CapabilityManifestBuilder,
    general_skill_snapshot_digest,
    tool_snapshot_digest,
)
from app.core.harness_invocation_store import (
    HarnessInvocationConflict,
    HarnessInvocationStore,
    logical_action_key,
)
from app.db.models import ChatSession, GeneralSkill, ModelConfig, Skill, Tool, new_id
from app.general_skills.runner import GeneralSkillRunner
from app.knowledge.citations import knowledge_citations_from_results
from app.knowledge.schema import KnowledgeSearchRequest
from app.knowledge.service import KnowledgeService
from app.tools.tool_executor import ToolExecutor
from app.tools.tool_schema import ToolCall

CapabilityHandler = Callable[[dict[str, Any]], dict[str, Any]]
ApprovalChecker = Callable[[CapabilityDescriptor, dict[str, Any]], bool]
TraceSink = Callable[[str, dict[str, Any]], None]


class HarnessCapabilityInvoker:
    """Invoke only capabilities frozen into one manifest with durable replay fences."""

    def __init__(
        self,
        db: Session,
        *,
        tenant_id: str,
        session: ChatSession,
        task_frame_id: str,
        run_id: str,
        manifest: CapabilityManifest,
        agent_id: str | None,
        active_skill: Skill | None,
        active_step_id: str | None,
        model_config: ModelConfig | None = None,
        user_id: str = "",
        approval_checker: ApprovalChecker | None = None,
        file_handlers: dict[str, CapabilityHandler] | None = None,
        trace_sink: TraceSink | None = None,
        persist_invocations: bool = True,
        authorized_step_ids: list[str] | None = None,
    ) -> None:
        self.db = db
        self.tenant_id = tenant_id
        self.session = session
        self.task_frame_id = task_frame_id
        self.run_id = run_id
        self.manifest = manifest
        self.agent_id = agent_id
        self.active_skill = active_skill
        self.active_step_id = active_step_id
        self.model_config = model_config
        self.user_id = user_id
        self.approval_checker = approval_checker
        self.file_handlers = dict(file_handlers or {})
        self.trace_sink = trace_sink
        self.persist_invocations = persist_invocations
        self.authorized_step_ids = [
            str(item) for item in (authorized_step_ids or []) if str(item)
        ]
        self._descriptors = {
            item.name: item for item in manifest.available if item.available
        }
        self._invocations = HarnessInvocationStore(db)

    def invoke(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        call_id: str | None = None,
    ) -> dict[str, Any]:
        descriptor = self._descriptors.get(name)
        if descriptor is None:
            return _failure(
                "CAPABILITY_NOT_AVAILABLE",
                "该能力不在当前 TaskFrame 的冻结清单中。",
            )
        current = self._currently_authorized_descriptor(descriptor)
        if current is None:
            return _failure(
                "CAPABILITY_AUTHORIZATION_REVOKED",
                "该能力已被撤权、归档、禁用或修改，请重新规划。",
            )
        effective_effect = _effective_effect(descriptor, arguments)
        if effective_effect != "read" and not self._is_approved(
            descriptor, arguments
        ):
            return _failure(
                "APPROVAL_REQUIRED",
                "该能力可能产生副作用，必须先获得明确批准。",
                effect_level=effective_effect,
            )

        if not self.persist_invocations:
            return self._dispatch_with_trace(
                descriptor,
                current,
                arguments,
                effective_effect,
            )

        action_key = None
        if effective_effect != "read":
            action_key = logical_action_key(
                tenant_id=self.tenant_id,
                task_frame_id=self.task_frame_id,
                step_id=self.active_step_id,
                tool_id=descriptor.capability_id,
                tool_name=descriptor.name,
                arguments=arguments,
            )
        try:
            claim = self._invocations.claim(
                tenant_id=self.tenant_id,
                session_id=self.session.id,
                task_id=self.task_frame_id,
                run_id=self.run_id,
                call_id=str(call_id or new_id("hcall")),
                tool_name=descriptor.name,
                arguments=arguments,
                logical_action_key=action_key,
                audit_arguments=_audit_arguments(arguments),
            )
        except HarnessInvocationConflict as exc:
            return _failure("INVOCATION_RECONCILIATION_REQUIRED", str(exc))
        if claim.replay is not None:
            return claim.replay
        if claim.record is None:
            return _failure("INVOCATION_CLAIM_FAILED", "无法建立能力调用凭据。")

        self._emit(
            "capability_invocation_started",
            {
                "capability_id": descriptor.capability_id,
                "name": descriptor.name,
                "effect_level": effective_effect,
                "arguments": _audit_arguments(arguments),
            },
        )
        try:
            result = self._dispatch(descriptor, current, arguments)
        except Exception as exc:  # noqa: BLE001 - capability failures are normalized
            result = _failure("CAPABILITY_EXECUTION_ERROR", str(exc))
        self._invocations.finish(
            claim.record,
            result,
            definitely_not_sent=_failure_was_not_sent(result),
        )
        self._emit(
            "capability_invocation_finished",
            {
                "capability_id": descriptor.capability_id,
                "name": descriptor.name,
                "success": result.get("success") is True,
                "result": _audit_result(result),
            },
        )
        return result

    def _dispatch_with_trace(
        self,
        descriptor: CapabilityDescriptor,
        current: CapabilityDescriptor,
        arguments: dict[str, Any],
        effective_effect: str,
    ) -> dict[str, Any]:
        self._emit(
            "capability_invocation_started",
            {
                "capability_id": descriptor.capability_id,
                "name": descriptor.name,
                "effect_level": effective_effect,
                "arguments": _audit_arguments(arguments),
            },
        )
        try:
            result = self._dispatch(descriptor, current, arguments)
        except Exception as exc:  # noqa: BLE001 - capability failures are normalized
            result = _failure("CAPABILITY_EXECUTION_ERROR", str(exc))
        self._emit(
            "capability_invocation_finished",
            {
                "capability_id": descriptor.capability_id,
                "name": descriptor.name,
                "success": result.get("success") is True,
                "result": _audit_result(result),
            },
        )
        return result

    def _dispatch(
        self,
        frozen: CapabilityDescriptor,
        current: CapabilityDescriptor,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        if frozen.kind == "tool":
            return self._invoke_tool(frozen, arguments)
        if frozen.kind == "knowledge":
            return self._invoke_knowledge(frozen, current, arguments)
        if frozen.kind == "general_skill":
            return self._invoke_general_skill(frozen, arguments)
        if frozen.kind == "file":
            handler = self.file_handlers.get(frozen.name)
            if handler is None:
                return _failure(
                    "CAPABILITY_HANDLER_UNAVAILABLE",
                    "当前 Runtime 未配置该文件能力。",
                )
            return handler(arguments)
        return _failure("UNSUPPORTED_CAPABILITY", "不支持的能力类型。")

    def _invoke_tool(
        self,
        descriptor: CapabilityDescriptor,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        source_name = str(descriptor.metadata.get("source_tool_name") or "").strip()
        tool = self.db.get(Tool, descriptor.capability_id)
        if (
            tool is None
            or tool.tenant_id != self.tenant_id
            or not tool.enabled
            or tool.name != source_name
            or tool_snapshot_digest(self.db, tool)
            != str(descriptor.metadata.get("content_digest") or "")
        ):
            return _failure(
                "CAPABILITY_SNAPSHOT_CHANGED",
                "工具配置已变化，请重新规划。",
            )
        result = ToolExecutor(self.db).execute(
            self.tenant_id,
            ToolCall(name=source_name, arguments=arguments),
            active_skill_id=self.active_skill.skill_id if self.active_skill else None,
            agent_id=self.agent_id,
        )
        return result.model_dump(mode="json")

    def _invoke_knowledge(
        self,
        frozen: CapabilityDescriptor,
        current: CapabilityDescriptor,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        query = str(arguments.get("query") or "").strip()
        if not query:
            return _failure("INVALID_ARGUMENTS", "知识检索 query 不能为空。")
        frozen_ids = {
            str(item)
            for item in frozen.metadata.get("allowed_knowledge_base_ids") or []
        }
        current_ids = {
            str(item)
            for item in current.metadata.get("allowed_knowledge_base_ids") or []
        }
        allowed = frozen_ids & current_ids
        requested = {
            str(item) for item in arguments.get("knowledge_base_ids") or []
        }
        selected = sorted(requested & allowed) if requested else sorted(allowed)
        if requested and not selected:
            return _failure(
                "KNOWLEDGE_NOT_AVAILABLE",
                "请求的知识库不在当前 TaskFrame 授权范围内。",
            )
        version_map = frozen.metadata.get("knowledge_version_by_base_id")
        version_map = version_map if isinstance(version_map, dict) else {}
        response = KnowledgeService(self.db).search(
            KnowledgeSearchRequest(
                tenant_id=self.tenant_id,
                agent_id=self.agent_id,
                query=query,
                knowledge_base_ids=selected,
                knowledge_base_version_ids=[
                    str(version_map[kb_id])
                    for kb_id in selected
                    if str(version_map.get(kb_id) or "").strip()
                ],
                max_chunks=max(1, min(int(arguments.get("max_chunks") or 8), 12)),
            ),
            self.model_config,
        )
        data = response.model_dump(mode="json")
        return {
            "success": True,
            "data": data,
            # SOP evidence must come from an objective gateway result, not from
            # the model's prose.  Expose normalized citations at the result root
            # so HarnessFrameExecutor can place them in TaskExecutionResult.
            "citations": knowledge_citations_from_results([data]),
        }

    def _invoke_general_skill(
        self,
        descriptor: CapabilityDescriptor,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        skill = self.db.get(GeneralSkill, descriptor.capability_id)
        if (
            skill is None
            or skill.tenant_id != self.tenant_id
            or skill.status != "published"
            or general_skill_snapshot_digest(skill)
            != str(descriptor.metadata.get("content_digest") or "")
        ):
            return _failure(
                "CAPABILITY_SNAPSHOT_CHANGED",
                "通用技能配置已变化，请重新规划。",
            )
        operation = str(arguments.get("operation") or "").strip().lower()
        query = str(arguments.get("query") or "").strip()
        if operation == "read":
            return {
                "success": True,
                "data": {
                    "slug": skill.slug,
                    "operation": "read",
                    "query": query,
                    "skill_markdown": skill.skill_markdown[:12_000],
                    "files": list(skill.skill_files_json or [])[:20],
                },
            }
        if operation != "execute" or not query:
            return _failure(
                "INVALID_ARGUMENTS",
                "通用技能需要 query，operation 只能是 read 或 execute。",
            )
        if self.model_config is None:
            return _failure(
                "MODEL_CONFIG_REQUIRED",
                "执行通用技能需要有效的模型配置。",
            )
        response = GeneralSkillRunner().run(
            skill,
            query,
            self.model_config,
            user_id=self.user_id,
        )
        success = bool(response.structured_result.get("success", True)) and not bool(
            response.stderr.strip()
        )
        data = response.model_dump(mode="json")
        if success:
            return {"success": True, "data": data}
        return _failure(
            "GENERAL_SKILL_FAILED",
            response.stderr or response.reply,
            data=data,
        )

    def _currently_authorized_descriptor(
        self,
        frozen: CapabilityDescriptor,
    ) -> CapabilityDescriptor | None:
        manifests = [
            CapabilityManifestBuilder(self.db).build(
                self.tenant_id,
                self.agent_id,
                self.active_skill,
                step_id,
            )
            for step_id in (self.authorized_step_ids or [self.active_step_id])
        ]
        for item in [item for manifest in manifests for item in manifest.available]:
            if (
                item.available
                and item.capability_id == frozen.capability_id
                and item.name == frozen.name
                and item.kind == frozen.kind
                and item.effect_level == frozen.effect_level
            ):
                frozen_digest = str(frozen.metadata.get("content_digest") or "")
                current_digest = str(item.metadata.get("content_digest") or "")
                if frozen_digest and frozen_digest != current_digest:
                    return None
                return item
        return None

    def _is_approved(
        self,
        descriptor: CapabilityDescriptor,
        arguments: dict[str, Any],
    ) -> bool:
        return bool(
            self.approval_checker
            and self.approval_checker(descriptor, arguments)
        )

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.trace_sink is not None:
            self.trace_sink(event_type, payload)


def _failure(code: str, message: str, **details: Any) -> dict[str, Any]:
    error = {"code": code, "message": message, "retryable": False}
    error.update(details)
    return {"success": False, "error": error}


def _effective_effect(
    descriptor: CapabilityDescriptor,
    arguments: dict[str, Any],
) -> str:
    if descriptor.kind == "general_skill" and str(
        arguments.get("operation") or ""
    ).strip().lower() == "read":
        return "read"
    return descriptor.effect_level


def _failure_was_not_sent(result: dict[str, Any]) -> bool:
    error = result.get("error")
    code = str(error.get("code") or "") if isinstance(error, dict) else ""
    return code in {
        "CAPABILITY_AUTHORIZATION_REVOKED",
        "CAPABILITY_HANDLER_UNAVAILABLE",
        "CAPABILITY_NOT_AVAILABLE",
        "CAPABILITY_SNAPSHOT_CHANGED",
        "INVALID_ARGUMENTS",
        "KNOWLEDGE_NOT_AVAILABLE",
        "MODEL_CONFIG_REQUIRED",
        "NOT_ALLOWED",
        "NOT_FOUND",
        "DISABLED",
        "UNSUPPORTED_TOOL_TYPE",
    }


def _audit_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        str(key): (
            "<redacted>"
            if any(
                token in str(key).lower()
                for token in ("content", "secret", "token", "password", "api_key")
            )
            else value
        )
        for key, value in arguments.items()
    }


def _audit_result(result: dict[str, Any]) -> dict[str, Any]:
    audited = dict(result)
    data = audited.get("data")
    if isinstance(data, dict):
        audited["data"] = {
            key: (
                "<redacted>"
                if str(key).lower()
                in {"content", "instructions", "stdout", "stderr", "skill_markdown"}
                else value
            )
            for key, value in data.items()
        }
    return audited


__all__ = ["HarnessCapabilityInvoker"]
