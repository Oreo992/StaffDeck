from __future__ import annotations

import re
from typing import Any

from app.capabilities.contracts import CapabilityManifest
from app.db.models import ChatSession, Skill
from app.harness.task_request import TaskRequirement
from app.harness.task_schema import PlannedTaskFrame

_SENSITIVE_KEY = re.compile(
    r"(?:^|_)(?:api_?key|access_?token|refresh_?token|secret|password|credential)(?:$|_)",
    re.IGNORECASE,
)
_MAX_SOURCE_CHARS = 4_000
_MAX_MEMORY_CHARS = 1_000
_MAX_PROJECTED_TEXT_CHARS = 8_000
_MAX_COLLECTION_ITEMS = 20


class TaskRequestCompiler:
    """Compile current state into an immutable, bounded Harness input contract."""

    def compile(
        self,
        frame: PlannedTaskFrame,
        session: ChatSession,
        skill: Skill | None,
        manifest: CapabilityManifest,
        memory_context: list[dict[str, object]] | None = None,
        prior_task_results: list[dict[str, Any]] | None = None,
        attachments: list[dict[str, Any]] | None = None,
        source_user_message: str | None = None,
    ) -> TaskRequirement:
        current_node = _current_node(skill, frame.target_step_id or session.active_step_id)
        expected_fields = _unique(
            [
                *_text_list((skill.content_json or {}).get("required_info") if skill else None),
                *_text_list((current_node or {}).get("expected_user_info")),
            ]
        )
        known_slots = _known_slots(frame, session) if frame.kind == "sop" else {}
        required_slots = [
            field for field in expected_fields if not _slot_satisfied(known_slots.get(field))
        ]
        requirements = _unique(
            [
                str((current_node or {}).get("instruction") or ""),
                "补齐以下字段：" + "、".join(required_slots) if required_slots else "",
                *frame.requirements,
            ]
        )
        goal = _goal(frame, skill, current_node, requirements)
        completion_criteria = _unique(
            [
                (
                    "收集并确认当前步骤要求的字段：" + "、".join(expected_fields)
                    if expected_fields
                    else ""
                ),
                *(_text_list((skill.content_json or {}).get("goal")) if skill else []),
                "完整处理 TaskRequirement 中的全部子需求。",
            ]
        )
        return TaskRequirement(
            task_frame_id=str(frame.task_id or ""),
            kind=frame.kind,
            goal=goal,
            source_user_message=str(
                source_user_message or frame.source_message or session.last_agent_question or ""
            ).strip()[:_MAX_SOURCE_CHARS],
            requirements=requirements or [goal],
            sop_context=_sop_context(skill, current_node) if frame.kind == "sop" else {},
            required_slots=required_slots,
            known_slots=known_slots,
            completion_criteria=completion_criteria,
            allowed_transitions=(
                _transitions(skill, current_node) if frame.kind == "sop" else []
            ),
            memory_projection=_memory_projection(memory_context),
            prior_task_results=_project_records(prior_task_results),
            attachments=_project_attachments(attachments),
            capability_manifest=manifest.model_copy(deep=True),
        )


def _current_node(skill: Skill | None, step_id: str | None) -> dict[str, Any] | None:
    if skill is None:
        return None
    content = skill.content_json or {}
    resolved_step_id = str(step_id or content.get("start_node_id") or "").strip()
    for node in content.get("nodes") or content.get("steps") or []:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("node_id") or node.get("step_id") or "").strip()
        if node_id == resolved_step_id:
            return _project_value(node)
    return None


def _known_slots(frame: PlannedTaskFrame, session: ChatSession) -> dict[str, Any]:
    merged = {**dict(session.slots_json or {}), **dict(frame.slot_hints or {})}
    projected: dict[str, Any] = {}
    for raw_key, value in merged.items():
        key = str(raw_key).strip()
        if not key or key.startswith("_") or not _slot_satisfied(value):
            continue
        projected[key] = "<redacted>" if _is_sensitive_key(key) else _project_value(value)
    return projected


