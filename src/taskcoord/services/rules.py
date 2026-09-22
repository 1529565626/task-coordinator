from __future__ import annotations

import json
import re

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from taskcoord.clock import utcnow
from taskcoord.context import request_id_var
from taskcoord.errors import ApiError
from taskcoord.models import Agent, IdempotencyRecord, Project, Task, TaskScope
from taskcoord.security import hash_token, new_token, verify_token
from taskcoord.services.audit_service import append_event
from taskcoord.services.lease_service import expire_due, lease_deadline, renew_lease
from taskcoord.services.serialize import task_dict
from taskcoord.services.transitions import next_status
from taskcoord.settings import Settings

_TASK_ID = re.compile(r"^TS-\d{3,}$")
_BRANCH = re.compile(r"^[\w./-]{1,200}$")
_SHA = re.compile(r"^[0-9a-f]{40}$")


def run_idempotent(session: Session, actor_kind: str, actor_id: str, key: str | None, request_hash: str, work):
    if not key:
        raise ApiError(400, "IDEMPOTENCY_KEY_REQUIRED", "写请求必须携带 Idempotency-Key")
    existing = _find_idempotency(session, actor_kind, actor_id, key)
    if existing is not None:
        return _replay(existing, request_hash)
    try:
        status, payload = work(session)
    except ApiError as exc:
        session.rollback()
        existing = _find_idempotency(session, actor_kind, actor_id, key)
        if existing is not None:
            return _replay(existing, request_hash)
        status, payload = exc.status, {
            "request_id": request_id_var.get(),
            "error": {"code": exc.code, "message": exc.message, "details": exc.details},
        }
        _store_idempotency(session, actor_kind, actor_id, key, request_hash, status, payload)
        return status, payload
    _store_idempotency(session, actor_kind, actor_id, key, request_hash, status, payload)
    return status, payload


def _find_idempotency(session: Session, actor_kind: str, actor_id: str, key: str) -> IdempotencyRecord | None:
    return session.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.actor_kind == actor_kind,
            IdempotencyRecord.actor_id == actor_id,
            IdempotencyRecord.idempotency_key == key,
        )
    )


def _replay(existing: IdempotencyRecord, request_hash: str):
    if existing.request_hash != request_hash:
        raise ApiError(409, "IDEMPOTENCY_KEY_REUSED", "相同 Idempotency-Key 不能用于不同请求")
    return existing.status_code, json.loads(existing.response_json)


def _store_idempotency(session, actor_kind, actor_id, key, request_hash, status, payload) -> None:
    session.add(
        IdempotencyRecord(
            actor_kind=actor_kind,
            actor_id=actor_id,
            idempotency_key=key,
            request_hash=request_hash,
            status_code=status,
            response_json=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            created_at=utcnow(),
        )
    )


def get_task(session: Session, task_id: str) -> Task:
    task = session.scalar(select(Task).options(selectinload(Task.scopes), selectinload(Task.project)).where(Task.id == task_id))
    if task is None:
        raise ApiError(404, "TASK_NOT_FOUND", f"{task_id} 不存在")
    return task


def require_task_id(task_id: str) -> str:
    if not _TASK_ID.fullmatch(task_id):
        raise ApiError(422, "VALIDATION_ERROR", "任务编号必须是 TS- 加至少三位数字")
    return task_id


def require_version(task: Task, version: int) -> None:
    if version != task.version:
        raise ApiError(
            409,
            "VERSION_CONFLICT",
            "任务版本已变化",
            {"version": task.version},
        )


def require_owner_token(task: Task, agent: Agent, claim_token: str) -> None:
    if task.owner_agent_id != agent.id:
        raise ApiError(403, "NOT_OWNER", "只有当前负责人可以执行此操作", {"owner": task.owner_agent_id})
    if not verify_token(claim_token, task.claim_token_hash):
        raise ApiError(409, "CLAIM_TOKEN_STALE", "claim token 已失效")


def replace_scopes(session: Session, task: Task, scopes: list[str]) -> None:
    cleaned = _clean_scopes(scopes)
    task.scopes.clear()
    session.flush()
    for scope in cleaned:
        session.add(TaskScope(task_id=task.id, scope=scope))


def _clean_scopes(scopes: list[str]) -> list[str]:
    cleaned: list[str] = []
    for scope in scopes:
        value = scope.strip()
        if not value:
            continue
        if value not in cleaned:
            cleaned.append(value)
    if not cleaned:
        raise ApiError(422, "VALIDATION_ERROR", "至少需要一个 scope")
    return cleaned


