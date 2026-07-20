#!/usr/bin/env python3
"""Switch the live Agent Team preview to the RC-backed Claude route."""

from __future__ import annotations

import json
import shlex
import urllib.request
from pathlib import Path


BASE_URL = "http://127.0.0.1:18173"
TENANT_ID = "tenant_demo"
TARGET_MODEL = "claude-opus-4-8"
TARGET_NAME = "RC Claude Opus 4.8"
SELECTABLE_MODEL = "claude-sonnet-4-6"
SELECTABLE_NAME = "RC Claude Sonnet 4.6"
CREDENTIAL_FILE = Path("/data/staffdeck-preview/login-credentials.json")
ENV_FILE = Path("/data/staffdeck-preview/backend.env")


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


def environment_value(path: Path, key: str) -> str:
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        current_key, separator, raw_value = line.partition("=")
        if separator and current_key.strip() == key:
            values = shlex.split(raw_value, comments=False, posix=True)
            return values[0] if values else ""
    return ""


def configured_model(models: list[dict[str, object]], model: str) -> dict[str, object] | None:
    return next(
        (item for item in models if item.get("model") == model),
        None,
    )


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
    if not isinstance(models, list):
        raise RuntimeError("Unexpected model config response")
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
        raise RuntimeError("Agent Team did not persist the RC-backed model")

    request_json(
        "POST",
        f"/api/enterprise/model-configs/{default_model['id']}/set-default?tenant_id={TENANT_ID}",
        payload={},
        token=token,
    )

    models = request_json(
        "GET",
        f"/api/enterprise/model-configs?tenant_id={TENANT_ID}",
        token=token,
    )
    if not isinstance(models, list):
        raise RuntimeError("Unexpected model config response")
    selectable = configured_model(models, SELECTABLE_MODEL)
    selectable_payload = {
        "tenant_id": TENANT_ID,
        "name": SELECTABLE_NAME,
        "provider": default_model["provider"],
        "base_url": default_model.get("base_url"),
        "model": SELECTABLE_MODEL,
        "temperature": default_model["temperature"],
        "max_output_tokens": default_model["max_output_tokens"],
        "extra_body": default_model.get("extra_body") or {},
        "is_default": False,
        "enabled": True,
    }
    if selectable:
        selectable = request_json(
            "PUT",
            f"/api/enterprise/model-configs/{selectable['id']}",
            selectable_payload,
            token=token,
        )
    else:
        api_key = environment_value(ENV_FILE, "DEMO_MODEL_API_KEY")
        if not api_key:
            raise RuntimeError("DEMO_MODEL_API_KEY is missing from the private environment")
        selectable = request_json(
            "POST",
            "/api/enterprise/model-configs",
            {**selectable_payload, "api_key": api_key},
            token=token,
        )
    if selectable.get("model") != SELECTABLE_MODEL or not selectable.get("enabled"):
        raise RuntimeError("Agent Team did not persist the selectable Sonnet model")

    test_result = request_json(
        "POST",
        f"/api/enterprise/model-configs/{selectable['id']}/test?tenant_id={TENANT_ID}",
        payload={},
        token=token,
        timeout=660,
    )
    if not test_result.get("success"):
        raise RuntimeError("RC-backed Sonnet model connectivity test failed")
    print(
        "Configured RC-backed Agent Team models PASS "
        f"default={TARGET_MODEL} selectable={SELECTABLE_MODEL}"
    )


if __name__ == "__main__":
    main()
