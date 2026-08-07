from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, select

from app.core.capability_manifest import CapabilityManifestBuilder
from app.core.harness_capability_invoker import HarnessCapabilityInvoker
from app.db.models import (
    AgentProfile,
    AgentResourceBinding,
    ChatSession,
    GeneralSkill,
    HarnessInvocationRecord,
    KnowledgeBase,
    Tool,
)
from app.knowledge.schema import KnowledgeSearchResponse
from app.knowledge.service import KnowledgeService
from app.tools.tool_executor import ToolExecutor
from app.tools.tool_schema import ToolResult


def _memory_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _seed(db: Session, *, method: str = "GET") -> tuple[AgentProfile, ChatSession, Tool]:
    agent = AgentProfile(
        id="agent-overall",
        tenant_id="tenant-demo",
        name="QQQ",
        is_overall=True,
    )
    chat_session = ChatSession(
        id="session-1",
        tenant_id="tenant-demo",
        agent_id=agent.id,
    )
    tool = Tool(
        id="tool-lookup",
        tenant_id="tenant-demo",
        name="product.lookup",
        method=method,
        url="https://example.invalid/products",
        input_schema={"type": "object"},
    )
    db.add(agent)
    db.add(chat_session)
    db.add(tool)
    db.add(
        AgentResourceBinding(
            tenant_id="tenant-demo",
            agent_id=agent.id,
            resource_type="tool",
            resource_id=tool.id,
            status="active",
        )
    )
    db.commit()
    return agent, chat_session, tool


def _invoker(
    db: Session,
    agent: AgentProfile,
    chat_session: ChatSession,
    *,
    approval=False,
    file_handlers=None,
) -> HarnessCapabilityInvoker:
    manifest = CapabilityManifestBuilder(db).build(
        "tenant-demo", agent.id, None, None
    )
    return HarnessCapabilityInvoker(
        db,
        tenant_id="tenant-demo",
        session=chat_session,
        task_frame_id="task-1",
        run_id="run-1",
        manifest=manifest,
        agent_id=agent.id,
        active_skill=None,
        active_step_id=None,
        approval_checker=(lambda _descriptor, _arguments: approval),
        file_handlers=file_handlers,
    )


def test_read_tool_uses_existing_executor_and_records_completed_invocation(
    monkeypatch,
) -> None:
    engine = _memory_engine()
    calls: list[tuple[str, str | None]] = []

    def fake_execute(self, tenant_id, tool_call, active_skill_id=None, agent_id=None):  # noqa: ANN001
        calls.append((tool_call.name, agent_id))
        return ToolResult(tool_name=tool_call.name, success=True, data={"asin": "A1"})

    monkeypatch.setattr(ToolExecutor, "execute", fake_execute)
    with Session(engine) as db:
        agent, chat_session, _tool = _seed(db)
        result = _invoker(db, agent, chat_session).invoke(
            "product.lookup", {"asin": "A1"}, call_id="call-1"
        )
        records = db.exec(select(HarnessInvocationRecord)).all()

    assert result["success"] is True
    assert calls == [("product.lookup", "agent-overall")]
    assert len(records) == 1
    assert records[0].status == "completed"


def test_write_tool_requires_approval_and_replays_without_second_side_effect(
    monkeypatch,
) -> None:
    engine = _memory_engine()
    calls = 0

    def fake_execute(self, tenant_id, tool_call, active_skill_id=None, agent_id=None):  # noqa: ANN001
        nonlocal calls
        calls += 1
        return ToolResult(tool_name=tool_call.name, success=True, data={"sent": True})

    monkeypatch.setattr(ToolExecutor, "execute", fake_execute)
    with Session(engine) as db:
        agent, chat_session, _tool = _seed(db, method="POST")
        denied = _invoker(db, agent, chat_session).invoke(
            "product.lookup", {"api_key": "secret", "asin": "A1"}, call_id="call-1"
        )
        assert db.exec(select(HarnessInvocationRecord)).all() == []

        invoker = _invoker(db, agent, chat_session, approval=True)
        first = invoker.invoke(
            "product.lookup", {"api_key": "secret", "asin": "A1"}, call_id="call-2"
        )
        replay = invoker.invoke(
            "product.lookup", {"api_key": "secret", "asin": "A1"}, call_id="call-3"
        )
        records = db.exec(select(HarnessInvocationRecord)).all()

    assert denied["error"]["code"] == "APPROVAL_REQUIRED"
    assert first["success"] is True
    assert replay["idempotent_replay"] is True
    assert calls == 1
    assert len(records) == 1
    assert records[0].arguments_json["api_key"] == "<redacted>"


def test_file_capability_uses_runtime_handler_only_after_approval() -> None:
    engine = _memory_engine()
    sent: list[str] = []

    def send_file(arguments):  # noqa: ANN001
        sent.append(arguments["filename"])
        return {"success": True, "data": {"artifact_id": "artifact-1"}}

    with Session(engine) as db:
        agent, chat_session, _tool = _seed(db)
        denied = _invoker(
            db,
            agent,
            chat_session,
            file_handlers={"send_file": send_file},
        ).invoke(
            "send_file",
            {"filename": "report.html", "content": "<html></html>"},
            call_id="file-1",
        )
        allowed = _invoker(
            db,
            agent,
            chat_session,
            approval=True,
            file_handlers={"send_file": send_file},
        ).invoke(
            "send_file",
            {"filename": "report.html", "content": "<html></html>"},
            call_id="file-2",
        )

    assert denied["error"]["code"] == "APPROVAL_REQUIRED"
    assert allowed["success"] is True
    assert sent == ["report.html"]


