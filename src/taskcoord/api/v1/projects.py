from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from taskcoord.api.deps import db_session, ok, perform, require_admin, require_csrf, require_reader
from taskcoord.auth import Actor
from taskcoord.context import request_id_var
from taskcoord.schemas import ProjectIn, ProjectPatch
from taskcoord.services.serialize import project_dict
from taskcoord.services.task_service import create_project, get_project, list_projects, update_project

router = APIRouter()


@router.get("/projects")
def get_projects(session: Session = Depends(db_session), _actor: Actor = Depends(require_reader)) -> dict:
    return ok(projects=[project_dict(project) for project in list_projects(session)])


@router.post("/projects")
def post_project(
    payload: ProjectIn,
    request: Request,
    session: Session = Depends(db_session),
    actor: Actor = Depends(require_csrf),
):
    require_admin(actor)

    def work(_session: Session):
        project = create_project(_session, payload.key, payload.name, payload.description, payload.active)
        return 200, {"request_id": request_id_var.get(), "project": project_dict(project)}

    return perform(request, session, actor, work)


@router.get("/projects/{project_key}")
def get_one(project_key: str, session: Session = Depends(db_session), _actor: Actor = Depends(require_reader)) -> dict:
    return ok(project=project_dict(get_project(session, project_key)))


@router.patch("/projects/{project_key}")
def patch_project(
    project_key: str,
    payload: ProjectPatch,
    request: Request,
    session: Session = Depends(db_session),
    actor: Actor = Depends(require_csrf),
):
    require_admin(actor)

    def work(_session: Session):
        project = update_project(
            _session,
            get_project(_session, project_key),
            name=payload.name,
            description=payload.description,
            active=payload.active,
        )
        return 200, {"request_id": request_id_var.get(), "project": project_dict(project)}

    return perform(request, session, actor, work)
