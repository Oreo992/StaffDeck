from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.db.models import Tool
from app.runtime.contracts import (
    ExecutionSegment,
    HarnessStructuredOutput,
    RepairContract,
    SopAuditOutcome,
    SopAuditResult,
    ToolEffectLevel,
)


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "enabled"}


def tool_effect_level(tool: Tool) -> str:
    explicit = str(tool.effect_level or "").strip().lower()
    if explicit in {item.value for item in ToolEffectLevel}:
        return explicit
    config = tool.config_json if isinstance(tool.config_json, dict) else {}
    schema = tool.input_schema if isinstance(tool.input_schema, dict) else {}
    if _truthy(config.get("requires_confirmation", schema.get("requires_confirmation"))):
        return ToolEffectLevel.DESTRUCTIVE.value
    if str(tool.method or "").upper() == "GET":
        return ToolEffectLevel.READ.value
    return ToolEffectLevel.WRITE.value


@dataclass(slots=True)
class EvidenceLedger:
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    approvals: set[str] = field(default_factory=set)
    knowledge_refs: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)

    def record_tool_result(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        success: bool,
        data: Any = None,
        error: Any = None,
    ) -> None:
        self.tool_results.append(
            {
                "tool_name": tool_name,
                "arguments": dict(arguments),
                "success": success,
                "data": data,
                "error": error,
            }
        )

    def has_successful_tool(self, tool_name: str) -> bool:
        return any(
            item.get("tool_name") == tool_name and item.get("success") is True
            for item in self.tool_results
        )

    def has_knowledge_evidence(self) -> bool:
        return bool(self.knowledge_refs)


