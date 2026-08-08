from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class CapabilityEvolutionLearnRequest(BaseModel):
    tenant_id: str
    period_days: int = Field(default=30, ge=1, le=90)


class CapabilityEvolutionActionRequest(BaseModel):
    tenant_id: str


class CapabilityEvolutionProposalRead(BaseModel):
    id: str
    tenant_id: str
    agent_id: str
    target_kind: Literal["general_skill"] = "general_skill"
    target_resource_id: str
    target_label: str
    title: str
    summary: str
    instruction: str
    evidence: list[dict[str, object]] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    before_content: str
    after_content: str
    status: Literal["pending", "applied", "rejected"]
    reuse_count: int = 0
    last_reused_at: datetime | None = None
    applied_at: datetime | None = None
    rejected_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
