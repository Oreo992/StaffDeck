from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

from app.artifacts.html_delivery import (
    HtmlArtifactPublisher,
    dashboard_payload,
    is_html_delivery_followup,
    is_html_delivery_request,
    render_html_report,
)
from app.core.agent_loop import AgentLoop
from app.db.models import AgentEvent, ChatSession, Message


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


@pytest.mark.parametrize("message", ["生成好了吗", "HTML 报告链接呢", "为什么没有 HTML 链接"])
def test_html_delivery_followup_is_detected(message: str) -> None:
    assert is_html_delivery_followup(message)


def test_rendered_report_escapes_model_content() -> None:
    html = render_html_report("市场报告", "<script>alert(1)</script>\n结论")

    assert '<script id="dashboard-data" type="application/json">' in html
    assert "<script>alert(1)</script>" not in html
    assert "\\u003cscript>alert(1)\\u003c/script>" in html
    assert "executive-html-dashboard" in html


def test_rendered_report_keeps_native_charts_responsive_on_mobile() -> None:
    html = render_html_report("移动端报告", "【核心判断】验证响应式图表。")

    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in html
    assert "@media (max-width: 640px)" in html
    assert "new ResizeObserver" in html
    assert "chartResizeObserver?.observe(el)" in html


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
        "B0DNK7RHZS",
        "B0GJSDGY7N",
    ]
    assert matrix["rows"][0] == {
        "dim": "价格",
        "asin_1": "$66.46",
        "asin_2": "$79.99",
    }


def _amazon_tool_results(*, include_history: bool = True) -> list[dict[str, object]]:
    details = [
        {
            "asin": "B0DNK7RHZS",
            "title": "FEPPO Corded Mattress Vacuum",
            "brand": "FEPPO",
            "sellerName": "FEPPO-LIFESTYLE",
            "asinUrl": "https://www.amazon.com/dp/B0DNK7RHZS",
            "zoomImageUrl": "https://m.media-amazon.com/images/I/41HSUHC4kPL._AC_US600_.jpg",
            "imageUrl": "https://m.media-amazon.com/images/I/41HSUHC4kPL._AC_US200_.jpg",
            "price": 66.46,
            "rating": 4.4,
            "ratings": 1750,
            "reviews": 731,
            "bsrRank": 7917,
            "bsrLabel": "Home & Kitchen",
            "subcategories": [{"rank": 21, "label": "Handheld Vacuums"}],
            "nodeLabelPath": "Home & Kitchen:Vacuums:Handheld Vacuums",
            "lqs": 100,
            "weight": "3.8 pounds",
            "dimensions": '11.9"L x 9.84"W x 7.67"H',
            "fulfillment": "FBA",
            "features": ["16Kpa suction", "140°F drying", "HEPA filtration"],
            "overviews": '{"Power Source":"Corded Electric"}',
            "badge": {"amazonChoice": "N", "ebc": "Y", "video": "Y"},
        },
        {
            "asin": "B0GJSDGY7N",
            "title": "FEPPO Cordless Mattress Vacuum",
            "brand": "FEPPO",
            "sellerName": "FEPPO-LIFESTYLE",
            "asinUrl": "https://www.amazon.com/dp/B0GJSDGY7N",
            "zoomImageUrl": "https://m.media-amazon.com/images/I/51kTanHhlML._AC_US600_.jpg",
            "imageUrl": "https://m.media-amazon.com/images/I/51kTanHhlML._AC_US200_.jpg",
            "price": 79.99,
            "rating": 4.6,
            "ratings": 107,
            "reviews": 81,
            "bsrRank": 45507,
            "bsrLabel": "Home & Kitchen",
            "subcategories": [{"rank": 69, "label": "Handheld Vacuums"}],
            "nodeLabelPath": "Home & Kitchen:Vacuums:Handheld Vacuums",
            "lqs": 99,
            "weight": "2.9 pounds",
            "dimensions": '9.84"L x 9.84"W x 5.11"H',
            "fulfillment": "FBA",
            "features": ["18Kpa suction", "Cordless", "Dust sensor"],
            "overviews": '{"Power Source":"Battery Powered"}',
            "badge": {"amazonChoice": "Y", "ebc": "Y", "video": "Y"},
        },
    ]
    results: list[dict[str, object]] = [
        {
            "tool_name": "at_sellersprite.asin_detail",
            "arguments": {"marketplace": "US", "asin": detail["asin"]},
            "success": True,
            "data": {"code": "OK", "data": detail},
        }
        for detail in details
    ]
    if include_history:
        for index, detail in enumerate(details):
            results.append(
                {
                    "tool_name": "at_sellersprite.keepa_info",
                    "arguments": {"marketplace": "US", "asin": detail["asin"]},
                    "success": True,
                    "data": {
                        "code": "OK",
                        "data": {
                            "asin": detail["asin"],
                            "price": [
                                {"timePoint": 1783000000000, "value": 70 + index},
                                {"timePoint": 1782000000000, "value": 80 + index},
                            ],
                            "bsr": [
                                {"timePoint": 1783000000000, "value": 8000 + index},
                                {"timePoint": 1782000000000, "value": 9000 + index},
                            ],
                        },
                    },
                }
            )
    return results


