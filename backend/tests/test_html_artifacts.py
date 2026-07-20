from __future__ import annotations

import httpx
import pytest

from app.artifacts.html_delivery import (
    HtmlArtifactPublisher,
    is_html_delivery_request,
    render_html_report,
)
from app.core.agent_loop import AgentLoop
from app.db.models import ChatSession


@pytest.mark.parametrize(
    "message",
    [
        "全部整理成html",
        "请导出 HTML 报告",
        "我没有看到html",
        "给我一个网页版看板",
        "把两个 ASIN 的对比以html格式呈现",
    ],
)
def test_html_delivery_intent_is_detected(message: str) -> None:
    assert is_html_delivery_request(message)


def test_plain_html_mention_is_not_treated_as_delivery_request() -> None:
    assert not is_html_delivery_request("HTML 和 PDF 有什么区别？")


def test_rendered_report_escapes_model_content() -> None:
    html = render_html_report("市场报告", "<script>alert(1)</script>\n结论")

    assert "<script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "white-space: pre-wrap" in html


def test_publisher_uploads_html_and_returns_validated_public_url(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
            pass

        def __enter__(self):  # noqa: ANN204
            return self

        def __exit__(self, *args):  # noqa: ANN002, ANN204
            return None

        def post(self, url, files):  # noqa: ANN001
            captured["url"] = url
            captured["files"] = files
            return httpx.Response(
                200,
                json={"url": "https://agent.neospark.cn/files/report.html"},
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr(httpx, "Client", FakeClient)
    publisher = HtmlArtifactPublisher(
        upload_url="http://host.docker.internal:9900/api/upload",
        public_url_prefix="https://agent.neospark.cn/files/",
    )

    url = publisher.publish("市场报告", "完整结论", "session-test")

    assert url == "https://agent.neospark.cn/files/report.html"
    assert captured["url"] == "http://host.docker.internal:9900/api/upload"
    filename, body, content_type = captured["files"]["file"]  # type: ignore[index]
    assert filename.endswith(".html")
    assert "完整结论" in body.decode("utf-8")
    assert content_type.startswith("text/html")


def test_publisher_rejects_unexpected_public_url(monkeypatch) -> None:
    class FakeClient:
        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
            pass

        def __enter__(self):  # noqa: ANN204
            return self

        def __exit__(self, *args):  # noqa: ANN002, ANN204
            return None

        def post(self, url, files):  # noqa: ANN001
            return httpx.Response(
                200,
                json={"url": "https://evil.example/report.html"},
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr(httpx, "Client", FakeClient)
    publisher = HtmlArtifactPublisher(
        upload_url="http://host.docker.internal:9900/api/upload",
        public_url_prefix="https://agent.neospark.cn/files/",
    )

    with pytest.raises(ValueError, match="unexpected public URL"):
        publisher.publish("报告", "内容", "session-test")


def test_agent_loop_appends_a_real_clickable_link_even_if_model_claimed_one() -> None:
    class FakePublisher:
        enabled = True
        public_url_prefix = "https://agent.neospark.cn/files/"

        def __init__(self) -> None:
            self.calls = 0

        def publish(self, title, content, artifact_id):  # noqa: ANN001
            self.calls += 1
            return "https://agent.neospark.cn/files/verified.html"

    class FakeEvents:
        def record(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return None

    loop = AgentLoop.__new__(AgentLoop)
    loop.html_artifacts = FakePublisher()
    loop.events = FakeEvents()

    result = loop._with_html_artifact(
        "整理成 HTML",
        ChatSession(id="session_test", tenant_id="tenant_demo"),
        "报告正文（模型声称：https://agent.neospark.cn/files/made-up.html）",
    )

    assert loop.html_artifacts.calls == 1
    assert result.endswith(
        "[打开 HTML 报告](https://agent.neospark.cn/files/verified.html)"
    )
