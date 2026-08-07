from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, select

from app.core.harness_shadow_recorder import HarnessShadowRecorder
from app.db.models import ChatSession, HarnessTaskFrameRecord, HarnessTurnRecord
from app.session.session_schema import (
    ChatTurnRequest,
    ChatTurnResponse,
    PendingTask,
    RouterDecision,
    SessionPublic,
)


def _memory_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _request() -> ChatTurnRequest:
    return ChatTurnRequest(
        tenant_id="tenant-demo",
        session_id="session-1",
        client_turn_id="client-turn-1",
        message="按流程分析 A1",
    )


def _response() -> ChatTurnResponse:
    return ChatTurnResponse(
        reply="分析完成",
        session_id="session-1",
        router_decision=RouterDecision(
            decision="start_new_task",
            task_frames=[
                PendingTask(
                    task_id="task-amazon-1",
                    target_skill_id="amazon-research",
                    target_step_id="collect-market-data",
                    user_intent="分析 A1",
                    slot_hints={"asin": "A1"},
                )
            ],
        ),
        session_state=SessionPublic(
            session_id="session-1",
            tenant_id="tenant-demo",
            active_skill_id="amazon-research",
            active_step_id="collect-market-data",
        ),
    )


def test_shadow_recorder_is_disabled_without_opening_a_database_session() -> None:
    opened = False

    def fail_if_opened() -> Session:
        nonlocal opened
        opened = True
        raise AssertionError("disabled shadow recorder must not open a Session")

    recorder = HarnessShadowRecorder(fail_if_opened, enabled=False)

    assert recorder.record_completed_turn(_request(), _response()) is False
    assert opened is False


def test_shadow_recorder_persists_turn_and_task_frame_in_independent_session() -> None:
    engine = _memory_engine()
    with Session(engine) as primary_db:
        primary_db.add(ChatSession(id="session-1", tenant_id="tenant-demo"))
        primary_db.commit()

    opened_sessions = 0

    def shadow_session() -> Session:
        nonlocal opened_sessions
        opened_sessions += 1
        return Session(engine)

    recorder = HarnessShadowRecorder(shadow_session, enabled=True)

    assert recorder.record_completed_turn(_request(), _response()) is True
    assert recorder.record_completed_turn(_request(), _response()) is True
    assert opened_sessions == 2

    with Session(engine) as verify_db:
        turns = verify_db.exec(select(HarnessTurnRecord)).all()
        frames = verify_db.exec(select(HarnessTaskFrameRecord)).all()

    assert len(turns) == 1
    assert turns[0].status == "completed"
    assert turns[0].response_json["reply"] == "分析完成"
    assert len(frames) == 1
    assert frames[0].task_id == "task-amazon-1"
    assert frames[0].kind == "sop"
    assert frames[0].status == "running"
    assert frames[0].slots_json == {"asin": "A1"}


def test_shadow_recorder_skips_missing_client_turn_id() -> None:
    recorder = HarnessShadowRecorder(
        lambda: (_ for _ in ()).throw(AssertionError("must not open")),
        enabled=True,
    )
    request = _request().model_copy(update={"client_turn_id": None})

    assert recorder.record_completed_turn(request, _response()) is False


def test_shadow_recorder_swallows_database_failures() -> None:
    def broken_session() -> Session:
        raise RuntimeError("shadow database unavailable")

    recorder = HarnessShadowRecorder(broken_session, enabled=True)

    assert recorder.record_completed_turn(_request(), _response()) is False
