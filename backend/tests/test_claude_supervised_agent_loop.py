from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.core.agent_loop import AgentLoop, ClaudeSupervisedOutcome
from app.db.models import (
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
