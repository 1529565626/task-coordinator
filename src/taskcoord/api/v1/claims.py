from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from taskcoord.api.deps import db_session, ensure_same_agent, perform, require_admin, require_agent, require_csrf
from taskcoord.auth import Actor
from taskcoord.context import request_id_var
from taskcoord.errors import ApiError
from taskcoord.models import Agent
from taskcoord.schemas import ClaimIn, DeliverIn, TakeoverIn, TokenIn
from taskcoord.services import claim_service
from taskcoord.services.rules import get_task
from taskcoord.services.serialize import task_dict
from taskcoord.services.task_service import _reason

router = APIRouter()


def _agent(session: Session, actor: Actor) -> Agent:
    assert actor.agent is not None
    return actor.agent


@router.post("/tasks/{task_id}/claim")
def post_claim(
    task_id: str,
    payload: ClaimIn,
    request: Request,
    session: Session = Depends(db_session),
    actor: Actor = Depends(require_agent),
):
    ensure_same_agent(actor, payload.agent_id)

    def work(_session: Session):
        token = claim_service.claim(
            _session,
            request.app.state.settings,
            get_task(_session, task_id),
            _agent(_session, actor),
            payload.branch_name,
            payload.continue_from,
        )
        return 200, {"request_id": request_id_var.get(), "task": task_dict(get_task(_session, task_id)), "claim_token": token}

    return perform(request, session, actor, work)


@router.post("/tasks/{task_id}/heartbeat")
def post_heartbeat(
    task_id: str,
    payload: TokenIn,
    request: Request,
    session: Session = Depends(db_session),
    actor: Actor = Depends(require_agent),
):
    ensure_same_agent(actor, payload.agent_id)

    def work(_session: Session):
        task = get_task(_session, task_id)
        claim_service.heartbeat(_session, request.app.state.settings, task, _agent(_session, actor), payload.claim_token)
        return 200, {"request_id": request_id_var.get(), "task": task_dict(get_task(_session, task_id))}

    return perform(request, session, actor, work)


@router.post("/tasks/{task_id}/release")
def post_release(
    task_id: str,
    payload: TokenIn,
    request: Request,
    session: Session = Depends(db_session),
    actor: Actor = Depends(require_csrf),
):
    def work(_session: Session):
        task = get_task(_session, task_id)
        if actor.agent is not None:
            ensure_same_agent(actor, payload.agent_id)
            claim_service.release(_session, task, actor.agent, payload.claim_token, payload.reason)
        else:
            require_admin(actor)
            if task.status != "claimed":
                raise ApiError(409, "ILLEGAL_TRANSITION", "只有认领中的任务可以释放", {"status": task.status})
            _reason(payload.reason)
            from taskcoord.services.claim_service import _clear_claim, _move

            _move(_session, task, "release", actor_user=actor.label, reason=payload.reason, event_type="task.released")
            _clear_claim(task)
        return 200, {"request_id": request_id_var.get(), "task": task_dict(get_task(_session, task_id))}

    return perform(request, session, actor, work)


@router.post("/tasks/{task_id}/deliver")
def post_deliver(
    task_id: str,
    payload: DeliverIn,
    request: Request,
    session: Session = Depends(db_session),
    actor: Actor = Depends(require_agent),
):
    ensure_same_agent(actor, payload.agent_id)

    def work(_session: Session):
        claim_service.deliver(
            _session,
            get_task(_session, task_id),
            _agent(_session, actor),
            payload.claim_token,
            payload.branch_name,
            payload.commit,
            payload.tests,
            payload.notes,
        )
        return 200, {"request_id": request_id_var.get(), "task": task_dict(get_task(_session, task_id))}

    return perform(request, session, actor, work)


@router.post("/tasks/{task_id}/takeover")
def post_takeover(
    task_id: str,
    payload: TakeoverIn,
    request: Request,
    session: Session = Depends(db_session),
    actor: Actor = Depends(require_csrf),
):
    require_admin(actor)

    def work(_session: Session):
        owner = _session.get(Agent, payload.agent_id)
        if owner is None:
            raise ApiError(404, "AGENT_NOT_FOUND", "目标 Agent 不存在")
        claim_service.takeover(_session, request.app.state.settings, get_task(_session, task_id), owner, actor.label, payload.reason)
        return 200, {"request_id": request_id_var.get(), "task": task_dict(get_task(_session, task_id))}

    return perform(request, session, actor, work)


@router.post("/tasks/{task_id}/reissue-claim-token")
def post_reissue(task_id: str, request: Request, session: Session = Depends(db_session), actor: Actor = Depends(require_agent)):
    def work(_session: Session):
        token = claim_service.reissue(_session, get_task(_session, task_id), _agent(_session, actor))
        return 200, {
            "request_id": request_id_var.get(),
            "task": task_dict(get_task(_session, task_id)),
            "claim_token": token,
        }

    return perform(request, session, actor, work)
