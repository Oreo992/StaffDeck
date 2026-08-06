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
        assert "下载入口" in result["data"]["instruction"]
        assert "公网" not in result["data"]["instruction"]
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


def test_claude_file_create_does_not_publish_html_implicitly(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path))

    class FakePublisher:
        enabled = True
        public_url_prefix = "https://agent.neospark.cn/files/"

        publish_calls = 0

        def publish_document(self, document: str, _artifact_id: str) -> str:
            self.publish_calls += 1
            return "https://agent.neospark.cn/files/report.html"

    with _test_session() as db:
        user, chat_session = _seed(db)
        loop = AgentLoop(db)
        loop.html_artifacts = FakePublisher()  # type: ignore[assignment]

        result = loop._execute_claude_file_create(
            {
                "filename": "report.html",
                "content": "<!doctype html><html><body>报告</body></html>",
                "content_type": "text/html; charset=utf-8",
            },
            ChatTurnRequest(
                tenant_id="tenant_demo",
                session_id=chat_session.id,
                user_id=user.id,
                message="把报告转成 HTML 文件发给我",
            ),
            chat_session,
            "turn_html",
        )

        assert _requests_file_delivery("把报告转成 HTML 文件发给我") is True
        assert result["success"] is True
        assert loop.html_artifacts.publish_calls == 0


def test_claude_publish_html_tool_publishes_the_exact_created_document(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path))

    class FakePublisher:
        enabled = True
        published_document = ""

        def publish_document(self, document: str, _artifact_id: str) -> str:
            self.published_document = document
            return "https://agent.neospark.cn/files/existing.html"

    with _test_session() as db:
        user, chat_session = _seed(db)
        loop = AgentLoop(db)
        loop.html_artifacts = FakePublisher()  # type: ignore[assignment]
        document = "<!doctype html><html><body>产品画像 销量与趋势 竞争格局</body></html>"
        request = ChatTurnRequest(
            tenant_id="tenant_demo",
            session_id=chat_session.id,
            user_id=user.id,
            message="生成 HTML 文件和公网链接",
        )
        created = loop._execute_claude_file_create(
            {
                "filename": "report.html",
                "content": document,
                "content_type": "text/html; charset=utf-8",
            },
            request,
            chat_session,
            "turn_existing",
        )

        published = loop._execute_claude_html_publish(
            {"path": created["data"]["artifact"]["path"]},
            request,
            chat_session,
            "turn_existing",
        )

        assert published["success"] is True
        assert published["data"]["url"] == "https://agent.neospark.cn/files/existing.html"
        assert loop.html_artifacts.published_document == document


def test_claude_publish_html_rejects_an_artifact_from_another_turn(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path))

    class FakePublisher:
        enabled = True

        def publish_document(self, _document: str, _artifact_id: str) -> str:
            raise AssertionError("publisher must not be called")

    with _test_session() as db:
        user, chat_session = _seed(db)
        loop = AgentLoop(db)
        loop.html_artifacts = FakePublisher()  # type: ignore[assignment]
        artifact = publish_text_artifact(
            tenant_id="tenant_demo",
            session_id=chat_session.id,
            task_frame_id="turn_old",
            filename="report.html",
            content="<!doctype html><html><body>old</body></html>",
            content_type="text/html; charset=utf-8",
        )
        loop._pending_assistant_artifacts[chat_session.id] = [artifact]

        result = loop._execute_claude_html_publish(
            {"path": "report.html"},
            ChatTurnRequest(
                tenant_id="tenant_demo",
                session_id=chat_session.id,
                user_id=user.id,
                message="发布公网链接",
            ),
            chat_session,
            "turn_new",
        )

        assert result["success"] is False
        assert result["error"]["code"] == "ARTIFACT_NOT_FOUND"
