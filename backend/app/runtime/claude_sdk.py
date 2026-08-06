from __future__ import annotations

import asyncio
import inspect
import json
import re
import threading
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from app.runtime.contracts import (
    HarnessRunRequest,
    HarnessRunResult,
    HarnessStructuredOutput,
)


STRUCTURED_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "slot_updates": {"type": "object"},
        "completed_step_ids": {"type": "array", "items": {"type": "string"}},
        "evidence_refs": {"type": "array", "items": {"type": "string"}},
        "needs_user_input": {"type": "boolean"},
        "user_question": {"type": ["string", "null"]},
        "next_step_id": {"type": ["string", "null"]},
    },
    "required": ["reply", "slot_updates"],
    "additionalProperties": False,
}


class _ActiveRuns:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runs: dict[str, tuple[asyncio.AbstractEventLoop, Any]] = {}

    def bind(self, run_id: str, client: Any) -> None:
        with self._lock:
            self._runs[run_id] = (asyncio.get_running_loop(), client)

    def release(self, run_id: str) -> None:
        with self._lock:
            self._runs.pop(run_id, None)

    def cancel(self, run_id: str) -> bool:
        with self._lock:
            active = self._runs.get(run_id)
        if not active:
            return False
        loop, client = active
        future = asyncio.run_coroutine_threadsafe(client.interrupt(), loop)
        try:
            future.result(timeout=5)
        except Exception:
            return False
        return True


ACTIVE_CLAUDE_RUNS = _ActiveRuns()


