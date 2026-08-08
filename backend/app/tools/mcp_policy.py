"""Conservative policy helpers for curated external MCP integrations."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable


LINGXING_OFFICIAL_INTEGRATION = "lingxing_official"

# These names are documented as queries by Lingxing.  Unknown tools, including
# future tools returned by tools/list, deliberately do not enter this list.
LINGXING_QQQ_READ_TOOL_NAMES = frozenset(
    {
        "get_my_sids",
        "get_fba_stock_list",
        "erp_listing",
        "query_erp_competitive_monitor",
        "query_erp_follow_sale_monitor",
        "get_custom_report_list",
        "get_custom_report_by_id",
        "get_custom_indicator_list",
        "get_custom_indicator_field",
    }
)


def suggested_effect_level(integration_kind: str | None, tool_name: str) -> str | None:
    """Return an explicit safe effect level for known curated tools only."""

    if integration_kind == LINGXING_OFFICIAL_INTEGRATION:
        return "read" if tool_name in LINGXING_QQQ_READ_TOOL_NAMES else "write"
    return None


def is_qqq_recommended_read_tool(integration_kind: str | None, tool_name: str) -> bool:
    return (
        integration_kind == LINGXING_OFFICIAL_INTEGRATION
        and tool_name in LINGXING_QQQ_READ_TOOL_NAMES
    )


class MCPServerRateLimiter:
    """Small process-local limiter shared by all tools on one MCP server."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._next_allowed_at: dict[str, float] = {}

    def wait(self, server_id: str, rate_limit_per_second: float | None) -> None:
        if not rate_limit_per_second or rate_limit_per_second <= 0:
            return
        interval = 1 / rate_limit_per_second
        with self._lock:
            now = self._clock()
            due_at = max(now, self._next_allowed_at.get(server_id, now))
            self._next_allowed_at[server_id] = due_at + interval
        delay = due_at - self._clock()
        if delay > 0:
            self._sleep(delay)


MCP_SERVER_RATE_LIMITER = MCPServerRateLimiter()
