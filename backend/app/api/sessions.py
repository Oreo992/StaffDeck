from __future__ import annotations

import json
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.api.chat import message_read, session_read
from app.db import get_session
from app.db.models import AgentEvent, ChatSession, Message, User, utc_now
from app.security.auth import get_current_user
from app.security.tenant import ensure_tenant

router = APIRouter(prefix="/api/enterprise/sessions", tags=["enterprise:sessions"])
SESSION_LOG_EXPORT_SCHEMA = "staffdeck.conversation-log.v1"


class SessionLogExportRequest(BaseModel):
    session_ids: list[str] = Field(min_length=1, max_length=500)


@router.get("")
def list_sessions(
    tenant_id: str = Query(...),
    agent_id: str | None = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[dict]:
    _ensure_request_tenant(tenant_id, current_user)
    ensure_tenant(db, tenant_id)
    conditions = [ChatSession.tenant_id == tenant_id, ChatSession.user_id == current_user.id]
    if agent_id:
        conditions.append(ChatSession.agent_id == agent_id)
    rows = db.exec(
        select(ChatSession).where(*conditions).order_by(ChatSession.updated_at.desc())
    ).all()
    return [session_read(row).model_dump() for row in rows]


@router.post("/export")
def export_session_logs(
    request: SessionLogExportRequest,
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> Response:
    _ensure_request_tenant(tenant_id, current_user)
    session_ids = list(dict.fromkeys(request.session_ids))
    rows = [
        _get_chat_session(db, tenant_id, current_user.id, session_id)
        for session_id in session_ids
    ]
    exported_at = datetime.now(UTC)
    return _json_download_response(
        {
            "schema_version": SESSION_LOG_EXPORT_SCHEMA,
            "exported_at": exported_at,
            "count": len(rows),
            "items": [_session_detail_payload(db, tenant_id, row) for row in rows],
        },
        f"staffdeck-conversation-logs-{exported_at.strftime('%Y%m%d-%H%M%S')}.json",
    )


@router.get("/{session_id}/export")
def export_session_log(
    session_id: str,
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> Response:
    _ensure_request_tenant(tenant_id, current_user)
    row = _get_chat_session(db, tenant_id, current_user.id, session_id)
    return _json_download_response(
        {
            "schema_version": SESSION_LOG_EXPORT_SCHEMA,
            "exported_at": datetime.now(UTC),
            "item": _session_detail_payload(db, tenant_id, row),
        },
        f"staffdeck-conversation-log-{_safe_filename_part(session_id)}.json",
    )


@router.get("/{session_id}")
def get_session_detail(
    session_id: str,
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict:
    _ensure_request_tenant(tenant_id, current_user)
    row = _get_chat_session(db, tenant_id, current_user.id, session_id)
    return _session_detail_payload(db, tenant_id, row)


def _session_detail_payload(db: Session, tenant_id: str, row: ChatSession) -> dict:
    messages = db.exec(
        select(Message)
        .where(Message.tenant_id == tenant_id, Message.session_id == row.id)
        .order_by(Message.created_at)
    ).all()
    events = db.exec(
        select(AgentEvent)
        .where(AgentEvent.tenant_id == tenant_id, AgentEvent.session_id == row.id)
        .order_by(AgentEvent.created_at)
    ).all()
    return {
        "session": session_read(row).model_dump(),
        "messages": [message_read(message).model_dump() for message in messages],
        "events": [
            {
                "id": event.id,
                "event_type": event.event_type,
                "payload": event.payload_json,
                "created_at": event.created_at.isoformat(),
            }
            for event in events
        ],
    }


def _json_download_response(payload: object, filename: str) -> Response:
    content = json.dumps(
        jsonable_encoder(payload),
        ensure_ascii=False,
        indent=2,
    ).encode("utf-8")
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _safe_filename_part(value: str) -> str:
    safe = "".join(
        character if character.isalnum() or character in "-_" else "-" for character in value
    )
    return safe.strip("-") or "session"


@router.post("/{session_id}/reset")
def reset_session(
    session_id: str,
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict:
    _ensure_request_tenant(tenant_id, current_user)
    row = _get_chat_session(db, tenant_id, current_user.id, session_id)
    row.active_skill_id = None
    row.active_step_id = None
    row.slots_json = {}
    row.skill_stack_json = []
    row.pending_tasks_json = []
    row.resume_after_answer_json = None
    row.summary = None
    row.last_agent_question = None
    row.status = "active"
    row.updated_at = utc_now()
    db.add(row)
    db.commit()
    db.refresh(row)
    return session_read(row).model_dump()


def _get_chat_session(db: Session, tenant_id: str, user_id: str, session_id: str) -> ChatSession:
    ensure_tenant(db, tenant_id)
    row = db.get(ChatSession, session_id)
    if not row or row.tenant_id != tenant_id or row.user_id != user_id:
        raise HTTPException(status_code=404, detail="Session not found")
    return row


def _ensure_request_tenant(tenant_id: str, current_user: User) -> None:
    if tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="Tenant mismatch")
