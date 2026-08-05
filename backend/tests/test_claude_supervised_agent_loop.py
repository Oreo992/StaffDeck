from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.core.agent_loop import AgentLoop, ClaudeSupervisedOutcome
from app.db.models import (
    AgentEvent,
    ChatSession,
    HumanHandoffRequest,
    ModelConfig,
    Skill,
    Tenant,
    Tool,
    UIConfig,
)
from app.runtime.contracts import (
    HarnessRunRequest,
    HarnessRunResult,
    HarnessStructuredOutput,
)
from app.security.encryption import encrypt_secret
from app.session.session_schema import ChatTurnRequest
from app.session.session_schema import RouterDecision
from app.tools.tool_schema import ToolResult


class _RepairingHarness:
    def __init__(self) -> None:
        self.requests: list[HarnessRunRequest] = []

    async def run_segment(self, request: HarnessRunRequest) -> HarnessRunResult:
        self.requests.append(request)
        if len(self.requests) == 1:
            return HarnessRunResult(
                session_id="sdk-session-1",
                output=HarnessStructuredOutput(
                    reply="候选结果",
                    slot_updates={"request_type": "price"},
                ),
                num_turns=2,
            )
        assert request.execute_tool is not None
        awaitable_or_result = request.execute_tool("product.price_query", {"product_name": "A1"})
        assert isinstance(awaitable_or_result, dict)
        return HarnessRunResult(
            session_id="sdk-session-2",
            output=HarnessStructuredOutput(
                reply="A1 当前价格为 99 元。",
                slot_updates={"request_type": "price"},
            ),
            num_turns=3,
        )

    async def resume(
        self, checkpoint_id: str, request: HarnessRunRequest
    ) -> HarnessRunResult:
        request.resume_session_id = checkpoint_id
        return await self.run_segment(request)

    def cancel(self, run_id: str) -> bool:
        return False


class _UnexpectedHarness:
    async def run_segment(self, request: HarnessRunRequest) -> HarnessRunResult:
        raise AssertionError("side-effect segment must not run before explicit approval")

    async def resume(
        self, checkpoint_id: str, request: HarnessRunRequest
    ) -> HarnessRunResult:
        raise AssertionError("side-effect segment must not resume before explicit approval")

    def cancel(self, run_id: str) -> bool:
        return False


class _ConversationHarness:
    def __init__(self) -> None:
        self.requests: list[HarnessRunRequest] = []

    async def run_segment(self, request: HarnessRunRequest) -> HarnessRunResult:
        self.requests.append(request)
        turn = len(self.requests)
        return HarnessRunResult(
            session_id=f"conversation-session-{turn}",
            output=HarnessStructuredOutput(reply=f"普通对话回复 {turn}"),
            num_turns=1,
        )

    async def resume(
        self, checkpoint_id: str, request: HarnessRunRequest
    ) -> HarnessRunResult:
        request.resume_session_id = checkpoint_id
        return await self.run_segment(request)

    def cancel(self, run_id: str) -> bool:
        return False


class _SopActivatingHarness:
    def __init__(self) -> None:
        self.requests: list[HarnessRunRequest] = []
        self.sop_calls = 0

    async def run_segment(self, request: HarnessRunRequest) -> HarnessRunResult:
        self.requests.append(request)
        tool_names = {tool.name for tool in request.tools}
        if "staffdeck.activate_sop" in tool_names:
            assert request.execute_tool is not None
            result = request.execute_tool("staffdeck.activate_sop", {"skill_id": "graph_demo"})
            assert isinstance(result, dict) and result["success"] is True
            return HarnessRunResult(
                session_id="agent-session-1",
                output=HarnessStructuredOutput(reply="我会按流程查询。"),
                is_error=True,
                error_code="max_turns",
                error_message="Checkpoint requested after SOP activation.",
                num_turns=2,
            )

        self.sop_calls += 1
        if self.sop_calls == 1:
            return HarnessRunResult(
                session_id="agent-session-2",
                output=HarnessStructuredOutput(
                    reply="正在准备查询。",
                    slot_updates={"request_type": "price", "product_name": "A1"},
                ),
                num_turns=1,
            )
        assert request.execute_tool is not None
        result = request.execute_tool("product.price_query", {"product_name": "A1"})
        assert isinstance(result, dict) and result["success"] is True
        return HarnessRunResult(
            session_id="agent-session-3",
            output=HarnessStructuredOutput(
                reply="A1 当前价格为 99 元。",
                slot_updates={"request_type": "price", "product_name": "A1"},
            ),
            num_turns=2,
        )

    async def resume(
        self, checkpoint_id: str, request: HarnessRunRequest
    ) -> HarnessRunResult:
        request.resume_session_id = checkpoint_id
        return await self.run_segment(request)

    def cancel(self, run_id: str) -> bool:
        return False


