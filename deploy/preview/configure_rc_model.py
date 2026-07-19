#!/usr/bin/env python3
"""Switch the live StaffDeck preview to the RC-backed Claude route."""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path


BASE_URL = "http://127.0.0.1:18173"
TENANT_ID = "tenant_demo"
TARGET_MODEL = "claude-opus-4-8"
TARGET_NAME = "RC Claude Opus 4.8"
CREDENTIAL_FILE = Path("/data/staffdeck-preview/login-credentials.json")


def request_json(
    method: str,
    path: str,
    payload: dict[str, object] | None = None,
    token: str | None = None,
    timeout: int = 20,
) -> object:
    headers: dict[str, str] = {}
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode("utf-8")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    target = urllib.request.Request(
        BASE_URL + path,
        data=body,
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(target, timeout=timeout) as response:
        return json.load(response)


def main() -> None:
    credentials = json.loads(CREDENTIAL_FILE.read_text(encoding="utf-8"))
    admin = next(item for item in credentials["accounts"] if item["username"] == "admin")
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

    updated = request_json(
        "PUT",
        f"/api/enterprise/model-configs/{default_model['id']}",
        {
            "tenant_id": TENANT_ID,
            "name": TARGET_NAME,
            "model": TARGET_MODEL,
        },
        token=token,
    )
    if updated.get("model") != TARGET_MODEL:
        raise RuntimeError("StaffDeck did not persist the RC-backed model")

    test_result = request_json(
        "POST",
        f"/api/enterprise/model-configs/{default_model['id']}/test?tenant_id={TENANT_ID}",
        payload={},
        token=token,
        timeout=660,
    )
    if not test_result.get("success"):
        raise RuntimeError("RC-backed model connectivity test failed")
    print(f"Configured RC-backed StaffDeck model PASS model={TARGET_MODEL}")


if __name__ == "__main__":
    main()
