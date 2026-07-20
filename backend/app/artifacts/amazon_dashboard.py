from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from typing import Any


_ASIN_DETAIL_SUFFIX = "asin_detail"
_KEEPA_SUFFIX = "keepa_info"


def _tool_data(result: dict[str, Any]) -> dict[str, Any] | None:
    if not result.get("success") or not isinstance(result.get("data"), dict):
        return None
    data = result["data"]
    if isinstance(data.get("data"), dict) and (
        "code" in data or set(data).issubset({"data", "message", "code"})
    ):
        data = data["data"]
    return data if isinstance(data, dict) else None


def _timestamp_date(value: Any) -> str | None:
    if not isinstance(value, (int, float)) or value <= 0:
        return None
    seconds = value / 1000 if value > 10_000_000_000 else value
    try:
        return datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%d")
    except (OverflowError, OSError, ValueError):
        return None


def _money(value: Any) -> str:
    return f"${value:,.2f}" if isinstance(value, (int, float)) else "—"


def _number(value: Any) -> str:
    return f"{value:,}" if isinstance(value, (int, float)) else "—"


def _rank(value: Any) -> str:
    return f"#{value:,}" if isinstance(value, (int, float)) else "—"


def _overview(detail: dict[str, Any], key: str) -> str | None:
    raw = detail.get("overviews")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return None
    if not isinstance(raw, dict):
        return None
    value = raw.get(key)
    return str(value).strip() if value not in (None, "") else None


def _badges(detail: dict[str, Any]) -> list[str]:
    raw = detail.get("badge")
    if not isinstance(raw, dict):
        return []
    badges: list[str] = []
    if str(raw.get("bestSeller") or "").upper() == "Y":
        badges.append("best-seller")
    if str(raw.get("amazonChoice") or "").upper() == "Y":
        badges.append("amazon-choice")
    if str(raw.get("newRelease") or "").upper() == "Y":
        badges.append("new-release")
    return badges


def _highlight(detail: dict[str, Any]) -> str | None:
    raw = detail.get("badge")
    labels: list[str] = []
    if isinstance(raw, dict):
        if str(raw.get("amazonChoice") or "").upper() == "Y":
            labels.append("Amazon's Choice")
        if str(raw.get("ebc") or "").upper() == "Y":
            labels.append("A+")
        if str(raw.get("video") or "").upper() == "Y":
            labels.append("视频")
    return " · ".join(labels) or None


def _card(detail: dict[str, Any]) -> dict[str, Any]:
    image = detail.get("zoomImageUrl") or detail.get("imageUrl")
    fallback = detail.get("imageUrl") if detail.get("zoomImageUrl") else None
    card = {
        "asin": detail.get("asin"),
        "title": detail.get("title"),
        "brand": detail.get("brand"),
        "image_url": image,
        "image_url_fallback": fallback,
        "asin_url": detail.get("asinUrl"),
        "price": detail.get("price"),
        "rating": detail.get("rating"),
        "ratings_count": detail.get("ratings"),
        "reviews_count": detail.get("reviews"),
        "bsr_rank": detail.get("bsrRank"),
        "category_path": detail.get("nodeLabelPath"),
        "fulfillment": detail.get("fulfillment"),
        "available_date": _timestamp_date(detail.get("availableDate")),
        "seller_name": detail.get("sellerName"),
        "badges": _badges(detail),
        "highlight": _highlight(detail),
        "source": "SellerSprite asin_detail",
    }
    return {key: value for key, value in card.items() if value not in (None, "", [])}


def _kpis(details: list[dict[str, Any]]) -> dict[str, Any]:
    prices = [item["price"] for item in details if isinstance(item.get("price"), (int, float))]
    ratings = [
        item["rating"] for item in details if isinstance(item.get("rating"), (int, float))
    ]
    ranks = [
        item["bsrRank"] for item in details if isinstance(item.get("bsrRank"), (int, float))
    ]
    items: list[dict[str, Any]] = [
        {"label": "对比 ASIN", "value": len(details), "tone": "brand", "size": "lg"}
    ]
    if prices:
        items.append({"label": "最低当前价", "value": _money(min(prices)), "tone": "good"})
    if ratings:
        items.append({"label": "最高评分", "value": f"{max(ratings):.1f}", "tone": "good"})
    if ranks:
        items.append({"label": "最佳大类 BSR", "value": _rank(min(ranks)), "tone": "brand"})
    return {"type": "kpi_row", "kpis": items}