def _transitions(
    skill: Skill | None,
    current_node: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if skill is None or current_node is None:
        return []
    node_id = str(current_node.get("node_id") or current_node.get("step_id") or "").strip()
    transitions: list[dict[str, Any]] = []
    for edge in (skill.content_json or {}).get("edges") or []:
        if not isinstance(edge, dict):
            continue
        if str(edge.get("source_node_id") or "").strip() != node_id:
            continue
        transition = {
            key: _project_value(edge.get(key))
            for key in ("next_node_id", "condition", "label", "priority", "predicate_json")
            if edge.get(key) not in (None, "")
        }
        transitions.append(transition)
    return transitions


def _goal(
    frame: PlannedTaskFrame,
    skill: Skill | None,
    current_node: dict[str, Any] | None,
    requirements: list[str],
) -> str:
    node_name = str((current_node or {}).get("name") or "").strip()
    skill_name = str(getattr(skill, "name", "") or "").strip()
    if frame.kind == "sop" and (node_name or skill_name):
        return f"完成 {skill_name or 'SOP'} 的{node_name or '当前步骤'}。"
    user_intent = " ".join(str(frame.user_intent or "").split()).strip()
    if user_intent:
        return user_intent
    if requirements:
        return requirements[0]
    return "完成用户本轮请求。"


def _sop_context(skill: Skill | None, current_node: dict[str, Any] | None) -> dict[str, Any]:
    if skill is None:
        return {}
    return {
        "skill_id": skill.skill_id,
        "skill_name": skill.name,
        "step": current_node or {},
    }


def _memory_projection(
    memory_context: list[dict[str, object]] | None,
) -> list[dict[str, str]]:
    projected: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in memory_context or []:
        if not isinstance(item, dict):
            continue
        content = " ".join(str(item.get("content") or "").split()).strip()
        if not content or content in seen:
            continue
        seen.add(content)
        projected.append(
            {
                "kind": str(item.get("kind") or "memory")[:100],
                "content": content[:_MAX_MEMORY_CHARS],
            }
        )
        if len(projected) >= _MAX_COLLECTION_ITEMS:
            break
    return projected


def _project_records(records: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [
        _project_value(item)
        for item in (records or [])[:_MAX_COLLECTION_ITEMS]
        if isinstance(item, dict)
    ]


def _project_attachments(attachments: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    allowed = {
        "id",
        "filename",
        "content_type",
        "size",
        "kind",
        "text",
        "preview",
        "python_summary",
        "error",
    }
    return [
        {
            key: _project_value(value)
            for key, value in item.items()
            if key in allowed and value not in (None, "")
        }
        for item in (attachments or [])[:_MAX_COLLECTION_ITEMS]
        if isinstance(item, dict)
    ]


def _project_value(value: Any) -> Any:
    if isinstance(value, str):
        return value[:_MAX_PROJECTED_TEXT_CHARS]
    if isinstance(value, dict):
        return {
            str(key)[:200]: _project_value(item)
            for key, item in list(value.items())[:_MAX_COLLECTION_ITEMS]
        }
    if isinstance(value, list):
        return [_project_value(item) for item in value[:_MAX_COLLECTION_ITEMS]]
    if isinstance(value, tuple):
        return [_project_value(item) for item in value[:_MAX_COLLECTION_ITEMS]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:_MAX_PROJECTED_TEXT_CHARS]


def _is_sensitive_key(key: str) -> bool:
    normalized = re.sub(r"(?<!^)(?=[A-Z])", "_", key).lower()
    return bool(_SENSITIVE_KEY.search(normalized))


def _text_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return _unique([str(item or "") for item in value])


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = " ".join(str(value or "").split()).strip()
        if text and text not in result:
            result.append(text)
    return result


def _slot_satisfied(value: object) -> bool:
    return value not in (None, "", [], {})


__all__ = ["TaskRequestCompiler"]
