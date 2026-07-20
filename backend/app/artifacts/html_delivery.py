from __future__ import annotations

import html
import json
import re
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from app.artifacts.amazon_dashboard import build_amazon_dashboard_blocks
from app.config import get_settings


_HTML_FORMAT = re.compile(r"(?i)html|网页(?:版|链接|报告|看板)?|网页版|在线看板")
_DELIVERY_ACTION = re.compile(
    r"整理|生成|导出|输出|呈现|转换|转成|做成|给我|发我|直接发|下载|链接|没有看到|没看到|看不到|打开"
)
_DELIVERY_FOLLOWUP = re.compile(
    r"(?:生成|做好|完成|整理)好(?:了)?吗|"
    r"(?:报告|文件|网页|HTML).{0,8}(?:好了吗|完成了吗|链接呢|在哪|发我)|"
    r"(?:链接|网址).{0,8}(?:呢|在哪|发我|给我|没有|没看到|看不到)|"
    r"(?:怎么|为什么).{0,12}(?:没|没有|不).{0,8}(?:链接|网址|HTML)"
)
_SECTION_HEADER = re.compile(r"^【([^】]+)】\s*(.*)$")
_MARKDOWN_HEADER = re.compile(r"^#{1,4}\s+(.+?)\s*$")
_LIST_ITEM = re.compile(r"^\s*(?:[-*•]|\d+[.)、])\s*(.+?)\s*$")
_ASIN = re.compile(r"\bB0[A-Z0-9]{8}\b", re.I)
_TEMPLATE_PATH = Path(__file__).with_name("templates") / "dashboard-template.html"
_SCRIPT_OPEN = '<script id="dashboard-data" type="application/json">'
_SCRIPT_CLOSE = "</script>"
_TEMPLATE_MARKER = "<!-- executive-html-dashboard · Agent Team -->\n"


def is_html_delivery_request(message: str) -> bool:
    """Return true only when HTML is requested as a deliverable, not discussed."""
    text = re.sub(r"\s+", " ", str(message or "")).strip()
    return bool(text and _HTML_FORMAT.search(text) and _DELIVERY_ACTION.search(text))


def is_html_delivery_followup(message: str) -> bool:
    """Return true for status/link follow-ups that need prior HTML context."""
    text = re.sub(r"\s+", " ", str(message or "")).strip()
    return bool(text and _DELIVERY_FOLLOWUP.search(text))


@lru_cache(maxsize=1)
def _dashboard_template() -> str:
    template = _TEMPLATE_PATH.read_text(encoding="utf-8")
    if _SCRIPT_OPEN not in template or _SCRIPT_CLOSE not in template:
        raise RuntimeError("executive HTML dashboard template is invalid")
    return template


