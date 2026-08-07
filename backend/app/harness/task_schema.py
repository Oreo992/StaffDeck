from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

RouterDecisionValue = Literal[
    "continue_active",
    "switch_to_pending",
    "create_pending",
    "update_pending",
    "complete_task",
    "start_new_task",
    "answer_only",
    "handoff_human",
    "clarify",
]
TaskFrameKind = Literal["sop", "conversation"]
TaskFrameRunStatus = Literal[
    "queued",
    "running",
    "awaiting_user",
    "blocked",
    "completed",
    "handoff",
    "failed",
    "cancelled",
]


class PlannedTaskFrame(BaseModel):
    task_id: str | None = None
    kind: TaskFrameKind = "conversation"
    status: TaskFrameRunStatus = "queued"
    decision: RouterDecisionValue = "answer_only"
    target_skill_id: str | None = None
    target_step_id: str | None = None
    user_intent: str | None = None
    requirements: list[str] = Field(default_factory=list)
    slot_hints: dict[str, Any] = Field(default_factory=dict)
    depends_on_task_ids: list[str] = Field(default_factory=list)
    source_message: str | None = None

    @field_validator("slot_hints", mode="before")
    @classmethod
    def _default_null_slot_hints(cls, value: Any) -> Any:
        return {} if value is None else value

    @field_validator("requirements", "depends_on_task_ids", mode="before")
    @classmethod
    def _default_null_lists(cls, value: Any) -> Any:
        return [] if value is None else value


class TaskUpdate(BaseModel):
    task_id: str
    status: str | None = None
    target_skill_id: str | None = None
    target_step_id: str | None = None
    user_intent: str | None = None
    reason: str | None = None
    source_message: str | None = None
    slot_hints: dict[str, Any] = Field(default_factory=dict)
    remove: bool = False


class TurnPlan(BaseModel):
    """Scene/SOP intent decision for one user turn, without capability selection."""

    decision: RouterDecisionValue = "answer_only"
    selected_task_id: str | None = None
    confidence: float = 0.0
    user_intent: str | None = None
    reason: str | None = None
    clarification_question: str | None = None
    task_frames: list[PlannedTaskFrame] = Field(default_factory=list)
    task_updates: list[TaskUpdate] = Field(default_factory=list)

    @field_validator("task_frames", "task_updates", mode="before")
    @classmethod
    def _default_null_lists(cls, value: Any) -> Any:
        return [] if value is None else value


__all__ = [
    "PlannedTaskFrame",
    "RouterDecisionValue",
    "TaskFrameKind",
    "TaskFrameRunStatus",
    "TaskUpdate",
    "TurnPlan",
]
