from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

AgentOperationsItemKind = Literal[
    "session",
    "scheduled_run",
    "scheduled_task",
    "handoff",
    "evolution_proposal",
]
AgentCapabilityChangeKind = Literal["sop", "tool", "knowledge", "skill"]


class AgentOperationsMetricsRead(BaseModel):
    running: int = 0
    awaiting_confirmation: int = 0
    completed: int = 0
    effective_tasks: int = 0
    capability_changes: int = 0


class AgentOperationsItemRead(BaseModel):
    id: str
    kind: AgentOperationsItemKind
    title: str
    description: str = ""
    status: str
    timestamp: str
    session_id: Optional[str] = None


class AgentOperationsTrendPointRead(BaseModel):
    date: str
    value: int = 0


class AgentCapabilityChangeRead(BaseModel):
    id: str
    kind: AgentCapabilityChangeKind
    label: str
    timestamp: str
    phase: Literal["applied"] = "applied"
    instruction: str = ""
    reuse_count: int = 0


class AgentOperationsSummaryRead(BaseModel):
    agent_id: str
    timezone: str
    period_days: int
    generated_at: str
    metrics: AgentOperationsMetricsRead
    attention_items: list[AgentOperationsItemRead] = Field(default_factory=list)
    today_items: list[AgentOperationsItemRead] = Field(default_factory=list)
    recent_items: list[AgentOperationsItemRead] = Field(default_factory=list)
    completion_trend: list[AgentOperationsTrendPointRead] = Field(default_factory=list)
    capability_changes: list[AgentCapabilityChangeRead] = Field(default_factory=list)
