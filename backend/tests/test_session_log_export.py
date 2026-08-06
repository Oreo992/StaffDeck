import json

import pytest
from fastapi import HTTPException
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.api.sessions import SessionLogExportRequest, export_session_log, export_session_logs
from app.db.models import AgentEvent, ChatSession, Message, Tenant, User


def _test_session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _seed(db: Session) -> User:
    db.add(Tenant(id="tenant_demo", name="Demo"))
    user = User(
        id="user_demo",
        tenant_id="tenant_demo",
        username="demo",
        password_hash="hashed",
    )
    db.add(user)
    db.add(ChatSession(id="session_one", tenant_id="tenant_demo", user_id=user.id))
    db.add(Message(tenant_id="tenant_demo", session_id="session_one", role="user", content="你好"))
    db.add(
        AgentEvent(
            tenant_id="tenant_demo",
            session_id="session_one",
            event_type="router_decision_created",
            payload_json={"decision": "answer_only"},
        )
    )
    db.commit()
    return user


def test_single_and_batch_session_log_exports_are_downloadable_json() -> None:
    with _test_session() as db:
        user = _seed(db)

        single = export_session_log("session_one", "tenant_demo", user, db)
        batch = export_session_logs(
            SessionLogExportRequest(session_ids=["session_one", "session_one"]),
            "tenant_demo",
            user,
            db,
        )

        single_payload = json.loads(single.body)
        batch_payload = json.loads(batch.body)
        assert single.media_type == "application/json"
        assert "attachment;" in single.headers["content-disposition"]
        assert single_payload["schema_version"] == "staffdeck.conversation-log.v1"
        assert single_payload["item"]["messages"][0]["content"] == "你好"
        assert batch_payload["count"] == 1
        assert batch_payload["items"][0]["events"][0]["event_type"] == "router_decision_created"


def test_session_log_export_does_not_cross_user_boundary() -> None:
    with _test_session() as db:
        _seed(db)
        other = User(
            id="user_other",
            tenant_id="tenant_demo",
            username="other",
            password_hash="hashed",
        )
        db.add(other)
        db.commit()

        with pytest.raises(HTTPException) as exc_info:
            export_session_log("session_one", "tenant_demo", other, db)

        assert exc_info.value.status_code == 404
