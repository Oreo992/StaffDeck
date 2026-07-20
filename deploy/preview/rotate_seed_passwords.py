#!/usr/bin/env python3
"""Rotate Agent Team's seeded credentials and store replacements server-side."""

from __future__ import annotations

import json
import os
import secrets
import urllib.error
import urllib.request
from pathlib import Path


BASE_URL = "http://127.0.0.1:18173"
TENANT_ID = "tenant_demo"
CREDENTIAL_FILE = Path("/data/staffdeck-preview/login-credentials.json")
ACCOUNTS = (
    {"id": "admin", "username": "admin", "old_password": "admin"},
    {"id": "user_demo", "username": "user_demo", "old_password": "demo"},
)


def request_json(
    method: str,
    path: str,
    payload: dict[str, object],
    token: str | None = None,
) -> dict[str, object]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        BASE_URL + path,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def login(username: str, password: str) -> dict[str, object]:
    return request_json(
        "POST",
        "/api/auth/login",
        {"tenant_id": TENANT_ID, "username": username, "password": password},
    )


def login_rejected(username: str, password: str) -> bool:
    try:
        login(username, password)
    except urllib.error.HTTPError as error:
        return error.code == 401
    return False


def main() -> None:
    if CREDENTIAL_FILE.exists():
        CREDENTIAL_FILE.chmod(0o600)
        print(f"Rotated credential file already exists: {CREDENTIAL_FILE}")
        return

    admin_session = login("admin", "admin")
    admin_token = str(admin_session["token"])
    replacements = [
        {
            "tenant_id": TENANT_ID,
            "username": account["username"],
            "password": secrets.token_urlsafe(24),
        }
        for account in ACCOUNTS
    ]

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(CREDENTIAL_FILE, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(
                {"url": "https://preview.agentteam.neospark.cn", "accounts": replacements},
                handle,
                ensure_ascii=False,
                indent=2,
            )
            handle.write("\n")

        for account, replacement in zip(ACCOUNTS, replacements, strict=True):
            request_json(
                "PUT",
                f"/api/auth/users/{account['id']}",
                {"tenant_id": TENANT_ID, "password": replacement["password"]},
                admin_token,
            )

        for account, replacement in zip(ACCOUNTS, replacements, strict=True):
            login(str(replacement["username"]), str(replacement["password"]))
            if not login_rejected(account["username"], account["old_password"]):
                raise RuntimeError(f"Seeded password still works for {account['username']}")
    except Exception:
        # Keep the private recovery file if rotation was only partially applied.
        CREDENTIAL_FILE.chmod(0o600)
        raise

    print(f"Rotated seeded passwords; private credentials: {CREDENTIAL_FILE}")


if __name__ == "__main__":
    main()
