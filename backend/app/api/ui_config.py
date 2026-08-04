from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from app.db import get_session
from app.db.models import ModelConfig, UIConfig, User, utc_now
from app.security.auth import get_current_user, require_current_tenant
from app.security.permissions import ensure_tenant_admin
from app.security.tenant import ensure_tenant

enterprise_router = APIRouter(
    prefix="/api/enterprise/ui-config",
    tags=["enterprise:ui-config"],
    dependencies=[Depends(get_current_user)],
)
chat_router = APIRouter(prefix="/api/chat/ui-config", tags=["chat:ui-config"])


class UIConfigRead(BaseModel):
    tenant_id: str
    show_thinking_trace: bool
    show_skill_trace: bool
    show_tool_trace: bool
    reflection_max_rounds: int
    agent_loop_max_actions: int
    claude_runtime_enabled: bool
    claude_model_config_id: str | None
    claude_skill_allowlist: list[str]
    claude_max_repair_rounds: int
    updated_at: str

    model_config = ConfigDict(from_attributes=True)


class UIConfigUpdateRequest(BaseModel):
    tenant_id: str
    show_thinking_trace: bool = True
    show_skill_trace: bool = True
    show_tool_trace: bool = True
    reflection_max_rounds: int = Field(default=1, ge=0, le=5)
    agent_loop_max_actions: int = Field(default=6, ge=1, le=20)
    claude_runtime_enabled: bool = False
    claude_model_config_id: str | None = None
    claude_skill_allowlist: list[str] = Field(default_factory=list)
    claude_max_repair_rounds: int = Field(default=2, ge=0, le=5)


def ui_config_read(row: UIConfig) -> UIConfigRead:
    return UIConfigRead(
        tenant_id=row.tenant_id,
        show_thinking_trace=row.show_thinking_trace,
        show_skill_trace=row.show_skill_trace,
        show_tool_trace=row.show_tool_trace,
        reflection_max_rounds=row.reflection_max_rounds,
        agent_loop_max_actions=row.agent_loop_max_actions,
        claude_runtime_enabled=row.claude_runtime_enabled,
        claude_model_config_id=row.claude_model_config_id,
        claude_skill_allowlist=row.claude_skill_allowlist_json or [],
        claude_max_repair_rounds=row.claude_max_repair_rounds,
        updated_at=row.updated_at.isoformat(),
    )


def get_or_create_ui_config(db: Session, tenant_id: str) -> UIConfig:
    ensure_tenant(db, tenant_id)
    row = db.get(UIConfig, tenant_id)
    if not row:
        row = UIConfig(tenant_id=tenant_id)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


@enterprise_router.get("", response_model=UIConfigRead, dependencies=[Depends(require_current_tenant)])
def get_enterprise_ui_config(
    tenant_id: str = Query(...), db: Session = Depends(get_session)
) -> UIConfigRead:
    return ui_config_read(get_or_create_ui_config(db, tenant_id))


@enterprise_router.put("", response_model=UIConfigRead)
def update_enterprise_ui_config(
    request: UIConfigUpdateRequest,
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> UIConfigRead:
    ensure_tenant_admin(request.tenant_id, current_user)
    row = get_or_create_ui_config(db, request.tenant_id)
    row.show_thinking_trace = request.show_thinking_trace
    row.show_skill_trace = request.show_skill_trace
    row.show_tool_trace = request.show_tool_trace
    row.reflection_max_rounds = request.reflection_max_rounds
    row.agent_loop_max_actions = request.agent_loop_max_actions
    if request.claude_runtime_enabled and not request.claude_model_config_id:
        raise HTTPException(status_code=400, detail="Claude Runtime model is required")
    if request.claude_runtime_enabled and not request.claude_skill_allowlist:
        raise HTTPException(status_code=400, detail="Claude Runtime skill allowlist is required")
    if request.claude_model_config_id:
        model = db.get(ModelConfig, request.claude_model_config_id)
        if (
            not model
            or model.tenant_id != request.tenant_id
            or not model.enabled
            or model.provider != "claude_agent_sdk"
        ):
            raise HTTPException(
                status_code=400,
                detail="Claude Runtime model must be an enabled claude_agent_sdk ModelConfig",
            )
    row.claude_runtime_enabled = request.claude_runtime_enabled
    row.claude_model_config_id = request.claude_model_config_id
    row.claude_skill_allowlist_json = list(dict.fromkeys(request.claude_skill_allowlist))
    row.claude_max_repair_rounds = request.claude_max_repair_rounds
    row.updated_at = utc_now()
    db.add(row)
    db.commit()
    db.refresh(row)
    return ui_config_read(row)


@chat_router.get("", response_model=UIConfigRead)
def get_chat_ui_config(
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> UIConfigRead:
    if tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="Tenant mismatch")
    return ui_config_read(get_or_create_ui_config(db, tenant_id))