def _comparison_charts(details: list[dict[str, Any]]) -> dict[str, Any] | None:
    specs = [
        ("当前价格（USD）", "bar-v", "price"),
        ("当前评分", "bar-v", "rating"),
        ("大类 BSR（越低越好）", "bar-h", "bsrRank"),
    ]
    charts: list[dict[str, Any]] = []
    for title, chart_type, key in specs:
        data = [
            {"name": str(item.get("asin") or "ASIN"), "value": item[key]}
            for item in details
            if isinstance(item.get(key), (int, float))
        ]
        if data:
            charts.append({"title": title, "type": chart_type, "data": data})
    if not charts:
        return None
    return {
        "type": "chart_grid",
        "title": "核心指标横向对比",
        "lede": "全部来自 SellerSprite asin_detail 当前快照",
        "cols": min(len(charts), 3),
        "charts": charts,
    }


def _trend_points(values: Any) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        return []
    daily: dict[str, float] = {}
    for item in values:
        if not isinstance(item, dict):
            continue
        date = _timestamp_date(item.get("timePoint"))
        value = item.get("value")
        if date and isinstance(value, (int, float)) and value >= 0:
            daily[date] = round(float(value), 2)
    points = [{"name": date[5:], "value": value} for date, value in sorted(daily.items())]
    if len(points) <= 32:
        return points
    stride = math.ceil(len(points) / 32)
    sampled = points[::stride]
    if sampled[-1] != points[-1]:
        sampled.append(points[-1])
    return sampled


