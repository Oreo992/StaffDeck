from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
import hashlib
from typing import Any

from fastapi import HTTPException
from sqlmodel import Session, select

from app.capability_evolution.schema import CapabilityEvolutionProposalRead
from app.db.models import (
    AgentEvent,
    AgentResourceBinding,
    CapabilityEvolutionProposal,
    ChatSession,
    GeneralSkill,
    MessageFeedback,
    User,
    utc_now,
)
from app.security.permissions import ensure_agent_scope_manager


LEARNING_SECTION = "## 经审核沉淀的经验"


class CapabilityEvolutionService:
    def __init__(self, db: Session):
        self.db = db

    def learn_from_recent_feedback(
        self,
        *,
        tenant_id: str,
        agent_id: str,
        current_user: User,
        period_days: int = 30,
        now: datetime | None = None,
    ) -> list[CapabilityEvolutionProposal]:
        ensure_agent_scope_manager(self.db, tenant_id, agent_id, current_user)
        now = now or utc_now()
        cutoff = now - timedelta(days=period_days)
        feedback_rows = self.db.exec(
            select(MessageFeedback)
            .join(ChatSession, MessageFeedback.session_id == ChatSession.id)
            .where(
                MessageFeedback.tenant_id == tenant_id,
                MessageFeedback.rating == "down",
                MessageFeedback.analysis_status == "analyzed",
                MessageFeedback.updated_at >= cutoff,
                ChatSession.tenant_id == tenant_id,
                ChatSession.agent_id == agent_id,
            )
            .order_by(MessageFeedback.updated_at.asc())
        ).all()
        if not feedback_rows:
            return []

        bindings = self.db.exec(
            select(AgentResourceBinding).where(
                AgentResourceBinding.tenant_id == tenant_id,
                AgentResourceBinding.agent_id == agent_id,
                AgentResourceBinding.resource_type == "general_skill",
                AgentResourceBinding.status == "active",
            )
        ).all()
        skills = {
            row.slug: row
            for row in self.db.exec(
                select(GeneralSkill).where(
                    GeneralSkill.tenant_id == tenant_id,
                    GeneralSkill.id.in_([binding.resource_id for binding in bindings]),
                    GeneralSkill.status == "published",
                )
            ).all()
        }
        session_ids = sorted({row.session_id for row in feedback_rows})
        loaded_by_session: dict[str, list[AgentEvent]] = defaultdict(list)
        if session_ids:
            for event in self.db.exec(
                select(AgentEvent)
                .where(
                    AgentEvent.tenant_id == tenant_id,
                    AgentEvent.session_id.in_(session_ids),
                    AgentEvent.event_type == "claude_skill_loaded",
                )
                .order_by(AgentEvent.created_at.asc())
            ).all():
                loaded_by_session[event.session_id].append(event)

        created: list[CapabilityEvolutionProposal] = []
        for feedback in feedback_rows:
            skill = _latest_loaded_skill(loaded_by_session[feedback.session_id], skills)
            instruction = str((feedback.analysis_json or {}).get("suggested_action") or "").strip()
            if not skill or not instruction:
                continue
            fingerprint = _fingerprint(agent_id, skill.id, feedback.analysis_bucket, instruction)
            existing = self.db.exec(
                select(CapabilityEvolutionProposal).where(
                    CapabilityEvolutionProposal.tenant_id == tenant_id,
                    CapabilityEvolutionProposal.agent_id == agent_id,
                    CapabilityEvolutionProposal.fingerprint == fingerprint,
                    CapabilityEvolutionProposal.status.in_(["pending", "applied"]),
                )
            ).first()
            if existing:
                continue
            before = skill.skill_markdown
            proposal = CapabilityEvolutionProposal(
                tenant_id=tenant_id,
                agent_id=agent_id,
                target_resource_id=skill.id,
                target_label=skill.name,
                title=f"改进「{skill.name}」的执行规则",
                summary=feedback.analysis_summary or feedback.analysis_reason or instruction,
                instruction=instruction,
                evidence_json=[
                    {
                        "feedback_id": feedback.id,
                        "session_id": feedback.session_id,
                        "bucket": feedback.analysis_bucket or "unknown",
                        "summary": feedback.analysis_summary or "",
                        "reason": feedback.analysis_reason or "",
                        "evidence": list((feedback.analysis_json or {}).get("evidence") or []),
                    }
                ],
                source_refs_json=[feedback.id, feedback.session_id],
                before_content=before,
                after_content=_append_instruction(before, instruction),
                fingerprint=fingerprint,
                created_by_user_id=current_user.id,
                created_at=now,
                updated_at=now,
            )
            self.db.add(proposal)
            created.append(proposal)
        if created:
            self.db.commit()
            for proposal in created:
                self.db.refresh(proposal)
        return created

    def list_proposals(
        self,
        *,
        tenant_id: str,
        agent_id: str,
        current_user: User,
        status: str | None = None,
    ) -> list[CapabilityEvolutionProposalRead]:
        ensure_agent_scope_manager(self.db, tenant_id, agent_id, current_user)
        conditions = [
            CapabilityEvolutionProposal.tenant_id == tenant_id,
            CapabilityEvolutionProposal.agent_id == agent_id,
        ]
        if status:
            conditions.append(CapabilityEvolutionProposal.status == status)
        rows = self.db.exec(
            select(CapabilityEvolutionProposal)
            .where(*conditions)
            .order_by(CapabilityEvolutionProposal.created_at.desc())
        ).all()
        return [self._read(row) for row in rows]

    def apply_proposal(
        self,
        *,
        tenant_id: str,
        agent_id: str,
        proposal_id: str,
        current_user: User,
        now: datetime | None = None,
    ) -> CapabilityEvolutionProposal:
        ensure_agent_scope_manager(self.db, tenant_id, agent_id, current_user)
        proposal = self._proposal(tenant_id, agent_id, proposal_id)
        if proposal.status != "pending":
            raise HTTPException(status_code=409, detail="Evolution proposal is no longer pending")
        skill = self.db.get(GeneralSkill, proposal.target_resource_id)
        if not skill or skill.tenant_id != tenant_id:
            raise HTTPException(status_code=404, detail="Target general skill not found")
        if skill.skill_markdown != proposal.before_content:
            raise HTTPException(
                status_code=409,
                detail="Target skill changed after this proposal was generated; learn again before applying",
            )
        now = now or utc_now()
        skill.skill_markdown = proposal.after_content
        skill.skill_files_json = _replace_skill_markdown(
            skill.skill_files_json,
            proposal.after_content,
        )
        metadata = dict(skill.metadata_json or {})
        history = list(metadata.get("evolution_history") or [])
        history.append({"proposal_id": proposal.id, "applied_at": now.isoformat()})
        skill.metadata_json = {**metadata, "evolution_history": history[-50:]}
        skill.updated_at = now
        proposal.status = "applied"
        proposal.reviewed_by_user_id = current_user.id
        proposal.applied_at = now
        proposal.updated_at = now
        self.db.add(skill)
        self.db.add(proposal)
        self.db.add(
            AgentEvent(
                tenant_id=tenant_id,
                session_id=_source_session_id(proposal),
                event_type="capability_evolution_applied",
                payload_json={
                    "proposal_id": proposal.id,
                    "agent_id": agent_id,
                    "target_kind": proposal.target_kind,
                    "target_resource_id": skill.id,
                    "slug": skill.slug,
                    "instruction": proposal.instruction,
                },
                created_at=now,
            )
        )
        self.db.commit()
        self.db.refresh(proposal)
        return proposal

    def reject_proposal(
        self,
        *,
        tenant_id: str,
        agent_id: str,
        proposal_id: str,
        current_user: User,
        now: datetime | None = None,
    ) -> CapabilityEvolutionProposal:
        ensure_agent_scope_manager(self.db, tenant_id, agent_id, current_user)
        proposal = self._proposal(tenant_id, agent_id, proposal_id)
        if proposal.status != "pending":
            raise HTTPException(status_code=409, detail="Evolution proposal is no longer pending")
        now = now or utc_now()
        proposal.status = "rejected"
        proposal.reviewed_by_user_id = current_user.id
        proposal.rejected_at = now
        proposal.updated_at = now
        self.db.add(proposal)
        self.db.commit()
        self.db.refresh(proposal)
        return proposal

    def proposal_read(self, proposal: CapabilityEvolutionProposal) -> CapabilityEvolutionProposalRead:
        return self._read(proposal)

    def _proposal(
        self,
        tenant_id: str,
        agent_id: str,
        proposal_id: str,
    ) -> CapabilityEvolutionProposal:
        proposal = self.db.get(CapabilityEvolutionProposal, proposal_id)
        if not proposal or proposal.tenant_id != tenant_id or proposal.agent_id != agent_id:
            raise HTTPException(status_code=404, detail="Evolution proposal not found")
        return proposal

    def _read(self, proposal: CapabilityEvolutionProposal) -> CapabilityEvolutionProposalRead:
        reuse_events: list[AgentEvent] = []
        if proposal.applied_at:
            skill = self.db.get(GeneralSkill, proposal.target_resource_id)
            if skill:
                reuse_events = self.db.exec(
                    select(AgentEvent)
                    .join(ChatSession, AgentEvent.session_id == ChatSession.id)
                    .where(
                        AgentEvent.tenant_id == proposal.tenant_id,
                        AgentEvent.event_type == "claude_skill_loaded",
                        AgentEvent.created_at > proposal.applied_at,
                        ChatSession.tenant_id == proposal.tenant_id,
                        ChatSession.agent_id == proposal.agent_id,
                    )
                    .order_by(AgentEvent.created_at.asc())
                ).all()
                reuse_events = [
                    event
                    for event in reuse_events
                    if str((event.payload_json or {}).get("slug") or "") == skill.slug
                ]
        return CapabilityEvolutionProposalRead(
            id=proposal.id,
            tenant_id=proposal.tenant_id,
            agent_id=proposal.agent_id,
            target_resource_id=proposal.target_resource_id,
            target_label=proposal.target_label,
            title=proposal.title,
            summary=proposal.summary,
            instruction=proposal.instruction,
            evidence=list(proposal.evidence_json or []),
            source_refs=list(proposal.source_refs_json or []),
            before_content=proposal.before_content,
            after_content=proposal.after_content,
            status=proposal.status,  # type: ignore[arg-type]
            reuse_count=len(reuse_events),
            last_reused_at=reuse_events[-1].created_at if reuse_events else None,
            applied_at=proposal.applied_at,
            rejected_at=proposal.rejected_at,
            created_at=proposal.created_at,
            updated_at=proposal.updated_at,
        )