class SopSupervisor:
    """Compiles bounded Claude work and reconciles claims against objective evidence."""

    def compile_segment(
        self,
        skill_content: dict[str, Any],
        active_step_id: str,
        slots: dict[str, Any],
        tools: list[Tool],
    ) -> ExecutionSegment:
        nodes = [item for item in skill_content.get("nodes", []) if isinstance(item, dict)]
        nodes_by_id = {str(item.get("node_id") or ""): item for item in nodes}
        tool_by_name = {tool.name: tool for tool in tools}
        skill_id = str(skill_content.get("skill_id") or "")
        current_id = active_step_id
        selected_nodes: list[dict[str, Any]] = []
        selected_tools: list[str] = []
        visited: set[str] = set()
        boundary = "checkpoint"
        next_step_id: str | None = None
        requires_approval = False

        while current_id and current_id not in visited:
            node = nodes_by_id.get(current_id)
            if not node:
                boundary = "invalid_graph"
                break
            visited.add(current_id)
            if self._is_handoff_node(node):
                if selected_nodes:
                    boundary = "handoff"
                    next_step_id = current_id
                    break
                selected_nodes.append(node)
                boundary = "handoff"
                break
            if self._requires_human_confirmation(node, slots):
                if selected_nodes:
                    boundary = "human_confirmation"
                    next_step_id = current_id
                    break
                selected_nodes.append(node)
                boundary = "human_confirmation"
                break
            node_tool_names = self._node_tool_names(node)
            node_effects = [
                tool_effect_level(tool_by_name[name])
                for name in node_tool_names
                if name in tool_by_name
            ]
            node_has_side_effect = any(effect != ToolEffectLevel.READ.value for effect in node_effects)
            if node_has_side_effect and selected_nodes:
                boundary = "side_effect"
                next_step_id = current_id
                break
            selected_nodes.append(node)
            for name in node_tool_names:
                if name in tool_by_name and name not in selected_tools:
                    selected_tools.append(name)
            if node_has_side_effect:
                boundary = "side_effect"
                requires_approval = True
                break

            outgoing = self._outgoing(skill_content, current_id)
            if not outgoing:
                boundary = (
                    "terminal"
                    if current_id in {str(item) for item in skill_content.get("terminal_node_ids", [])}
                    else "checkpoint"
                )
                break
            target = self._select_edge_target(outgoing, slots)
            if not target:
                boundary = "branch"
                break
            target_node = nodes_by_id.get(target)
            if not target_node:
                boundary = "invalid_graph"
                break
            if self._is_handoff_node(target_node):
                boundary = "handoff"
                next_step_id = target
                break
            if self._requires_human_confirmation(target_node, slots):
                boundary = "human_confirmation"
                next_step_id = target
                break
            target_effects = [
                tool_effect_level(tool_by_name[name])
                for name in self._node_tool_names(target_node)
                if name in tool_by_name
            ]
            if any(effect != ToolEffectLevel.READ.value for effect in target_effects):
                boundary = "side_effect"
                next_step_id = target
                break
            current_id = target

        return ExecutionSegment(
            skill_id=skill_id,
            start_step_id=active_step_id,
            node_ids=[str(item.get("node_id") or "") for item in selected_nodes],
            nodes=[dict(item) for item in selected_nodes],
            allowed_tool_names=selected_tools,
            boundary=boundary,
            next_step_id=next_step_id,
            requires_approval=requires_approval,
        )

    def audit(
        self,
        segment: ExecutionSegment,
        skill_content: dict[str, Any],
        slots: dict[str, Any],
        output: HarnessStructuredOutput,
        evidence: EvidenceLedger,
        *,
        attempt: int,
        max_repairs: int,
    ) -> SopAuditResult:
        allowed_slot_names = {
            str(item or "").strip()
            for item in skill_content.get("required_info", [])
            if str(item or "").strip()
        }
        for node in skill_content.get("nodes", []):
            if not isinstance(node, dict):
                continue
            allowed_slot_names.update(
                str(item or "").strip()
                for item in node.get("expected_user_info", [])
                if str(item or "").strip()
            )
        accepted_slot_updates = {
            key: value
            for key, value in output.slot_updates.items()
            if key in allowed_slot_names
        }
        merged_slots = {**slots, **accepted_slot_updates}
        missing: list[str] = []
        completed: list[str] = []
        for node in segment.nodes:
            node_id = str(node.get("node_id") or "")
            node_missing: list[str] = []
            for field_name in node.get("expected_user_info", []):
                field_key = str(field_name)
                if not self._has_slot(merged_slots, field_key):
                    node_missing.append(f"slot:{field_key}")
            for tool_name in self._node_tool_names(node):
                if not evidence.has_successful_tool(tool_name):
                    node_missing.append(f"tool:{tool_name}")
            if self._is_knowledge_node(node) and not evidence.has_knowledge_evidence():
                node_missing.append(f"knowledge:{node_id}")
            if node_missing:
                missing.extend(item for item in node_missing if item not in missing)
            else:
                completed.append(node_id)

        if segment.requires_approval:
            for tool_name in segment.allowed_tool_names:
                if tool_name not in evidence.approvals:
                    marker = f"approval:{tool_name}"
                    if marker not in missing:
                        missing.append(marker)

        active_step_id = segment.node_ids[-1] if segment.node_ids else segment.start_step_id
        if output.needs_user_input and missing:
            return SopAuditResult(
                outcome=SopAuditOutcome.BLOCKED,
                active_step_id=active_step_id,
                missing_evidence=missing,
                completed_step_ids=completed,
            )
        if missing:
            if any(item.startswith("approval:") for item in missing):
                outcome = SopAuditOutcome.AWAITING_APPROVAL
            elif attempt >= max_repairs:
                outcome = SopAuditOutcome.FAILED
            else:
                outcome = SopAuditOutcome.REPAIR
            repair = None
            if outcome == SopAuditOutcome.REPAIR:
                repair = RepairContract(
                    required_steps=[item for item in segment.node_ids if item not in completed],
                    missing_evidence=missing,
                    allowed_tools=segment.allowed_tool_names,
                    completed_steps=completed,
                    attempt=attempt + 1,
                )
            return SopAuditResult(
                outcome=outcome,
                active_step_id=active_step_id,
                missing_evidence=missing,
                repair_contract=repair,
                completed_step_ids=completed,
            )

        next_step_id = segment.next_step_id
        if not next_step_id:
            outgoing = self._outgoing(skill_content, active_step_id)
            next_step_id = self._select_edge_target(outgoing, merged_slots)
        if segment.boundary == "branch" and not next_step_id:
            marker = f"branch:{active_step_id}"
            if attempt >= max_repairs:
                outcome = SopAuditOutcome.FAILED
                repair = None
            else:
                outcome = SopAuditOutcome.REPAIR
                repair = RepairContract(
                    required_steps=[active_step_id],
                    missing_evidence=[marker],
                    allowed_tools=segment.allowed_tool_names,
                    completed_steps=completed,
                    attempt=attempt + 1,
                )
            return SopAuditResult(
                outcome=outcome,
                active_step_id=active_step_id,
                missing_evidence=[marker],
                repair_contract=repair,
                completed_step_ids=completed,
            )
        return SopAuditResult(
            outcome=SopAuditOutcome.PASSED,
            active_step_id=active_step_id,
            completed_step_ids=completed,
            next_step_id=next_step_id,
            accepted_slot_updates=accepted_slot_updates,
        )

    def _outgoing(self, skill_content: dict[str, Any], node_id: str) -> list[dict[str, Any]]:
        edges = [
            item
            for item in skill_content.get("edges", [])
            if isinstance(item, dict) and str(item.get("source_node_id") or "") == node_id
        ]
        return sorted(edges, key=lambda item: int(item.get("priority") or 0))

    def _select_edge_target(
        self, edges: list[dict[str, Any]], slots: dict[str, Any]
    ) -> str | None:
        if not edges:
            return None
        matched: list[str] = []
        defaults: list[str] = []
        for edge in edges:
            target = str(edge.get("next_node_id") or "").strip()
            predicate = edge.get("predicate_json")
            if isinstance(predicate, dict):
                if self._predicate_matches(predicate, slots):
                    matched.append(target)
                continue
            condition = str(edge.get("condition") or "").strip().lower()
            if not condition or condition in {"default", "else"}:
                defaults.append(target)
        if len(matched) == 1:
            return matched[0]
        if not matched and len(edges) == 1:
            return str(edges[0].get("next_node_id") or "").strip() or None
        if not matched and len(defaults) == 1:
            return defaults[0]
        return None

    def _predicate_matches(self, predicate: dict[str, Any], slots: dict[str, Any]) -> bool:
        slot = str(predicate.get("slot") or "").strip()
        op = str(predicate.get("op") or "eq").strip().lower()
        value = slots.get(slot)
        expected = predicate.get("value")
        if not slot:
            return False
        if op == "exists":
            return self._has_slot(slots, slot) is bool(expected if expected is not None else True)
        if op == "in":
            return isinstance(expected, list) and value in expected
        if op == "eq":
            return value == expected
        return False

    def _node_tool_names(self, node: dict[str, Any]) -> list[str]:
        names: list[str] = []
        for action in node.get("allowed_actions", []):
            normalized = str(action or "").strip()
            if normalized.startswith("call_tool:"):
                name = normalized.split(":", 1)[1].strip()
                if name and name not in names:
                    names.append(name)
        return names

    def _is_knowledge_node(self, node: dict[str, Any]) -> bool:
        actions = {str(item or "").strip() for item in node.get("allowed_actions", [])}
        return str(node.get("type") or "").strip() == "knowledge_query" or bool(
            actions & {"knowledge_query", "query_knowledge"}
        )

    def _is_handoff_node(self, node: dict[str, Any]) -> bool:
        actions = {str(item or "").strip() for item in node.get("allowed_actions", [])}
        return str(node.get("type") or "").strip() == "handoff" or "handoff_human" in actions

    def _requires_human_confirmation(
        self, node: dict[str, Any], slots: dict[str, Any]
    ) -> bool:
        expected = {str(item or "").strip() for item in node.get("expected_user_info", [])}
        return "confirmation" in expected and not self._has_slot(slots, "confirmation")

    def _has_slot(self, slots: dict[str, Any], name: str) -> bool:
        value = slots.get(name)
        return value is not None and value != ""
