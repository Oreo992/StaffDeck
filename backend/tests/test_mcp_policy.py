from app.tools.mcp_policy import MCPServerRateLimiter


def test_mcp_server_rate_limiter_spaces_calls_per_server() -> None:
    now = 0.0
    sleeps: list[float] = []

    def clock() -> float:
        return now

    def sleep(delay: float) -> None:
        nonlocal now
        sleeps.append(delay)
        now += delay

    limiter = MCPServerRateLimiter(clock=clock, sleep=sleep)
    limiter.wait("lingxing", 1)
    limiter.wait("lingxing", 1)
    limiter.wait("another-server", 1)

    assert sleeps == [1.0]
