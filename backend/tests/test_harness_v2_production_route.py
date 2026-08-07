from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, select

from app.agents.branching import ensure_open_gallery_binding
from app.core.agent_loop import AgentLoop
from app.db.models import (
    AgentEvent,
    AgentProfile,
    AgentResourceBinding,
    ChatSession,
    HarnessRunRecord,
    HarnessTaskFrameRecord,
    Message,
    ModelConfig,
    Tenant,
    Tool,
    UIConfig,
)
from app.runtime.contracts import HarnessRunResult, HarnessStructuredOutput
from app.security.encryption import encrypt_secret
from app.session.helpers import public_session
from app.session.session_schema import (
    ChatTurnRequest,
    ChatTurnResponse,
    RouterDecision,
    StepAgentResult,
)
from app.tools.tool_executor import ToolExecutor
from app.tools.tool_schema import ToolResult


def _test_session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_allowlisted_new_legacy_turn_uses_harness_engine_not_step_agent() -> None:
    with _test_session() as db:
        db.add(Tenant(id="tenant-demo", name="Demo"))
        db.add(
            AgentProfile(
                id="agent-overall",
                tenant_id="tenant-demo",
                name="Overall",
                is_overall=True,
            )
        )
        db.add(AgentProfile(id="agent-canary", tenant_id="tenant-demo", name="Canary"))
        db.add(
            UIConfig(
                tenant_id="tenant-demo",
                harness_v2_enabled=True,
                harness_v2_agent_allowlist_json=["agent-canary"],
            )
        )
        db.commit()
        loop = AgentLoop(db)
        calls: list[str] = []

        class FakeEngine:
            def run(self, request: ChatTurnRequest) -> ChatTurnResponse:
                calls.append(request.message)
                session = loop._get_or_create_session(request)
                session.runtime_state_json = {"execution_engine": "harness_v2"}
                db.add(session)
                db.commit()
                return ChatTurnResponse(
                    reply="Harness 完成",
                    session_id=session.id,
                    step_result=StepAgentResult(reply="Harness 完成"),
                    session_state=public_session(session),
                )

            def close(self) -> None:
                return None

        loop.harness_v2_engine_factory = lambda _owner: FakeEngine()
        loop._prepare_turn = lambda *_args, **_kwargs: (_ for _ in ()).throw(  # type: ignore[method-assign]
            AssertionError("Legacy StepAgent path must not run")
        )

        response = loop.handle_turn(
            ChatTurnRequest(
                tenant_id="tenant-demo",
                user_id="user-demo",
                agent_id="agent-canary",
                runtime_mode="legacy",
                message="研究 A1",
            )
        )

        assert response.reply == "Harness 完成"
        assert calls == ["研究 A1"]


def test_existing_harness_session_stays_on_harness_when_canary_is_later_disabled() -> None:
    with _test_session() as db:
        db.add(Tenant(id="tenant-demo", name="Demo"))
        db.add(AgentProfile(id="agent-canary", tenant_id="tenant-demo", name="Canary"))
        db.add(UIConfig(tenant_id="tenant-demo", harness_v2_enabled=False))
        db.commit()
        loop = AgentLoop(db)
        session = loop._get_or_create_session(
            ChatTurnRequest(
                tenant_id="tenant-demo",
                user_id="user-demo",
                agent_id="agent-canary",
                runtime_mode="legacy",
                message="first",
            )
        )
        session.runtime_state_json = {"execution_engine": "harness_v2"}
        db.add(session)
        db.commit()

        assert loop._request_uses_harness_v2_runtime(
            ChatTurnRequest(
                tenant_id="tenant-demo",
                user_id="user-demo",
                session_id=session.id,
                message="继续",
            )
        )


