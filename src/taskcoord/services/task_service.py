from __future__ import annotations

import re

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from taskcoord.clock import utcnow
from taskcoord.errors import ApiError
from taskcoord.models import Agent, Project, Task, TaskEvent, TaskScope
from taskcoord.services.audit_service import append_event
from taskcoord.services.claim_service import _clear_claim, _move, _reason
from taskcoord.services.lease_service import lease_deadline
from taskcoord.services.rules import get_task, replace_scopes, require_task_id, require_version, scope_conflicts
from taskcoord.services.transitions import next_status
from taskcoord.settings import Settings

_PROJECT_KEY = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def create_project(session: Session, key: str, name: str, description: str, active: bool = True) -> Project:
    if not _PROJECT_KEY.fullmatch(key):
        raise ApiError(422, "VALIDATION_ERROR", "project key 只能包含小写字母、数字、下划线和连字符")
    if session.scalar(select(Project).where(Project.key == key)):
        raise ApiError(409, "PROJECT_EXISTS", "项目已存在")
    now = utcnow()
    project = Project(key=key, name=name.strip(), description=description or "", active=active, created_at=now, updated_at=now)
    if not project.name:
        raise ApiError(422, "VALIDATION_ERROR", "项目名称不能为空")
    session.add(project)
    session.flush()
    return project


def update_project(session: Session, project: Project, *, name: str | None, description: str | None, active: bool | None) -> Project:
    if name is not None:
        if not name.strip():
            raise ApiError(422, "VALIDATION_ERROR", "项目名称不能为空")
        project.name = name.strip()
    if description is not None:
        project.description = description
    if active is not None:
        project.active = active
    project.updated_at = utcnow()
    return project


def get_project(session: Session, key: str) -> Project:
    project = session.scalar(select(Project).where(Project.key == key))
    if project is None:
        raise ApiError(404, "PROJECT_NOT_FOUND", "项目不存在")
    return project


def list_projects(session: Session) -> list[Project]:
    return list(session.scalars(select(Project).order_by(Project.key)).all())


def create_task(
    session: Session,
    *,
    task_id: str,
    project: Project,
    title: str,
    description: str,
    priority: int,
    scopes: list[str],
    actor_user: str,
) -> Task:
    require_task_id(task_id)
    if session.get(Task, task_id):
        raise ApiError(409, "TASK_EXISTS", "任务编号已存在")
    if priority < 0:
        raise ApiError(422, "VALIDATION_ERROR", "优先级不能为负数")
    if not title.strip():
        raise ApiError(422, "VALIDATION_ERROR", "标题不能为空")
    now = utcnow()
    task = Task(
        id=task_id,
        project_id=project.id,
        title=title.strip(),
        description=description or "",
        status="pending_confirmation",
        priority=priority,
        version=1,
        created_at=now,
        updated_at=now,
    )
    session.add(task)
    session.flush()
    replace_scopes(session, task, scopes)
    append_event(
        session,
        event_type="task.created",
        task_id=task.id,
        old_status=None,
        new_status=task.status,
        old_version=None,
        new_version=1,
        actor_user=actor_user,
        payload={"project_key": project.key},
    )
    session.flush()
    return get_task(session, task.id)


def update_task(
    session: Session,
    task: Task,
    *,
    version: int,
    title: str | None,
    description: str | None,
    priority: int | None,
    scopes: list[str] | None,
    actor_user: str,
) -> Task:
    if task.status in {"done", "cancelled"}:
        raise ApiError(409, "ILLEGAL_TRANSITION", "已结束的任务不能修改", {"status": task.status})
    require_version(task, version)
    if title is not None:
        if not title.strip():
            raise ApiError(422, "VALIDATION_ERROR", "标题不能为空")
        task.title = title.strip()
    if description is not None:
        task.description = description
    if priority is not None:
        if priority < 0:
            raise ApiError(422, "VALIDATION_ERROR", "优先级不能为负数")
        task.priority = priority
    if scopes is not None:
        if task.status in {"claimed", "review"}:
            conflicts = scope_conflicts(session, task, scopes)
            if conflicts:
                from taskcoord.services.rules import _conflict_details

                raise ApiError(409, "SCOPE_CONFLICT", "修改后的范围与已占用任务冲突", _conflict_details(conflicts))
        replace_scopes(session, task, scopes)
    old_version = task.version
    task.version += 1
    task.updated_at = utcnow()
    append_event(
        session,
        event_type="task.updated",
        task_id=task.id,
        old_status=task.status,
        new_status=task.status,
        old_version=old_version,
        new_version=task.version,
        actor_user=actor_user,
    )
    return task


def confirm_task(session: Session, task: Task, actor_user: str) -> Task:
    _move(session, task, "confirm", actor_user=actor_user, event_type="task.confirmed")
    return task


def block_task(session: Session, task: Task, reason: str, *, actor_user: str | None = None, actor_agent_id: str | None = None) -> Task:
    _reason(reason)
    _move(
        session,
        task,
        "block",
        actor_user=actor_user,
        actor_agent_id=actor_agent_id,
        reason=reason.strip(),
        event_type="task.blocked",
    )
    task.blocked_reason = reason.strip()
    _clear_claim(task, keep_owner=True)
    return task


def unblock_task(session: Session, task: Task, reason: str, actor_user: str) -> Task:
    _reason(reason)
    _move(session, task, "unblock", actor_user=actor_user, reason=reason.strip(), event_type="task.unblocked")
    task.blocked_reason = None
    return task


def cancel_task(session: Session, task: Task, reason: str, actor_user: str) -> Task:
    _reason(reason)
    _move(session, task, "cancel", actor_user=actor_user, reason=reason.strip(), event_type="task.cancelled")
    _clear_claim(task, keep_owner=True)
    return task


