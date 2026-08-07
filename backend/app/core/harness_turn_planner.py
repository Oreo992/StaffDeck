from __future__ import annotations

import hashlib

from app.db.models import ChatSession
from app.harness.task_schema import PlannedTaskFrame, TaskUpdate, TurnPlan
from app.session.session_schema import PendingTask, RouterDecision

_EXECUTABLE_DECISIONS = {
    "continue_active",
    "switch_to_pending",
    "start_new_task",
}


def turn_plan_from_router_decision(
    decision: RouterDecision,
    session: ChatSession,
    *,
    source_message: str,
    source_turn_id: str,
) -> TurnPlan:
    """Translate scene selection into immutable Harness task frames."""

    frames: list[PlannedTaskFrame] = []
    if decision.decision == "answer_only":
        frames.append(
            PlannedTaskFrame(
                task_id=_task_id(session.id, source_turn_id, 0),
                kind="conversation",
                decision="answer_only",
                user_intent=decision.user_intent or source_message,
                source_message=source_message,
            )
        )
    elif decision.decision in _EXECUTABLE_DECISIONS:
        candidates = list(decision.task_frames or [])
        if not candidates:
            candidates = [
                PendingTask(
                    task_id=decision.selected_task_id,
                    decision=decision.decision,
                    target_skill_id=decision.target_skill_id,
                    target_step_id=decision.target_step_id,
                    confidence=decision.confidence,
                    user_intent=decision.user_intent,
                    reason=decision.reason,
                    source_message=decision.source_message,
                    slot_hints=dict(decision.slot_hints or {}),
                )
            ]
        frames = [
            _frame_from_candidate(
                candidate,
                decision,
                session,
                source_message=source_message,
                source_turn_id=source_turn_id,
                index=index,
            )
            for index, candidate in enumerate(candidates)
        ]

    task_updates = [
        TaskUpdate(
            task_id=item.task_id,
            status=item.status,
            target_skill_id=item.target_skill_id,
            target_step_id=item.target_step_id,
            user_intent=item.user_intent,
            reason=item.reason,
            source_message=item.source_message,
            slot_hints=dict(item.slot_hints or {}),
            remove=item.remove,
        )
        for item in decision.task_updates
    ]
    return TurnPlan(
        decision=decision.decision,
        selected_task_id=decision.selected_task_id,
        confidence=decision.confidence,
        user_intent=decision.user_intent,
        reason=decision.reason,
        clarification_question=decision.clarification_question,
        task_frames=frames,
        task_updates=task_updates,
    )


def _frame_from_candidate(
    candidate: PendingTask,
    decision: RouterDecision,
    session: ChatSession,
    *,
    source_message: str,
    source_turn_id: str,
    index: int,
) -> PlannedTaskFrame:
    skill_id = candidate.target_skill_id or decision.target_skill_id
    if not skill_id and decision.decision == "continue_active":
        skill_id = session.active_skill_id
    step_id = candidate.target_step_id or decision.target_step_id
    if not step_id and skill_id == session.active_skill_id:
        step_id = session.active_step_id
    slot_hints = {
        **dict(decision.slot_hints or {}),
        **dict(candidate.slot_hints or {}),
    }
    return PlannedTaskFrame(
        task_id=candidate.task_id or _task_id(session.id, source_turn_id, index),
        kind="sop" if skill_id else "conversation",
        decision=candidate.decision or decision.decision,
        target_skill_id=skill_id,
        target_step_id=step_id,
        user_intent=candidate.user_intent or decision.user_intent or source_message,
        slot_hints=slot_hints,
        source_message=candidate.source_message or source_message,
    )


def _task_id(session_id: str, source_turn_id: str, index: int) -> str:
    source = f"{session_id}:{source_turn_id}:{index}".encode()
    return f"task_{hashlib.sha256(source).hexdigest()[:24]}"


__all__ = ["turn_plan_from_router_decision"]
