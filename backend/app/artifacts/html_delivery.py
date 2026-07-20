from __future__ import annotations

import html
import re
from datetime import UTC, datetime
from urllib.parse import urlparse

import httpx

from app.config import get_settings


_HTML_FORMAT = re.compile(r"(?i)html|网页(?:版|链接|报告|看板)?|网页版|在线看板")
_DELIVERY_ACTION = re.compile(
    r"整理|生成|导出|转换|转成|做成|给我|发我|直接发|下载|链接|没有看到|没看到|看不到|打开"
)


def is_html_delivery_request(message: str) -> bool:
    """Return true only when HTML is requested as a deliverable, not discussed."""
    text = re.sub(r"\s+", " ", str(message or "")).strip()
    return bool(text and _HTML_FORMAT.search(text) and _DELIVERY_ACTION.search(text))


def render_html_report(title: str, content: str) -> str:
    """Render model text as inert, self-contained HTML without executable markup."""
    safe_title = html.escape(title.strip() or "Agent Team 报告")
    safe_content = html.escape(content.strip())
    generated_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{safe_title}</title>
  <style>
    :root {{ color-scheme: light; font-family: Inter, ui-sans-serif, system-ui, -apple-system, "PingFang SC", sans-serif; }}
    body {{ margin: 0; background: #f7f8fb; color: #17191f; }}
    main {{ box-sizing: border-box; width: min(960px, calc(100% - 32px)); margin: 32px auto; padding: 40px; border: 1px solid #e5e8ef; border-radius: 20px; background: #fff; box-shadow: 0 12px 36px rgba(28, 35, 50, .06); }}
    header {{ margin-bottom: 28px; padding-bottom: 20px; border-bottom: 1px solid #eceef3; }}
    h1 {{ margin: 0 0 8px; font-size: clamp(26px, 4vw, 38px); line-height: 1.2; }}
    .meta {{ color: #737b8c; font-size: 13px; }}
    article {{ white-space: pre-wrap; overflow-wrap: anywhere; font-size: 16px; line-height: 1.8; }}
    @media (max-width: 640px) {{ main {{ margin: 16px auto; padding: 24px; border-radius: 14px; }} }}
  </style>
</head>
<body>
  <main>
    <header><h1>{safe_title}</h1><div class="meta">NeoSpark · {generated_at}</div></header>
    <article>{safe_content}</article>
  </main>
</body>
</html>
"""


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

    def publish(self, title: str, content: str, artifact_id: str) -> str:
        if not self.enabled:
            raise RuntimeError("HTML artifact publishing is not configured")
        safe_id = re.sub(r"[^A-Za-z0-9_-]+", "-", artifact_id).strip("-")[-48:]
        filename = f"agent-team-{safe_id or 'report'}.html"
        document = render_html_report(title, content).encode("utf-8")
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