def test_structured_amazon_results_create_native_rich_dashboard_blocks() -> None:
    payload = dashboard_payload(
        "ASIN 深度对比",
        "HTML 成品正在生成，稍后会附上链接。\n\n【核心判断】无线款产品力更强。",
        "对比 B0DNK7RHZS、B0GJSDGY7N，以 HTML 格式呈现",
        tool_results=_amazon_tool_results(),
    )
    serialized = json.dumps(payload, ensure_ascii=False)
    block_types = [block["type"] for block in payload["blocks"]]

    assert "HTML 成品正在生成" not in serialized
    assert block_types[:3] == ["kpi_row", "callout", "product_grid"]
    assert "matrix_table" in block_types
    product_grid = next(block for block in payload["blocks"] if block["type"] == "product_grid")
    assert len(product_grid["items"]) == 2
    assert product_grid["items"][0]["image_url"].endswith("_AC_US600_.jpg")
    chart_grids = [block for block in payload["blocks"] if block["type"] == "chart_grid"]
    charts = [chart for block in chart_grids for chart in block["charts"]]
    assert {chart["type"] for chart in charts} >= {"line", "bar-v", "bar-h", "radar"}
    assert any("90 天" in block["title"] for block in chart_grids)
    radar = next(chart for chart in charts if chart["type"] == "radar")
    assert all(indicator["max"] == 100 for indicator in radar["indicators"])


def test_dashboard_does_not_invent_trend_charts_without_keepa_results() -> None:
    payload = dashboard_payload(
        "ASIN 深度对比",
        "【核心判断】先按静态数据对比。",
        "以 HTML 格式呈现",
        tool_results=_amazon_tool_results(include_history=False),
    )

    chart_grids = [block for block in payload["blocks"] if block["type"] == "chart_grid"]
    assert not any("90 天" in block["title"] for block in chart_grids)
    notes = next(block for block in payload["blocks"] if block["type"] == "source_notes")
    assert "未取得 Keepa" in json.dumps(notes, ensure_ascii=False)


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


