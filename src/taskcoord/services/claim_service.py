from __future__ import annotations

from sqlalchemy.orm import Session

from taskcoord.clock import utcnow
from taskcoord.errors import ApiError
from taskcoord.models import Agent, Task
from taskcoord.security import hash_token, new_token
from taskcoord.services.audit_service import append_event
from taskcoord.services.lease_service import expire_due, renew_lease
from taskcoord.services.rules import apply_claim, require_owner_token
from taskcoord.services.transitions import next_status
from taskcoord.settings import Settings

_SHA_LEN = 40


def claim(session: Session, settings: Settings, task: Task, agent: Agent, branch_name: str, continue_from: list[str]) -> str:
    agent.last_seen_at = utcnow()
    return apply_claim(session, settings, task, agent, branch_name, continue_from)


def heartbeat(session: Session, settings: Settings, task: Task, agent: Agent, claim_token: str) -> None:
    now = utcnow()
    expire_due(session, now)
    if task.status != "claimed":
        raise ApiError(409, "ILLEGAL_TRANSITION", "任务不在认领中", {"status": task.status})
    require_owner_token(task, agent, claim_token)
    old_version = task.version
    renew_lease(task, settings, now)
    agent.last_seen_at = now
    append_event(
        session,
        event_type="task.heartbeat",
        task_id=task.id,
        old_status="claimed",
        new_status="claimed",
        old_version=old_version,
        new_version=task.version,
        actor_agent_id=agent.id,
        payload={"lease_expires_at": task.lease_expires_at.isoformat()},
    )


def renew_from_detail(session: Session, settings: Settings, task: Task, agent: Agent, claim_token: str) -> bool:
    if task.status != "claimed" or task.owner_agent_id != agent.id:
        return False
    if not claim_token:
        return False
    require_owner_token(task, agent, claim_token)
    old_version = task.version
    now = utcnow()
    renew_lease(task, settings, now)
    append_event(
        session,
        event_type="task.lease_renewed",
        task_id=task.id,
        old_status="claimed",
        new_status="claimed",
        old_version=old_version,
        new_version=task.version,
        actor_agent_id=agent.id,
        payload={"source": "detail"},
    )
    return True


def release(session: Session, task: Task, agent: Agent, claim_token: str, reason: str) -> None:
    _reason(reason)
    if task.status != "claimed":
        raise ApiError(409, "ILLEGAL_TRANSITION", "只有认领中的任务可以释放", {"status": task.status})
    require_owner_token(task, agent, claim_token)
    _move(session, task, "release", actor_agent_id=agent.id, reason=reason, event_type="task.released")
    _clear_claim(task)


def deliver(
    session: Session,
    task: Task,
    agent: Agent,
    claim_token: str,
    branch_name: str,
    commit: str,
    tests: str,
    notes: str | None,
) -> None:
    if task.status != "claimed":
        raise ApiError(409, "ILLEGAL_TRANSITION", "只有认领中的任务可以交付", {"status": task.status})
    require_owner_token(task, agent, claim_token)
    if not branch_name.strip():
        raise ApiError(422, "VALIDATION_ERROR", "必须登记分支名")
    normalized = commit.strip().lower()
    if len(normalized) != _SHA_LEN or any(ch not in "0123456789abcdef" for ch in normalized):
        raise ApiError(422, "VALIDATION_ERROR", "commit 必须是 40 位 SHA")
    if not tests.strip():
        raise ApiError(422, "VALIDATION_ERROR", "必须登记实际测试结果")
    _move(session, task, "deliver", actor_agent_id=agent.id, reason=notes, event_type="task.delivered", payload={
        "branch_name": branch_name.strip(),
        "commit": normalized,
        "tests": tests.strip(),
        "notes": (notes or "").strip(),
    })
    task.branch_name = branch_name.strip()
    task.delivery_commit = normalized
    task.delivery_tests = tests.strip()
    task.delivery_notes = (notes or "").strip() or None
    _clear_claim(task, keep_owner=True)


def takeover(session: Session, settings: Settings, task: Task, new_owner: Agent, actor_user: str, reason: str) -> None:
    _reason(reason)
    if not new_owner.enabled:
        raise ApiError(403, "AGENT_DISABLED", "目标 Agent 已停用")
    old_owner = task.owner_agent_id
    try:
        next_status(task.status, "takeover")
    except ValueError as exc:
        raise ApiError(409, "ILLEGAL_TRANSITION", "当前状态不能接管", {"status": task.status}) from exc
    _move(
        session,
        task,
        "takeover",
        actor_user=actor_user,
        reason=reason,
        event_type="task.taken_over",
        payload={"previous_owner": old_owner, "new_owner": new_owner.id},
    )
    now = utcnow()
    task.owner_agent_id = new_owner.id
    task.claim_token_hash = hash_token(new_token())
    task.claim_reissue_pending = True
    task.lease_expires_at = now + _minutes(settings)
    task.legacy_owner = None


def reissue(session: Session, task: Task, agent: Agent) -> str:
    if task.status != "claimed" or task.owner_agent_id != agent.id:
        raise ApiError(403, "NOT_OWNER", "只有当前负责人可以重新领取 claim token")
    if not task.claim_reissue_pending:
        raise ApiError(409, "ILLEGAL_TRANSITION", "当前任务没有待重新发放的 claim token")
    token = new_token()
    old_version = task.version
    task.claim_token_hash = hash_token(token)
    task.claim_reissue_pending = False
    task.version += 1
    task.updated_at = utcnow()
    append_event(
        session,
        event_type="task.claim_token_reissued",
        task_id=task.id,
        old_status=task.status,
        new_status=task.status,
        old_version=old_version,
        new_version=task.version,
        actor_agent_id=agent.id,
    )
    return token


def _move(session, task, action, *, actor_agent_id=None, actor_user=None, reason=None, event_type, payload=None):
    try:
        new_status = next_status(task.status, action)
    except ValueError as exc:
        raise ApiError(409, "ILLEGAL_TRANSITION", "非法状态转换", {"status": task.status, "action": action}) from exc
    old_status = task.status
    old_version = task.version
    task.status = new_status
    task.version += 1
    task.updated_at = utcnow()
    if reason:
        task.status_reason = reason
    append_event(
        session,
        event_type=event_type,
        task_id=task.id,
        old_status=old_status,
        new_status=task.status,
        old_version=old_version,
        new_version=task.version,
        actor_agent_id=actor_agent_id,
        actor_user=actor_user,
        payload=payload or ({"reason": reason} if reason else {}),
    )


def _clear_claim(task: Task, keep_owner: bool = False) -> None:
    task.claim_token_hash = None
    task.claim_reissue_pending = False
    task.lease_expires_at = None
    if not keep_owner:
        task.owner_agent_id = None


def _reason(reason: str) -> None:
    if not reason or not reason.strip():
        raise ApiError(422, "VALIDATION_ERROR", "必须填写原因")


def _minutes(settings: Settings):
    from datetime import timedelta

    return timedelta(minutes=settings.lease_duration_minutes)
