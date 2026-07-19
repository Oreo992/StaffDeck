#!/usr/bin/env python3
"""Create the private, persistent server state for the NeoSpark preview."""

from __future__ import annotations

import json
import os
import secrets
import subprocess
from pathlib import Path


STATE_DIR = Path("/data/staffdeck-preview")
APP_DATA_DIR = STATE_DIR / "app"
ENV_FILE = STATE_DIR / "backend.env"
CONTAINER_UID = 10001
CONTAINER_GID = 10001
DEFAULT_MODEL_NAME = "claude-opus-4-8"


def container_environment(container: str) -> dict[str, str]:
    result = subprocess.run(
        ["docker", "inspect", "--format", "{{json .Config.Env}}", container],
        check=True,
        capture_output=True,
        text=True,
    )
    values: dict[str, str] = {}
    for entry in json.loads(result.stdout):
        key, separator, value = entry.partition("=")
        if separator:
            values[key] = value
    return values


def upsert_environment_setting(path: Path, key: str, value: str) -> None:
    replacement = f"{key}={json.dumps(value)}"
    lines = path.read_text(encoding="utf-8").splitlines()
    updated: list[str] = []
    replaced = False
    for line in lines:
        if line.startswith(f"{key}="):
            updated.append(replacement)
            replaced = True
        else:
            updated.append(line)
    if not replaced:
        updated.append(replacement)

    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(temporary, flags, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write("\n".join(updated) + "\n")
    os.replace(temporary, path)
    path.chmod(0o600)


def main() -> None:
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    APP_DATA_DIR.mkdir(mode=0o750, exist_ok=True)
    os.chown(APP_DATA_DIR, CONTAINER_UID, CONTAINER_GID)

    if ENV_FILE.exists():
        upsert_environment_setting(ENV_FILE, "DEMO_MODEL_NAME", DEFAULT_MODEL_NAME)
        print(f"Updated private environment defaults: {ENV_FILE}")
        return

    litellm_key = container_environment("litellm-proxy").get("LITELLM_MASTER_KEY", "")
    if not litellm_key:
        raise RuntimeError("LITELLM_MASTER_KEY is missing from litellm-proxy")

    values = {
        "APP_NAME": "StaffDeck Preview",
        "APP_SECRET": secrets.token_urlsafe(64),
        "DEMO_MODEL_BASE_URL": "http://host.docker.internal:4000/v1",
        "DEMO_MODEL_NAME": DEFAULT_MODEL_NAME,
        "DEMO_MODEL_API_KEY": litellm_key,
        "MODEL_THINKING_MODE": "",
        "MODEL_THINKING_MODELS": "",
        "TOOL_TIMEOUT_SECONDS": "30",
        "GENERAL_SKILL_RUNTIME_AUTO_INSTALL": "true",
        "GENERAL_SKILL_RUNTIME_PACKAGES": "requests,httpx",
    }
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(ENV_FILE, flags, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={json.dumps(value)}\n")
    print(f"Created private environment: {ENV_FILE}")


if __name__ == "__main__":
    main()