def test_unknown_write_failure_keeps_fence_and_blocks_retry() -> None:
    engine = _memory_engine()
    calls = 0

    def uncertain_send(_arguments):  # noqa: ANN001
        nonlocal calls
        calls += 1
        return {
            "success": False,
            "error": {"code": "TIMEOUT", "message": "provider outcome unknown"},
        }

    with Session(engine) as db:
        agent, chat_session, _tool = _seed(db)
        invoker = _invoker(
            db,
            agent,
            chat_session,
            approval=True,
            file_handlers={"send_file": uncertain_send},
        )
        first = invoker.invoke(
            "send_file",
            {"filename": "report.html", "content": "<html></html>"},
            call_id="file-unknown-1",
        )
        retry = invoker.invoke(
            "send_file",
            {"filename": "report.html", "content": "<html></html>"},
            call_id="file-unknown-2",
        )
        records = db.exec(select(HarnessInvocationRecord)).all()

    assert first["error"]["code"] == "TIMEOUT"
    assert retry["error"]["code"] == "INVOCATION_RECONCILIATION_REQUIRED"
    assert calls == 1
    assert records[0].status == "outcome_unknown"
    assert records[0].logical_action_key


def test_general_skill_read_is_read_only_but_execute_remains_gated() -> None:
    engine = _memory_engine()
    with Session(engine) as db:
        agent, chat_session, _tool = _seed(db)
        skill = GeneralSkill(
            id="general-research",
            tenant_id="tenant-demo",
            slug="research",
            name="研究方法",
            skill_markdown="# Research\n\n先读取真实资料。",
            status="published",
        )
        db.add(skill)
        db.add(
            AgentResourceBinding(
                tenant_id="tenant-demo",
                agent_id=agent.id,
                resource_type="general_skill",
                resource_id=skill.id,
                status="active",
            )
        )
        db.commit()
        invoker = _invoker(db, agent, chat_session)

        read_result = invoker.invoke(
            "general_skill.research",
            {"query": "研究 A1", "operation": "read"},
            call_id="skill-read",
        )
        execute_result = invoker.invoke(
            "general_skill.research",
            {"query": "研究 A1", "operation": "execute"},
            call_id="skill-execute",
        )

    assert read_result["success"] is True
    assert read_result["data"]["skill_markdown"].startswith("# Research")
    assert execute_result["error"]["code"] == "APPROVAL_REQUIRED"


def test_knowledge_search_intersects_requested_ids_with_frozen_scope(
    monkeypatch,
) -> None:
    engine = _memory_engine()
    captured: dict[str, object] = {}

    def fake_search(self, request, model_config=None):  # noqa: ANN001
        captured["ids"] = request.knowledge_base_ids
        captured["query"] = request.query
        return KnowledgeSearchResponse(evidence_pack=[{"content": "真实资料"}])

    monkeypatch.setattr(KnowledgeService, "search", fake_search)
    with Session(engine) as db:
        agent, chat_session, _tool = _seed(db)
        knowledge = KnowledgeBase(
            id="kb-market",
            tenant_id="tenant-demo",
            name="市场资料",
            status="active",
        )
        db.add(knowledge)
        db.add(
            AgentResourceBinding(
                tenant_id="tenant-demo",
                agent_id=agent.id,
                resource_type="knowledge_base",
                resource_id=knowledge.id,
                status="active",
            )
        )
        db.commit()
        invoker = _invoker(db, agent, chat_session)

        denied = invoker.invoke(
            "knowledge_search",
            {"query": "A1", "knowledge_base_ids": ["kb-other"]},
            call_id="knowledge-denied",
        )
        result = invoker.invoke(
            "knowledge_search",
            {"query": "A1", "knowledge_base_ids": ["kb-market"]},
            call_id="knowledge-allowed",
        )

    assert denied["error"]["code"] == "KNOWLEDGE_NOT_AVAILABLE"
    assert result["success"] is True
    assert captured == {"ids": ["kb-market"], "query": "A1"}


def test_changed_tool_snapshot_is_rejected_before_executor(monkeypatch) -> None:
    engine = _memory_engine()

    def fail_execute(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("changed capability must not execute")

    monkeypatch.setattr(ToolExecutor, "execute", fail_execute)
    with Session(engine) as db:
        agent, chat_session, tool = _seed(db)
        invoker = _invoker(db, agent, chat_session)
        tool.url = "https://example.invalid/changed"
        db.add(tool)
        db.commit()

        result = invoker.invoke("product.lookup", {"asin": "A1"}, call_id="call-1")

    assert result["error"]["code"] == "CAPABILITY_AUTHORIZATION_REVOKED"
