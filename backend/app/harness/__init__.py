"""Harness v2 foundations shared by runtime adapters.

This package is introduced independently from the v0.3 execution engine so the
contracts can stabilize before database-backed lifecycle components are enabled.
"""

from app.harness.contracts import (
    HarnessLimits,
    HarnessToolCall,
    HarnessToolContext,
    HarnessToolError,
    HarnessToolResult,
    HarnessToolSpec,
)
from app.harness.errors import HarnessExecutionError, harness_error

__all__ = [
    "HarnessExecutionError",
    "HarnessLimits",
    "HarnessToolCall",
    "HarnessToolContext",
    "HarnessToolError",
    "HarnessToolResult",
    "HarnessToolSpec",
    "harness_error",
]