def _latest_loaded_skill(
    events: list[AgentEvent],
    skills_by_slug: dict[str, GeneralSkill],
) -> GeneralSkill | None:
    for event in reversed(events):
        slug = str((event.payload_json or {}).get("slug") or "")
        if slug in skills_by_slug:
            return skills_by_slug[slug]
    return None


def _append_instruction(markdown: str, instruction: str) -> str:
    normalized = markdown.rstrip()
    bullet = f"- {instruction.strip()}"
    if bullet in normalized:
        return f"{normalized}\n"
    if LEARNING_SECTION in normalized:
        return f"{normalized}\n{bullet}\n"
    return f"{normalized}\n\n{LEARNING_SECTION}\n\n{bullet}\n"


def _replace_skill_markdown(files: list[dict[str, Any]], markdown: str) -> list[dict[str, Any]]:
    result = [dict(item) for item in (files or [])]
    target = next(
        (item for item in result if str(item.get("path") or "").replace("\\", "/").endswith("SKILL.md")),
        None,
    )
    payload = {
        "path": str((target or {}).get("path") or "SKILL.md"),
        "content": markdown,
        "size": len(markdown.encode("utf-8")),
        "mime_type": str((target or {}).get("mime_type") or "text/markdown"),
    }
    if target is None:
        result.insert(0, payload)
    else:
        target.update(payload)
    return result


def _fingerprint(agent_id: str, resource_id: str, bucket: str | None, instruction: str) -> str:
    value = "|".join([agent_id, resource_id, bucket or "unknown", " ".join(instruction.split()).lower()])
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _source_session_id(proposal: CapabilityEvolutionProposal) -> str:
    for value in proposal.source_refs_json or []:
        if str(value).startswith("session_"):
            return str(value)
    return f"evolution-{proposal.agent_id}"
