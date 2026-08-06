from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.chat import download_workspace_artifact
from app.artifacts.workspace_delivery import (
    WorkspaceArtifactError,
    publish_text_artifact,
    read_published_artifact,
)
from app.core.agent_loop import AgentLoop, _requests_file_delivery
from app.db.models import ChatSession, Message, Tenant, User
from app.session.session_schema import ChatTurnRequest


def _test_session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _seed(db: Session) -> tuple[User, ChatSession]:
    db.add(Tenant(id="tenant_demo", name="Demo"))
    user = User(
        id="user_demo",
        tenant_id="tenant_demo",
        username="demo",
        password_hash="hashed",
    )
    chat_session = ChatSession(
        id="session_demo",
        tenant_id="tenant_demo",
        user_id=user.id,
        runtime_mode="claude_supervised",
    )
    db.add(user)
    db.add(chat_session)
    db.commit()
    return user, chat_session


def test_workspace_artifact_round_trip_and_integrity(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path))
    artifact = publish_text_artifact(
        tenant_id="tenant_demo",
        session_id="session_demo",
        task_frame_id="turn_demo",
        filename="reports/result.csv",
        content="asin,score\nB012345678,92\n",
        description="选品结果",
    )

    data, filename, media_type = read_published_artifact(
        tenant_id="tenant_demo",
        session_id="session_demo",
        task_frame_id="turn_demo",
        artifact=artifact,
    )

    assert data.startswith(b"asin,score")
    assert filename == "result.csv"
    assert media_type == "text/csv"
    artifact["sha256"] = "0" * 64
    with pytest.raises(WorkspaceArtifactError, match="changed"):
        read_published_artifact(
            tenant_id="tenant_demo",
            session_id="session_demo",
            task_frame_id="turn_demo",
            artifact=artifact,
        )


def test_claude_file_tool_persists_downloadable_message_artifact(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path))
    with _test_session() as db:
        user, chat_session = _seed(db)
        loop = AgentLoop(db)
        result = loop._execute_claude_file_create(
            {
                "filename": "report.md",
                "content": "# 选品结论\n\n建议继续验证。",
                "description": "研究报告",
            },
            ChatTurnRequest(
                tenant_id="tenant_demo",
                session_id=chat_session.id,
                user_id=user.id,
                message="把报告作为文件发给我",
            ),
            chat_session,
            "turn_demo",
        )
        loop._finalize_turn(
            chat_session,
            "tenant_demo",
            "报告已生成。",
            user_message_id="turn_demo",
        )
        db.commit()

        assistant = db.exec(select(Message).where(Message.role == "assistant")).one()
        artifacts = assistant.metadata_json["harness_artifacts"]
        response = download_workspace_artifact(
            session_id=chat_session.id,
            task_frame_id="turn_demo",
            tenant_id="tenant_demo",
            path="report.md",
            current_user=user,
            db=db,
        )

        assert result["success"] is True
        assert artifacts[0]["display_name"] == "report.md"
        assert response.body.decode("utf-8").startswith("# 选品结论")
        assert response.headers["x-content-type-options"] == "nosniff"


def test_artifact_download_requires_message_publication_and_session_access(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path))
    with _test_session() as db:
        user, chat_session = _seed(db)
        publish_text_artifact(
            tenant_id="tenant_demo",
            session_id=chat_session.id,
            task_frame_id="turn_hidden",
            filename="hidden.txt",
            content="not published",
        )

        with pytest.raises(HTTPException) as missing:
            download_workspace_artifact(
                session_id=chat_session.id,
                task_frame_id="turn_hidden",
                tenant_id="tenant_demo",
                path="hidden.txt",
                current_user=user,
                db=db,
            )

        assert missing.value.status_code == 404


def test_claude_html_delivery_creates_download_and_verified_public_link(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path))

    class FakePublisher:
        enabled = True
        public_url_prefix = "https://agent.neospark.cn/files/"

        def publish(self, *_args, **_kwargs) -> str:
            return "https://agent.neospark.cn/files/report.html"

    with _test_session() as db:
        _user, chat_session = _seed(db)
        loop = AgentLoop(db)
        loop.html_artifacts = FakePublisher()  # type: ignore[assignment]

        reply = loop._prepare_claude_artifact_delivery(
            "把报告转成 HTML 文件发给我",
            chat_session,
            "turn_html",
            "核心结论：建议继续验证市场容量。",
        )
        loop._finalize_turn(
            chat_session,
            "tenant_demo",
            reply,
            user_message_id="turn_html",
        )
        db.commit()

        assistant = db.exec(select(Message).where(Message.role == "assistant")).one()
        assert _requests_file_delivery("把报告转成 HTML 文件发给我") is True
        assert "https://agent.neospark.cn/files/report.html" in reply
        assert assistant.metadata_json["harness_artifacts"][0]["path"] == "agent-report.html"