def scope_conflicts(session: Session, task: Task, scopes: list[str] | None = None) -> list[Task]:
    wanted = scopes if scopes is not None else [row.scope for row in task.scopes]
    if not wanted:
        return []
    rows = session.scalars(
        select(Task)
        .join(TaskScope)
        .options(selectinload(Task.scopes))
        .where(
            Task.project_id == task.project_id,
            Task.status.in_(("claimed", "review")),
            Task.id != task.id,
            TaskScope.scope.in_(wanted),
        )
    ).unique().all()
    return list(rows)


def _conflict_details(rows: list[Task]) -> dict:
    return {
        "conflicts": [
            {
                "id": row.id,
                "owner": row.owner_agent_id,
                "legacy_owner": row.legacy_owner,
                "status": row.status,
                "scopes": [item.scope for item in row.scopes],
                "lease_expires_at": isoformat_lease(row),
            }
            for row in rows
        ]
    }


def isoformat_lease(task: Task) -> str | None:
    from taskcoord.clock import isoformat

    return isoformat(task.lease_expires_at)


def ensure_continue_from(session: Session, task: Task, agent: Agent, continue_from: list[str]) -> None:
    conflicts = scope_conflicts(session, task)
    conflict_ids = {row.id for row in conflicts}
    requested = list(dict.fromkeys(continue_from))
    requested_set = set(requested)
    if conflict_ids - requested_set:
        raise ApiError(409, "SCOPE_CONFLICT", "任务范围与已占用任务冲突", _conflict_details(conflicts))
    for task_id in requested:
        previous = get_task(session, task_id)
        if previous.project_id != task.project_id or previous.owner_agent_id != agent.id or previous.status != "review":
            raise ApiError(
                409,
                "CONTINUE_FROM_INVALID",
                "continue_from 只允许同一项目、同一负责人、处于 review 的前序任务",
                {"task_id": task_id},
            )
        if task_id not in conflict_ids:
            raise ApiError(409, "CONTINUE_FROM_INVALID", "continue_from 包含不冲突的任务", {"task_id": task_id})


def apply_claim(session: Session, settings: Settings, task: Task, agent: Agent, branch_name: str, continue_from: list[str]) -> str:
    if not agent.enabled:
        raise ApiError(403, "AGENT_DISABLED", "Agent 已停用")
    if not _BRANCH.fullmatch(branch_name or ""):
        raise ApiError(422, "VALIDATION_ERROR", "分支名不合法")
    now = utcnow()
    expire_due(session, now)
    project = session.get(Project, task.project_id)
    if project is None or not project.active:
        raise ApiError(409, "PROJECT_INACTIVE", "项目未启用，不能认领")
    if task.status == "claimed" and task.owner_agent_id:
        raise ApiError(
            409,
            "TASK_ALREADY_CLAIMED",
            f"{task.id} 已被 {task.owner_agent_id} 认领",
            {"owner": task.owner_agent_id, "lease_expires_at": isoformat_lease(task)},
        )
    if task.status != "ready":
        raise ApiError(409, "ILLEGAL_TRANSITION", "只有 ready 任务可以认领", {"status": task.status})
    ensure_continue_from(session, task, agent, continue_from)
    try:
        new_status = next_status(task.status, "claim")
    except ValueError as exc:
        raise ApiError(409, "ILLEGAL_TRANSITION", "当前状态不能认领", {"status": task.status}) from exc
    token = new_token()
    old_status = task.status
    old_version = task.version
    task.status = new_status
    task.owner_agent_id = agent.id
    task.claim_token_hash = hash_token(token)
    task.claim_reissue_pending = False
    task.lease_expires_at = lease_deadline(settings, now)
    task.branch_name = branch_name
    task.version += 1
    task.updated_at = now
    task.status_reason = None
    append_event(
        session,
        event_type="task.claimed",
        task_id=task.id,
        old_status=old_status,
        new_status=task.status,
        old_version=old_version,
        new_version=task.version,
        actor_agent_id=agent.id,
        payload={"branch_name": branch_name},
    )
    if continue_from:
        append_event(
            session,
            event_type="task.continued_from",
            task_id=task.id,
            old_status=old_status,
            new_status=task.status,
            old_version=old_version,
            new_version=task.version,
            actor_agent_id=agent.id,
            payload={"continue_from": continue_from},
        )
    return token


def touch_agent(agent: Agent) -> None:
    agent.last_seen_at = utcnow()
