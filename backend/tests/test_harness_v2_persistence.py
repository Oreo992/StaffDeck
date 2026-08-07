from __future__ import annotations

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

from app.core.harness_session_lease import HarnessSessionLeaseStore
from app.core.harness_session_lock import HarnessSessionBusy
from app.core.harness_turn_store import HarnessTurnConflict, HarnessTurnStore
from app.db import database
from app.db.models import ChatSession, HarnessInvocationRecord
from app.session.session_schema import ChatTurnRequest, ChatTurnResponse, SessionPublic


def _memory_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _request(message: str = "hello") -> ChatTurnRequest:
    return ChatTurnRequest(
        tenant_id="tenant-demo",
        session_id="session-1",
        client_turn_id="client-turn-1",
        message=message,
    )


def test_harness_schema_initialization_is_idempotent_and_preserves_existing_data(
    monkeypatch,
    tmp_path,
) -> None:
    db_path = tmp_path / "harness-lifecycle.db"
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE legacy_marker (id VARCHAR PRIMARY KEY, value VARCHAR)"))
        conn.execute(text("INSERT INTO legacy_marker VALUES ('keep', 'unchanged')"))
    monkeypatch.setattr(database, "database_url", f"sqlite:///{db_path}")
    monkeypatch.setattr(database, "engine", engine)

    database.init_db()
    database.init_db()

    tables = set(inspect(engine).get_table_names())
    assert {
        "harness_task_frames",
        "harness_runs",
        "harness_turns",
        "harness_session_leases",
        "harness_invocations",
    } <= tables
    with engine.begin() as conn:
        assert conn.execute(text("SELECT value FROM legacy_marker WHERE id = 'keep'")).scalar_one() == (
            "unchanged"
        )


def test_turn_receipt_replays_completed_response_and_rejects_mismatched_reuse() -> None:
    engine = _memory_engine()
    with Session(engine) as db:
        session = ChatSession(id="session-1", tenant_id="tenant-demo")
        db.add(session)
        db.commit()
        store = HarnessTurnStore(db)

        claim = store.claim(session, _request())
        assert claim.record is not None
        expected = ChatTurnResponse(
            reply="done",
            session_id=session.id,
            session_state=SessionPublic(
                session_id=session.id,
                tenant_id=session.tenant_id,
            ),
        )
        store.complete(claim.record, expected)

        assert store.claim(session, _request()).replay == expected
        try:
            store.claim(session, _request("different"))
        except HarnessTurnConflict as exc:
            assert "不能用于不同" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("mismatched client_turn_id reuse was not blocked")


def test_session_lease_blocks_parallel_owner_and_can_be_reacquired() -> None:
    engine = _memory_engine()
    with Session(engine) as first_db, Session(engine) as second_db:
        session = ChatSession(id="session-1", tenant_id="tenant-demo")
        first_db.add(session)
        first_db.commit()
        first = HarnessSessionLeaseStore(first_db)
        second = HarnessSessionLeaseStore(second_db)

        lease = first.acquire(session)
        try:
            second.acquire(session)
        except HarnessSessionBusy:
            pass
        else:  # pragma: no cover
            raise AssertionError("parallel session lease was not blocked")

        first.release(lease)
        replacement = second.acquire(session)
        assert replacement.lease_owner != lease.lease_owner
        second.release(replacement)


def test_invocation_uniqueness_blocks_duplicate_call_and_logical_action() -> None:
    engine = _memory_engine()
    base = {
        "tenant_id": "tenant-demo",
        "session_id": "session-1",
        "task_id": "task-1",
        "run_id": "run-1",
        "tool_name": "send_file",
        "request_digest": "sha256:request",
    }
    with Session(engine) as db:
        db.add(
            HarnessInvocationRecord(
                **base,
                call_id="call-1",
                logical_action_key="send-file:report",
            )
        )
        db.commit()

        db.add(HarnessInvocationRecord(**base, call_id="call-1"))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
        else:  # pragma: no cover
            raise AssertionError("duplicate run/call invocation was not blocked")

        db.add(
            HarnessInvocationRecord(
                **base,
                call_id="call-2",
                logical_action_key="send-file:report",
            )
        )
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
        else:  # pragma: no cover
            raise AssertionError("duplicate logical action was not blocked")
