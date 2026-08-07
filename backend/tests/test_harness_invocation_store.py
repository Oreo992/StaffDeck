from __future__ import annotations

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.core.harness_invocation_store import (
    HarnessInvocationConflict,
    HarnessInvocationStore,
    logical_action_key,
)
from app.db.models import HarnessInvocationRecord


def _engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _claim(store: HarnessInvocationStore, *, call_id: str, action_key: str):
    return store.claim(
        tenant_id="tenant-demo",
        session_id="session-1",
        task_id="task-1",
        run_id="run-1",
        call_id=call_id,
        tool_name="orders.create",
        arguments={"order_id": "O-1"},
        logical_action_key=action_key,
    )


def test_completed_logical_action_replays_without_second_invocation() -> None:
    engine = _engine()
    with Session(engine) as db:
        store = HarnessInvocationStore(db)
        first = _claim(store, call_id="call-1", action_key="action-1")
        assert first.record is not None and first.replay is None
        store.finish(first.record, {"success": True, "data": {"order_id": "O-1"}})

        replay = _claim(store, call_id="call-2", action_key="action-1")

        assert replay.record is not None
        assert replay.replay == {
            "success": True,
            "data": {
                "order_id": "O-1",
                "idempotent_replay": True,
                "replayed_from_invocation_id": first.record.id,
            },
            "idempotent_replay": True,
        }
        assert len(db.exec(select(HarnessInvocationRecord)).all()) == 1


def test_unknown_outcome_blocks_retry_but_not_sent_failure_releases_action() -> None:
    engine = _engine()
    with Session(engine) as db:
        store = HarnessInvocationStore(db)
        uncertain = _claim(store, call_id="call-1", action_key="action-1")
        assert uncertain.record is not None
        store.finish(
            uncertain.record,
            {"success": False, "error": {"code": "TIMEOUT", "message": "unknown"}},
        )
        with pytest.raises(HarnessInvocationConflict, match="不会自动重试"):
            _claim(store, call_id="call-2", action_key="action-1")

        safe_failure = _claim(store, call_id="call-3", action_key="action-2")
        assert safe_failure.record is not None
        store.finish(
            safe_failure.record,
            {"success": False, "error": {"code": "NOT_ALLOWED", "message": "denied"}},
            definitely_not_sent=True,
        )
        replacement = _claim(store, call_id="call-4", action_key="action-2")
        assert replacement.record is not None
        assert replacement.record.id != safe_failure.record.id


def test_same_run_call_id_with_different_request_is_rejected() -> None:
    engine = _engine()
    with Session(engine) as db:
        store = HarnessInvocationStore(db)
        first = _claim(store, call_id="call-1", action_key="action-1")
        assert first.record is not None

        with pytest.raises(HarnessInvocationConflict, match="call_id"):
            store.claim(
                tenant_id="tenant-demo",
                session_id="session-1",
                task_id="task-1",
                run_id="run-1",
                call_id="call-1",
                tool_name="orders.create",
                arguments={"order_id": "O-2"},
                logical_action_key="action-2",
            )


def test_logical_action_key_is_scoped_to_task_step_tool_and_key_arguments() -> None:
    first = logical_action_key(
        tenant_id="tenant-demo",
        task_frame_id="task-1",
        step_id="submit",
        tool_id="tool-orders",
        tool_name="orders.create",
        arguments={"order_id": "O-1", "note": "first"},
        key_fields=["order_id"],
    )
    same = logical_action_key(
        tenant_id="tenant-demo",
        task_frame_id="task-1",
        step_id="submit",
        tool_id="tool-orders",
        tool_name="orders.create",
        arguments={"note": "changed", "order_id": "O-1"},
        key_fields=["order_id"],
    )
    different_task = logical_action_key(
        tenant_id="tenant-demo",
        task_frame_id="task-2",
        step_id="submit",
        tool_id="tool-orders",
        tool_name="orders.create",
        arguments={"order_id": "O-1"},
        key_fields=["order_id"],
    )

    assert first == same
    assert first != different_task
