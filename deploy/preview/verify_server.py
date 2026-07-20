#!/usr/bin/env python3
"""Verify the public NeoSpark Agent Team preview without printing credentials."""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path


BASE_URL = "https://preview.agentteam.neospark.cn"
TENANT_ID = "tenant_demo"
CREDENTIAL_FILE = Path("/data/staffdeck-preview/login-credentials.json")
EXPECTED_MODEL = "claude-opus-4-8"


def request(
    method: str,
    path: str,
    payload: dict[str, object] | None = None,
    token: str | None = None,
    timeout: int = 20,
) -> tuple[int, str, bytes]:
    headers: dict[str, str] = {}
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode("utf-8")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    target = urllib.request.Request(BASE_URL + path, data=body, headers=headers, method=method)
    with urllib.request.urlopen(target, timeout=timeout) as response:
        return response.status, response.headers.get_content_type(), response.read()


def request_json(
    method: str,
    path: str,
    payload: dict[str, object] | None = None,
    token: str | None = None,
    timeout: int = 20,
) -> object:
    status, _, body = request(method, path, payload, token, timeout)
    if status != 200:
        raise RuntimeError(f"Unexpected HTTP status for {path}: {status}")
    return json.loads(body)


def main() -> None:
    credentials = json.loads(CREDENTIAL_FILE.read_text(encoding="utf-8"))
    admin = next(item for item in credentials["accounts"] if item["username"] == "admin")

    health = request_json("GET", "/api/health")
    if health != {"status": "ok", "app": "Agent Team"}:
        raise RuntimeError("Unexpected health response")

    status, content_type, html = request("GET", "/workspace/gallery")
    if status != 200 or content_type != "text/html" or b"Agent Team" not in html:
        raise RuntimeError("Workspace gallery did not return the Agent Team SPA")

    login = request_json(
        "POST",
        "/api/auth/login",
        {
            "tenant_id": TENANT_ID,
            "username": admin["username"],
            "password": admin["password"],
        },
    )
    token = str(login["token"])
    models = request_json(
        "GET",
        f"/api/enterprise/model-configs?tenant_id={TENANT_ID}",
        token=token,
    )
    defaults = [item for item in models if item.get("is_default") and item.get("enabled")]
    if len(defaults) != 1:
        raise RuntimeError("Expected exactly one enabled default model")
    default_model = defaults[0]
    if default_model.get("model") != EXPECTED_MODEL:
        raise RuntimeError("Default model is not the expected RC-backed route")

    test_result = request_json(
        "POST",
        f"/api/enterprise/model-configs/{default_model['id']}/test?tenant_id={TENANT_ID}",
        payload={},
        token=token,
        timeout=660,
    )
    if not test_result.get("success"):
        raise RuntimeError("Default model connectivity test failed")

    print(
        "Agent Team preview verification PASS "
        f"model={default_model['model']} provider={default_model['provider']}"
    )


if __name__ == "__main__":
    main()