def accept_task(session: Session, task: Task, actor_user: str) -> Task:
    if task.status != "review":
        raise ApiError(409, "ILLEGAL_TRANSITION", "只有待验收任务可以通过", {"status": task.status})
    _move(session, task, "accept", actor_user=actor_user, event_type="task.accepted")
    task.accepted_by = actor_user
    task.accepted_at = utcnow()
    _clear_claim(task, keep_owner=True)
    return task


def reject_task(session: Session, settings: Settings, task: Task, actor_user: str, reason: str, return_to: str) -> Task:
    _reason(reason)
    if return_to not in {"owner", "ready"}:
        raise ApiError(422, "VALIDATION_ERROR", "return_to 只能是 owner 或 ready")
    action = "reject_owner" if return_to == "owner" else "reject_ready"
    if return_to == "owner" and not task.owner_agent_id:
        raise ApiError(409, "ILLEGAL_TRANSITION", "任务没有可退回的负责人")
    _move(
        session,
        task,
        action,
        actor_user=actor_user,
        reason=reason.strip(),
        event_type="task.rejected",
        payload={"return_to": return_to, "reason": reason.strip()},
    )
    if return_to == "ready":
        _clear_claim(task, keep_owner=False)
    else:
        from taskcoord.security import hash_token, new_token

        task.claim_token_hash = hash_token(new_token())
        task.claim_reissue_pending = True
        task.lease_expires_at = lease_deadline(settings, utcnow()) if settings.lease_enabled else None
    return task


def list_tasks(
    session: Session,
    *,
    project_key: str | None,
    status: str | None,
    owner: str | None,
    scope: str | None,
    query: str | None,
    limit: int,
    offset: int,
) -> list[Task]:
    stmt = select(Task).options(selectinload(Task.scopes), selectinload(Task.project)).order_by(Task.priority, Task.created_at, Task.id)
    if project_key:
        stmt = stmt.join(Project).where(Project.key == project_key)
    if status:
        stmt = stmt.where(Task.status == status)
    if owner:
        stmt = stmt.where(Task.owner_agent_id == owner)
    if scope:
        stmt = stmt.join(TaskScope).where(TaskScope.scope == scope)
    if query:
        like = f"%{query}%"
        stmt = stmt.where(or_(Task.id.like(like), Task.title.like(like), Task.description.like(like)))
    stmt = stmt.limit(min(max(limit, 1), 1000)).offset(max(offset, 0))
    return list(session.scalars(stmt).unique().all())


def next_task(session: Session, project_key: str) -> Task | None:
    project = get_project(session, project_key)
    if not project.active:
        raise ApiError(409, "PROJECT_INACTIVE", "项目未启用")
    rows = list_tasks(session, project_key=project_key, status="ready", owner=None, scope=None, query=None, limit=1000, offset=0)
    for task in rows:
        if not scope_conflicts(session, task):
            return task
    return None


def list_events(session: Session, *, task_id: str | None, agent_id: str | None, limit: int, offset: int) -> list[TaskEvent]:
    stmt = select(TaskEvent).order_by(TaskEvent.server_created_at.desc(), TaskEvent.event_id.desc())
    if task_id:
        stmt = stmt.where(TaskEvent.task_id == task_id)
    if agent_id:
        stmt = stmt.where(TaskEvent.actor_agent_id == agent_id)
    stmt = stmt.limit(min(max(limit, 1), 1000)).offset(max(offset, 0))
    return list(session.scalars(stmt).all())


def create_agent(session: Session, *, agent_id: str, display_name: str, machine_name: str, client_type: str) -> tuple[Agent, str]:
    from taskcoord.security import new_token, hash_token

    if not re.fullmatch(r"^[a-z0-9][a-z0-9-]{1,78}$", agent_id) or agent_id in {"codex", "cursor", "agent"}:
        raise ApiError(422, "VALIDATION_ERROR", "agent id 需要稳定的小写短名，并区分客户端和机器")
    if client_type not in {"codex", "cursor", "other"}:
        raise ApiError(422, "VALIDATION_ERROR", "client_type 只能是 codex、cursor 或 other")
    if session.get(Agent, agent_id):
        raise ApiError(409, "AGENT_EXISTS", "Agent 已存在，不能复用名称")
    token = new_token()
    agent = Agent(
        id=agent_id,
        display_name=display_name.strip() or agent_id,
        machine_name=machine_name.strip(),
        client_type=client_type,
        enabled=True,
        token_hash=hash_token(token),
        created_at=utcnow(),
    )
    session.add(agent)
    session.flush()
    return agent, token


def rotate_agent_token(session: Session, agent: Agent) -> str:
    from taskcoord.security import hash_token, new_token

    token = new_token()
    agent.token_hash = hash_token(token)
    return token


def list_agents(session: Session) -> list[tuple[Agent, list[str]]]:
    agents = list(session.scalars(select(Agent).order_by(Agent.id)).all())
    tasks = session.scalars(select(Task).where(Task.status.in_(("claimed", "review")), Task.owner_agent_id.is_not(None))).all()
    grouped: dict[str, list[str]] = {}
    for task in tasks:
        grouped.setdefault(task.owner_agent_id, []).append(task.id)
    return [(agent, grouped.get(agent.id, [])) for agent in agents]


def assert_known_action(status: str, action: str) -> None:
    try:
        next_status(status, action)
    except ValueError as exc:
        raise ApiError(409, "ILLEGAL_TRANSITION", "非法状态转换", {"status": status, "action": action}) from exc
