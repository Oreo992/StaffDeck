from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.api.ui_config import UIConfigUpdateRequest, update_enterprise_ui_config
from app.db.models import ModelConfig, Tenant, User


def _test_session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _admin() -> User:
    return User(
        id="admin",
        tenant_id="tenant_demo",
        username="admin",
        password_hash="x",
        role="admin",
    )


def test_admin_can_enable_claude_runtime_with_model_and_skill_allowlist() -> None:
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.add(
            ModelConfig(
                id="model_claude",
                tenant_id="tenant_demo",
                name="Claude",
                provider="claude_agent_sdk",
                api_key_encrypted="encrypted",
                model="claude-test",
                enabled=True,
            )
        )
        db.commit()

        result = update_enterprise_ui_config(
            UIConfigUpdateRequest(
                tenant_id="tenant_demo",
                claude_runtime_enabled=True,
                claude_model_config_id="model_claude",
                claude_skill_allowlist=["graph_demo", "graph_demo"],
                claude_max_repair_rounds=2,
            ),
            db=db,
            current_user=_admin(),
        )

        assert result.claude_runtime_enabled is True
        assert result.claude_model_config_id == "model_claude"
        assert result.claude_skill_allowlist == ["graph_demo"]
        assert result.claude_max_repair_rounds == 2


def test_admin_cannot_bind_non_claude_model_to_claude_runtime() -> None:
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.add(
            ModelConfig(
                id="model_openai",
                tenant_id="tenant_demo",
                name="OpenAI",
                provider="openai_compatible",
                api_key_encrypted="encrypted",
                model="gpt-test",
            )
        )
        db.commit()

        with pytest.raises(HTTPException) as exc_info:
            update_enterprise_ui_config(
                UIConfigUpdateRequest(
                    tenant_id="tenant_demo",
                    claude_runtime_enabled=True,
                    claude_model_config_id="model_openai",
                    claude_skill_allowlist=["graph_demo"],
                ),
                db=db,
                current_user=_admin(),
            )

        assert exc_info.value.status_code == 400


def test_admin_cannot_enable_runtime_without_allowlisted_skill() -> None:
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.add(
            ModelConfig(
                id="model_claude",
                tenant_id="tenant_demo",
                name="Claude",
                provider="claude_agent_sdk",
                api_key_encrypted="encrypted",
                model="claude-test",
            )
        )
        db.commit()

        with pytest.raises(HTTPException) as exc_info:
            update_enterprise_ui_config(
                UIConfigUpdateRequest(
                    tenant_id="tenant_demo",
                    claude_runtime_enabled=True,
                    claude_model_config_id="model_claude",
                    claude_skill_allowlist=[],
                ),
                db=db,
                current_user=_admin(),
            )

        assert exc_info.value.status_code == 400
