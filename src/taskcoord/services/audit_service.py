from __future__ import annotations

import json
import uuid

from sqlalchemy.orm import Session

from taskcoord.clock import utcnow
from taskcoord.context import idempotency_key_var, request_id_var
from taskcoord.models import TaskEvent


def append_event(
    session: Session,
    *,
    event_type: str,
    task_id: str | None,
    old_status: str | None,
    new_status: str | None,
    old_version: int | None,
    new_version: int | None,
    actor_agent_id: str | None = None,
    actor_user: str | None = None,
    payload: dict | None = None,
) -> TaskEvent:
    event = TaskEvent(
        event_id="evt_" + uuid.uuid4().hex,
        task_id=task_id,
        event_type=event_type,
        actor_agent_id=actor_agent_id,
        actor_user=actor_user,
        old_status=old_status,
        new_status=new_status,
        old_version=old_version,
        new_version=new_version,
        request_id=request_id_var.get() or None,
        idempotency_key=idempotency_key_var.get() or None,
        payload_json=json.dumps(payload or {}, ensure_ascii=False, separators=(",", ":")),
        server_created_at=utcnow(),
    )
    session.add(event)
    return event
