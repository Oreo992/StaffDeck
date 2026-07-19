from __future__ import annotations

import os
import re
from typing import Any


SECRET_PATTERN = re.compile(r"\$\{secret\.([A-Z0-9_]+)\}")


def resolve_secret_reference(value: str) -> str:
    """Resolve ${secret.NAME} placeholders from the process environment."""

    def replace(match: re.Match[str]) -> str:
        return os.getenv(match.group(1), "")

    return SECRET_PATTERN.sub(replace, value)


def resolve_secret_references(value: Any) -> Any:
    """Recursively resolve secret placeholders without mutating persisted config."""

    if isinstance(value, str):
        return resolve_secret_reference(value)
    if isinstance(value, dict):
        return {str(key): resolve_secret_references(item) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_secret_references(item) for item in value]
    if isinstance(value, tuple):
        return tuple(resolve_secret_references(item) for item in value)
    return value
