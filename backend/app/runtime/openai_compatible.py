from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import re
import threading
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from openai import OpenAI
from pydantic import ValidationError

from app.runtime.contracts import (
    HarnessRunRequest,
    HarnessRunResult,
    HarnessStructuredOutput,
    HarnessTool,
)

_SUBMIT_TOOL_NAME = "submit_result"
_SUBMIT_SCHEMA: dict[str, Any] = {
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
        self._runs: dict[str, tuple[Any, threading.Event]] = {}

    def bind(self, run_id: str, client: Any, cancelled: threading.Event) -> None:
        with self._lock:
            self._runs[run_id] = (client, cancelled)

    def release(self, run_id: str) -> None:
        with self._lock:
            self._runs.pop(run_id, None)

    def cancel(self, run_id: str) -> bool:
        with self._lock:
            active = self._runs.get(run_id)
        if active is None:
            return False
        client, cancelled = active
        cancelled.set()
        close = getattr(client, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001 - cancellation is already accepted
                return True
        return True


ACTIVE_OPENAI_RUNS = _ActiveRuns()


class OpenAICompatibleRuntimeAdapter:
    """Autonomous, transcript-preserving tool loop for OpenAI-compatible models."""

    def __init__(
        self,
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        self._client_factory = client_factory

    async def run_segment(self, request: HarnessRunRequest) -> HarnessRunResult:
        client = self._client(request)
        cancelled = threading.Event()
        ACTIVE_OPENAI_RUNS.bind(request.run_id, client, cancelled)
        tool_names = _tool_name_map(request.tools)
        inverse_names = {sdk_name: source_name for source_name, sdk_name in tool_names.items()}
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": _system_prompt(request.system_prompt)},
            {"role": "user", "content": request.prompt},
        ]
        tool_specs = [
            _tool_spec(tool, tool_names[tool.name]) for tool in request.tools
        ]
        tool_specs.append(
            {
                "type": "function",
                "function": {
                    "name": _SUBMIT_TOOL_NAME,
                    "description": "提交本次 TaskRequirement 的最终结构化结果。",
                    "parameters": _SUBMIT_SCHEMA,
                },
            }
        )
        usage: dict[str, int] = {}
        text_parts: list[str] = []
        try:
            self._emit(request, "harness.started", {"resume": request.resume_session_id})
            for turn_no in range(1, max(1, request.max_turns) + 1):
                if cancelled.is_set():
                    return _cancelled_result(request, turn_no - 1, usage, text_parts)
                completion = await asyncio.to_thread(
                    client.chat.completions.create,
                    model=request.model,
                    messages=messages,
                    tools=tool_specs,
                    tool_choice="auto",
                    **_completion_options(request),
                )
                _merge_usage(usage, getattr(completion, "usage", None))
                message = _first_message(completion)
                content = _content_text(getattr(message, "content", None))
                if content:
                    text_parts.append(content)
                calls = list(getattr(message, "tool_calls", None) or [])
                if not calls:
                    output = _structured_content(content)
                    if output is None:
                        output = HarnessStructuredOutput(reply=content.strip())
                    return self._success_result(
                        request, output, turn_no, usage, text_parts, completion
                    )

                messages.append(_assistant_message(message, content, calls))
                for call in calls:
                    call_id = str(getattr(call, "id", "") or "")
                    function = getattr(call, "function", None)
                    runtime_name = str(getattr(function, "name", "") or "")
                    arguments = _arguments(getattr(function, "arguments", "{}"))
                    if runtime_name == _SUBMIT_TOOL_NAME:
                        try:
                            output = HarnessStructuredOutput.model_validate(arguments)
                        except ValidationError as exc:
                            messages.append(
                                _tool_message(
                                    call_id,
                                    runtime_name,
                                    {"success": False, "error": {"code": "INVALID_RESULT", "message": str(exc)}},
                                )
                            )
                            continue
                        return self._success_result(
                            request, output, turn_no, usage, text_parts, completion
                        )
                    source_name = inverse_names.get(runtime_name)
                    if source_name is None or request.execute_tool is None:
                        result = {
                            "success": False,
                            "error": {
                                "code": "CAPABILITY_NOT_AVAILABLE",
                                "message": "该能力不在当前冻结清单中。",
                            },
                        }
                    else:
                        self._emit(
                            request,
                            "harness.tool_started",
                            {"tool_name": source_name, "turn": turn_no},
                        )
                        result = request.execute_tool(source_name, arguments)
                        if inspect.isawaitable(result):
                            result = await result
                        self._emit(
                            request,
                            "harness.tool_finished",
                            {
                                "tool_name": source_name,
                                "turn": turn_no,
                                "success": bool(result.get("success")),
                            },
                        )
                    messages.append(_tool_message(call_id, runtime_name, result))

            return HarnessRunResult(
                session_id=request.resume_session_id or f"openai:{request.run_id}",
                is_error=True,
                error_code="action_budget_exhausted",
                error_message="Runtime reached its autonomous action budget.",
                text="\n".join(text_parts).strip(),
                num_turns=max(1, request.max_turns),
                usage=usage,
                stop_reason="max_turns",
            )
        except Exception as exc:  # noqa: BLE001 - normalize provider failures
            if cancelled.is_set():
                return _cancelled_result(request, 0, usage, text_parts)
            self._emit(request, "harness.failed", {"error": str(exc)})
            return HarnessRunResult(
                session_id=request.resume_session_id or f"openai:{request.run_id}",
                is_error=True,
                error_code="openai_compatible_runtime_error",
                error_message=str(exc),
                text="\n".join(text_parts).strip(),
                usage=usage,
            )
        finally:
            ACTIVE_OPENAI_RUNS.release(request.run_id)
            close = getattr(client, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:  # noqa: BLE001 - run result is already finalized
                    close = None

    async def resume(
        self, checkpoint_id: str, request: HarnessRunRequest
    ) -> HarnessRunResult:
        return await self.run_segment(replace(request, resume_session_id=checkpoint_id))

    def cancel(self, run_id: str) -> bool:
        return ACTIVE_OPENAI_RUNS.cancel(run_id)

    def _client(self, request: HarnessRunRequest) -> Any:
        kwargs: dict[str, Any] = {"api_key": request.api_key}
        base_url = str(request.environment.get("OPENAI_BASE_URL") or "").strip()
        if base_url:
            kwargs["base_url"] = base_url
        timeout = str(request.environment.get("OPENAI_TIMEOUT_SECONDS") or "").strip()
        if timeout:
            kwargs["timeout"] = float(timeout)
        factory = self._client_factory or OpenAI
        return factory(**kwargs)

    def _success_result(
        self,
        request: HarnessRunRequest,
        output: HarnessStructuredOutput,
        turn_no: int,
        usage: dict[str, int],
        text_parts: list[str],
        completion: Any,
    ) -> HarnessRunResult:
        result = HarnessRunResult(
            session_id=request.resume_session_id or f"openai:{request.run_id}",
            output=output,
            text="\n".join(text_parts).strip(),
            num_turns=turn_no,
            usage=usage,
            stop_reason=_finish_reason(completion),
        )
        self._emit(
            request,
            "harness.completed",
            {"session_id": result.session_id, "num_turns": turn_no},
        )
        return result

    def _emit(self, request: HarnessRunRequest, event_type: str, payload: dict[str, Any]) -> None:
        if request.event_sink is not None:
            request.event_sink(event_type, payload)


def _system_prompt(prompt: str) -> str:
    return (
        f"{prompt.strip()}\n\n"
        "你拥有本次 TaskRequirement 的连续自主工具循环。每轮至多调用一个能力，并在收到真实结果后再决定下一步。"
        "任务完成或需要用户输入时，必须调用 submit_result；不得用正文假装已经调用工具。"
    ).strip()


def _completion_options(request: HarnessRunRequest) -> dict[str, Any]:
    options: dict[str, Any] = {}
    temperature = str(request.environment.get("OPENAI_TEMPERATURE") or "").strip()
    if temperature:
        options["temperature"] = float(temperature)
    max_tokens = str(request.environment.get("OPENAI_MAX_OUTPUT_TOKENS") or "").strip()
    if max_tokens:
        options["max_tokens"] = max(1, int(max_tokens))
    raw_extra = str(request.environment.get("OPENAI_EXTRA_BODY_JSON") or "").strip()
    if raw_extra:
        parsed = json.loads(raw_extra)
        if not isinstance(parsed, dict):
            raise ValueError("OPENAI_EXTRA_BODY_JSON must contain a JSON object")
        options["extra_body"] = parsed
    return options


def _tool_name_map(tools: list[HarnessTool]) -> dict[str, str]:
    result: dict[str, str] = {}
    used = {_SUBMIT_TOOL_NAME}
    for tool in tools:
        normalized = re.sub(r"[^a-zA-Z0-9_-]+", "_", tool.name).strip("_") or "capability"
        candidate = normalized[:64]
        if candidate in used:
            digest = hashlib.sha256(tool.name.encode("utf-8")).hexdigest()[:8]
            candidate = f"{normalized[:55]}_{digest}"
        used.add(candidate)
        result[tool.name] = candidate
    return result


def _tool_spec(tool: HarnessTool, runtime_name: str) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": runtime_name,
            "description": tool.description or tool.name,
            "parameters": tool.input_schema or {"type": "object"},
        },
    }


def _assistant_message(message: Any, content: str, calls: list[Any]) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": content or None,
        "tool_calls": [
            {
                "id": str(getattr(call, "id", "") or ""),
                "type": "function",
                "function": {
                    "name": str(getattr(getattr(call, "function", None), "name", "") or ""),
                    "arguments": str(
                        getattr(getattr(call, "function", None), "arguments", "{}") or "{}"
                    ),
                },
            }
            for call in calls
        ],
    }


