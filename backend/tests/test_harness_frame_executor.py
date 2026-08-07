from __future__ import annotations

from collections.abc import Awaitable
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, select

from app.capabilities.contracts import CapabilityDescriptor, CapabilityManifest
from app.core.harness_frame_executor import HarnessFrameExecutor
from app.core.harness_task_frame_store import TaskFrameStore
from app.db.models import ChatSession, HarnessRunRecord, HarnessTaskFrameRecord
from app.harness.task_request import TaskRequirement
from app.harness.task_schema import PlannedTaskFrame
from app.runtime.contracts import (
    HarnessRunRequest,
    HarnessRunResult,
    HarnessStructuredOutput,
)


def _memory_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _claimed_frame(db: Session) -> HarnessTaskFrameRecord:
    session = ChatSession(id="session-1", tenant_id="tenant-demo")
    db.add(session)
    db.commit()
    frame = TaskFrameStore(db).persist_plan(
        session,
        "turn-1",
        [
            PlannedTaskFrame(
                task_id="task-1",
                kind="sop",
                target_skill_id="research",
                target_step_id="collect",
            )
        ],
    )[0]
    return TaskFrameStore(db).claim(frame.id, "worker-1")


def _requirement() -> TaskRequirement:
    return TaskRequirement(
        task_frame_id="task-1",
        kind="sop",
        goal="完成研究",
        requirements=["查询并形成结论"],
        allowed_transitions=[{"next_node_id": "reply"}],
        capability_manifest=CapabilityManifest(
            available=[
                CapabilityDescriptor(
                    capability_id="lookup",
                    name="catalog.lookup",
                    kind="tool",
                    effect_level="read",
                    description="查询目录",
                    input_schema={"type": "object"},
                ),
                CapabilityDescriptor(
                    capability_id="send",
                    name="send_file",
                    kind="file",
                    effect_level="write",
                    description="发送文件",
                    input_schema={"type": "object"},
                ),
            ],
            snapshot_revision="sha256:test",
        ),
    )


class _FakeRuntime:
    def __init__(
        self,
        result: HarnessRunResult,
        tool_calls: list[tuple[str, dict[str, Any]]] | None = None,
    ) -> None:
        self.result = result
        self.tool_calls = list(tool_calls or [])
        self.requests: list[HarnessRunRequest] = []
        self.tool_results: list[dict[str, Any]] = []

    async def run_segment(self, request: HarnessRunRequest) -> HarnessRunResult:
        self.requests.append(request)
        assert request.execute_tool is not None
        for name, arguments in self.tool_calls:
            invoked = request.execute_tool(name, arguments)
            if isinstance(invoked, Awaitable):
                invoked = await invoked
            self.tool_results.append(invoked)
        return self.result

    async def resume(
        self,
        checkpoint_id: str,
        request: HarnessRunRequest,
    ) -> HarnessRunResult:
        request.resume_session_id = checkpoint_id
        return await self.run_segment(request)

    def cancel(self, run_id: str) -> bool:
        return bool(run_id)


