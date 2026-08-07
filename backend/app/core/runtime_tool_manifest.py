from __future__ import annotations

import hashlib
import json

from app.capabilities.contracts import CapabilityDescriptor, CapabilityKind, CapabilityManifest
from app.runtime.contracts import HarnessTool


def capability_manifest_from_runtime_tools(
    tools: list[HarnessTool],
    *,
    namespace: str,
) -> CapabilityManifest:
    """Freeze an already-scoped Runtime tool list into the shared manifest contract."""

    available: list[CapabilityDescriptor] = []
    seen: set[str] = set()
    for tool in tools:
        name = str(tool.name or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        available.append(
            CapabilityDescriptor(
                capability_id=f"{namespace}:{name}",
                name=name,
                kind=_capability_kind(name),
                effect_level=tool.effect_level.value,
                description=tool.description,
                input_schema=dict(tool.input_schema or {"type": "object"}),
                metadata={"runtime_scoped": True, "namespace": namespace},
            )
        )
    canonical = [
        item.model_dump(mode="json")
        for item in sorted(available, key=lambda descriptor: descriptor.name)
    ]
    digest_source = json.dumps(
        {"namespace": namespace, "available": canonical},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return CapabilityManifest(
        available=available,
        snapshot_revision=f"sha256:{hashlib.sha256(digest_source).hexdigest()}",
    )


def _capability_kind(name: str) -> CapabilityKind:
    if name in {"send_file", "publish_file"}:
        return "file"
    if name == "knowledge_search":
        return "knowledge"
    if name == "load_skill" or name.startswith("general_skill."):
        return "general_skill"
    return "tool"


__all__ = ["capability_manifest_from_runtime_tools"]