def _consume_return(generator: Iterator[dict[str, object]]) -> ClaudeSupervisedOutcome:
    while True:
        try:
            next(generator)
        except StopIteration as exc:
            assert isinstance(exc.value, ClaudeSupervisedOutcome)
            return exc.value


def _skill(tool_name: str = "product.price_query") -> Skill:
    return Skill(
        id="skill_row",
        tenant_id="tenant_demo",
        skill_id="graph_demo",
        version="1.0.0",
        name="Graph Demo",
        status="published",
        content_json={
            "skill_id": "graph_demo",
            "name": "Graph Demo",
            "required_info": ["request_type", "product_name"],
            "nodes": [
                {
                    "node_id": "collect",
                    "type": "collect_info",
                    "name": "分类",
                    "instruction": "分类请求",
                    "expected_user_info": ["request_type"],
                    "allowed_actions": ["continue_flow"],
                },
                {
                    "node_id": "query",
                    "type": "tool_call",
                    "name": "调用工具",
                    "instruction": "调用工具",
                    "expected_user_info": ["product_name"],
                    "allowed_actions": [f"call_tool:{tool_name}"],
                },
                {
                    "node_id": "reply",
                    "type": "response",
                    "name": "回复",
                    "instruction": "回复用户",
                    "expected_user_info": [],
                    "allowed_actions": ["answer_user"],
                },
            ],
            "edges": [
                {"source_node_id": "collect", "next_node_id": "query"},
                {"source_node_id": "query", "next_node_id": "reply"},
            ],
            "start_node_id": "collect",
            "terminal_node_ids": ["reply"],
        },
    )


def _seed_runtime(db: Session, *, effect_level: str = "read") -> tuple[ChatSession, Skill, Tool]:
    db.add(Tenant(id="tenant_demo", name="Demo"))
    model = ModelConfig(
        id="model_claude",
        tenant_id="tenant_demo",
        name="Claude",
        provider="claude_agent_sdk",
        api_key_encrypted=encrypt_secret("secret"),
        model="claude-test",
    )
    db.add(model)
    db.add(
        UIConfig(
            tenant_id="tenant_demo",
            claude_runtime_enabled=True,
            claude_model_config_id=model.id,
            claude_skill_allowlist_json=["graph_demo"],
            claude_max_repair_rounds=2,
            agent_loop_max_actions=6,
        )
    )
    skill = _skill("product.price_query" if effect_level == "read" else "notice.send")
    db.add(skill)
    tool = Tool(
        id="tool_demo",
        tenant_id="tenant_demo",
        name="product.price_query" if effect_level == "read" else "notice.send",
        description="Demo tool",
        method="POST",
        url="https://example.test/tool",
        effect_level=effect_level,
        input_schema={
            "type": "object",
            "properties": {"product_name": {"type": "string"}},
            "required": ["product_name"],
        },
    )
    db.add(tool)
    chat_session = ChatSession(
        id="session_demo",
        tenant_id="tenant_demo",
        user_id="user_demo",
        runtime_mode="claude_supervised",
        active_skill_id="graph_demo",
        active_step_id="collect",
        slots_json={"product_name": "A1"},
    )
    db.add(chat_session)
    db.commit()
    return chat_session, skill, tool


def _test_session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_supervised_loop_repairs_with_same_sdk_session_and_commits_only_after_evidence() -> None:
    with _test_session() as db:
        chat_session, skill, tool = _seed_runtime(db)
        loop = AgentLoop(db)
        harness = _RepairingHarness()
        loop.claude_runtime = harness  # type: ignore[assignment]
        loop._execute_tool_call = lambda *args, **kwargs: ToolResult(  # type: ignore[method-assign]
            tool_name="product.price_query", success=True, data={"price": 99}
        )

        outcome = _consume_return(
            loop._stream_claude_supervised_response(
                ChatTurnRequest(
                    tenant_id="tenant_demo",
                    session_id=chat_session.id,
                    user_id="user_demo",
                    message="查询 A1 价格",
                ),
                chat_session,
                skill,
                [tool],
                None,
                {},
                "turn_demo",
            )
        )
        db.refresh(chat_session)

        assert outcome.reply == "A1 当前价格为 99 元。"
        assert [item.resume_session_id for item in harness.requests] == [None, "sdk-session-1"]
        assert chat_session.runtime_state_json["sdk_session_id"] == "sdk-session-2"
        assert chat_session.runtime_state_json["status"] == "completed"
        assert chat_session.active_skill_id is None
        assert chat_session.runtime_state_json["checkpoints"][0]["completed_step_ids"] == [
            "collect",
            "query",
            "reply",
        ]


