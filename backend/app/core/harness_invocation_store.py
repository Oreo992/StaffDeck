from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.core.tool_replay_policy import ToolReplayPolicy
from app.db.models import HarnessInvocationRecord, utc_now


class HarnessInvocationConflict(RuntimeError):
    """A durable invocation fence prevents an unsafe retry."""


@dataclass(frozen=True)
class HarnessInvocationClaim:
    record: HarnessInvocationRecord | None
    replay: dict[str, Any] | None = None


class HarnessInvocationStore:
    """Exactly-once claims and replay for capability invocations."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def claim(
        self,
        *,
        tenant_id: str,
        session_id: str,
        task_id: str,
        run_id: str,
        call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        logical_action_key: str | None = None,
    ) -> HarnessInvocationClaim:
        digest = request_digest(tool_name, arguments)
        existing = self._find_call(run_id, call_id)
        if existing is not None:
            if existing.request_digest != digest:
                raise HarnessInvocationConflict(
                    "同一个 run_id/call_id 不能用于不同的 Harness 工具请求。"
                )
            return self._existing_claim(existing)

        if logical_action_key:
            prior = self._find_action(logical_action_key)
            if prior is not None:
                return self._existing_claim(prior)

        record = HarnessInvocationRecord(
            tenant_id=tenant_id,
            session_id=session_id,
            task_id=task_id,
            run_id=run_id,
            call_id=call_id,
            tool_name=tool_name,
            request_digest=digest,
            logical_action_key=logical_action_key,
            status="started",
            arguments_json=dict(arguments),
        )
        self.db.add(record)
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            existing = self._find_call(run_id, call_id)
            if existing is not None:
                if existing.request_digest != digest:
                    raise HarnessInvocationConflict(
                        "同一个 run_id/call_id 不能用于不同的 Harness 工具请求。"
                    )
                return self._existing_claim(existing)
            if logical_action_key:
                prior = self._find_action(logical_action_key)
                if prior is not None:
                    return self._existing_claim(prior)
            raise
        self.db.refresh(record)
        return HarnessInvocationClaim(record=record)

    def finish(
        self,
        record: HarnessInvocationRecord,
        result: dict[str, Any],
        *,
        definitely_not_sent: bool = False,
    ) -> None:
        now = utc_now()
        success = result.get("success") is True
        if success:
            status = "completed"
            action_key = record.logical_action_key
        elif definitely_not_sent:
            status = "failed"
            action_key = None
        else:
            status = "outcome_unknown"
            action_key = record.logical_action_key
        update_result = self.db.exec(
            update(HarnessInvocationRecord)
            .where(
                HarnessInvocationRecord.id == record.id,
                HarnessInvocationRecord.status == "started",
                HarnessInvocationRecord.request_digest == record.request_digest,
            )
            .values(
                status=status,
                logical_action_key=action_key,
                result_json=dict(result),
                response_cache_json=dict(result),
                finished_at=now,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        if getattr(update_result, "rowcount", 0) != 1:
            self.db.rollback()
            raise HarnessInvocationConflict(
                "Harness invocation 已由其他执行者更新，不能覆盖结果。"
            )
        self.db.commit()
        self.db.refresh(record)

    def cancel(self, record: HarnessInvocationRecord) -> None:
        now = utc_now()
        update_result = self.db.exec(
            update(HarnessInvocationRecord)
            .where(
                HarnessInvocationRecord.id == record.id,
                HarnessInvocationRecord.status == "started",
            )
            .values(
                status="cancelled",
                logical_action_key=None,
                finished_at=now,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        if getattr(update_result, "rowcount", 0) != 1:
            self.db.rollback()
            raise HarnessInvocationConflict(
                "Harness invocation 已由其他执行者更新，不能取消。"
            )
        self.db.commit()
        self.db.refresh(record)

    def _find_call(self, run_id: str, call_id: str) -> HarnessInvocationRecord | None:
        return self.db.exec(
            select(HarnessInvocationRecord).where(
                HarnessInvocationRecord.run_id == run_id,
                HarnessInvocationRecord.call_id == call_id,
            )
        ).first()

    def _find_action(self, action_key: str) -> HarnessInvocationRecord | None:
        return self.db.exec(
            select(HarnessInvocationRecord).where(
                HarnessInvocationRecord.logical_action_key == action_key
            )
        ).first()

    @staticmethod
    def _existing_claim(prior: HarnessInvocationRecord) -> HarnessInvocationClaim:
        if prior.status == "completed" and prior.response_cache_json.get("success") is True:
            return HarnessInvocationClaim(record=prior, replay=_replayed_result(prior))
        raise HarnessInvocationConflict(
            "相同副作用调用已有未完成或结果未知的持久化记录；"
            "为避免重复提交，Harness 不会自动重试，请先核对外部系统状态。"
        )


def logical_action_key(
    *,
    tenant_id: str,
    task_frame_id: str,
    step_id: str | None,
    tool_id: str,
    tool_name: str,
    arguments: dict[str, Any],
    key_fields: list[str] | None = None,
) -> str:
    key_arguments = ToolReplayPolicy.arguments(arguments, key_fields)
    signature = ToolReplayPolicy.signature(tool_name, key_arguments)
    canonical = json.dumps(
        {
            "tenant_id": tenant_id,
            "task_frame_id": task_frame_id,
            "step_id": step_id,
            "tool_id": tool_id,
            "signature": signature,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def request_digest(name: str, arguments: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"name": name, "arguments": arguments},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _replayed_result(invocation: HarnessInvocationRecord) -> dict[str, Any]:
    result = dict(invocation.response_cache_json or {})
    data = result.get("data")
    replay_metadata = {
        "idempotent_replay": True,
        "replayed_from_invocation_id": invocation.id,
    }
    if isinstance(data, dict):
        result["data"] = {**data, **replay_metadata}
    else:
        result["data"] = {"result": data, **replay_metadata}
    result["idempotent_replay"] = True
    return result


__all__ = [
    "HarnessInvocationClaim",
    "HarnessInvocationConflict",
    "HarnessInvocationStore",
    "logical_action_key",
    "request_digest",
]
