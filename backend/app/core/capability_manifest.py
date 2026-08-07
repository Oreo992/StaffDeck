from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlmodel import Session, select

from app.agents.branching import (
    get_agent,
    is_bound_resource_visible_for_agent,
    is_open_gallery_resource,
    visible_knowledge_base_versions,
    visible_tool_rows,
)
from app.capabilities.contracts import CapabilityDescriptor, CapabilityManifest
from app.db.models import (
    AgentResourceBinding,
    GeneralSkill,
    KnowledgeBase,
    MCPServer,
    Skill,
    Tool,
)
from app.runtime.sop_supervisor import tool_effect_level

RESERVED_CAPABILITY_NAMES = {
    "activate_sop",
    "capability_describe",
    "capability_search",
    "knowledge_search",
    "load_skill",
    "publish_file",
    "send_file",
}


class CapabilityAuthorizationError(RuntimeError):
    pass


class CapabilityManifestBuilder:
    """Freeze the capabilities visible to one agent and optional SOP step."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def build(
        self,
        tenant_id: str,
        agent_id: str | None,
        skill: Skill | None,
        step_id: str | None,
    ) -> CapabilityManifest:
        if agent_id and get_agent(self.db, tenant_id, agent_id) is None:
            raise CapabilityAuthorizationError(
                "当前员工不存在、已归档或不属于该租户。"
            )

        refs = current_step_capability_refs(skill, step_id)
        available = _builtin_descriptors()
        unavailable: list[CapabilityDescriptor] = []

        general_rows = _visible_general_skills(self.db, tenant_id, agent_id)
        general_by_ref = {
            ref: row for row in general_rows for ref in (row.id, row.slug)
        }
        for row in general_rows:
            scope = _scope(row)
            if scope is None:
                unavailable.append(
                    _unavailable(row.id, f"general_skill.{row.slug}", "general_skill")
                )
                continue
            explicitly_allowed = any(
                general_by_ref.get(ref) is row for ref in refs["general_skill_ids"]
            )
            if scope == "sop_specific" and not explicitly_allowed:
                continue
            available.append(
                CapabilityDescriptor(
                    capability_id=row.id,
                    name=f"general_skill.{row.slug}",
                    kind="general_skill",
                    capability_scope=scope,
                    effect_level=_general_skill_effect(row),
                    description=row.description or row.name,
                    input_schema={
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "operation": {
                                "type": "string",
                                "enum": ["read", "execute"],
                            },
                        },
                        "required": ["query", "operation"],
                        "additionalProperties": False,
                    },
                    metadata={
                        "slug": row.slug,
                        "content_digest": general_skill_snapshot_digest(row),
                        "execution_policy": "inspect_then_decide",
                        "sop_explicitly_allowed": explicitly_allowed,
                    },
                )
            )

        tool_rows = visible_tool_rows(
            self.db, tenant_id, agent_id, include_inactive=False
        )
        tool_by_ref = {ref: row for row in tool_rows for ref in (row.id, row.name)}
        for row in tool_rows:
            scope = _scope(row)
            if scope is None:
                unavailable.append(_unavailable(row.id, row.name, "tool"))
                continue
            explicitly_allowed = any(
                tool_by_ref.get(ref) is row for ref in refs["tool_ids"]
            )
            if scope == "sop_specific" and not explicitly_allowed:
                continue
            if row.allowed_skills_json and (
                skill is None or skill.skill_id not in row.allowed_skills_json
            ):
                if explicitly_allowed:
                    unavailable.append(
                        _unavailable(
                            row.id,
                            row.name,
                            "tool",
                            scope=scope,
                            reason="当前工具的 allowed_skills 未授权该 SOP。",
                        )
                    )
                continue
            invocation_name = _available_invocation_name(row.name, row.id, available)
            effect = tool_effect_level(row)
            available.append(
                CapabilityDescriptor(
                    capability_id=row.id,
                    name=invocation_name,
                    kind="tool",
                    capability_scope=scope,
                    effect_level=effect,
                    description=row.description or row.display_name or row.name,
                    input_schema=dict(row.input_schema or {}),
                    metadata={
                        "source_tool_name": row.name,
                        "tool_type": row.tool_type,
                        "method": row.method,
                        "side_effect": effect,
                        "content_digest": tool_snapshot_digest(self.db, row),
                        "sop_explicitly_allowed": explicitly_allowed,
                    },
                )
            )

        visible_knowledge = visible_knowledge_base_versions(
            self.db, tenant_id, agent_id, include_inactive=False
        )
        knowledge_ids = _allowed_knowledge_ids(
            self.db,
            visible_knowledge,
            refs["knowledge_base_ids"],
            unavailable,
        )
        if knowledge_ids:
            version_by_base = {
                kb_id: visible_knowledge[kb_id].id for kb_id in knowledge_ids
            }
            available.append(
                CapabilityDescriptor(
                    capability_id="knowledge.search",
                    name="knowledge_search",
                    kind="knowledge",
                    effect_level="read",
                    description="检索当前任务已授权的企业知识库。",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "knowledge_base_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "max_chunks": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 12,
                            },
                        },
                        "required": ["query"],
                        "additionalProperties": False,
                    },
                    metadata={
                        "allowed_knowledge_base_ids": knowledge_ids,
                        "knowledge_version_by_base_id": version_by_base,
                        "side_effect": "read",
                    },
                )
            )

        unavailable.extend(
            _missing_explicit_references(
                self.db,
                tenant_id,
                refs,
                general_by_ref,
                tool_by_ref,
                set(knowledge_ids),
            )
        )
        return CapabilityManifest(
            available=available,
            unavailable_references=unavailable,
            snapshot_revision=_snapshot_revision(available, unavailable),
        )


def current_step_capability_refs(
    skill: Skill | None,
    step_id: str | None,
) -> dict[str, list[str]]:
    result = {
        "general_skill_ids": [],
        "tool_ids": [],
        "knowledge_base_ids": [],
    }
    node = _current_node(skill, step_id)
    refs = (node or {}).get("capability_refs")
    if isinstance(refs, dict):
        for key in result:
            result[key] = _text_list(refs.get(key))
    for action in _text_list((node or {}).get("allowed_actions")):
        if action.startswith("call_tool:"):
            tool_ref = action.partition(":")[2].strip()
            if tool_ref and tool_ref not in result["tool_ids"]:
                result["tool_ids"].append(tool_ref)
    knowledge_scope = (node or {}).get("knowledge_scope")
    if isinstance(knowledge_scope, dict):
        for kb_id in _text_list(knowledge_scope.get("knowledge_base_ids")):
            if kb_id not in result["knowledge_base_ids"]:
                result["knowledge_base_ids"].append(kb_id)
    return result


def tool_snapshot_digest(db: Session, tool: Tool) -> str:
    server_payload: dict[str, Any] | None = None
    if tool.mcp_server_id:
        server = db.get(MCPServer, tool.mcp_server_id)
        if server is not None:
            server_payload = {
                "id": server.id,
                "tenant_id": server.tenant_id,
                "transport": server.transport,
                "url": server.url,
                "headers": server.headers_json or {},
                "command": server.command,
                "args": server.args_json or [],
                "env": server.env_json or {},
                "cwd": server.cwd,
                "enabled": server.enabled,
            }
    return _digest(
        {
            "id": tool.id,
            "tenant_id": tool.tenant_id,
            "name": tool.name,
            "tool_type": tool.tool_type,
            "method": tool.method,
            "url": tool.url,
            "headers": tool.headers_json or {},
            "auth": tool.auth_json or {},
            "config": tool.config_json or {},
            "input_schema": tool.input_schema or {},
            "output_schema": tool.output_schema or {},
            "allowed_skills": tool.allowed_skills_json or [],
            "mcp_server_id": tool.mcp_server_id,
            "effect_level": tool.effect_level,
            "enabled": tool.enabled,
            "mcp_server": server_payload,
        }
    )


def general_skill_snapshot_digest(skill: GeneralSkill) -> str:
    return _digest(
        {
            "id": skill.id,
            "tenant_id": skill.tenant_id,
            "slug": skill.slug,
            "name": skill.name,
            "description": skill.description,
            "skill_markdown": skill.skill_markdown,
            "skill_files": skill.skill_files_json or [],
            "metadata": skill.metadata_json or {},
            "permissions": skill.permissions_json or {},
            "runtime_config": skill.runtime_config_json or {},
            "status": skill.status,
        }
    )


def _builtin_descriptors() -> list[CapabilityDescriptor]:
    return [
        CapabilityDescriptor(
            capability_id="builtin.file.send",
            name="send_file",
            kind="file",
            effect_level="write",
            description="把生成内容作为文件发送给用户下载。",
            input_schema={
                "type": "object",
                "properties": {
                    "filename": {"type": "string"},
                    "content": {"type": "string"},
                    "description": {"type": "string"},
                    "content_type": {"type": "string"},
                },
                "required": ["filename", "content"],
                "additionalProperties": False,
            },
            metadata={"provider": "builtin.file", "side_effect": "write"},
        ),
        CapabilityDescriptor(
            capability_id="builtin.file.publish",
            name="publish_file",
            kind="file",
            effect_level="write",
            description="将本轮已发送的 HTML 文件发布为公网链接。",
            input_schema={
                "type": "object",
                "properties": {"artifact_id": {"type": "string", "minLength": 1}},
                "required": ["artifact_id"],
                "additionalProperties": False,
            },
            metadata={"provider": "builtin.file", "side_effect": "write"},
        ),
    ]


def _visible_general_skills(
    db: Session,
    tenant_id: str,
    agent_id: str | None,
) -> list[GeneralSkill]:
    agent = get_agent(db, tenant_id, agent_id)
    rows = db.exec(
        select(GeneralSkill).where(
            GeneralSkill.tenant_id == tenant_id,
            GeneralSkill.status == "published",
        )
    ).all()
    if agent_id and not agent:
        return []
    if not agent or agent.is_overall:
        return [
            row
            for row in rows
            if is_open_gallery_resource(db, tenant_id, "general_skill", row)
        ]
    bindings = db.exec(
        select(AgentResourceBinding).where(
            AgentResourceBinding.tenant_id == tenant_id,
            AgentResourceBinding.agent_id == agent.id,
            AgentResourceBinding.resource_type == "general_skill",
            AgentResourceBinding.status == "active",
        )
    ).all()
    by_id = {row.id: row for row in rows}
    return [
        row
        for binding in bindings
        if (row := by_id.get(binding.resource_id)) is not None
        and is_bound_resource_visible_for_agent(
            db, tenant_id, "general_skill", row, binding
        )
    ]


def _allowed_knowledge_ids(
    db: Session,
    visible: dict[str, Any],
    explicit_refs: list[str],
    unavailable: list[CapabilityDescriptor],
) -> list[str]:
    allowed: list[str] = []
    for kb_id, version in visible.items():
        root = db.get(KnowledgeBase, kb_id)
        scope = _scope(version)
        if scope == "general" and root is not None:
            scope = _scope(root)
        if scope is None:
            if kb_id in explicit_refs:
                unavailable.append(_unavailable(kb_id, kb_id, "knowledge"))
            continue
        if scope == "sop_specific" and kb_id not in explicit_refs:
            continue
        allowed.append(kb_id)
    return allowed


def _missing_explicit_references(
    db: Session,
    tenant_id: str,
    refs: dict[str, list[str]],
    general_by_ref: dict[str, GeneralSkill],
    tool_by_ref: dict[str, Tool],
    knowledge_ids: set[str],
) -> list[CapabilityDescriptor]:
    missing: list[CapabilityDescriptor] = []
    for ref in refs["general_skill_ids"]:
        if ref not in general_by_ref:
            missing.append(
                _unavailable(
                    ref,
                    ref,
                    "general_skill",
                    scope="sop_specific",
                    reason=_explicit_reason(db.get(GeneralSkill, ref), tenant_id),
                )
            )
    for ref in refs["tool_ids"]:
        if ref not in tool_by_ref:
            missing.append(
                _unavailable(
                    ref,
                    ref,
                    "tool",
                    scope="sop_specific",
                    reason=_explicit_reason(db.get(Tool, ref), tenant_id),
                )
            )
    for ref in refs["knowledge_base_ids"]:
        if ref not in knowledge_ids:
            missing.append(
                _unavailable(
                    ref,
                    ref,
                    "knowledge",
                    scope="sop_specific",
                    reason=_explicit_reason(db.get(KnowledgeBase, ref), tenant_id),
                )
            )
    return missing


def _scope(row: object | None) -> str | None:
    if row is None:
        return "general"
    value = str(getattr(row, "capability_scope", "") or "").strip()
    if not value:
        metadata = getattr(row, "metadata_json", None)
        config = getattr(row, "config_json", None)
        source = metadata if isinstance(metadata, dict) else config
        value = str((source or {}).get("capability_scope") or "general").strip()
    return value if value in {"general", "sop_specific"} else None


def _general_skill_effect(skill: GeneralSkill) -> str:
    permissions = skill.permissions_json if isinstance(skill.permissions_json, dict) else {}
    configured = str(permissions.get("effect_level") or "").strip().lower()
    if configured in {"read", "write", "destructive"}:
        return configured
    return "write"


def _current_node(skill: Skill | None, step_id: str | None) -> dict[str, Any] | None:
    if skill is None:
        return None
    content = skill.content_json or {}
    resolved = str(step_id or content.get("start_node_id") or "").strip()
    for node in content.get("nodes") or content.get("steps") or []:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("node_id") or node.get("step_id") or "").strip()
        if node_id and node_id == resolved:
            return node
    return None


def _text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _available_invocation_name(
    preferred: str,
    capability_id: str,
    available: list[CapabilityDescriptor],
) -> str:
    used = {item.name for item in available} | RESERVED_CAPABILITY_NAMES
    if preferred not in used:
        return preferred
    base = f"external_tool.{capability_id}"
    candidate = base
    suffix = 2
    while candidate in used:
        candidate = f"{base}.{suffix}"
        suffix += 1
    return candidate


def _unavailable(
    capability_id: str,
    name: str,
    kind: str,
    *,
    scope: str = "general",
    reason: str = "能力范围配置无效，已按 fail-closed 禁用。",
) -> CapabilityDescriptor:
    return CapabilityDescriptor(
        capability_id=capability_id,
        name=name,
        kind=kind,  # type: ignore[arg-type]
        capability_scope=scope,  # type: ignore[arg-type]
        available=False,
        unavailable_reason=reason,
    )


def _explicit_reason(row: object | None, tenant_id: str) -> str:
    if row is None or str(getattr(row, "tenant_id", "")) != tenant_id:
        return "SOP 引用的能力不存在。"
    return "SOP 引用的能力未发布、未启用或未绑定到当前员工。"


def _snapshot_revision(
    available: list[CapabilityDescriptor],
    unavailable: list[CapabilityDescriptor],
) -> str:
    def stable_key(item: CapabilityDescriptor) -> tuple[str, str, str]:
        return (item.kind, item.name, item.capability_id)

    payload = {
        "available": [
            item.model_dump(mode="json") for item in sorted(available, key=stable_key)
        ],
        "unavailable": [
            item.model_dump(mode="json") for item in sorted(unavailable, key=stable_key)
        ],
    }
    return _digest(payload)


def _digest(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = [
    "CapabilityAuthorizationError",
    "CapabilityManifestBuilder",
    "current_step_capability_refs",
    "general_skill_snapshot_digest",
    "tool_snapshot_digest",
]
