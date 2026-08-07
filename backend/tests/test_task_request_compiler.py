from __future__ import annotations

from copy import deepcopy

from app.capabilities.contracts import CapabilityDescriptor, CapabilityManifest
from app.core.task_request_compiler import TaskRequestCompiler
from app.db.models import ChatSession, Skill
from app.harness.task_request import TaskExecutionResult
from app.harness.task_schema import PlannedTaskFrame


def _manifest() -> CapabilityManifest:
    return CapabilityManifest(
        available=[
            CapabilityDescriptor(
                capability_id="tool_lookup",
                name="catalog.lookup",
                kind="tool",
                effect_level="read",
            )
        ],
        snapshot_revision="revision-1",
    )


def _skill() -> Skill:
    return Skill(
        tenant_id="tenant_demo",
        skill_id="amazon_research",
        name="Amazon 选品研究",
        status="published",
        content_json={
            "goal": ["形成可复核的选品结论"],
            "required_info": ["marketplace"],
            "nodes": [
                {
                    "node_id": "classify",
                    "name": "确认研究范围",
                    "instruction": "识别站点和研究类型。",
                    "expected_user_info": ["marketplace", "request_type"],
                },
                {"node_id": "price", "name": "查询价格"},
                {"node_id": "trend", "name": "分析趋势"},
            ],
            "edges": [
                {
                    "source_node_id": "classify",
                    "next_node_id": "price",
                    "predicate_json": {
                        "slot": "request_type",
                        "op": "eq",
                        "value": "price",
                    },
                    "priority": 10,
                },
                {
                    "source_node_id": "classify",
                    "next_node_id": "trend",
                    "condition": "default",
                    "priority": 0,
                },
            ],
            "start_node_id": "classify",
            "terminal_node_ids": ["price", "trend"],
        },
    )


def test_compile_sop_request_preserves_deterministic_graph_contract() -> None:
    session = ChatSession(
        id="session_test",
        tenant_id="tenant_demo",
        active_skill_id="amazon_research",
        active_step_id="classify",
        slots_json={"marketplace": "US"},
    )
    frame = PlannedTaskFrame(
        task_id="task_1",
        kind="sop",
        target_skill_id="amazon_research",
        target_step_id="classify",
        user_intent="帮我做美国站选品分析",
        requirements=["给出证据来源"],
    )

    result = TaskRequestCompiler().compile(frame, session, _skill(), _manifest())

    assert result.goal == "完成 Amazon 选品研究 的确认研究范围。"
    assert result.required_slots == ["request_type"]
    assert result.known_slots == {"marketplace": "US"}
    assert result.requirements == [
        "识别站点和研究类型。",
        "补齐以下字段：request_type",
        "给出证据来源",
    ]
    assert result.allowed_transitions[0]["predicate_json"] == {
        "slot": "request_type",
        "op": "eq",
        "value": "price",
    }
    assert result.allowed_transitions[1]["condition"] == "default"
    assert result.capability_manifest.snapshot_revision == "revision-1"


def test_compile_does_not_mutate_session_and_redacts_sensitive_slots() -> None:
    session = ChatSession(
        id="session_test",
        tenant_id="tenant_demo",
        slots_json={
            "marketplace": "US",
            "api_key": "top-secret",
            "accessToken": "secret-token",
            "_runtime_checkpoint": "private",
        },
    )
    before = deepcopy(session.slots_json)
    frame = PlannedTaskFrame(task_id="task_1", kind="sop", target_step_id="classify")

    result = TaskRequestCompiler().compile(frame, session, _skill(), _manifest())

    assert session.slots_json == before
    assert result.known_slots == {
        "marketplace": "US",
        "api_key": "<redacted>",
        "accessToken": "<redacted>",
    }


def test_conversation_request_does_not_inherit_sop_slots() -> None:
    session = ChatSession(
        id="session_test",
        tenant_id="tenant_demo",
        active_skill_id="amazon_research",
        active_step_id="classify",
        slots_json={"marketplace": "US"},
    )
    frame = PlannedTaskFrame(
        task_id="task_chat",
        kind="conversation",
        user_intent="解释什么是 BSR",
        requirements=["使用通俗中文"],
    )

    result = TaskRequestCompiler().compile(
        frame,
        session,
        None,
        _manifest(),
        source_user_message="解释什么是 BSR",
    )

    assert result.goal == "解释什么是 BSR"
    assert result.known_slots == {}
    assert result.required_slots == []
    assert result.sop_context == {}


def test_compile_projects_memory_attachments_and_prior_results_safely() -> None:
    session = ChatSession(id="session_test", tenant_id="tenant_demo")
    frame = PlannedTaskFrame(task_id="task_chat", kind="conversation")
    oversized = "x" * 12_000
    attachment = {
        "id": "att_1",
        "filename": "report.html",
        "content_type": "text/html",
        "size": len(oversized),
        "kind": "text",
        "text": oversized,
        "preview": "preview",
        "data_url": "data:text/html;base64,do-not-copy",
    }

    result = TaskRequestCompiler().compile(
        frame,
        session,
        None,
        _manifest(),
        memory_context=[
            {"kind": "profile", "content": "  用户偏好 简洁  "},
            {"kind": "duplicate", "content": "用户偏好 简洁"},
            {"kind": "long", "content": oversized},
        ],
        prior_task_results=[{"task_id": "prior", "result": oversized}],
        attachments=[attachment],
        source_user_message=oversized,
    )

    assert len(result.source_user_message) == 4_000
    assert len(result.memory_projection) == 2
    assert len(result.memory_projection[1]["content"]) == 1_000
    assert "data_url" not in result.attachments[0]
    assert len(result.attachments[0]["text"]) == 8_000
    assert len(result.prior_task_results[0]["result"]) == 8_000


def test_task_execution_result_has_closed_status_contract() -> None:
    result = TaskExecutionResult(
        task_frame_id="task_1",
        status="awaiting_user",
        reply_fragment="请补充站点。",
    )

    assert result.status == "awaiting_user"