def test_skillless_claude_conversation_reuses_session_with_read_tools_without_sop() -> None:
    with _test_session() as db:
        chat_session, _skill_row, _tool = _seed_runtime(db)
        chat_session.active_skill_id = None
        chat_session.active_step_id = None
        chat_session.slots_json = {"preserved": "value"}
        ui_config = db.get(UIConfig, "tenant_demo")
        assert ui_config is not None
        ui_config.claude_skill_allowlist_json = []
        db.add(ui_config)
        db.add(chat_session)
        db.commit()

        loop = AgentLoop(db)
        harness = _ConversationHarness()
        loop.claude_runtime = harness  # type: ignore[assignment]

        first = _consume_return(
            loop._stream_claude_conversation_response(
                ChatTurnRequest(
                    tenant_id="tenant_demo",
                    session_id=chat_session.id,
                    user_id="user_demo",
                    message="你好",
                ),
                chat_session,
                [_skill()],
                [_tool],
                "你是 QQQ 的 Claude 版本。",
                {},
                "turn_greeting_1",
            )
        )
        second = _consume_return(
            loop._stream_claude_conversation_response(
                ChatTurnRequest(
                    tenant_id="tenant_demo",
                    session_id=chat_session.id,
                    user_id="user_demo",
                    message="你还记得我吗？",
                ),
                chat_session,
                [_skill()],
                [_tool],
                "你是 QQQ 的 Claude 版本。",
                {},
                "turn_greeting_2",
            )
        )
        db.refresh(chat_session)

        assert first.reply == "普通对话回复 1"
        assert second.reply == "普通对话回复 2"
        assert [item.resume_session_id for item in harness.requests] == [
            None,
            "conversation-session-1",
        ]
        assert all(item.execute_tool is not None for item in harness.requests)
        assert [tool.name for tool in harness.requests[0].tools] == ["product.price_query"]
        assert "可自主完成任务" in harness.requests[0].system_prompt
        assert "StaffDeck" not in harness.requests[0].system_prompt
        assert chat_session.runtime_state_json["sdk_session_id"] == "conversation-session-2"
        assert chat_session.runtime_state_json["status"] == "completed"
        assert chat_session.active_skill_id is None
        assert chat_session.active_step_id is None
        assert chat_session.slots_json == {"preserved": "value"}


def test_claude_agent_can_request_and_complete_a_validated_sop_in_the_same_turn() -> None:
    with _test_session() as db:
        chat_session, skill, tool = _seed_runtime(db)
        chat_session.active_skill_id = None
        chat_session.active_step_id = None
        chat_session.slots_json = {"product_name": "A1"}
        db.add(chat_session)
        db.commit()

        loop = AgentLoop(db)
        harness = _SopActivatingHarness()
        loop.claude_runtime = harness  # type: ignore[assignment]
        loop._execute_tool_call = lambda *args, **kwargs: ToolResult(  # type: ignore[method-assign]
            tool_name="product.price_query", success=True, data={"price": 99}
        )

        outcome = _consume_return(
            loop._stream_claude_conversation_response(
                ChatTurnRequest(
                    tenant_id="tenant_demo",
                    session_id=chat_session.id,
                    user_id="user_demo",
                    message="按标准流程查询 A1 价格",
                ),
                chat_session,
                [skill],
                [tool],
                "你是 QQQ 的 Claude 版本。",
                {},
                "turn_activate_sop",
            )
        )
        db.refresh(chat_session)

        assert outcome.reply == "A1 当前价格为 99 元。"
        assert [item.resume_session_id for item in harness.requests] == [
            None,
            "agent-session-1",
            "agent-session-2",
        ]
        assert harness.requests[0].max_turns == 12
        assert all("StaffDeck" not in item.system_prompt for item in harness.requests)
        assert chat_session.runtime_state_json["activated_skill_id"] == "graph_demo"
        assert chat_session.runtime_state_json["sdk_session_id"] == "agent-session-3"
        assert chat_session.runtime_state_json["status"] == "completed"
        assert chat_session.active_skill_id is None
        assert any(
            event.event_type == "claude_sop_activated"
            for event in db.exec(select(AgentEvent)).all()
        )


