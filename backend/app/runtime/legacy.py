from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import replace

from app.runtime.contracts import HarnessRunRequest, HarnessRunResult


LegacySegmentRunner = Callable[
    [HarnessRunRequest],
    HarnessRunResult | Awaitable[HarnessRunResult],
]


class LegacyRuntimeAdapter:
    """Adapts the existing orchestration entry point to the harness contract."""

    def __init__(
        self,
        runner: LegacySegmentRunner,
        cancel_runner: Callable[[str], bool] | None = None,
    ) -> None:
        self._runner = runner
        self._cancel_runner = cancel_runner

    async def run_segment(self, request: HarnessRunRequest) -> HarnessRunResult:
        result = self._runner(request)
        if inspect.isawaitable(result):
            result = await result
        return result

    async def resume(
        self, checkpoint_id: str, request: HarnessRunRequest
    ) -> HarnessRunResult:
        return await self.run_segment(replace(request, resume_session_id=checkpoint_id))

    def cancel(self, run_id: str) -> bool:
        return self._cancel_runner(run_id) if self._cancel_runner else False
