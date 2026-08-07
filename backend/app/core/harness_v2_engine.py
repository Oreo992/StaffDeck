from __future__ import annotations

import asyncio
import hashlib
import json
from copy import deepcopy
from typing import Any

from app.capabilities.contracts import CapabilityDescriptor, CapabilityManifest
from app.core.cancellation import is_chat_turn_cancelled
from app.core.capability_manifest import CapabilityManifestBuilder
from app.core.harness_capability_invoker import HarnessCapabilityInvoker
from app.core.harness_frame_executor import HarnessFrameExecutor
from app.core.harness_session_lease import (
    HarnessSessionLeaseStore,
    HarnessSessionLeaseToken,
)
from app.core.harness_sop_bridge import (
    evidence_ledger_from_task_result,
    structured_output_from_task_result,
)
from app.core.harness_task_frame_store import TaskFrameStore
from app.core.harness_turn_planner import turn_plan_from_router_decision
from app.core.harness_turn_store import HarnessTurnStore
from app.core.task_request_compiler import TaskRequestCompiler
from app.db.models import ChatSession, HarnessTaskFrameRecord, Message, Skill, Tool, UIConfig
from app.harness.task_request import TaskExecutionResult
from app.harness.task_schema import PlannedTaskFrame
from app.memory.service import memory_read
from app.runtime.contracts import ExecutionSegment, SopAuditOutcome
from app.security.encryption import decrypt_secret
from app.session.helpers import public_session
from app.session.session_schema import (
    ChatTurnRequest,
    ChatTurnResponse,
    RouterDecision,
    StepAgentResult,
)


class HarnessV2Cancelled(RuntimeError):
    """Raised when the user cancellation fence is observed."""