def test_real_harness_engine_persists_conversation_frame_and_skips_response_agent() -> None:
    with _test_session() as db:
        db.add(Tenant(id="tenant-demo", name="Demo"))
        db.add(
            AgentProfile(
                id="agent-canary",
                tenant_id="tenant-demo",
                name="Canary",
                is_overall=True,
                persona_prompt="你是选品员工。",
            )
        )
        db.add(
            UIConfig(
                tenant_id="tenant-demo",
                harness_v2_enabled=True,
                harness_v2_agent_allowlist_json=["agent-canary"],
            )
        )
        db.add(
            ModelConfig(
                id="model-default",
                tenant_id="tenant-demo",
                name="Default",
                provider="openai_compatible",
                api_key_encrypted=encrypt_secret("secret"),
                model="demo-model",
                is_default=True,
            )
        )
        db.commit()
        loop = AgentLoop(db)
        requests = []

        class FakeRuntime:
            async def run_segment(self, request):
                requests.append(request)
                return HarnessRunResult(
                    session_id="runtime-session-1",
                    output=HarnessStructuredOutput(reply="A1 研究完成"),
                    num_turns=1,
                )

            async def resume(self, checkpoint_id, request):
                return await self.run_segment(request)

            def cancel(self, run_id):
                return False

        loop.legacy_harness_runtime = FakeRuntime()
        loop.router.decide = lambda *_args, **_kwargs: RouterDecision(  # type: ignore[method-assign]
            decision="answer_only",
            user_intent="研究 A1",
            reason="普通自主任务",
        )
        loop.response_generator.generate = lambda *_args, **_kwargs: (_ for _ in ()).throw(  # type: ignore[method-assign]
            AssertionError("ResponseAgent must not run on Harness v2")
        )

        response = loop.handle_turn(
            ChatTurnRequest(
                tenant_id="tenant-demo",
                user_id="user-demo",
                agent_id="agent-canary",
                runtime_mode="legacy",
                message="研究 A1",
                client_turn_id="client-turn-1",
            )
        )
        replay = loop.handle_turn(
            ChatTurnRequest(
                tenant_id="tenant-demo",
                user_id="user-demo",
                session_id=response.session_id,
                agent_id="agent-canary",
                runtime_mode="legacy",
                message="研究 A1",
                client_turn_id="client-turn-1",
            )
        )
        session = loop.db.get(ChatSession, response.session_id)
        frames = db.exec(select(HarnessTaskFrameRecord)).all()
        runs = db.exec(select(HarnessRunRecord)).all()
        messages = db.exec(select(Message).order_by(Message.created_at)).all()

        assert response.reply == "A1 研究完成"
        assert replay.reply == response.reply
        assert replay.session_id == response.session_id
        assert len(requests) == 1
        assert frames[0].status == "completed"
        assert runs[0].status == "completed"
        assert [item.role for item in messages] == ["user", "assistant"]
        assert session.runtime_state_json["execution_engine"] == "harness_v2"
        assert session.runtime_state_json["runtime_session_id"] == "runtime-session-1"


def test_sop_graph_advances_only_after_gateway_evidence_passes_audit(monkeypatch) -> None:
    with _test_session() as db:
        db.add(Tenant(id="tenant-demo", name="Demo"))
        db.add(
            AgentProfile(
                id="agent-overall",
                tenant_id="tenant-demo",
                name="Overall",
                is_overall=True,
            )
        )
        db.add(AgentProfile(id="agent-canary", tenant_id="tenant-demo", name="Canary"))
        db.add(
            UIConfig(
                tenant_id="tenant-demo",
                harness_v2_enabled=True,
                harness_v2_agent_allowlist_json=["agent-canary"],
                claude_max_repair_rounds=2,
            )
        )
        db.add(
            ModelConfig(
                id="model-default",
                tenant_id="tenant-demo",
                name="Default",
                provider="openai_compatible",
                api_key_encrypted=encrypt_secret("secret"),
                model="demo-model",
                is_default=True,
            )
        )
        from app.db.models import Skill

        skill = Skill(
            id="skill-row",
            tenant_id="tenant-demo",
            skill_id="research-sop",
            version="1.0.0",
            name="研究 SOP",
            status="published",
            content_json={
                "skill_id": "research-sop",
                "start_node_id": "lookup",
                "terminal_node_ids": ["lookup"],
                "required_info": ["asin"],
                "nodes": [
                    {
                        "node_id": "lookup",
                        "name": "查询商品",
                        "instruction": "查询商品真实数据",
                        "expected_user_info": ["asin"],
                        "allowed_actions": ["call_tool:product.lookup"],
                    }
                ],
                "edges": [],
            },
        )
        tool = Tool(
            id="tool-lookup",
            tenant_id="tenant-demo",
            name="product.lookup",
            method="GET",
            url="https://example.invalid/product",
            input_schema={
                "type": "object",
                "properties": {"asin": {"type": "string"}},
                "required": ["asin"],
            },
        )
        db.add(skill)
        db.add(tool)
        db.add(
            AgentResourceBinding(
                tenant_id="tenant-demo",
                agent_id="agent-canary",
                resource_type="skill",
                resource_id=skill.id,
                status="active",
            )
        )
        db.add(
            AgentResourceBinding(
                tenant_id="tenant-demo",
                agent_id="agent-canary",
                resource_type="tool",
                resource_id=tool.id,
                status="active",
            )
        )
        db.commit()
        ensure_open_gallery_binding(db, "tenant-demo", "skill", skill.id, "active")
        ensure_open_gallery_binding(db, "tenant-demo", "tool", tool.id, "active")
        db.commit()
        tool_calls: list[str] = []

        def fake_execute(self, tenant_id, tool_call, active_skill_id=None, agent_id=None):
            tool_calls.append(tool_call.name)
            return ToolResult(
                tool_name=tool_call.name,
                success=True,
                data={"asin": "A1", "price": 99},
            )

        monkeypatch.setattr(ToolExecutor, "execute", fake_execute)
        loop = AgentLoop(db)

        class RepairingRuntime:
            def __init__(self) -> None:
                self.calls = 0

            async def run_segment(self, request):
                self.calls += 1
                if self.calls == 2:
                    result = request.execute_tool("product.lookup", {"asin": "A1"})
                    assert result["success"] is True
                return HarnessRunResult(
                    session_id="runtime-sop-1",
                    output=HarnessStructuredOutput(
                        reply="A1 真实价格为 99 元",
                        slot_updates={"asin": "A1"},
                        completed_step_ids=["lookup"],
                    ),
                    num_turns=1,
                )

            async def resume(self, checkpoint_id, request):
                return await self.run_segment(request)

            def cancel(self, run_id):
                return False

        runtime = RepairingRuntime()
        loop.legacy_harness_runtime = runtime
        loop.router.decide = lambda *_args, **_kwargs: RouterDecision(  # type: ignore[method-assign]
            decision="start_new_task",
            target_skill_id="research-sop",
            target_step_id="lookup",
            slot_hints={"asin": "A1"},
            user_intent="按流程研究 A1",
            reason="用户明确要求研究流程",
        )

        response = loop.handle_turn(
            ChatTurnRequest(
                tenant_id="tenant-demo",
                user_id="user-demo",
                agent_id="agent-canary",
                runtime_mode="legacy",
                message="按流程研究 A1",
                client_turn_id="client-sop-1",
            )
        )
        session = db.get(ChatSession, response.session_id)
        audits = db.exec(
            select(AgentEvent)
            .where(AgentEvent.event_type == "sop_audit")
            .order_by(AgentEvent.created_at)
        ).all()
        runs = db.exec(select(HarnessRunRecord).order_by(HarnessRunRecord.created_at)).all()

        assert response.reply == "A1 真实价格为 99 元"
        assert runtime.calls == 2
        assert tool_calls == ["product.lookup"]
        assert [item.payload_json["outcome"] for item in audits] == ["repair", "passed"]
        assert [item.status for item in runs] == ["completed", "completed"]
        assert session.active_skill_id is None
        assert session.active_step_id is None


