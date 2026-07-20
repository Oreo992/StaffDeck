from __future__ import annotations

import json
import re

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

    assert '<script id="dashboard-data" type="application/json">' in html
    assert "<script>alert(1)</script>" not in html
    assert "\\u003cscript>alert(1)\\u003c/script>" in html
    assert "executive-html-dashboard" in html


def test_rendered_report_maps_analysis_to_original_dashboard_blocks() -> None:
    content = """【局限声明】数据来自 SellerSprite，统计截至今日。

【核心判断】两个 ASIN 属于同品牌，应按变体策略分析。

【关键指标对比】
- 价格：$66.46 ｜ $79.99
- 评分：4.4 ｜ 4.6
- BSR：#7,917 ｜ #45,507

【产品力差异】第一个更适合走量，第二个更适合利润款。

【下一步建议】
1. 先验证两个 ASIN 的变体关系
2. 再按关键词拆分广告组
"""
    html = render_html_report(
        "ASIN 深度对比",
        content,
        message="对比 B0DNK7RHZS 和 B0GJSDGY7N，以 HTML 格式呈现",
    )
    match = re.search(
        r'<script id="dashboard-data" type="application/json">(.*?)</script>',
        html,
        re.S,
    )
    assert match
    payload = json.loads(match.group(1))
    assert payload["title"] == "ASIN 深度对比"
    assert [block["type"] for block in payload["blocks"]] == [
        "source_notes",
        "callout",
        "matrix_table",
        "narrative",
        "decisions",
    ]
    matrix = payload["blocks"][2]
    assert [column["label"] for column in matrix["columns"]] == [
        "对比维度",
        "B0DNK7RHZS",
        "B0GJSDGY7N",
    ]
    assert matrix["rows"][0] == {
        "dim": "价格",
        "asin_1": "$66.46",
        "asin_2": "$79.99",
    }


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

    url = publisher.publish(
        "市场报告",
        "完整结论",
        "session-test",
        message="整理成 HTML",
    )

    assert url == "https://agent.neospark.cn/files/report.html"
    assert captured["url"] == "http://host.docker.internal:9900/api/upload"
    filename, body, content_type = captured["files"]["file"]  # type: ignore[index]
    assert filename.endswith(".html")
    document = body.decode("utf-8")
    assert "完整结论" in document
    assert "executive-html-dashboard" in document
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

        def publish(self, title, content, artifact_id, *, message=""):  # noqa: ANN001
            self.calls += 1
            assert message == "整理成 HTML"
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
