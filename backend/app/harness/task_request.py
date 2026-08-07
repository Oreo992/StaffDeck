from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.capabilities.contracts import CapabilityManifest


class TaskRequirement(BaseModel):
    """Immutable input contract for one Harness task execution."""

    task_frame_id: str
    kind: Literal["sop", "conversation"]
    goal: str
    source_user_message: str = ""
    requirements: list[str] = Field(default_factory=list)
    sop_context: dict[str, Any] = Field(default_factory=dict)
    required_slots: list[str] = Field(default_factory=list)
    known_slots: dict[str, Any] = Field(default_factory=dict)
    completion_criteria: list[str] = Field(default_factory=list)
    allowed_transitions: list[dict[str, Any]] = Field(default_factory=list)
    memory_projection: list[dict[str, str]] = Field(default_factory=list)
    prior_task_results: list[dict[str, Any]] = Field(default_factory=list)
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    capability_manifest: CapabilityManifest = Field(default_factory=CapabilityManifest)


class TaskExecutionResult(BaseModel):
    """Candidate result returned by a Harness agent before SOP reconciliation."""

    task_frame_id: str
    status: Literal[
        "completed",
        "awaiting_user",
        "handoff",
        "failed",
        "blocked",
        "action_budget",
        "cancelled",
    ]
    reply_fragment: str = ""
    slot_updates: dict[str, Any] = Field(default_factory=dict)
    completed_step_ids: list[str] = Field(default_factory=list)
    next_step_id: str | None = None
    citations: list[dict[str, Any]] = Field(default_factory=list)
    evidence_results: list[dict[str, Any]] = Field(default_factory=list)
    capability_results: list[dict[str, Any]] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    task_summary: str = ""
    action_count: int = 0
    runtime_session_id: str | None = None
    error: dict[str, Any] | None = None


__all__ = ["TaskExecutionResult", "TaskRequirement"]
