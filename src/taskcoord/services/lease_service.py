from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from taskcoord.clock import ensure_utc
from taskcoord.models import Task
from taskcoord.services.audit_service import append_event
from taskcoord.settings import Settings


def lease_deadline(settings: Settings, now: datetime) -> datetime:
    return now + timedelta(minutes=settings.lease_duration_minutes)


def expire_due(session: Session, now: datetime, *, actor_user: str | None = None) -> list[str]:
    now = ensure_utc(now) or now
    rows = session.scalars(select(Task).where(Task.status == "claimed", Task.lease_expires_at.is_not(None))).all()
    expired: list[str] = []
    for task in rows:
        deadline = ensure_utc(task.lease_expires_at)
        if deadline is None or deadline > now:
            continue
        old_status = task.status
        old_version = task.version
        old_owner = task.owner_agent_id
        task.status = "ready"
        task.owner_agent_id = None
        task.claim_token_hash = None
        task.claim_reissue_pending = False
        task.lease_expires_at = None
        task.version += 1
        task.updated_at = now
        task.status_reason = "租约到期"
        append_event(
            session,
            event_type="task.claim_expired",
            task_id=task.id,
            old_status=old_status,
            new_status=task.status,
            old_version=old_version,
            new_version=task.version,
            actor_user=actor_user or "system",
            payload={"previous_owner": old_owner},
        )
        expired.append(task.id)
    if expired:
        session.flush()
    return expired


def renew_lease(task: Task, settings: Settings, now: datetime) -> None:
    task.lease_expires_at = lease_deadline(settings, now)
    task.updated_at = now
    task.version += 1
