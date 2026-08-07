from __future__ import annotations

from copy import deepcopy

from app.core.harness_turn_planner import turn_plan_from_router_decision
from app.db.models import ChatSession
from app.session.session_schema import PendingTask, RouterDecision, TaskUpdate


def test_answer_only_stays_conversation_even_with_active_sop() -> None:
    session = ChatSession(
        id="session-1",
        tenant_id="tenant-demo",
        active_skill_id="amazon_research",
        active_step_id="research",
        slots_json={"asin": "A1"},
    )
    decision = RouterDecision(
        decision="answer_only",
        target_skill_id="amazon_research",
        target_step_id="research",
        user_intent="解释什么是 BSR",
        confidence=0.9,
    )

    plan = turn_plan_from_router_decision(
        decision,
        session,
        source_message="解释什么是 BSR",
        source_turn_id="turn-1",
    )

    assert len(plan.task_frames) == 1
    frame = plan.task_frames[0]
    assert frame.kind == "conversation"
    assert frame.target_skill_id is None
    assert frame.target_step_id is None
    assert frame.user_intent == "解释什么是 BSR"


def test_executable_router_tasks_preserve_order_and_sop_selection() -> None:
    session = ChatSession(id="session-1", tenant_id="tenant-demo")
    decision = RouterDecision(
        decision="start_new_task",
        target_skill_id="amazon_research",
        target_step_id="collect",
        user_intent="研究 A1",
        slot_hints={"asin": "A1"},
        task_frames=[
            PendingTask(
                task_id="research",
                decision="start_new_task",
                target_skill_id="amazon_research",
                target_step_id="collect",
                user_intent="研究 A1",
                slot_hints={"asin": "A1"},
            ),
            PendingTask(
                task_id="report",
                decision="start_new_task",
                target_skill_id="report_generation",
                target_step_id="draft",
                user_intent="生成报告",
            ),
        ],
        task_updates=[TaskUpdate(task_id="old", status="completed", reason="已完成")],
    )
    before = deepcopy(decision.model_dump(mode="json"))

    plan = turn_plan_from_router_decision(
        decision,
        session,
        source_message="研究 A1 并生成报告",
        source_turn_id="turn-2",
    )

    assert [frame.task_id for frame in plan.task_frames] == ["research", "report"]
    assert [frame.kind for frame in plan.task_frames] == ["sop", "sop"]
    assert plan.task_frames[0].slot_hints == {"asin": "A1"}
    assert plan.task_frames[0].source_message == "研究 A1 并生成报告"
    assert plan.task_updates[0].task_id == "old"
    assert plan.task_updates[0].status == "completed"
    assert decision.model_dump(mode="json") == before


def test_generated_task_id_is_stable_for_turn_and_index() -> None:
    session = ChatSession(id="session-1", tenant_id="tenant-demo")
    decision = RouterDecision(decision="answer_only", user_intent="你好")

    first = turn_plan_from_router_decision(
        decision,
        session,
        source_message="你好",
        source_turn_id="turn-stable",
    )
    second = turn_plan_from_router_decision(
        decision,
        session,
        source_message="你好",
        source_turn_id="turn-stable",
    )

    assert first.task_frames[0].task_id == second.task_frames[0].task_id
    assert first.task_frames[0].task_id.startswith("task_")


def test_clarify_and_handoff_do_not_start_a_runtime_frame() -> None:
    session = ChatSession(id="session-1", tenant_id="tenant-demo")

    clarify = turn_plan_from_router_decision(
        RouterDecision(
            decision="clarify",
            clarification_question="你想研究哪个站点？",
        ),
        session,
        source_message="帮我研究",
        source_turn_id="turn-clarify",
    )
    handoff = turn_plan_from_router_decision(
        RouterDecision(decision="handoff_human", reason="用户要求人工"),
        session,
        source_message="转人工",
        source_turn_id="turn-handoff",
    )

    assert clarify.task_frames == []
    assert clarify.clarification_question == "你想研究哪个站点？"
    assert handoff.task_frames == []