def test_write_sop_waits_for_explicit_approval_before_runtime_or_tool(monkeypatch) -> None:
    from app.db.models import Skill

    with _test_session() as db:
        db.add(Tenant(id="tenant-demo", name="Demo"))
        db.add(
            AgentProfile(
                id="agent-canary",
                tenant_id="tenant-demo",
                name="Canary",
                is_overall=True,
            )
        )
        db.add(
            UIConfig(
                tenant_id="tenant-demo",
                harness_v2_enabled=True,
                harness_v2_agent_allowlist_json=["agent-canary"],
            )
        )
        db.add(
            ModelConfig(
                id="model-default",
                tenant_id="tenant-demo",
                name="Default",
                provider="openai_compatible",
                api_key_encrypted=encrypt_secret("secret"),
                model="demo-model",
                is_default=True,
            )
        )
        skill = Skill(
            id="skill-notify-row",
            tenant_id="tenant-demo",
            skill_id="notify-sop",
            version="1.0.0",
            name="通知 SOP",
            status="published",
            content_json={
                "skill_id": "notify-sop",
                "start_node_id": "notify",
                "terminal_node_ids": ["notify"],
                "nodes": [
                    {
                        "node_id": "notify",
                        "name": "发送通知",
                        "instruction": "调用工具发送通知",
                        "allowed_actions": ["call_tool:notify.send"],
                    }
                ],
                "edges": [],
            },
        )
        tool = Tool(
            id="tool-notify",
            tenant_id="tenant-demo",
            name="notify.send",
            method="POST",
            url="https://example.invalid/notify",
            input_schema={
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
            },
        )
        db.add(skill)
        db.add(tool)
        for resource_type, resource_id in (("skill", skill.id), ("tool", tool.id)):
            db.add(
                AgentResourceBinding(
                    tenant_id="tenant-demo",
                    agent_id="agent-canary",
                    resource_type=resource_type,
                    resource_id=resource_id,
                    status="active",
                )
            )
        db.commit()
        ensure_open_gallery_binding(db, "tenant-demo", "skill", skill.id, "active")
        ensure_open_gallery_binding(db, "tenant-demo", "tool", tool.id, "active")
        db.commit()

        tool_calls: list[str] = []

        def fake_execute(self, tenant_id, tool_call, active_skill_id=None, agent_id=None):
            tool_calls.append(tool_call.name)
            return ToolResult(tool_name=tool_call.name, success=True, data={"sent": True})

        monkeypatch.setattr(ToolExecutor, "execute", fake_execute)
        loop = AgentLoop(db)

        class WriteRuntime:
            calls = 0

            async def run_segment(self, request):
                self.calls += 1
                result = request.execute_tool("notify.send", {"message": "完成"})
                assert result["success"] is True
                return HarnessRunResult(
                    session_id="runtime-write-1",
                    output=HarnessStructuredOutput(
                        reply="通知已发送",
                        completed_step_ids=["notify"],
                    ),
                    num_turns=2,
                )

            async def resume(self, checkpoint_id, request):
                return await self.run_segment(request)

            def cancel(self, run_id):
                return False

        runtime = WriteRuntime()
        loop.legacy_harness_runtime = runtime

        def decide(message, *_args, **_kwargs):
            return RouterDecision(
                decision="continue_active" if message == "确认执行" else "start_new_task",
                target_skill_id="notify-sop",
                target_step_id="notify",
                user_intent="发送通知",
                reason="继续已选择的通知 SOP",
            )

        loop.router.decide = decide  # type: ignore[method-assign]
        first = loop.handle_turn(
            ChatTurnRequest(
                tenant_id="tenant-demo",
                user_id="user-demo",
                agent_id="agent-canary",
                runtime_mode="legacy",
                message="按流程发送通知",
                client_turn_id="client-write-1",
            )
        )

        assert "确认执行" in first.reply
        assert runtime.calls == 0
        assert tool_calls == []

        second = loop.handle_turn(
            ChatTurnRequest(
                tenant_id="tenant-demo",
                user_id="user-demo",
                session_id=first.session_id,
                runtime_mode="legacy",
                message="确认执行",
                client_turn_id="client-write-2",
            )
        )

        assert second.reply == "通知已发送"
        assert runtime.calls == 1
        assert tool_calls == ["notify.send"]


