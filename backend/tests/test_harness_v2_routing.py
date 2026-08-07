from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

from app.api.chat import session_read
from app.api.ui_config import UIConfigUpdateRequest, update_enterprise_ui_config
from app.core.harness_v2_routing import select_harness_v2_canary
from app.db import database
from app.db.models import ChatSession, Tenant, UIConfig, User


def _admin() -> User:
    return User(
        id="user-admin",
        tenant_id="tenant-demo",
        username="admin",
        role="admin",
        password_hash="x",
    )


def _config(*, enabled: bool = True) -> UIConfig:
    return UIConfig(
        tenant_id="tenant-demo",
        harness_v2_enabled=enabled,
        harness_v2_agent_allowlist_json=["agent-canary"],
    )


def _session(runtime_mode: str = "legacy") -> ChatSession:
    return ChatSession(
        id="session-1",
        tenant_id="tenant-demo",
        agent_id="agent-canary",
        runtime_mode=runtime_mode,
    )


def test_canary_selects_only_allowlisted_new_legacy_session() -> None:
    selected = select_harness_v2_canary(
        _config(),
        _session(),
        agent_id="agent-canary",
        is_new_session=True,
    )
    existing = select_harness_v2_canary(
        _config(),
        _session(),
        agent_id="agent-canary",
        is_new_session=False,
    )
    claude = select_harness_v2_canary(
        _config(),
        _session("claude_supervised"),
        agent_id="agent-canary",
        is_new_session=True,
    )

    assert selected.selected is True
    assert existing.reason == "existing_session_compatibility"
    assert claude.reason == "runtime_mode_not_legacy"


def test_canary_fails_closed_when_disabled_or_agent_is_not_allowlisted() -> None:
    disabled = select_harness_v2_canary(
        _config(enabled=False),
        _session(),
        agent_id="agent-canary",
        is_new_session=True,
    )
    denied = select_harness_v2_canary(
        _config(),
        _session(),
        agent_id="agent-other",
        is_new_session=True,
    )

    assert disabled.reason == "harness_v2_disabled"
    assert denied.reason == "agent_not_allowlisted"


def test_canary_selection_is_sticky_for_existing_harness_session() -> None:
    session = _session()
    session.runtime_state_json = {"execution_engine": "harness_v2"}

    selected = select_harness_v2_canary(
        _config(),
        session,
        agent_id="agent-canary",
        is_new_session=False,
    )

    assert selected.selected is True
    assert selected.reason == "session_locked_to_harness_v2"


def test_session_read_exposes_the_locked_execution_engine() -> None:
    session = _session()
    session.runtime_state_json = {"execution_engine": "harness_v2"}

    assert session_read(session).execution_engine == "harness_v2"


def test_admin_cannot_enable_canary_without_agent_allowlist() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Tenant(id="tenant-demo", name="Demo"))
        db.commit()

        with pytest.raises(HTTPException) as exc_info:
            update_enterprise_ui_config(
                UIConfigUpdateRequest(
                    tenant_id="tenant-demo",
                    harness_v2_enabled=True,
                    harness_v2_agent_allowlist=[],
                ),
                db=db,
                current_user=_admin(),
            )

    assert exc_info.value.status_code == 400


def test_existing_settings_update_does_not_disable_omitted_canary_fields() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Tenant(id="tenant-demo", name="Demo"))
        db.add(_config())
        db.commit()

        result = update_enterprise_ui_config(
            UIConfigUpdateRequest(tenant_id="tenant-demo"),
            db=db,
            current_user=_admin(),
        )

    assert result.harness_v2_enabled is True
    assert result.harness_v2_agent_allowlist == ["agent-canary"]


def test_canary_columns_migrate_idempotently_on_existing_ui_config(
    monkeypatch,
    tmp_path,
) -> None:
    db_path = tmp_path / "old-ui-config.db"
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE ui_configs ("
                "tenant_id VARCHAR PRIMARY KEY, "
                "show_thinking_trace BOOLEAN NOT NULL DEFAULT 1, "
                "show_skill_trace BOOLEAN NOT NULL DEFAULT 1, "
                "show_tool_trace BOOLEAN NOT NULL DEFAULT 1"
                ")"
            )
        )
        conn.execute(text("INSERT INTO ui_configs (tenant_id) VALUES ('tenant-demo')"))
    monkeypatch.setattr(database, "database_url", f"sqlite:///{db_path}")
    monkeypatch.setattr(database, "engine", engine)

    database.init_db()
    database.init_db()

    columns = {item["name"] for item in inspect(engine).get_columns("ui_configs")}
    with engine.begin() as conn:
        enabled = conn.execute(
            text(
                "SELECT harness_v2_enabled FROM ui_configs "
                "WHERE tenant_id = 'tenant-demo'"
            )
        ).scalar_one()

    assert {"harness_v2_enabled", "harness_v2_agent_allowlist_json"} <= columns
    assert enabled == 0