class LegacyHarnessV2Engine:
    """Production Legacy engine: one Router plan, autonomous Runtime, evidence commit."""

    def __init__(self, owner: Any) -> None:
        self.owner = owner
        self.db = owner.db
        self.events = owner.events
        self.runtime = owner.legacy_harness_runtime
        self.frames = TaskFrameStore(self.db)
        self.turns = HarnessTurnStore(self.db)
        self.leases = HarnessSessionLeaseStore(self.db)
        self.compiler = TaskRequestCompiler()
        self.manifests = CapabilityManifestBuilder(self.db)
        self.lease: HarnessSessionLeaseToken | None = None
        self.turn_record: Any = None
        self.session: ChatSession | None = None

    def run(self, request: ChatTurnRequest) -> ChatTurnResponse:
        session = self.owner._get_or_create_session(request)
        self.session = session
        self._lock_session_to_harness(session)
        self.db.add(session)
        self.db.commit()
        self.db.refresh(session)
        self.lease = self.leases.acquire(session)
        try:
            claim = self.turns.claim(session, request)
            self.turn_record = claim.record
            if claim.replay is not None:
                return claim.replay
            return self._run_claimed_turn(request, session)
        except HarnessV2Cancelled as exc:
            self.turns.finish_with_error(
                self.turn_record,
                status="cancelled",
                code="HARNESS_V2_CANCELLED",
                message=str(exc),
            )
            state = dict(session.runtime_state_json or {})
            state.update({"status": "cancelled", "active_run_id": None})
            session.runtime_state_json = state
            self.db.add(session)
            self.db.commit()
            raise
        except Exception as exc:
            self.turns.finish_with_error(
                self.turn_record,
                status="failed",
                code="HARNESS_V2_TURN_FAILED",
                message=str(exc),
            )
            raise

    def close(self) -> None:
        self.leases.release(self.lease)
        self.lease = None

    def _run_claimed_turn(
        self, request: ChatTurnRequest, session: ChatSession
    ) -> ChatTurnResponse:
        self.owner._mark_session_running(session)
        user_message = self.owner._append_message(
            request.tenant_id,
            session.id,
            "user",
            request.message,
            metadata=self.owner._user_message_metadata(request),
        )
        self.turns.bind_user_message(self.turn_record, user_message.id)
        bind_turn = getattr(self.events, "bind_turn", None)
        if callable(bind_turn):
            bind_turn(user_message.id, request.client_turn_id)
        self.events.record(
            request.tenant_id,
            session.id,
            "user_message_received",
            {
                "message_id": user_message.id,
                "turn_id": user_message.id,
                "client_turn_id": request.client_turn_id,
                "message": request.message,
                "channel": request.channel,
                "user_id": request.user_id,
                "execution_engine": "harness_v2",
            },
        )
        model_config = self.owner._get_request_model(request, session.agent_id)
        if model_config is None:
            raise RuntimeError("没有可用于 Harness v2 的模型配置。")
        api_key = decrypt_secret(model_config.api_key_encrypted)
        if not api_key:
            raise RuntimeError("Harness v2 模型没有配置 API Key。")
        skills = self.owner._list_published_skills(request.tenant_id, session.agent_id)
        tools = self.owner._list_enabled_tools(request.tenant_id, session.agent_id)
        memory_context = [
            memory_read(row)
            for row in self.owner.memory.context_memories(
                request.tenant_id,
                request.user_id,
                agent_id=session.agent_id,
            )
        ]
        self.db.commit()
        self.db.refresh(session)
        self._raise_if_cancelled(request, user_message.id)
        conversation_context = self.owner._conversation_context(
            session, model_config=model_config
        )
        self.leases.renew(self.lease)
        router_decision = self.owner.router.decide(
            request.message,
            session,
            skills,
            model_config,
            deepcopy(conversation_context),
            memory_context,
        )
        self.owner._hydrate_router_decision_from_context(
            session, router_decision, skills, memory_context
        )
        plan = turn_plan_from_router_decision(
            router_decision,
            session,
            source_message=request.message,
            source_turn_id=user_message.id,
        )
        self.events.record(
            request.tenant_id,
            session.id,
            "turn_plan_created",
            {
                **plan.model_dump(mode="json"),
                "turn_id": user_message.id,
                "execution_engine": "harness_v2",
            },
        )
        self.events.record(
            request.tenant_id,
            session.id,
            "router_decision_created",
            {
                **router_decision.model_dump(mode="json"),
                "turn_id": user_message.id,
                "execution_engine": "harness_v2",
            },
        )
        self.db.commit()

        immediate = self._immediate_result(router_decision, request, session)
        results: list[TaskExecutionResult] = []
        if immediate is not None:
            results.append(immediate)
        elif plan.task_frames:
            records = self.frames.persist_plan(session, user_message.id, plan.task_frames)
            for record in records:
                self._raise_if_cancelled(request, user_message.id)
                if not self.frames.dependencies_satisfied(record):
                    results.append(
                        TaskExecutionResult(
                            task_frame_id=record.task_id,
                            status="blocked",
                            reply_fragment="前置任务完成后才能继续该任务。",
                            error={"code": "DEPENDENCY_WAITING"},
                        )
                    )
                    continue
                results.append(
                    self._run_frame(
                        request,
                        session,
                        user_message.id,
                        record,
                        model_config,
                        api_key,
                        skills,
                        tools,
                        memory_context,
                        conversation_context,
                    )
                )
        else:
            results.append(
                TaskExecutionResult(
                    task_frame_id=f"turn:{user_message.id}",
                    status="completed",
                    reply_fragment=(
                        router_decision.clarification_question
                        or router_decision.reason
                        or "请补充你希望我完成的具体任务。"
                    ),
                )
            )

        self._raise_if_cancelled(request, user_message.id)
        reply = _combined_reply(results)
        last_result = results[-1]
        step_result = _step_result(last_result)
        runtime_state = dict(session.runtime_state_json or {})
        runtime_state.update(
            {
                "execution_engine": "harness_v2",
                "status": last_result.status,
                "active_run_id": None,
                "last_task_frame_id": last_result.task_frame_id,
            }
        )
        session.runtime_state_json = runtime_state
        self.owner._finalize_turn(
            session,
            request.tenant_id,
            reply,
            step_result,
            request.message,
            user_message_id=user_message.id,
        )
        self.db.add(session)
        self.db.commit()
        self.db.refresh(session)
        self.owner._enqueue_memory_capture(
            request,
            session,
            step_result,
            None,
            model_config,
        )
        response = ChatTurnResponse(
            reply=reply,
            session_id=session.id,
            router_decision=router_decision,
            step_result=step_result,
            session_state=public_session(session),
        )
        self.turns.complete(self.turn_record, response)
        return response

    def _run_frame(
        self,
        request: ChatTurnRequest,
        session: ChatSession,
        user_message_id: str,
        record: HarnessTaskFrameRecord,
        model_config: Any,
        api_key: str,
        skills: list[Skill],
        tools: list[Tool],
        memory_context: list[dict[str, object]],
        conversation_context: dict[str, object],
    ) -> TaskExecutionResult:
        frame = _planned_frame(record)
        skill = next(
            (item for item in skills if item.skill_id == frame.target_skill_id),
            None,
        )
        if frame.kind == "sop" and skill is None:
            return self._finish_unclaimed_failure(record, "SOP_NOT_AVAILABLE", "对应 SOP 当前不可用。")
        if skill is not None:
            self._activate_sop(session, frame, skill)
            self.db.commit()
            self.db.refresh(session)
        lease_owner = f"harness:{user_message_id}:{record.id}"
        claimed = self.frames.claim(record.id, lease_owner)
        result = self._execute_claimed_frame(
            request,
            session,
            user_message_id,
            claimed,
            frame,
            skill,
            model_config,
            api_key,
            tools,
            memory_context,
            conversation_context,
            lease_owner,
        )
        finish_status = result.status
        if finish_status == "action_budget":
            finish_status = "blocked"
        if finish_status not in {
            "awaiting_user",
            "blocked",
            "completed",
            "handoff",
            "failed",
            "cancelled",
        }:
            finish_status = "failed"
        self.frames.finish(
            claimed.id,
            lease_owner,
            status=finish_status,
            result=result.model_dump(mode="json"),
            error=result.error,
        )
        return result

    def _execute_claimed_frame(
        self,
        request: ChatTurnRequest,
        session: ChatSession,
        user_message_id: str,
        record: HarnessTaskFrameRecord,
        frame: PlannedTaskFrame,
        skill: Skill | None,
        model_config: Any,
        api_key: str,
        tools: list[Tool],
        memory_context: list[dict[str, object]],
        conversation_context: dict[str, object],
        lease_owner: str,
    ) -> TaskExecutionResult:
        ui_config = self.db.get(UIConfig, request.tenant_id)
        max_repairs = max(
            0,
            min(int(getattr(ui_config, "claude_max_repair_rounds", 2) or 2), 5),
        )
        max_turns = max(
            2,
            min(int(getattr(ui_config, "agent_loop_max_actions", 6) or 6) * 2, 40),
        )
        approved = _approved_capabilities(session, request.message)
        segment = self._segment(skill, session, tools)
        if segment is not None and segment.requires_approval:
            missing_approvals = [
                name for name in segment.allowed_tool_names if name not in approved
            ]
            if missing_approvals:
                _set_pending_approval(session, missing_approvals)
                self.db.add(session)
                self.db.commit()
                return TaskExecutionResult(
                    task_frame_id=record.task_id,
                    status="awaiting_user",
                    reply_fragment=(
                        "继续执行将调用可能产生副作用的能力："
                        + "、".join(missing_approvals)
                        + "。请明确回复“确认执行”或“取消”。"
                    ),
                    error={"code": "APPROVAL_REQUIRED"},
                )
        manifest = self._manifest(
            request.tenant_id,
            session.agent_id,
            skill,
            segment.node_ids if segment is not None else [frame.target_step_id or ""],
        )
        requirement = self.compiler.compile(
            frame,
            session,
            skill,
            manifest,
            memory_context=memory_context,
            prior_task_results=list(self.frames.dependency_results(record).values()),
            attachments=[item.model_dump(mode="json") for item in request.attachments],
            source_user_message=request.message,
        )
        record.task_requirement_json = requirement.model_dump(mode="json")
        self.db.add(record)
        self.db.commit()
        runtime_session_id = str(
            (session.runtime_state_json or {}).get("runtime_session_id") or ""
        ).strip() or None
        repair_contract: dict[str, Any] | None = None
        last_result: TaskExecutionResult | None = None

        for attempt in range(max_repairs + 1):
            self.leases.renew(self.lease)
            self._raise_if_cancelled(request, user_message_id)

            def trace(event_type: str, payload: dict[str, Any]) -> None:
                if event_type == "harness.run_bound":
                    state = dict(session.runtime_state_json or {})
                    state["active_run_id"] = payload.get("run_id")
                    session.runtime_state_json = state
                    self.db.add(session)
                self.events.record(
                    request.tenant_id,
                    session.id,
                    event_type,
                    {
                        **payload,
                        "turn_id": user_message_id,
                        "task_frame_id": record.task_id,
                        "execution_engine": "harness_v2",
                    },
                )
                self.db.commit()

            invoker = HarnessCapabilityInvoker(
                self.db,
                tenant_id=request.tenant_id,
                session=session,
                task_frame_id=record.task_id,
                run_id="owned-by-frame-executor",
                manifest=manifest,
                agent_id=session.agent_id,
                active_skill=skill,
                active_step_id=frame.target_step_id,
                authorized_step_ids=(segment.node_ids if segment is not None else None),
                model_config=model_config,
                user_id=str(request.user_id or ""),
                approval_checker=lambda descriptor, _arguments: (
                    descriptor.name in approved
                    or (descriptor.kind == "file" and _file_delivery_requested(request.message))
                ),
                file_handlers={
                    "send_file": lambda arguments: self.owner._execute_claude_file_send(
                        arguments, request, session, user_message_id
                    ),
                    "publish_file": lambda arguments: self.owner._execute_claude_file_publish(
                        arguments, request, session, user_message_id
                    ),
                },
                trace_sink=trace,
                persist_invocations=False,
            )
            runtime_context = json.dumps(
                {
                    "recent_conversation": conversation_context,
                    "repair_contract": repair_contract,
                    "attempt": attempt,
                },
                ensure_ascii=False,
                default=str,
            )
            environment = {
                "OPENAI_BASE_URL": str(model_config.base_url or ""),
                "OPENAI_TIMEOUT_SECONDS": "600",
                "OPENAI_TEMPERATURE": str(model_config.temperature),
                "OPENAI_MAX_OUTPUT_TOKENS": str(model_config.max_output_tokens),
                "OPENAI_EXTRA_BODY_JSON": json.dumps(
                    model_config.extra_body_json or {}, ensure_ascii=True
                ),
            }
            result = asyncio.run(
                HarnessFrameExecutor(self.db).execute(
                    record,
                    lease_owner=lease_owner,
                    requirement=requirement,
                    runtime=self.runtime,
                    model=model_config.model,
                    api_key=api_key,
                    invoke_capability=invoker.invoke,
                    resume_session_id=runtime_session_id,
                    max_turns=max_turns,
                    event_sink=trace,
                    environment=environment,
                    system_prompt=self.owner._get_persona_prompt(
                        request.tenant_id, session.agent_id
                    ),
                    runtime_context=runtime_context,
                )
            )
            last_result = result
            runtime_session_id = result.runtime_session_id or runtime_session_id
            state = dict(session.runtime_state_json or {})
            state["runtime_session_id"] = runtime_session_id
            state["active_run_id"] = None
            session.runtime_state_json = state
            self.db.add(session)
            self.db.commit()
            if result.status in {
                "failed",
                "blocked",
                "action_budget",
                "awaiting_user",
                "cancelled",
            }:
                self._remember_pending_capabilities(session, result)
                return result
            if segment is None or skill is None:
                return result

            audit = self.owner.sop_supervisor.audit(
                segment,
                skill.content_json or {},
                session.slots_json or {},
                structured_output_from_task_result(result),
                evidence_ledger_from_task_result(
                    result,
                    approved_capability_names=approved,
                ),
                attempt=attempt,
                max_repairs=max_repairs,
            )
            self.events.record(
                request.tenant_id,
                session.id,
                "sop_audit",
                {
                    **audit.model_dump(mode="json"),
                    "turn_id": user_message_id,
                    "task_frame_id": record.task_id,
                    "execution_engine": "harness_v2",
                },
            )
            self.db.commit()
            if audit.outcome == SopAuditOutcome.REPAIR:
                repair_contract = (
                    audit.repair_contract.model_dump(mode="json")
                    if audit.repair_contract
                    else audit.model_dump(mode="json")
                )
                continue
            if audit.outcome == SopAuditOutcome.PASSED:
                return self._commit_sop_audit(session, skill, result, audit)
            if audit.outcome == SopAuditOutcome.BLOCKED:
                result.status = "awaiting_user"
                return result
            if audit.outcome == SopAuditOutcome.AWAITING_APPROVAL:
                _set_pending_approval(session, segment.allowed_tool_names)
                result.status = "awaiting_user"
                result.reply_fragment = (
                    "继续执行需要你的明确确认。请回复“确认执行”或“取消”。"
                )
                return result
            result.status = "failed"
            result.error = {
                "code": f"SOP_AUDIT_{audit.outcome.value.upper()}",
                "missing_evidence": audit.missing_evidence,
            }
            return result
        return last_result or TaskExecutionResult(
            task_frame_id=record.task_id,
            status="failed",
            reply_fragment="Harness v2 未产生执行结果。",
            error={"code": "EMPTY_RUNTIME_RESULT"},
        )

    def _commit_sop_audit(
        self,
        session: ChatSession,
        skill: Skill,
        result: TaskExecutionResult,
        audit: Any,
    ) -> TaskExecutionResult:
        slots = dict(session.slots_json or {})
        slots.update(audit.accepted_slot_updates)
        session.slots_json = slots
        terminal_ids = {
            str(item) for item in (skill.content_json or {}).get("terminal_node_ids", [])
        }
        if audit.next_step_id:
            session.active_skill_id = skill.skill_id
            session.active_step_id = audit.next_step_id
        elif audit.active_step_id in terminal_ids:
            self.owner.runtime.complete_current_skill(session)
        else:
            session.active_skill_id = skill.skill_id
            session.active_step_id = audit.active_step_id
        session.awaiting_input_json = None
        self.db.add(session)
        self.db.commit()
        result.completed_step_ids = list(audit.completed_step_ids)
        result.next_step_id = audit.next_step_id
        result.slot_updates = dict(audit.accepted_slot_updates)
        return result

    def _manifest(
        self,
        tenant_id: str,
        agent_id: str | None,
        skill: Skill | None,
        step_ids: list[str],
    ) -> CapabilityManifest:
        manifests = [
            self.manifests.build(tenant_id, agent_id, skill, step_id or None)
            for step_id in (step_ids or [""])
        ]
        available = _dedupe_descriptors(
            [item for manifest in manifests for item in manifest.available]
        )
        unavailable = _dedupe_descriptors(
            [item for manifest in manifests for item in manifest.unavailable_references]
        )
        snapshot = json.dumps(
            [item.model_dump(mode="json") for item in [*available, *unavailable]],
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        return CapabilityManifest(
            available=available,
            unavailable_references=unavailable,
            snapshot_revision="sha256:"
            + hashlib.sha256(snapshot.encode("utf-8")).hexdigest(),
        )

    def _segment(
        self,
        skill: Skill | None,
        session: ChatSession,
        tools: list[Tool],
    ) -> ExecutionSegment | None:
        if skill is None or not session.active_step_id:
            return None
        return self.owner.sop_supervisor.compile_segment(
            skill.content_json or {},
            session.active_step_id,
            session.slots_json or {},
            tools,
        )

    def _activate_sop(
        self,
        session: ChatSession,
        frame: PlannedTaskFrame,
        skill: Skill,
    ) -> None:
        if session.active_skill_id != skill.skill_id:
            session.active_skill_id = skill.skill_id
            session.active_step_id = frame.target_step_id or self.owner._first_step_id(skill)
            session.slots_json = dict(frame.slot_hints or {})
        else:
            session.active_step_id = session.active_step_id or frame.target_step_id
            session.slots_json = {
                **dict(session.slots_json or {}),
                **dict(frame.slot_hints or {}),
            }

    def _immediate_result(
        self,
        decision: RouterDecision,
        request: ChatTurnRequest,
        session: ChatSession,
    ) -> TaskExecutionResult | None:
        if decision.decision == "complete_task":
            self.owner.runtime.complete_current_skill(session)
            return TaskExecutionResult(
                task_frame_id=f"turn:{request.client_turn_id or 'complete'}",
                status="completed",
                reply_fragment="当前任务已结束。",
            )
        if decision.decision == "handoff_human":
            step = StepAgentResult(action="handoff", handoff=True, reply="已转交人工处理。")
            self.owner._create_human_handoff_request(
                request.tenant_id, session, None, step
            )
            return TaskExecutionResult(
                task_frame_id=f"turn:{request.client_turn_id or 'handoff'}",
                status="handoff",
                reply_fragment=step.reply,
            )
        if decision.decision == "clarify":
            return TaskExecutionResult(
                task_frame_id=f"turn:{request.client_turn_id or 'clarify'}",
                status="awaiting_user",
                reply_fragment=(
                    decision.clarification_question or "请补充任务所需的信息。"
                ),
            )
        return None

    def _remember_pending_capabilities(
        self, session: ChatSession, result: TaskExecutionResult
    ) -> None:
        pending = [
            str(item.get("tool_name") or "")
            for item in result.capability_results
            if isinstance(item.get("error"), dict)
            and item["error"].get("code") == "APPROVAL_REQUIRED"
            and str(item.get("tool_name") or "")
        ]
        if pending:
            _set_pending_approval(session, pending)
            self.db.add(session)
            self.db.commit()

    def _finish_unclaimed_failure(
        self, record: HarnessTaskFrameRecord, code: str, message: str
    ) -> TaskExecutionResult:
        lease_owner = f"harness:failure:{record.id}"
        claimed = self.frames.claim(record.id, lease_owner)
        result = TaskExecutionResult(
            task_frame_id=record.task_id,
            status="failed",
            reply_fragment=message,
            error={"code": code},
        )
        self.frames.finish(
            claimed.id,
            lease_owner,
            status="failed",
            result=result.model_dump(mode="json"),
            error=result.error,
        )
        return result

    def _lock_session_to_harness(self, session: ChatSession) -> None:
        state = dict(session.runtime_state_json or {})
        current = str(state.get("execution_engine") or "")
        if current and current != "harness_v2":
            raise RuntimeError("当前会话已经锁定到其他执行引擎。")
        state["execution_engine"] = "harness_v2"
        state.setdefault("status", "ready")
        session.runtime_state_json = state

    def _raise_if_cancelled(self, request: ChatTurnRequest, turn_id: str) -> None:
        if is_chat_turn_cancelled(session_id=self.session.id, turn_id=turn_id):
            raise HarnessV2Cancelled("Harness v2 execution was cancelled.")
        client_turn_id = str(request.client_turn_id or "").strip()
        if client_turn_id and is_chat_turn_cancelled(
            session_id=self.session.id, turn_id=client_turn_id
        ):
            raise HarnessV2Cancelled("Harness v2 execution was cancelled.")


def _planned_frame(record: HarnessTaskFrameRecord) -> PlannedTaskFrame:
    return PlannedTaskFrame(
        task_id=record.task_id,
        kind=record.kind,
        status="queued",
        decision=record.decision,
        target_skill_id=record.skill_id,
        target_step_id=record.step_id,
        user_intent=record.user_intent,
        requirements=list(record.requirements_json or []),
        slot_hints=dict(record.slots_json or {}),
        depends_on_task_ids=list(record.depends_on_json or []),
        source_message=_source_message(record),
    )


def _source_message(record: HarnessTaskFrameRecord) -> str:
    session = record._sa_instance_state.session if hasattr(record, "_sa_instance_state") else None
    if session is None:
        return ""
    message = session.get(Message, record.source_turn_id)
    return str(message.content or "") if message is not None else ""


def _dedupe_descriptors(items: list[CapabilityDescriptor]) -> list[CapabilityDescriptor]:
    by_key: dict[tuple[str, str, str], CapabilityDescriptor] = {}
    for item in items:
        by_key[(item.kind, item.capability_id, item.name)] = item
    return list(by_key.values())


def _combined_reply(results: list[TaskExecutionResult]) -> str:
    replies = [item.reply_fragment.strip() for item in results if item.reply_fragment.strip()]
    return "\n\n".join(dict.fromkeys(replies)) or "本轮任务已完成。"


def _step_result(result: TaskExecutionResult) -> StepAgentResult:
    action = {
        "awaiting_user": "ask_user",
        "handoff": "handoff",
        "completed": "advance",
    }.get(result.status, "reply")
    return StepAgentResult(
        action=action,
        reply=result.reply_fragment,
        slot_updates=dict(result.slot_updates),
        knowledge_results=list(result.evidence_results),
        next_step_id=result.next_step_id,
        is_step_completed=result.status == "completed",
        handoff=result.status == "handoff",
    )


def _approved_capabilities(session: ChatSession, message: str) -> set[str]:
    state = dict(session.runtime_state_json or {})
    approved = {
        str(item) for item in state.get("approved_capability_names", []) if str(item)
    }
    pending = {
        str(item) for item in state.get("pending_capability_names", []) if str(item)
    }
    if _explicit_approval(message):
        approved.update(pending)
        state["approved_capability_names"] = sorted(approved)
        state.pop("pending_capability_names", None)
        session.runtime_state_json = state
    elif _explicit_rejection(message):
        state.pop("pending_capability_names", None)
        session.runtime_state_json = state
    return approved


def _set_pending_approval(session: ChatSession, names: list[str]) -> None:
    state = dict(session.runtime_state_json or {})
    state["pending_capability_names"] = sorted({str(item) for item in names if str(item)})
    state["status"] = "awaiting_approval"
    session.runtime_state_json = state


def _explicit_approval(message: str) -> bool:
    normalized = "".join(str(message or "").lower().split()).strip("，。！？、,.!?")
    return normalized in {"确认", "确认执行", "同意", "同意执行", "可以执行"}


def _explicit_rejection(message: str) -> bool:
    normalized = "".join(str(message or "").lower().split()).strip("，。！？、,.!?")
    return normalized in {"取消", "拒绝", "不同意", "不要执行", "停止"}


def _file_delivery_requested(message: str) -> bool:
    text = str(message or "").lower()
    file_words = ("文件", "附件", "html", "网页", "网址", "csv", "json", "markdown")
    action_words = ("生成", "发送", "发我", "给我", "下载", "发布", "转换", "导出")
    return any(word in text for word in file_words) and any(
        word in text for word in action_words
    )


__all__ = ["LegacyHarnessV2Engine"]
