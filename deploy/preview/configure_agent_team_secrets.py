#!/usr/bin/env python3
"""Copy Agent Team MCP credentials into the preview's private environment by reference.

The values are never printed and the destination remains mode 0600. This script is
intended to run on the NeoSpark host before recreating the preview container.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


DEFAULT_SOURCE_ROOT = Path("/opt/cc-platform")
DEFAULT_ENV_FILE = Path("/data/staffdeck-preview/backend.env")


def _secret(path: Path) -> str:
    value = json.loads(path.read_text(encoding="utf-8")).get("secret_key")
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"Required secret_key is missing from {path}")
    return value.strip()


def _upsert_many(path: Path, values: dict[str, str]) -> None:
    existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    remaining = dict(values)
    updated: list[str] = []
    for line in existing:
        key, separator, _value = line.partition("=")
        if separator and key in remaining:
            updated.append(f"{key}={json.dumps(remaining.pop(key))}")
        else:
            updated.append(line)
    for key in sorted(remaining):
        updated.append(f"{key}={json.dumps(remaining[key])}")

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
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    args = parser.parse_args()

    skill_root = args.source_root / "base" / ".claude" / "skills"
    values = {
        "AGENT_TEAM_SELLERSPRITE_MCP_KEY": _secret(skill_root / "sellersprite" / "config.json"),
        "AGENT_TEAM_SORFTIME_MCP_KEY": _secret(skill_root / "sorftime" / "config.json"),
        "TOOL_TIMEOUT_SECONDS": "120",
    }
    _upsert_many(args.env_file, values)
    print("Configured Agent Team MCP secret references in the private preview environment")


if __name__ == "__main__":
    main()
