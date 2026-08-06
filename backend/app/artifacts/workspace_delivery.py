from __future__ import annotations

import hashlib
import mimetypes
import os
import re
from pathlib import Path, PurePosixPath
from typing import Any

from app import paths


MAX_ARTIFACT_BYTES = 25 * 1024 * 1024
MAX_ARTIFACTS_PER_TURN = 20
_SAFE_ID = re.compile(r"[^A-Za-z0-9_-]+")


class WorkspaceArtifactError(RuntimeError):
    pass


def normalize_artifact_path(value: str) -> str:
    raw = str(value or "").replace("\\", "/").strip()
    if not raw or "\x00" in raw:
        raise WorkspaceArtifactError("Artifact path is invalid.")
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise WorkspaceArtifactError("Artifact path traversal is denied.")
    if path.parts[0].startswith("."):
        raise WorkspaceArtifactError("Hidden artifact paths are denied.")
    return path.as_posix()


def artifact_workspace(tenant_id: str, session_id: str, task_frame_id: str) -> Path:
    return (
        paths.user_data_dir()
        / "generated-artifacts"
        / _safe_component(tenant_id)
        / _safe_component(session_id)
        / _safe_component(task_frame_id)
    )


def publish_text_artifact(
    *,
    tenant_id: str,
    session_id: str,
    task_frame_id: str,
    filename: str,
    content: str,
    description: str = "",
    content_type: str | None = None,
) -> dict[str, Any]:
    normalized = normalize_artifact_path(filename)
    data = str(content).encode("utf-8")
    if len(data) > MAX_ARTIFACT_BYTES:
        raise WorkspaceArtifactError("Artifact exceeds the file-size limit.")
    root = artifact_workspace(tenant_id, session_id, task_frame_id)
    target = _resolved_target(root, normalized)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    temporary.write_bytes(data)
    temporary.replace(target)
    media_type = content_type or _content_type_for(target.name)
    return {
        "type": "workspace_file",
        "task_frame_id": task_frame_id,
        "path": normalized,
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
        "display_name": target.name,
        "description": description.strip() or "Claude 生成文件",
        "content_type": media_type,
        "operation": "created",
        "source": "claude_supervised",
    }


def read_published_artifact(
    *,
    tenant_id: str,
    session_id: str,
    task_frame_id: str,
    artifact: dict[str, Any],
) -> tuple[bytes, str, str]:
    normalized = normalize_artifact_path(str(artifact.get("path") or ""))
    root = artifact_workspace(tenant_id, session_id, task_frame_id)
    target = _resolved_target(root, normalized)
    if not target.is_file() or target.is_symlink():
        raise WorkspaceArtifactError("Artifact is unavailable.")
    data = target.read_bytes()
    if len(data) > MAX_ARTIFACT_BYTES:
        raise WorkspaceArtifactError("Artifact exceeds the file-size limit.")
    expected_size = artifact.get("size")
    expected_digest = str(artifact.get("sha256") or "").lower()
    digest = hashlib.sha256(data).hexdigest()
    if (isinstance(expected_size, int) and expected_size != len(data)) or (
        expected_digest and expected_digest != digest
    ):
        raise WorkspaceArtifactError("Artifact has changed.")
    filename = _safe_download_name(str(artifact.get("display_name") or target.name))
    media_type = str(artifact.get("content_type") or "").strip()
    media_type = media_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return data, filename, media_type


def _resolved_target(root: Path, relative_path: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    resolved_root = root.resolve()
    candidate = (resolved_root / Path(*PurePosixPath(relative_path).parts)).resolve()
    if candidate == resolved_root or resolved_root not in candidate.parents:
        raise WorkspaceArtifactError("Artifact escaped its workspace.")
    return candidate


def _safe_component(value: str) -> str:
    cleaned = _SAFE_ID.sub("-", str(value or "")).strip("-")[:96]
    if not cleaned:
        raise WorkspaceArtifactError("Artifact scope identifier is invalid.")
    return cleaned


def _safe_download_name(value: str) -> str:
    cleaned = "".join(
        character
        for character in value
        if character not in {"\r", "\n", "\x00"} and character.isprintable()
    ).strip()
    return cleaned[:180] or "artifact"


def _content_type_for(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    overrides = {
        ".csv": "text/csv",
        ".html": "text/html; charset=utf-8",
        ".htm": "text/html; charset=utf-8",
        ".json": "application/json",
        ".md": "text/markdown",
        ".txt": "text/plain; charset=utf-8",
    }
    return overrides.get(suffix) or mimetypes.guess_type(filename)[0] or "text/plain"
