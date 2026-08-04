from __future__ import annotations

import pytest

from app.runtime.claude_sdk import ClaudeAgentSdkAdapter
from app.runtime.contracts import (
    HarnessRunRequest,
    HarnessRunResult,
    HarnessStructuredOutput,
    HarnessTool,
)
from app.runtime.legacy import LegacyRuntimeAdapter


class _FakeClient:
    def __init__(self, options, messages):  # noqa: ANN001
        self.options = options
        self.messages = messages
        self.queries: list[str] = []
        self.interrupted = False

    async def __aenter__(self):  # noqa: ANN204
        return self

    async def __aexit__(self, *_args):  # noqa: ANN204
        return None

    async def query(self, prompt: str) -> None:
        self.queries.append(prompt)

    async def receive_response(self):  # noqa: ANN201
        for message in self.messages:
            yield message

    async def interrupt(self) -> None:
        self.interrupted = True


@pytest.mark.asyncio
async def test_adapter_disables_builtin_tools_and_resumes_sdk_session() -> None:
    from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

    created: list[_FakeClient] = []

    def factory(options):  # noqa: ANN001, ANN202
        client = _FakeClient(
            options,
            [
                AssistantMessage(content=[TextBlock("已完成")], model="claude-test"),
                ResultMessage(
                    subtype="success",
                    duration_ms=10,
                    duration_api_ms=8,
                    is_error=False,
                    num_turns=2,
                    session_id="sdk-session-1",
                    structured_output={"reply": "已完成", "slot_updates": {"x": "1"}},
                ),
            ],
        )
        created.append(client)
        return client

    adapter = ClaudeAgentSdkAdapter(client_factory=factory)
    result = await adapter.run_segment(
        HarnessRunRequest(
            run_id="turn-1",
            model="claude-test",
            api_key="secret",
            prompt="执行 SOP",
            system_prompt="严格遵守 StaffDeck",
            resume_session_id="sdk-session-old",
            tools=[
                HarnessTool(
                    name="product.price_query",
                    description="query price",
                    input_schema={"type": "object"},
                )
            ],
            execute_tool=lambda name, arguments: {
                "tool_name": name,
                "arguments": arguments,
                "success": True,
            },
        )
    )

    options = created[0].options
    assert options.tools == []
    assert options.strict_mcp_config is True
    assert set(options.mcp_servers) == {"staffdeck"}
    assert options.allowed_tools == ["mcp__staffdeck__product_price_query"]
    assert options.resume == "sdk-session-old"
    assert options.env["ANTHROPIC_API_KEY"] == "secret"
    assert result.session_id == "sdk-session-1"
    assert result.output == HarnessStructuredOutput(reply="已完成", slot_updates={"x": "1"})


@pytest.mark.asyncio
async def test_adapter_reports_invalid_or_missing_structured_output() -> None:
    from claude_agent_sdk import ResultMessage

    def factory(options):  # noqa: ANN001, ANN202
        return _FakeClient(
            options,
            [
                ResultMessage(
                    subtype="success",
                    duration_ms=10,
                    duration_api_ms=8,
                    is_error=False,
                    num_turns=1,
                    session_id="sdk-session-1",
                    structured_output=None,
                )
            ],
        )

    result = await ClaudeAgentSdkAdapter(client_factory=factory).run_segment(
        HarnessRunRequest(
            run_id="turn-1",
            model="claude-test",
            api_key="secret",
            prompt="执行 SOP",
            system_prompt="system",
        )
    )

    assert result.is_error is True
    assert result.error_code == "invalid_structured_output"


@pytest.mark.asyncio
async def test_legacy_adapter_uses_same_contract_for_resume_and_cancel() -> None:
    requests: list[HarnessRunRequest] = []

    def run(request: HarnessRunRequest) -> HarnessRunResult:
        requests.append(request)
        return HarnessRunResult(
            session_id=request.resume_session_id,
            output=HarnessStructuredOutput(reply="legacy"),
        )

    adapter = LegacyRuntimeAdapter(
        run,
        cancel_runner=lambda run_id: run_id == "turn-1",
    )
    request = HarnessRunRequest(
        run_id="turn-1",
        model="legacy-model",
        api_key="secret",
        prompt="run",
        system_prompt="system",
    )

    result = await adapter.resume("legacy-checkpoint", request)

    assert result.session_id == "legacy-checkpoint"
    assert requests[0].resume_session_id == "legacy-checkpoint"
    assert adapter.cancel("turn-1") is True