def _sections(content: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    title = ""
    lines: list[str] = []

    def flush() -> None:
        body = "\n".join(lines).strip()
        if body:
            sections.append((title, body))

    for raw_line in str(content or "").splitlines():
        line = raw_line.rstrip()
        match = _SECTION_HEADER.match(line.strip())
        if match:
            flush()
            title = match.group(1).strip()
            lines = [match.group(2).strip()] if match.group(2).strip() else []
            continue
        markdown_match = _MARKDOWN_HEADER.match(line.strip())
        if markdown_match:
            flush()
            title = markdown_match.group(1).strip()
            lines = []
            continue
        lines.append(line)
    flush()
    return sections or [("分析正文", str(content or "").strip())]


def _list_items(body: str) -> list[str]:
    items: list[str] = []
    for line in body.splitlines():
        match = _LIST_ITEM.match(line)
        if match and match.group(1).strip():
            items.append(match.group(1).strip())
    return items


def _decision_item(text: str) -> dict[str, str]:
    parts = re.split(r"[：:]", text, maxsplit=1)
    if len(parts) == 2 and parts[0].strip() and parts[1].strip():
        return {"title": parts[0].strip(), "detail": parts[1].strip()}
    return {"title": text.strip()}


def _matrix_block(
    title: str, body: str, asins: list[str]
) -> tuple[dict[str, object] | None, str]:
    rows: list[dict[str, str]] = []
    leftovers: list[str] = []
    for line in body.splitlines():
        match = _LIST_ITEM.match(line)
        item = match.group(1).strip() if match else line.strip()
        if not item:
            continue
        label_value = re.split(r"[：:]", item, maxsplit=1)
        if len(label_value) != 2:
            leftovers.append(item)
            continue
        values = re.split(r"\s*[｜|]\s*", label_value[1].strip(), maxsplit=1)
        if len(values) != 2 or not all(value.strip() for value in values):
            leftovers.append(item)
            continue
        rows.append(
            {
                "dim": label_value[0].strip(),
                "asin_1": values[0].strip(),
                "asin_2": values[1].strip(),
            }
        )
    if len(rows) < 2:
        return None, body
    labels = asins[:2] if len(asins) >= 2 else ["方案 A", "方案 B"]
    block: dict[str, object] = {
        "type": "matrix_table",
        "title": title or "关键指标对比",
        "columns": [
            {"key": "asin_1", "label": labels[0]},
            {"key": "asin_2", "label": labels[1]},
        ],
        "rows": rows,
    }
    return block, "\n".join(f"- {item}" for item in leftovers)


def _dashboard_blocks(content: str, message: str) -> list[dict[str, object]]:
    asins: list[str] = []
    for asin in _ASIN.findall(f"{message}\n{content}"):
        normalized = asin.upper()
        if normalized not in asins:
            asins.append(normalized)

    blocks: list[dict[str, object]] = []
    for title, body in _sections(content):
        lowered = title.lower()
        if any(word in lowered for word in ("局限", "口径", "来源", "声明", "数据说明")):
            blocks.append(
                {
                    "type": "source_notes",
                    "title": title or "数据口径与缺口",
                    "items": [{"source": title or "数据说明", "note": body}],
                }
            )
            continue
        if any(word in lowered for word in ("核心判断", "核心结论", "结论", "摘要")):
            blocks.append(
                {
                    "type": "callout",
                    "tone": "warn" if "谨慎" in body or "局限" in body else "info",
                    "title": title or "核心判断",
                    "body": body,
                }
            )
            continue
        if any(word in lowered for word in ("下一步", "行动", "建议")):
            items = _list_items(body)
            if items:
                blocks.append(
                    {
                        "type": "decisions",
                        "title": title or "下一步行动",
                        "items": [_decision_item(item) for item in items],
                    }
                )
                continue
        if any(word in lowered for word in ("对比", "指标", "矩阵")):
            matrix, leftover = _matrix_block(title, body, asins)
            if matrix:
                blocks.append(matrix)
                if leftover.strip():
                    blocks.append(
                        {"type": "narrative", "title": "补充说明", "body": leftover}
                    )
                continue
        blocks.append(
            {"type": "narrative", "title": title or "分析正文", "body": body}
        )
    return blocks


def dashboard_payload(
    title: str,
    content: str,
    message: str = "",
    *,
    tool_results: list[dict[str, Any]] | None = None,
) -> dict[str, object]:
    safe_title = title.strip() or "Agent Team 报告"
    analysis_blocks = _dashboard_blocks(content.strip(), message)
    rich_blocks = build_amazon_dashboard_blocks(tool_results or [], analysis_blocks)
    return {
        "eyebrow": "AGENT TEAM REPORT",
        "title": safe_title,
        "subtitle": "NeoSpark · 结构化分析报告",
        "date": datetime.now(UTC).strftime("%Y-%m-%d"),
        "author": "Agent Team",
        "source": "Agent Team 工具与分析结果",
        "blocks": rich_blocks or analysis_blocks,
    }


def render_html_report(
    title: str,
    content: str,
    message: str = "",
    *,
    tool_results: list[dict[str, Any]] | None = None,
) -> str:
    """Inject structured report data into the original executive dashboard template."""
    safe_title = html.escape(title.strip() or "Agent Team 报告")
    template = _dashboard_template().replace("__TITLE__", safe_title)
    head, _, after_open = template.partition(_SCRIPT_OPEN)
    _, separator, after_close = after_open.partition(_SCRIPT_CLOSE)
    if not separator:
        raise RuntimeError("executive HTML dashboard data slot is missing")
    payload = json.dumps(
        dashboard_payload(title, content, message, tool_results=tool_results),
        ensure_ascii=False,
        indent=2,
    ).replace("<", "\\u003c")
    document = head + _SCRIPT_OPEN + payload + _SCRIPT_CLOSE + after_close
    return _TEMPLATE_MARKER + document


class HtmlArtifactPublisher:
    def __init__(
        self,
        upload_url: str | None = None,
        public_url_prefix: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        settings = get_settings()
        self.upload_url = (
            settings.html_artifact_upload_url if upload_url is None else upload_url
        ).strip()
        self.public_url_prefix = (
            settings.html_artifact_public_url_prefix
            if public_url_prefix is None
            else public_url_prefix
        ).strip()
        self.timeout_seconds = timeout_seconds or settings.html_artifact_timeout_seconds

    @property
    def enabled(self) -> bool:
        return bool(self.upload_url)

    def publish(
        self,
        title: str,
        content: str,
        artifact_id: str,
        *,
        message: str = "",
        tool_results: list[dict[str, Any]] | None = None,
    ) -> str:
        if not self.enabled:
            raise RuntimeError("HTML artifact publishing is not configured")
        safe_id = re.sub(r"[^A-Za-z0-9_-]+", "-", artifact_id).strip("-")[-48:]
        filename = f"agent-team-{safe_id or 'report'}.html"
        document = render_html_report(
            title,
            content,
            message,
            tool_results=tool_results,
        ).encode("utf-8")
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(
                self.upload_url,
                files={"file": (filename, document, "text/html; charset=utf-8")},
            )
            response.raise_for_status()
        payload = response.json()
        url = str(payload.get("url") or "").strip() if isinstance(payload, dict) else ""
        parsed = urlparse(url)
        if not url or parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("file host returned an invalid public URL")
        if self.public_url_prefix and not url.startswith(self.public_url_prefix):
            raise ValueError("file host returned an unexpected public URL")
        return url