def _tool_message(call_id: str, name: str, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "name": name,
        "content": json.dumps(result, ensure_ascii=False, default=str),
    }


def _arguments(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    try:
        parsed = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _structured_content(content: str) -> HarnessStructuredOutput | None:
    try:
        parsed = json.loads(content)
    except (TypeError, json.JSONDecodeError):
        return None
    try:
        return HarnessStructuredOutput.model_validate(parsed)
    except ValidationError:
        return None


def _first_message(completion: Any) -> Any:
    choices = getattr(completion, "choices", None) or []
    if not choices:
        raise RuntimeError("OpenAI-compatible provider returned no choices.")
    return getattr(choices[0], "message", None)


def _content_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(
            str(item.get("text") or "")
            for item in value
            if isinstance(item, dict) and item.get("type") == "text"
        )
    return ""


def _merge_usage(target: dict[str, int], usage: object) -> None:
    if usage is None:
        return
    for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = getattr(usage, field, None)
        if value is not None:
            target[field] = target.get(field, 0) + int(value)


def _finish_reason(completion: Any) -> str | None:
    choices = getattr(completion, "choices", None) or []
    return str(getattr(choices[0], "finish_reason", "") or "") or None if choices else None


def _cancelled_result(
    request: HarnessRunRequest,
    num_turns: int,
    usage: dict[str, int],
    text_parts: list[str],
) -> HarnessRunResult:
    return HarnessRunResult(
        session_id=request.resume_session_id or f"openai:{request.run_id}",
        is_error=True,
        error_code="runtime_cancelled",
        error_message="Runtime execution was cancelled.",
        text="\n".join(text_parts).strip(),
        num_turns=max(0, num_turns),
        usage=usage,
        stop_reason="cancelled",
    )


__all__ = ["ACTIVE_OPENAI_RUNS", "OpenAICompatibleRuntimeAdapter"]
