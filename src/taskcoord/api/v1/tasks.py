from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from taskcoord.api.deps import db_session, ensure_same_agent, ok, perform, require_admin, require_csrf, require_reader
from taskcoord.auth import Actor
from taskcoord.context import request_id_var
from taskcoord.errors import ApiError
from taskcoord.schemas import ReasonIn, RejectIn, TaskIn, TaskPatch
from taskcoord.services.claim_service import renew_from_detail
from taskcoord.services.rules import get_task, require_owner_token
from taskcoord.services.serialize import event_dict, task_dict
from taskcoord.services.task_service import (
    accept_task,
    block_task,
    cancel_task,
    confirm_task,
    create_task,
    get_project,
    list_events,
    list_tasks,
    next_task,
    reject_task,
    unblock_task,
    update_task,
)

router = APIRouter()


@router.get("/tasks")
def get_tasks(
    project: str | None = None,
    status: str | None = None,
    owner: str | None = None,
    scope: str | None = None,
    q: str | None = None,
    limit: int = 200,
    offset: int = 0,
    session: Session = Depends(db_session),
    _actor: Actor = Depends(require_reader),
) -> dict:
    rows = list_tasks(session, project_key=project, status=status, owner=owner, scope=scope, query=q, limit=limit, offset=offset)
    return ok(tasks=[task_dict(task) for task in rows])


@router.get("/tasks/next")
def get_next(project: str, session: Session = Depends(db_session), _actor: Actor = Depends(require_reader)) -> dict:
    task = next_task(session, project)
    return ok(task=task_dict(task) if task else None)


@router.get("/tasks/{task_id}")
def get_one(
    task_id: str,
    request: Request,
    session: Session = Depends(db_session),
    actor: Actor = Depends(require_reader),
) -> dict:
    task = get_task(session, task_id)
    claim_token = request.headers.get("x-claim-token", "")
    if claim_token and actor.agent is not None:
        renew_from_detail(session, request.app.state.settings, task, actor.agent, claim_token)
    return ok(task=task_dict(task))


@router.get("/tasks/{task_id}/events")
def get_task_events(
    task_id: str,
    limit: int = 200,
    offset: int = 0,
    session: Session = Depends(db_session),
    _actor: Actor = Depends(require_reader),
) -> dict:
    get_task(session, task_id)
    rows = list_events(session, task_id=task_id, agent_id=None, limit=limit, offset=offset)
    return ok(events=[event_dict(row) for row in rows])


@router.post("/tasks")
def post_task(
    payload: TaskIn,
    request: Request,
    session: Session = Depends(db_session),
    actor: Actor = Depends(require_csrf),
):
    require_admin(actor)

    def work(_session: Session):
        project = get_project(_session, payload.project_key)
        task = create_task(
            _session,
            task_id=payload.id,
            project=project,
            title=payload.title,
            description=payload.description,
            priority=payload.priority,
            scopes=payload.scopes,
            actor_user=actor.label,
        )
        return 200, {"request_id": request_id_var.get(), "task": task_dict(task)}

    return perform(request, session, actor, work)


@router.patch("/tasks/{task_id}")
def patch_task(
    task_id: str,
    payload: TaskPatch,
    request: Request,
    session: Session = Depends(db_session),
    actor: Actor = Depends(require_csrf),
):
    require_admin(actor)

    def work(_session: Session):
        task = update_task(
            _session,
            get_task(_session, task_id),
            version=payload.version,
            title=payload.title,
            description=payload.description,
            priority=payload.priority,
            scopes=payload.scopes,
            actor_user=actor.label,
        )
        return 200, {"request_id": request_id_var.get(), "task": task_dict(task)}

    return perform(request, session, actor, work)


@router.post("/tasks/{task_id}/confirm")
def post_confirm(task_id: str, request: Request, session: Session = Depends(db_session), actor: Actor = Depends(require_csrf)):
    require_admin(actor)

    def work(_session: Session):
        task = confirm_task(_session, get_task(_session, task_id), actor.label)
        return 200, {"request_id": request_id_var.get(), "task": task_dict(task)}

    return perform(request, session, actor, work)


@router.post("/tasks/{task_id}/block")
def post_block(
    task_id: str,
    payload: ReasonIn,
    request: Request,
    session: Session = Depends(db_session),
    actor: Actor = Depends(require_csrf),
):
    def work(_session: Session):
        task = get_task(_session, task_id)
        if actor.agent is not None:
            body_agent = request.headers.get("x-agent-id", actor.agent.id)
            ensure_same_agent(actor, body_agent)
            token = request.headers.get("x-claim-token", "")
            if task.status != "claimed":
                raise ApiError(409, "ILLEGAL_TRANSITION", "负责人只能阻塞自己认领中的任务", {"status": task.status})
            require_owner_token(task, actor.agent, token)
            block_task(_session, task, payload.reason, actor_agent_id=actor.agent.id)
        else:
            require_admin(actor)
            block_task(_session, task, payload.reason, actor_user=actor.label)
        return 200, {"request_id": request_id_var.get(), "task": task_dict(get_task(_session, task_id))}

    return perform(request, session, actor, work)


@router.post("/tasks/{task_id}/unblock")
def post_unblock(
    task_id: str,
    payload: ReasonIn,
    request: Request,
    session: Session = Depends(db_session),
    actor: Actor = Depends(require_csrf),
):
    require_admin(actor)

    def work(_session: Session):
        task = unblock_task(_session, get_task(_session, task_id), payload.reason, actor.label)
        return 200, {"request_id": request_id_var.get(), "task": task_dict(task)}

    return perform(request, session, actor, work)


@router.post("/tasks/{task_id}/cancel")
def post_cancel(
    task_id: str,
    payload: ReasonIn,
    request: Request,
    session: Session = Depends(db_session),
    actor: Actor = Depends(require_csrf),
):
    require_admin(actor)

    def work(_session: Session):
        task = cancel_task(_session, get_task(_session, task_id), payload.reason, actor.label)
        return 200, {"request_id": request_id_var.get(), "task": task_dict(task)}

    return perform(request, session, actor, work)


@router.post("/tasks/{task_id}/accept")
def post_accept(task_id: str, request: Request, session: Session = Depends(db_session), actor: Actor = Depends(require_csrf)):
    require_admin(actor)

    def work(_session: Session):
        task = accept_task(_session, get_task(_session, task_id), actor.label)
        return 200, {"request_id": request_id_var.get(), "task": task_dict(task)}

    return perform(request, session, actor, work)


@router.post("/tasks/{task_id}/reject")
def post_reject(
    task_id: str,
    payload: RejectIn,
    request: Request,
    session: Session = Depends(db_session),
    actor: Actor = Depends(require_csrf),
):
    require_admin(actor)

    def work(_session: Session):
        task = reject_task(_session, request.app.state.settings, get_task(_session, task_id), actor.label, payload.reason, payload.return_to)
        return 200, {"request_id": request_id_var.get(), "task": task_dict(task)}

    return perform(request, session, actor, work)
