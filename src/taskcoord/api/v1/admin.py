from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from taskcoord.api.deps import db_session, ok, perform, require_admin, require_csrf, require_reader
from taskcoord.auth import Actor
from taskcoord.context import request_id_var
from taskcoord.models import Agent, Task, TaskEvent
from taskcoord.schemas import AgentIn, ImportCommitIn, RestoreDryRunIn
from taskcoord.services.backup_service import backup_database, inspect_backup, remember_export
from taskcoord.services.import_service import commit_import, preview_import
from taskcoord.services.serialize import agent_dict, event_dict, project_dict, task_dict
from taskcoord.services.task_service import create_agent, list_agents, list_projects, rotate_agent_token
from taskcoord.settings import ROOT_DIR

router = APIRouter()


@router.get("/agents")
def get_agents(session: Session = Depends(db_session), _actor: Actor = Depends(require_reader)) -> dict:
    return ok(agents=[agent_dict(agent, tasks) for agent, tasks in list_agents(session)])


@router.post("/agents")
def post_agent(
    payload: AgentIn,
    request: Request,
    session: Session = Depends(db_session),
    actor: Actor = Depends(require_csrf),
):
    require_admin(actor)

    def work(_session: Session):
        agent, token = create_agent(
            _session,
            agent_id=payload.id,
            display_name=payload.display_name,
            machine_name=payload.machine_name,
            client_type=payload.client_type,
        )
        return 200, {"request_id": request_id_var.get(), "agent": agent_dict(agent), "api_token": token}

    return perform(request, session, actor, work)


@router.post("/agents/{agent_id}/rotate-token")
def post_rotate(agent_id: str, request: Request, session: Session = Depends(db_session), actor: Actor = Depends(require_csrf)):
    require_admin(actor)

    def work(_session: Session):
        agent = _session.get(Agent, agent_id)
        if agent is None:
            from taskcoord.errors import ApiError

            raise ApiError(404, "AGENT_NOT_FOUND", "Agent 不存在")
        token = rotate_agent_token(_session, agent)
        return 200, {"request_id": request_id_var.get(), "agent": agent_dict(agent), "api_token": token}

    return perform(request, session, actor, work)


@router.get("/admin/export")
def export_snapshot(request: Request, session: Session = Depends(db_session), actor: Actor = Depends(require_admin)) -> dict:
    tasks = session.scalars(select(Task).options(selectinload(Task.scopes), selectinload(Task.project)).order_by(Task.id)).all()
    events = session.scalars(select(TaskEvent).order_by(TaskEvent.server_created_at)).all()
    snapshot = {
        "projects": [project_dict(project) for project in list_projects(session)],
        "tasks": [task_dict(task) for task in tasks],
        "agents": [agent_dict(agent, current) for agent, current in list_agents(session)],
        "events": [event_dict(event) for event in events],
    }
    export_dir = request.app.state.settings.backup_directory
    export_dir.mkdir(parents=True, exist_ok=True)
    from taskcoord.clock import utcnow

    path = export_dir / f"export-{utcnow().strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    remember_export(session, str(path))
    return ok(snapshot=snapshot, path=str(path))


@router.post("/admin/backup")
def post_backup(request: Request, session: Session = Depends(db_session), actor: Actor = Depends(require_csrf)):
    require_admin(actor)

    def work(_session: Session):
        report = backup_database(request.app.state.settings, _session)
        return 200, {"request_id": request_id_var.get(), "backup": report}

    return perform(request, session, actor, work)


@router.post("/admin/import/preview")
async def post_preview(request: Request, session: Session = Depends(db_session), actor: Actor = Depends(require_csrf)):
    require_admin(actor)
    raw = getattr(request.state, "raw_body", b"") or await request.body()

    def work(_session: Session):
        report = preview_import(_session, raw)
        return 200, {"request_id": request_id_var.get(), "report": report}

    return perform(request, session, actor, work)


@router.post("/admin/import/commit")
def post_commit(
    payload: ImportCommitIn,
    request: Request,
    session: Session = Depends(db_session),
    actor: Actor = Depends(require_csrf),
):
    require_admin(actor)

    def work(_session: Session):
        result = commit_import(
            _session,
            request.app.state.settings,
            payload.import_id,
            owner_map=payload.owner_map,
            claimed_resolutions=payload.claimed_resolutions,
            actor_user=actor.label,
        )
        return 200, {"request_id": request_id_var.get(), "result": result}

    return perform(request, session, actor, work)


@router.post("/admin/restore/dry-run")
def post_restore_dry_run(payload: RestoreDryRunIn, _actor: Actor = Depends(require_admin)) -> dict:
    path = Path(payload.path)
    if not path.is_absolute():
        path = ROOT_DIR / path
    return ok(backup=inspect_backup(path))