def _trend_charts(histories: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    charts: list[dict[str, Any]] = []
    for asin, history in histories.items():
        price = _trend_points(history.get("price"))
        if len(price) >= 2:
            charts.append({"title": f"{asin} · 价格趋势", "type": "line", "data": price})
        bsr = _trend_points(history.get("bsr"))
        if len(bsr) >= 2:
            charts.append(
                {"title": f"{asin} · BSR 趋势（越低越好）", "type": "line", "data": bsr}
            )
    if not charts:
        return None
    return {
        "type": "chart_grid",
        "title": "近 90 天真实趋势",
        "lede": "SellerSprite Keepa 快照；为保证可读性最多展示 32 个日期点",
        "cols": 2,
        "charts": charts,
    }


def _radar_chart(details: list[dict[str, Any]]) -> dict[str, Any] | None:
    if len(details) < 2:
        return None
    dimensions: list[tuple[str, str, str]] = [
        ("评分", "rating", "direct_rating"),
        ("Listing质量", "lqs", "direct"),
        ("评价积累", "ratings", "higher"),
        ("BSR竞争力", "bsrRank", "lower"),
        ("价格竞争力", "price", "lower"),
    ]
    available = [
        item
        for item in dimensions
        if all(isinstance(detail.get(item[1]), (int, float)) for detail in details)
    ]
    if len(available) < 3:
        return None
    series: list[dict[str, Any]] = []
    for detail in details:
        scores: list[float] = []
        for _, key, mode in available:
            value = float(detail[key])
            peers = [float(peer[key]) for peer in details]
            if mode == "direct_rating":
                score = value / 5 * 100
            elif mode == "direct":
                score = value
            elif mode == "higher":
                score = math.log10(value + 1) / math.log10(max(peers) + 1) * 100
            else:
                score = min(peers) / value * 100 if value > 0 else 0
            scores.append(round(max(0, min(score, 100)), 1))
        series.append({"name": detail.get("asin") or "ASIN", "value": scores})
    chart = {
        "title": "产品力雷达（本组归一化）",
        "type": "radar",
        "indicators": [{"name": label, "max": 100} for label, _, _ in available],
        "data": series,
    }
    return {
        "type": "chart_grid",
        "title": "产品力雷达",
        "lede": "评分按 5 分制换算；LQS 使用原值；评价、BSR、价格仅在本组内归一化，不代表市场绝对分",
        "cols": 1,
        "charts": [chart],
    }


def _matrix(details: list[dict[str, Any]]) -> dict[str, Any] | None:
    if len(details) < 2:
        return None
    columns = [
        {"key": f"product_{index + 1}", "label": str(item.get("asin") or index + 1)}
        for index, item in enumerate(details)
    ]

    def row(label: str, formatter: Any, conclusion: str = "") -> dict[str, Any]:
        result: dict[str, Any] = {"dim": label, "conclusion": conclusion}
        for index, detail in enumerate(details):
            result[f"product_{index + 1}"] = formatter(detail)
        return result

    prices = [item.get("price") for item in details]
    ratings = [item.get("rating") for item in details]
    ranks = [item.get("bsrRank") for item in details]
    rows = [
        row("品牌 / 卖家", lambda item: f"{item.get('brand') or '—'} / {item.get('sellerName') or '—'}"),
        row("当前售价", lambda item: _money(item.get("price")), "低价款更利于价格竞争"),
        row("评分 / 评分数", lambda item: f"{item.get('rating') or '—'} / {_number(item.get('ratings'))}"),
        row("大类 BSR", lambda item: _rank(item.get("bsrRank")), "排名越低越靠前"),
        row(
            "小类 BSR",
            lambda item: _rank((item.get("subcategories") or [{}])[0].get("rank"))
            if isinstance(item.get("subcategories"), list) and item.get("subcategories")
            else "—",
        ),
        row("重量", lambda item: str(item.get("weight") or "—")),
        row("尺寸", lambda item: str(item.get("dimensions") or "—")),
        row("供电方式", lambda item: _overview(item, "Power Source") or "—"),
        row("履约", lambda item: str(item.get("fulfillment") or "—")),
    ]
    if all(isinstance(value, (int, float)) for value in prices):
        rows[1]["is_winner"] = True
    if all(isinstance(value, (int, float)) for value in ratings + ranks):
        rows[2]["conclusion"] = "评分越高越好；评分数反映评价积累"
        rows[3]["is_winner"] = True
    return {
        "type": "matrix_table",
        "title": "产品参数与运营指标矩阵",
        "columns": columns,
        "rows": rows,
    }


def _feature_insights(details: list[dict[str, Any]]) -> dict[str, Any] | None:
    items: list[dict[str, Any]] = []
    for detail in details:
        features = [str(item).strip() for item in detail.get("features") or [] if str(item).strip()]
        if not features:
            continue
        items.append(
            {
                "title": str(detail.get("asin") or detail.get("title") or "产品"),
                "finding": features[0],
                "evidence": "\n".join(f"- {item}" for item in features[:3]),
                "meaning": "以上卖点来自 Listing features，属于卖家声明，未做实验室验证。",
                "source": "SellerSprite asin_detail.features",
            }
        )
    if not items:
        return None
    return {"type": "insight_stack", "title": "产品力卖点拆解", "items": items}


def build_amazon_dashboard_blocks(
    tool_results: list[dict[str, Any]],
    analysis_blocks: list[dict[str, Any]],
) -> list[dict[str, Any]] | None:
    details_by_asin: dict[str, dict[str, Any]] = {}
    histories_by_asin: dict[str, dict[str, Any]] = {}
    for result in tool_results:
        if not isinstance(result, dict):
            continue
        name = str(result.get("tool_name") or "")
        data = _tool_data(result)
        if not data:
            continue
        asin = str(data.get("asin") or (result.get("arguments") or {}).get("asin") or "").upper()
        if not asin:
            continue
        if name.endswith(_ASIN_DETAIL_SUFFIX):
            details_by_asin[asin] = data
        elif name.endswith(_KEEPA_SUFFIX):
            histories_by_asin[asin] = data
    if not details_by_asin:
        return None

    details = list(details_by_asin.values())
    callouts = [block for block in analysis_blocks if block.get("type") == "callout"]
    narratives = [
        block
        for block in analysis_blocks
        if block.get("type") == "narrative" and block.get("title") != "分析正文"
    ]
    decisions = [block for block in analysis_blocks if block.get("type") == "decisions"]
    source_items: list[dict[str, Any]] = []
    for block in analysis_blocks:
        if block.get("type") == "source_notes":
            source_items.extend(block.get("items") or [])

    blocks: list[dict[str, Any]] = [_kpis(details), *callouts]
    blocks.append(
        {
            "type": "product_grid",
            "title": "Amazon 商品卡片",
            "lede": "图片与跳转链接均来自 SellerSprite asin_detail",
            "items": [_card(detail) for detail in details],
        }
    )
    for block in (
        _comparison_charts(details),
        _trend_charts(histories_by_asin),
        _radar_chart(details),
        _matrix(details),
        _feature_insights(details),
    ):
        if block:
            blocks.append(block)
    blocks.extend(narratives)
    blocks.extend(decisions)

    source_items.append(
        {
            "source": "SellerSprite asin_detail",
            "note": "商品图、标题、品牌、价格、评分、评分数、BSR、参数和 Listing features 均直接来自工具返回。",
            "asof": datetime.now(UTC).strftime("%Y-%m-%d"),
            "confidence": "高（第三方工具快照）",
        }
    )
    if histories_by_asin:
        source_items.append(
            {
                "source": "SellerSprite keepa_info",
                "note": "价格与 BSR 趋势来自真实历史快照；图表最多保留 32 个日期点。",
                "coverage": "近 90 天（以工具实际返回范围为准）",
                "confidence": "高（第三方历史快照）",
            }
        )
    else:
        source_items.append(
            {
                "source": "历史趋势缺口",
                "note": "本轮未取得 Keepa 历史结果，因此没有生成时间趋势图，未使用静态值伪造趋势。",
                "gap": "price / BSR history",
                "confidence": "缺失",
            }
        )
    if _radar_chart(details):
        source_items.append(
            {
                "source": "产品力雷达计算口径",
                "note": "评分按 5 分制、LQS 按原值；评价积累使用对数缩放，BSR 与价格按本组最优值归一化。仅用于本组相对比较。",
                "confidence": "派生指标",
            }
        )
    blocks.append({"type": "source_notes", "title": "数据口径与缺口", "items": source_items})
    return blocks
