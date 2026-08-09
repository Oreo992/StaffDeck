from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from app.capability_evolution.schema import (
    CapabilityEvolutionActionRequest,
    CapabilityEvolutionLearnRequest,
    CapabilityEvolutionProposalRead,
    CapabilityEvolutionSummaryRead,
)
from app.capability_evolution.service import CapabilityEvolutionService
from app.db import get_session
from app.db.models import User
from app.security.auth import get_current_user


router = APIRouter(prefix="/api/enterprise/agents", tags=["capability-evolution"])


@router.get("/{agent_id}/evolution-summary", response_model=CapabilityEvolutionSummaryRead)
def get_evolution_summary(
    agent_id: str,
    tenant_id: str = Query(...),
    period_days: int = Query(default=30, ge=1, le=90),
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> CapabilityEvolutionSummaryRead:
    return CapabilityEvolutionService(db).evolution_summary(
        tenant_id=tenant_id,
        agent_id=agent_id,
        current_user=current_user,
        period_days=period_days,
    )


@router.get("/{agent_id}/evolution-proposals", response_model=list[CapabilityEvolutionProposalRead])
def list_evolution_proposals(
    agent_id: str,
    tenant_id: str = Query(...),
    status: str | None = Query(None),
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> list[CapabilityEvolutionProposalRead]:
    return CapabilityEvolutionService(db).list_proposals(
        tenant_id=tenant_id,
        agent_id=agent_id,
        current_user=current_user,
        status=status,
    )


@router.post("/{agent_id}/evolution-proposals/learn", response_model=list[CapabilityEvolutionProposalRead])
def learn_from_recent_sessions(
    agent_id: str,
    request: CapabilityEvolutionLearnRequest,
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> list[CapabilityEvolutionProposalRead]:
    service = CapabilityEvolutionService(db)
    rows = service.learn_from_recent_feedback(
        tenant_id=request.tenant_id,
        agent_id=agent_id,
        current_user=current_user,
        period_days=request.period_days,
    )
    return [service.proposal_read(row) for row in rows]


@router.post(
    "/{agent_id}/evolution-proposals/{proposal_id}/apply",
    response_model=CapabilityEvolutionProposalRead,
)
def apply_evolution_proposal(
    agent_id: str,
    proposal_id: str,
    request: CapabilityEvolutionActionRequest,
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> CapabilityEvolutionProposalRead:
    service = CapabilityEvolutionService(db)
    row = service.apply_proposal(
        tenant_id=request.tenant_id,
        agent_id=agent_id,
        proposal_id=proposal_id,
        current_user=current_user,
    )
    return service.proposal_read(row)


@router.post(
    "/{agent_id}/evolution-proposals/{proposal_id}/reject",
    response_model=CapabilityEvolutionProposalRead,
)
def reject_evolution_proposal(
    agent_id: str,
    proposal_id: str,
    request: CapabilityEvolutionActionRequest,
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> CapabilityEvolutionProposalRead:
    service = CapabilityEvolutionService(db)
    row = service.reject_proposal(
        tenant_id=request.tenant_id,
        agent_id=agent_id,
        proposal_id=proposal_id,
        current_user=current_user,
    )
    return service.proposal_read(row)