def test_legacy_harness_model_must_call_file_capabilities_to_deliver_and_publish(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path))
    with _test_session() as db:
        db.add(Tenant(id="tenant-demo", name="Demo"))
        db.add(
            AgentProfile(
                id="agent-canary",
                tenant_id="tenant-demo",
                name="Canary",
                is_overall=True,
            )
        )
        db.add(
            UIConfig(
                tenant_id="tenant-demo",
                harness_v2_enabled=True,
                harness_v2_agent_allowlist_json=["agent-canary"],
            )
        )
        db.add(
            ModelConfig(
                id="model-default",
                tenant_id="tenant-demo",
                name="Default",
                provider="openai_compatible",
                api_key_encrypted=encrypt_secret("secret"),
                model="demo-model",
                is_default=True,
            )
        )
        db.commit()
        loop = AgentLoop(db)

        class FakePublisher:
            enabled = True

            def __init__(self) -> None:
                self.documents: list[str] = []

            def publish_document(self, document, _artifact_id):
                self.documents.append(document)
                return "https://agent.example/files/report.html"

        publisher = FakePublisher()
        loop.html_artifacts = publisher

        class FileRuntime:
            async def run_segment(self, request):
                created = request.execute_tool(
                    "send_file",
                    {
                        "filename": "report.html",
                        "content": "<!doctype html><html><body>A1 报告</body></html>",
                        "content_type": "text/html; charset=utf-8",
                    },
                )
                assert created["success"] is True
                published = request.execute_tool(
                    "publish_file",
                    {"artifact_id": created["data"]["artifact"]["artifact_id"]},
                )
                assert published["success"] is True
                return HarnessRunResult(
                    session_id="runtime-file-1",
                    output=HarnessStructuredOutput(
                        reply=f"报告已发送：{published['data']['url']}"
                    ),
                    num_turns=3,
                )

            async def resume(self, checkpoint_id, request):
                return await self.run_segment(request)

            def cancel(self, run_id):
                return False

        loop.legacy_harness_runtime = FileRuntime()
        loop.router.decide = lambda *_args, **_kwargs: RouterDecision(  # type: ignore[method-assign]
            decision="answer_only",
            user_intent="生成 HTML 文件并发布公网链接",
            reason="通用文件交付任务",
        )

        response = loop.handle_turn(
            ChatTurnRequest(
                tenant_id="tenant-demo",
                user_id="user-demo",
                agent_id="agent-canary",
                runtime_mode="legacy",
                message="生成 HTML 文件并发布公网链接给我",
                client_turn_id="client-file-1",
            )
        )
        assistant = db.exec(
            select(Message).where(Message.role == "assistant")
        ).one()

        assert response.reply == "报告已发送：https://agent.example/files/report.html"
        assert publisher.documents == ["<!doctype html><html><body>A1 报告</body></html>"]
        artifacts = assistant.metadata_json["harness_artifacts"]
        assert len(artifacts) == 1
        assert artifacts[0]["display_name"] == "report.html"
        assert artifacts[0]["public_url"] == "https://agent.example/files/report.html"
        assert artifacts[0]["public_url"] == "https://agent.example/files/report.html"