def test_publisher_uploads_existing_html_without_wrapping(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
            pass

        def __enter__(self):  # noqa: ANN204
            return self

        def __exit__(self, *args):  # noqa: ANN002, ANN204
            return None

        def post(self, url, files):  # noqa: ANN001
            captured["files"] = files
            return httpx.Response(
                200,
                json={"url": "https://agent.neospark.cn/files/existing.html"},
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr(httpx, "Client", FakeClient)
    publisher = HtmlArtifactPublisher(
        upload_url="http://host.docker.internal:9900/api/upload",
        public_url_prefix="https://agent.neospark.cn/files/",
    )
    document = "<!doctype html><html><body>完整原始报告</body></html>"

    url = publisher.publish_document(document, "session-existing")

    assert url.endswith("existing.html")
    _filename, body, _content_type = captured["files"]["file"]  # type: ignore[index]
    assert body.decode("utf-8") == document


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

        def publish(  # noqa: ANN001
            self, title, content, artifact_id, *, message="", tool_results=None
        ):
            self.calls += 1
            assert message == "整理成 HTML"
            assert tool_results == [{"tool_name": "demo", "success": True}]
            return "https://agent.neospark.cn/files/verified.html"

    class FakeEvents:
        def record(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return None

    loop = AgentLoop.__new__(AgentLoop)
    loop.html_artifacts = FakePublisher()
    loop.events = FakeEvents()

    result = loop._with_html_artifact(
        "整理成 HTML",
        ChatSession(
            id="session_test",
            tenant_id="tenant_demo",
            slots_json={"_tool_results": [{"tool_name": "demo", "success": True}]},
        ),
        "报告正文（模型声称：https://agent.neospark.cn/files/made-up.html）",
    )

    assert loop.html_artifacts.calls == 1
    assert result.endswith(
        "[打开 HTML 报告](https://agent.neospark.cn/files/verified.html)"
    )


def test_agent_loop_publishes_contextual_html_followup_with_recovered_tool_results() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(
        engine,
        tables=[Message.__table__, AgentEvent.__table__],
    )
    now = datetime.now(UTC)
    request_id = "msg_html_request"
    session_id = "session_html_followup"

    class FakePublisher:
        enabled = True
        public_url_prefix = "https://agent.neospark.cn/files/"

        def publish(  # noqa: ANN001
            self, title, content, artifact_id, *, message="", tool_results=None
        ):
            assert message == "对比两个 ASIN，以 HTML 格式呈现"
            assert tool_results == [
                {
                    "tool_name": "at_sellersprite.asin_detail",
                    "arguments": {"asin": "B0DNK7RHZS", "marketplace": "US"},
                    "success": True,
                    "data": {"code": "OK", "data": {"asin": "B0DNK7RHZS"}},
                    "error": None,
                }
            ]
            return "https://agent.neospark.cn/files/contextual.html"

    class FakeEvents:
        def record(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return None

    with Session(engine) as db:
        db.add(
            Message(
                id=request_id,
                tenant_id="tenant_demo",
                session_id=session_id,
                role="user",
                content="对比两个 ASIN，以 HTML 格式呈现",
                created_at=now - timedelta(minutes=2),
            )
        )
        db.add(
            Message(
                id="msg_followup",
                tenant_id="tenant_demo",
                session_id=session_id,
                role="user",
                content="生成好了吗",
                created_at=now,
            )
        )
        db.add(
            AgentEvent(
                tenant_id="tenant_demo",
                session_id=session_id,
                event_type="tool_call_finished",
                payload_json={
                    "user_message_id": request_id,
                    "tool_name": "at_sellersprite.asin_detail",
                    "tool_call": {
                        "name": "at_sellersprite.asin_detail",
                        "arguments": {"asin": "B0DNK7RHZS", "marketplace": "US"},
                    },
                    "success": True,
                    "data": {"code": "OK", "data": {"asin": "B0DNK7RHZS"}},
                    "error": None,
                },
                created_at=now - timedelta(minutes=1),
            )
        )
        db.commit()

        loop = AgentLoop.__new__(AgentLoop)
        loop.db = db
        loop.html_artifacts = FakePublisher()
        loop.events = FakeEvents()
        result = loop._with_html_artifact(
            "生成好了吗",
            ChatSession(id=session_id, tenant_id="tenant_demo", slots_json={}),
            "报告正文已经整理好。",
        )

    assert result.endswith(
        "[打开 HTML 报告](https://agent.neospark.cn/files/contextual.html)"
    )


def test_agent_loop_does_not_publish_ambiguous_followup_without_html_context() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine, tables=[Message.__table__, AgentEvent.__table__])

    with Session(engine) as db:
        loop = AgentLoop.__new__(AgentLoop)
        loop.db = db
        loop.html_artifacts = object()
        result = loop._with_html_artifact(
            "生成好了吗",
            ChatSession(id="session_no_html", tenant_id="tenant_demo"),
            "还在处理。",
        )

    assert result == "还在处理。"