def test_skillless_greeting_uses_claude_agent_path_through_stream_endpoint() -> None:
    with _test_session() as db:
        chat_session, _skill_row, _tool = _seed_runtime(db)
        chat_session.active_skill_id = None
        chat_session.active_step_id = None
        model = db.get(ModelConfig, "model_claude")
        assert model is not None
        model.is_default = True
        db.add(model)
        db.add(chat_session)
        db.commit()

        loop = AgentLoop(db)
        harness = _ConversationHarness()
        loop.claude_runtime = harness  # type: ignore[assignment]
        loop.router.decide = lambda *args, **kwargs: RouterDecision(  # type: ignore[method-assign]
            decision="answer_only",
            confidence=0.99,
            user_intent="用户日常问候",
            reason="无明确业务诉求，不需要 SOP。",
            source_message="你好",
        )
        loop._select_general_capability = lambda *args, **kwargs: (  # type: ignore[method-assign]
            None,
            None,
        )
        loop._pace_stream = lambda: None  # type: ignore[method-assign]

        events = list(
            loop.handle_turn_stream(
                ChatTurnRequest(
                    tenant_id="tenant_demo",
                    session_id=chat_session.id,
                    user_id="user_demo",
                    message="你好",
                )
            )
        )

        complete = next(item for item in events if item["event"] == "complete")
        assert complete["data"]["reply"] == "普通对话回复 1"
        assert any(
            item["event"] == "status"
            and item["data"].get("phase") == "claude_conversation"
            for item in events
        )
        assert not any(item["event"] == "error" for item in events)
        assert harness.requests[0].resume_session_id is None


def test_router_sop_match_is_advisory_until_claude_requests_activation() -> None:
    with _test_session() as db:
        chat_session, skill_row, tool = _seed_runtime(db)
        chat_session.active_skill_id = None
        chat_session.active_step_id = None
        model = db.get(ModelConfig, "model_claude")
        assert model is not None
        model.is_default = True
        db.add(model)
        db.add(chat_session)
        db.commit()

        loop = AgentLoop(db)
        harness = _ConversationHarness()
        loop.claude_runtime = harness  # type: ignore[assignment]
        loop._list_published_skills = lambda *args, **kwargs: [  # type: ignore[method-assign]
            skill_row
        ]
        loop._list_enabled_tools = lambda *args, **kwargs: [tool]  # type: ignore[method-assign]
        loop.router.decide = lambda *args, **kwargs: RouterDecision(  # type: ignore[method-assign]
            decision="start_new_task",
            target_skill_id="graph_demo",
            target_step_id="collect",
            confidence=0.99,
            user_intent="生成研究执行方案但暂不运行",
            reason="任务与研究 SOP 匹配。",
            source_message="先给方案，不要运行",
        )
        loop._pace_stream = lambda: None  # type: ignore[method-assign]

        events = list(
            loop.handle_turn_stream(
                ChatTurnRequest(
                    tenant_id="tenant_demo",
                    session_id=chat_session.id,
                    user_id="user_demo",
                    message="先给方案，不要运行",
                )
            )
        )
        db.refresh(chat_session)

        complete = next(item for item in events if item["event"] == "complete")
        assert complete["data"]["reply"] == "普通对话回复 1"
        assert '"router_suggestion": "graph_demo"' in harness.requests[0].prompt
        assert harness.requests[0].max_turns == 3
        assert [tool.name for tool in harness.requests[0].tools] == [
            "product.price_query",
            "staffdeck.activate_sop",
        ]
        execute_tool = harness.requests[0].execute_tool
        assert execute_tool is not None
        denied = execute_tool("product.price_query", {"product_name": "A1"})
        assert isinstance(denied, dict)
        assert denied["success"] is False
        assert denied["error"]["code"] == "SOP_ACTIVATION_REQUIRED"
        assert chat_session.active_skill_id is None
        assert chat_session.active_step_id is None
        assert not any(
            event.event_type == "claude_sop_activated"
            for event in db.exec(select(AgentEvent)).all()
        )


