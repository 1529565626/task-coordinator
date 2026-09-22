from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from taskcoord.api.deps import db_session, ok, require_admin
from taskcoord.auth import Actor
from taskcoord.services.serialize import event_dict
from taskcoord.services.task_service import list_events

router = APIRouter()


@router.get("/events")
def get_events(
    task_id: str | None = None,
    agent_id: str | None = None,
    limit: int = 200,
    offset: int = 0,
    session: Session = Depends(db_session),
    _actor: Actor = Depends(require_admin),
) -> dict:
    rows = list_events(session, task_id=task_id, agent_id=agent_id, limit=limit, offset=offset)
    return ok(events=[event_dict(row) for row in rows])