@pytest.mark.asyncio
async def test_executor_delegates_one_continuous_run_and_keeps_graph_uncommitted() -> None:
    engine = _memory_engine()
    calls: list[tuple[str, dict[str, Any]]] = []

    def invoke(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        calls.append((name, arguments))
        return {"success": True, "data": {"items": ["A1"]}}

    runtime = _FakeRuntime(
        HarnessRunResult(
            session_id="sdk-session-new",
            output=HarnessStructuredOutput(
                reply="研究完成",
                slot_updates={"marketplace": "US"},
                completed_step_ids=["collect"],
                evidence_refs=["catalog.lookup:1"],
                next_step_id="reply",
            ),
            num_turns=4,
        ),
        tool_calls=[
            ("catalog.lookup", {"query": "A1"}),
            ("catalog.lookup", {"query": "A2"}),
        ],
    )
    with Session(engine) as db:
        frame = _claimed_frame(db)
        result = await HarnessFrameExecutor(db).execute(
            frame,
            lease_owner="worker-1",
            requirement=_requirement(),
            runtime=runtime,
            model="claude-test",
            api_key="secret",
            invoke_capability=invoke,
            resume_session_id="sdk-session-old",
            max_turns=12,
            system_prompt="你是 QQQ Claude。",
            runtime_context="保留本轮 Router 与可用 SOP 上下文。",
        )
        db.refresh(frame)
        runs = db.exec(select(HarnessRunRecord)).all()

    assert len(runtime.requests) == 1
    request = runtime.requests[0]
    assert request.resume_session_id == "sdk-session-old"
    assert request.max_turns == 12
    assert [tool.name for tool in request.tools] == ["catalog.lookup", "send_file"]
    assert request.tools[1].effect_level.value == "write"
    assert "StaffDeck" not in request.system_prompt
    assert "连续" in request.system_prompt
    assert request.system_prompt.startswith("你是 QQQ Claude。")
    assert request.prompt.startswith("保留本轮 Router 与可用 SOP 上下文。")
    assert "TaskRequirement" in request.prompt
    assert calls == [
        ("catalog.lookup", {"query": "A1"}),
        ("catalog.lookup", {"query": "A2"}),
    ]
    assert result.status == "completed"
    assert result.completed_step_ids == ["collect"]
    assert result.next_step_id == "reply"
    assert result.action_count == 4
    assert result.capability_results[0]["tool_name"] == "catalog.lookup"
    assert result.capability_results[0]["arguments"] == {"query": "A1"}
    assert runs[0].status == "completed"
    assert runs[0].task_requirement_json["goal"] == "完成研究"
    assert frame.status == "running"
    assert frame.step_id == "collect"
    assert request.execute_tool("catalog.lookup", {"query": "late"}) == {
        "success": False,
        "error": {
            "code": "HARNESS_RUN_CLOSED",
            "message": "本次 Runtime 已结束，不能继续调用能力。",
        },
    }


@pytest.mark.asyncio
async def test_executor_forces_approval_result_to_awaiting_user() -> None:
    engine = _memory_engine()

    def invoke(_name: str, _arguments: dict[str, Any]) -> dict[str, Any]:
        return {
            "success": False,
            "error": {"code": "APPROVAL_REQUIRED", "message": "需要确认"},
        }

    runtime = _FakeRuntime(
        HarnessRunResult(
            output=HarnessStructuredOutput(reply="我已经发送了文件"),
            num_turns=2,
        ),
        tool_calls=[("send_file", {"path": "report.html"})],
    )
    with Session(engine) as db:
        frame = _claimed_frame(db)
        result = await HarnessFrameExecutor(db).execute(
            frame,
            lease_owner="worker-1",
            requirement=_requirement(),
            runtime=runtime,
            model="claude-test",
            api_key="secret",
            invoke_capability=invoke,
        )

    assert result.status == "awaiting_user"
    assert result.reply_fragment != "我已经发送了文件"
    assert result.error == {"code": "APPROVAL_REQUIRED", "message": "需要确认"}


@pytest.mark.asyncio
async def test_executor_fails_closed_when_runtime_errors() -> None:
    engine = _memory_engine()
    runtime = _FakeRuntime(
        HarnessRunResult(
            is_error=True,
            error_code="runtime_down",
            error_message="SDK unavailable",
            num_turns=1,
        )
    )
    with Session(engine) as db:
        frame = _claimed_frame(db)
        result = await HarnessFrameExecutor(db).execute(
            frame,
            lease_owner="worker-1",
            requirement=_requirement(),
            runtime=runtime,
            model="claude-test",
            api_key="secret",
            invoke_capability=lambda _name, _arguments: {"success": True},
        )
        run = db.exec(select(HarnessRunRecord)).one()

    assert result.status == "failed"
    assert result.error == {"code": "runtime_down", "message": "SDK unavailable"}
    assert run.status == "failed"


@pytest.mark.asyncio
async def test_executor_persists_runtime_cancellation_as_cancelled() -> None:
    engine = _memory_engine()
    runtime = _FakeRuntime(
        HarnessRunResult(
            is_error=True,
            error_code="runtime_cancelled",
            error_message="request cancelled",
            num_turns=1,
        )
    )
    with Session(engine) as db:
        frame = _claimed_frame(db)
        result = await HarnessFrameExecutor(db).execute(
            frame,
            lease_owner="worker-1",
            requirement=_requirement(),
            runtime=runtime,
            model="claude-test",
            api_key="secret",
            invoke_capability=lambda _name, _arguments: {"success": True},
        )
        run = db.exec(select(HarnessRunRecord)).one()

    assert result.status == "cancelled"
    assert result.reply_fragment == "本次执行已取消。"
    assert run.status == "cancelled"


@pytest.mark.asyncio
async def test_executor_replays_duplicate_write_without_repeating_side_effect() -> None:
    engine = _memory_engine()
    side_effects: list[dict[str, Any]] = []

    def invoke(_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        side_effects.append(arguments)
        return {"success": True, "data": {"artifact_id": "artifact-1"}}

    runtime = _FakeRuntime(
        HarnessRunResult(
            output=HarnessStructuredOutput(reply="文件已发送"),
            num_turns=2,
        ),
        tool_calls=[
            ("send_file", {"path": "report.html"}),
            ("send_file", {"path": "report.html"}),
        ],
    )
    with Session(engine) as db:
        frame = _claimed_frame(db)
        await HarnessFrameExecutor(db).execute(
            frame,
            lease_owner="worker-1",
            requirement=_requirement(),
            runtime=runtime,
            model="claude-test",
            api_key="secret",
            invoke_capability=invoke,
        )

    assert side_effects == [{"path": "report.html"}]
    assert runtime.tool_results[0]["data"] == {"artifact_id": "artifact-1"}
    assert runtime.tool_results[1]["idempotent_replay"] is True


@pytest.mark.asyncio
async def test_executor_replays_write_across_repair_runs_for_same_task_frame() -> None:
    engine = _memory_engine()
    side_effect_count = 0

    def invoke(_name: str, _arguments: dict[str, Any]) -> dict[str, Any]:
        nonlocal side_effect_count
        side_effect_count += 1
        return {"success": True, "data": {"artifact_id": "artifact-1"}}

    first_runtime = _FakeRuntime(
        HarnessRunResult(output=HarnessStructuredOutput(reply="候选"), num_turns=1),
        tool_calls=[("send_file", {"path": "report.html"})],
    )
    repair_runtime = _FakeRuntime(
        HarnessRunResult(output=HarnessStructuredOutput(reply="修复完成"), num_turns=1),
        tool_calls=[("send_file", {"path": "report.html"})],
    )
    with Session(engine) as db:
        frame = _claimed_frame(db)
        executor = HarnessFrameExecutor(db)
        await executor.execute(
            frame,
            lease_owner="worker-1",
            requirement=_requirement(),
            runtime=first_runtime,
            model="claude-test",
            api_key="secret",
            invoke_capability=invoke,
        )
        await executor.execute(
            frame,
            lease_owner="worker-1",
            requirement=_requirement(),
            runtime=repair_runtime,
            model="claude-test",
            api_key="secret",
            invoke_capability=invoke,
        )
        runs = db.exec(select(HarnessRunRecord)).all()

    assert side_effect_count == 1
    assert repair_runtime.tool_results[0]["idempotent_replay"] is True
    assert len(runs) == 2
