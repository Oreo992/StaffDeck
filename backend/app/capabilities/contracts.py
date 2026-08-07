from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

CapabilityKind = Literal["general_skill", "knowledge", "tool", "file", "internal"]
CapabilityScope = Literal["general", "sop_specific"]
CapabilityEffect = Literal["read", "write", "destructive"]


class CapabilityDescriptor(BaseModel):
    capability_id: str
    name: str
    kind: CapabilityKind
    capability_scope: CapabilityScope = "general"
    effect_level: CapabilityEffect = "read"
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    available: bool = True
    unavailable_reason: str | None = None


class CapabilityManifest(BaseModel):
    available: list[CapabilityDescriptor] = Field(default_factory=list)
    unavailable_references: list[CapabilityDescriptor] = Field(default_factory=list)
    snapshot_revision: str = ""

    def allowed_names(self) -> set[str]:
        return {
            item.name
            for item in self.available
            if item.available and str(item.name or "").strip()
        }


__all__ = [
    "CapabilityDescriptor",
    "CapabilityEffect",
    "CapabilityKind",
    "CapabilityManifest",
    "CapabilityScope",
]
