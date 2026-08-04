from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

from pydantic import BaseModel, Field


class RuntimeMode(str, Enum):
    LEGACY = "legacy"
    CLAUDE_SUPERVISED = "claude_supervised"


class ToolEffectLevel(str, Enum):
    READ = "read"
    WRITE = "write"
    DESTRUCTIVE = "destructive"


class SopAuditOutcome(str, Enum):
    PASSED = "passed"
    REPAIR = "repair"
    AWAITING_APPROVAL = "awaiting_approval"
    BLOCKED = "blocked"
    FAILED = "failed"


class HarnessStructuredOutput(BaseModel):
    reply: str = ""
    slot_updates: dict[str, Any] = Field(default_factory=dict)
    completed_step_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    needs_user_input: bool = False
    user_question: str | None = None
    next_step_id: str | None = None


class HarnessTool(BaseModel):
    name: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})
    effect_level: ToolEffectLevel = ToolEffectLevel.READ


ToolExecutorCallback = Callable[
    [str, dict[str, Any]],
    dict[str, Any] | Awaitable[dict[str, Any]],
]
EventSink = Callable[[str, dict[str, Any]], None]


@dataclass(slots=True)
class HarnessRunRequest:
    run_id: str
    model: str
    api_key: str
    prompt: str
    system_prompt: str
    resume_session_id: str | None = None
    tools: list[HarnessTool] = field(default_factory=list)
    execute_tool: ToolExecutorCallback | None = None
    event_sink: EventSink | None = None
    max_turns: int = 20
    max_budget_usd: float | None = None
    environment: dict[str, str] = field(default_factory=dict)


class HarnessRunResult(BaseModel):
    session_id: str | None = None
    output: HarnessStructuredOutput = Field(default_factory=HarnessStructuredOutput)
    text: str = ""
    is_error: bool = False
    error_code: str | None = None
    error_message: str | None = None
    num_turns: int = 0
    usage: dict[str, Any] = Field(default_factory=dict)
    total_cost_usd: float | None = None
    stop_reason: str | None = None


class ExecutionSegment(BaseModel):
    skill_id: str
    start_step_id: str
    node_ids: list[str]
    nodes: list[dict[str, Any]]
    allowed_tool_names: list[str] = Field(default_factory=list)
    boundary: str = "checkpoint"
    next_step_id: str | None = None
    requires_approval: bool = False


class RepairContract(BaseModel):
    required_steps: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    completed_steps: list[str] = Field(default_factory=list)
    attempt: int = 1


class SopAuditResult(BaseModel):
    outcome: SopAuditOutcome
    active_step_id: str
    missing_evidence: list[str] = Field(default_factory=list)
    repair_contract: RepairContract | None = None
    completed_step_ids: list[str] = Field(default_factory=list)
    next_step_id: str | None = None
    accepted_slot_updates: dict[str, Any] = Field(default_factory=dict)


class HarnessRuntime(Protocol):
    async def run_segment(self, request: HarnessRunRequest) -> HarnessRunResult: ...

    async def resume(
        self, checkpoint_id: str, request: HarnessRunRequest
    ) -> HarnessRunResult: ...

    def cancel(self, run_id: str) -> bool: ...
