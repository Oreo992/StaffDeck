from __future__ import annotations

from collections.abc import Callable
import hashlib
import logging

from sqlalchemy import Engine
from sqlmodel import Session

from app.config import get_settings
from app.core.harness_task_frame_store import TaskFrameStore
from app.core.harness_turn_store import HarnessTurnClaim, HarnessTurnStore
from app.db.models import ChatSession
from app.harness.task_schema import PlannedTaskFrame
from app.session.session_schema import ChatTurnRequest, ChatTurnResponse, PendingTask

logger = logging.getLogger(__name__)


class HarnessShadowRecorder:
    """Best-effort Harness v2 persistence isolated from the runtime transaction."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        enabled: bool,
    ) -> None:
        self._session_factory = session_factory
        self.enabled = enabled

    def record_completed_turn(
        self,
        request: ChatTurnRequest,
        response: ChatTurnResponse,
    ) -> bool:
        if not self.enabled or not str(request.client_turn_id or "").strip():
            return False

        claim: HarnessTurnClaim | None = None
        turn_store: HarnessTurnStore | None = None
        try:
            with self._session_factory() as shadow_db:
                chat_session = shadow_db.get(ChatSession, response.session_id)
                if chat_session is None or chat_session.tenant_id != request.tenant_id:
                    return False

                shadow_request = request.model_copy(
                    update={"session_id": response.session_id}
                )
                turn_store = HarnessTurnStore(shadow_db)
                claim = turn_store.claim(chat_session, shadow_request)
                if claim.replay is not None:
                    return True

                frames = _shadow_task_frames(shadow_request, response)
                TaskFrameStore(shadow_db).persist_plan(
                    chat_session,
                    source_turn_id=str(shadow_request.client_turn_id),
                    frames=frames,
                )
                turn_store.complete(claim.record, response)
                return True
        except Exception as exc:  # Shadow writes must never affect the primary response.
            if turn_store is not None and claim is not None:
                try:
                    turn_store.finish_with_error(
                        claim.record,
                        status="failed",
                        code="shadow_record_failed",
                        message=str(exc),
                    )
                except Exception:
                    pass
            logger.warning(
                "Harness v2 shadow recording failed for session %s: %s",
                response.session_id,
                exc,
            )
            return False


def record_harness_v2_shadow_turn(
    request: ChatTurnRequest,
    response: ChatTurnResponse,
    *,
    target_engine: Engine | None = None,
    enabled: bool | None = None,
) -> bool:
    """Record a completed response without reusing the caller's database Session."""

    if enabled is None:
        enabled = get_settings().harness_v2_shadow_enabled
    if not enabled:
        return False
    if target_engine is None:
        from app.db.database import engine as target_engine

    recorder = HarnessShadowRecorder(
        lambda: Session(target_engine),
        enabled=True,
    )
    return recorder.record_completed_turn(request, response)


def _shadow_task_frames(
    request: ChatTurnRequest,
    response: ChatTurnResponse,
) -> list[PlannedTaskFrame]:
    decision = response.router_decision
    candidates = list(decision.task_frames) if decision and decision.task_frames else []
    if not candidates:
        candidates = [
            PendingTask(
                task_id=decision.selected_task_id if decision else None,
                decision=decision.decision if decision else "answer_only",
                target_skill_id=(
                    decision.target_skill_id
                    if decision
                    else response.session_state.active_skill_id
                ),
                target_step_id=(
                    decision.target_step_id
                    if decision
                    else response.session_state.active_step_id
                ),
                user_intent=decision.user_intent if decision else request.message,
                source_message=request.message,
                slot_hints=dict(decision.slot_hints) if decision else {},
            )
        ]

    frames: list[PlannedTaskFrame] = []
    for index, candidate in enumerate(candidates):
        skill_id = candidate.target_skill_id
        frames.append(
            PlannedTaskFrame(
                task_id=candidate.task_id or _shadow_task_id(request, index),
                kind="sop" if skill_id else "conversation",
                status=_shadow_status(candidate.decision, response),
                decision=candidate.decision,
                target_skill_id=skill_id,
                target_step_id=candidate.target_step_id,
                user_intent=candidate.user_intent,
                slot_hints=dict(candidate.slot_hints),
                source_message=candidate.source_message or request.message,
            )
        )
    return frames


def _shadow_task_id(request: ChatTurnRequest, index: int) -> str:
    source = f"{request.client_turn_id}:{index}".encode("utf-8")
    return f"shadow_{hashlib.sha256(source).hexdigest()[:24]}"


def _shadow_status(decision: str, response: ChatTurnResponse) -> str:
    if decision == "handoff_human":
        return "handoff"
    if decision == "clarify" or response.session_state.awaiting_input:
        return "awaiting_user"
    if response.session_state.active_skill_id:
        return "running"
    return "completed"


__all__ = ["HarnessShadowRecorder", "record_harness_v2_shadow_turn"]