class ClaudeAgentSdkAdapter:
    """Thin, lazy-loaded adapter around Anthropic's Claude Agent SDK."""

    def __init__(self, client_factory: Callable[[Any], Any] | None = None) -> None:
        self._client_factory = client_factory

    async def run_segment(self, request: HarnessRunRequest) -> HarnessRunResult:
        try:
            import claude_agent_sdk as sdk
        except ImportError:
            return HarnessRunResult(
                is_error=True,
                error_code="claude_sdk_unavailable",
                error_message="claude-agent-sdk is not installed",
            )

        tool_functions = self._build_tools(sdk, request)
        mcp_servers: dict[str, Any] = {}
        allowed_tools: list[str] = []
        if tool_functions:
            mcp_servers["runtime"] = sdk.create_sdk_mcp_server(
                name="runtime", version="1.0.0", tools=tool_functions
            )
            allowed_tools = [
                f"mcp__runtime__{self._sdk_tool_name(tool.name)}" for tool in request.tools
            ]
        options = sdk.ClaudeAgentOptions(
            tools=[],
            allowed_tools=allowed_tools,
            disallowed_tools=[
                "Bash",
                "Read",
                "Write",
                "Edit",
                "Glob",
                "Grep",
                "WebFetch",
                "WebSearch",
                "Task",
            ],
            system_prompt=request.system_prompt,
            mcp_servers=mcp_servers,
            strict_mcp_config=True,
            permission_mode="dontAsk",
            setting_sources=[],
            skills=[],
            model=request.model,
            resume=request.resume_session_id,
            max_turns=request.max_turns,
            max_budget_usd=request.max_budget_usd,
            env={"ANTHROPIC_API_KEY": request.api_key, **request.environment},
            output_format={"type": "json_schema", "schema": STRUCTURED_OUTPUT_SCHEMA},
        )
        factory = self._client_factory or sdk.ClaudeSDKClient
        client = factory(options)
        text_parts: list[str] = []
        result_message: Any = None
        try:
            async with client:
                ACTIVE_CLAUDE_RUNS.bind(request.run_id, client)
                self._emit(request, "harness.started", {"resume": request.resume_session_id})
                await client.query(request.prompt)
                async for message in client.receive_response():
                    message_name = type(message).__name__
                    if message_name == "AssistantMessage":
                        for block in getattr(message, "content", []):
                            if type(block).__name__ == "TextBlock":
                                text_parts.append(str(getattr(block, "text", "")))
                            elif type(block).__name__ == "ToolUseBlock":
                                self._emit(
                                    request,
                                    "harness.tool_requested",
                                    {
                                        "tool_name": getattr(block, "name", ""),
                                        "tool_use_id": getattr(block, "id", ""),
                                    },
                                )
                    elif message_name == "ResultMessage":
                        result_message = message
        except Exception as exc:
            self._emit(request, "harness.failed", {"error": str(exc)})
            return HarnessRunResult(
                is_error=True,
                error_code="claude_sdk_error",
                error_message=str(exc),
                text="".join(text_parts).strip(),
            )
        finally:
            ACTIVE_CLAUDE_RUNS.release(request.run_id)

        if result_message is None:
            return HarnessRunResult(
                is_error=True,
                error_code="missing_result_message",
                error_message="Claude SDK completed without ResultMessage",
                text="".join(text_parts).strip(),
            )
        raw_output = getattr(result_message, "structured_output", None)
        try:
            output = HarnessStructuredOutput.model_validate(raw_output)
        except Exception as exc:
            return HarnessRunResult(
                session_id=getattr(result_message, "session_id", None),
                is_error=True,
                error_code="invalid_structured_output",
                error_message=str(exc),
                text="".join(text_parts).strip(),
                num_turns=int(getattr(result_message, "num_turns", 0) or 0),
            )
        is_error = bool(getattr(result_message, "is_error", False))
        result = HarnessRunResult(
            session_id=getattr(result_message, "session_id", None),
            output=output,
            text="".join(text_parts).strip(),
            is_error=is_error,
            error_code="claude_result_error" if is_error else None,
            error_message="; ".join(getattr(result_message, "errors", None) or []) or None,
            num_turns=int(getattr(result_message, "num_turns", 0) or 0),
            usage=dict(getattr(result_message, "usage", None) or {}),
            total_cost_usd=getattr(result_message, "total_cost_usd", None),
            stop_reason=getattr(result_message, "stop_reason", None),
        )
        self._emit(
            request,
            "harness.completed" if not is_error else "harness.failed",
            {
                "session_id": result.session_id,
                "num_turns": result.num_turns,
                "stop_reason": result.stop_reason,
            },
        )
        return result

    def cancel(self, run_id: str) -> bool:
        return ACTIVE_CLAUDE_RUNS.cancel(run_id)

    async def resume(
        self, checkpoint_id: str, request: HarnessRunRequest
    ) -> HarnessRunResult:
        return await self.run_segment(replace(request, resume_session_id=checkpoint_id))

    def _build_tools(self, sdk: Any, request: HarnessRunRequest) -> list[Any]:
        if not request.execute_tool:
            return []
        built: list[Any] = []
        for descriptor in request.tools:
            sdk_name = self._sdk_tool_name(descriptor.name)

            async def execute(arguments: dict[str, Any], *, tool_name: str = descriptor.name):
                self._emit(request, "harness.tool_started", {"tool_name": tool_name})
                result = request.execute_tool(tool_name, arguments)
                if inspect.isawaitable(result):
                    result = await result
                self._emit(
                    request,
                    "harness.tool_finished",
                    {"tool_name": tool_name, "success": bool(result.get("success"))},
                )
                return {
                    "content": [
                        {"type": "text", "text": json.dumps(result, ensure_ascii=False, default=str)}
                    ],
                    "isError": not bool(result.get("success")),
                }

            built.append(sdk.tool(sdk_name, descriptor.description, descriptor.input_schema)(execute))
        return built

    def _sdk_tool_name(self, name: str) -> str:
        normalized = re.sub(r"[^a-zA-Z0-9_-]+", "_", name).strip("_")
        return normalized or "runtime_tool"

    def _emit(self, request: HarnessRunRequest, event_type: str, payload: dict[str, Any]) -> None:
        if request.event_sink:
            request.event_sink(event_type, payload)