def test_supervised_loop_stops_before_write_tool_and_requests_explicit_approval() -> None:
    with _test_session() as db:
        chat_session, skill, tool = _seed_runtime(db, effect_level="write")
        chat_session.slots_json = {"product_name": "A1", "request_type": "notice"}
        chat_session.active_step_id = "query"
        db.add(chat_session)
        db.commit()
        loop = AgentLoop(db)
        loop.claude_runtime = _UnexpectedHarness()  # type: ignore[assignment]

        outcome = _consume_return(
            loop._stream_claude_supervised_response(
                ChatTurnRequest(
                    tenant_id="tenant_demo",
                    session_id=chat_session.id,
                    user_id="user_demo",
                    message="发送通知",
                ),
                chat_session,
                skill,
                [tool],
                None,
                {},
                "turn_demo",
            )
        )

        assert "确认执行" in outcome.reply
        assert chat_session.awaiting_input_json["kind"] == "runtime_tool_approval"
        assert chat_session.runtime_state_json["status"] == "awaiting_approval"
        assert chat_session.active_step_id == "query"


def test_non_stream_endpoint_delegates_to_supervised_stream_instead_of_legacy_agents() -> None:
    with _test_session() as db:
        chat_session, _skill_row, _tool = _seed_runtime(db)
        loop = AgentLoop(db)

        def fail_legacy_prepare(*_args, **_kwargs):  # noqa: ANN202
            raise AssertionError("Legacy StepAgent path must not run")

        loop._prepare_turn = fail_legacy_prepare  # type: ignore[method-assign]
        loop.handle_turn_stream = lambda _request: iter(  # type: ignore[method-assign]
            [
                {
                    "event": "complete",
                    "data": {
                        "reply": "监督执行完成",
                        "session_id": chat_session.id,
                        "session_state": {
                            "session_id": chat_session.id,
                            "tenant_id": chat_session.tenant_id,
                            "user_id": chat_session.user_id,
                            "runtime_mode": "claude_supervised",
                        },
                    },
                }
            ]
        )

        result = loop.handle_turn(
            ChatTurnRequest(
                tenant_id="tenant_demo",
                session_id=chat_session.id,
                user_id="user_demo",
                message="继续",
            )
        )

        assert result.reply == "监督执行完成"
        assert result.session_state.runtime_mode == "claude_supervised"


def test_supervisor_owns_confirmation_and_handoff_checkpoints_without_calling_claude() -> None:
    with _test_session() as db:
        chat_session, skill, _tool = _seed_runtime(db)
        skill.content_json = {
            "skill_id": "graph_demo",
            "nodes": [
                {
                    "node_id": "confirm",
                    "type": "decision",
                    "expected_user_info": ["confirmation"],
                    "allowed_actions": ["ask_user"],
                },
                {
                    "node_id": "human",
                    "type": "handoff",
                    "allowed_actions": ["handoff_human"],
                },
            ],
            "edges": [],
        }
        chat_session.active_step_id = "confirm"
        chat_session.slots_json = {}
        db.add(skill)
        db.add(chat_session)
        db.commit()
        loop = AgentLoop(db)
        loop.claude_runtime = _UnexpectedHarness()  # type: ignore[assignment]

        confirmation = _consume_return(
            loop._stream_claude_supervised_response(
                ChatTurnRequest(
                    tenant_id="tenant_demo",
                    session_id=chat_session.id,
                    user_id="user_demo",
                    message="继续",
                ),
                chat_session,
                skill,
                [],
                None,
                {},
                "turn_confirm",
            )
        )
        assert confirmation.step_result.action == "ask_user"
        assert chat_session.awaiting_input_json["kind"] == "sop_confirmation"

        chat_session.active_step_id = "human"
        chat_session.awaiting_input_json = None
        db.add(chat_session)
        db.commit()
        handoff = _consume_return(
            loop._stream_claude_supervised_response(
                ChatTurnRequest(
                    tenant_id="tenant_demo",
                    session_id=chat_session.id,
                    user_id="user_demo",
                    message="转人工",
                ),
                chat_session,
                skill,
                [],
                None,
                {},
                "turn_handoff",
            )
        )

        assert handoff.step_result.handoff is True
        assert chat_session.status == "handoff"
        assert db.exec(select(HumanHandoffRequest)).first() is not None
