from __future__ import annotations

import asyncio
import json
import threading
from types import SimpleNamespace

from app.runtime.contracts import (
    HarnessRunRequest,
    HarnessRunResult,
    HarnessTool,
    ToolEffectLevel,
)
from app.runtime.openai_compatible import OpenAICompatibleRuntimeAdapter


def _tool_call(call_id: str, name: str, arguments: dict[str, object]) -> SimpleNamespace:
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments)),
    )


class _Completions:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            message = SimpleNamespace(
                content="",
                tool_calls=[_tool_call("call-1", "price_query", {"asin": "A1"})],
            )
        else:
            message = SimpleNamespace(
                content="",
                tool_calls=[
                    _tool_call(
                        "call-2",
                        "submit_result",
                        {"reply": "A1 当前价格 99 元", "slot_updates": {"asin": "A1"}},
                    )
                ],
            )
        return SimpleNamespace(
            id=f"response-{len(self.calls)}",
            choices=[SimpleNamespace(message=message, finish_reason="tool_calls")],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )


class _Client:
    def __init__(self) -> None:
        self.chat = SimpleNamespace(completions=_Completions())
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_openai_runtime_owns_continuous_tool_loop_and_submits_structured_result() -> None:
    client = _Client()
    tool_calls: list[tuple[str, dict[str, object]]] = []
    runtime = OpenAICompatibleRuntimeAdapter(client_factory=lambda **_kwargs: client)

    result = asyncio.run(
        runtime.run_segment(
            HarnessRunRequest(
                run_id="run-1",
                model="demo-model",
                api_key="secret",
                prompt="查询 A1 价格",
                system_prompt="你是选品员工",
                tools=[
                    HarnessTool(
                        name="price_query",
                        input_schema={
                            "type": "object",
                            "properties": {"asin": {"type": "string"}},
                            "required": ["asin"],
                        },
                        effect_level=ToolEffectLevel.READ,
                    )
                ],
                execute_tool=lambda name, arguments: (
                    tool_calls.append((name, arguments))
                    or {"success": True, "data": {"price": 99}}
                ),
                max_turns=4,
                environment={
                    "OPENAI_TEMPERATURE": "0.25",
                    "OPENAI_MAX_OUTPUT_TOKENS": "2048",
                    "OPENAI_EXTRA_BODY_JSON": '{"thinking":{"type":"disabled"}}',
                },
            )
        )
    )

    assert result.is_error is False
    assert result.output.reply == "A1 当前价格 99 元"
    assert result.output.slot_updates == {"asin": "A1"}
    assert result.num_turns == 2
    assert tool_calls == [("price_query", {"asin": "A1"})]
    first_call = client.chat.completions.calls[0]
    assert first_call["temperature"] == 0.25
    assert first_call["max_tokens"] == 2048
    assert first_call["extra_body"] == {"thinking": {"type": "disabled"}}
    second_messages = client.chat.completions.calls[1]["messages"]
    assert any(item.get("role") == "tool" and "99" in item.get("content", "") for item in second_messages)
    assert client.closed is True


def test_openai_runtime_fails_closed_when_action_budget_is_exhausted() -> None:
    class EndlessCompletions(_Completions):
        def create(self, **kwargs):
            self.calls.append(kwargs)
            message = SimpleNamespace(
                content="",
                tool_calls=[_tool_call(f"call-{len(self.calls)}", "price_query", {"asin": "A1"})],
            )
            return SimpleNamespace(
                choices=[SimpleNamespace(message=message, finish_reason="tool_calls")],
                usage=None,
            )

    client = _Client()
    client.chat.completions = EndlessCompletions()
    runtime = OpenAICompatibleRuntimeAdapter(client_factory=lambda **_kwargs: client)
    result = asyncio.run(
        runtime.run_segment(
            HarnessRunRequest(
                run_id="run-budget",
                model="demo-model",
                api_key="secret",
                prompt="循环",
                system_prompt="system",
                tools=[HarnessTool(name="price_query")],
                execute_tool=lambda *_args: {"success": True},
                max_turns=2,
            )
        )
    )

    assert result.is_error is True
    assert result.error_code == "action_budget_exhausted"
    assert result.num_turns == 2


def test_openai_runtime_cancel_closes_active_provider_request() -> None:
    started = threading.Event()
    released = threading.Event()

    class BlockingCompletions:
        def create(self, **_kwargs):
            started.set()
            released.wait(timeout=5)
            raise RuntimeError("provider connection closed")

    class BlockingClient:
        def __init__(self) -> None:
            self.chat = SimpleNamespace(completions=BlockingCompletions())

        def close(self) -> None:
            released.set()

    runtime = OpenAICompatibleRuntimeAdapter(
        client_factory=lambda **_kwargs: BlockingClient()
    )
    captured: list[object] = []

    def run() -> None:
        captured.append(
            asyncio.run(
                runtime.run_segment(
                    HarnessRunRequest(
                        run_id="run-cancel",
                        model="demo-model",
                        api_key="secret",
                        prompt="等待",
                        system_prompt="system",
                    )
                )
            )
        )

    thread = threading.Thread(target=run)
    thread.start()
    assert started.wait(timeout=2)
    assert runtime.cancel("run-cancel") is True
    thread.join(timeout=5)

    assert not thread.is_alive()
    result = captured[0]
    assert isinstance(result, HarnessRunResult)
    assert result.error_code == "runtime_cancelled"
