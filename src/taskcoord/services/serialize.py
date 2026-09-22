from __future__ import annotations

from taskcoord.clock import isoformat
from taskcoord.models import Agent, Project, Task, TaskEvent


def project_dict(project: Project) -> dict:
    return {
        "id": project.id,
        "key": project.key,
        "name": project.name,
        "description": project.description,
        "active": project.active,
        "created_at": isoformat(project.created_at),
        "updated_at": isoformat(project.updated_at),
    }


def task_dict(task: Task) -> dict:
    return {
        "id": task.id,
        "project_key": task.project.key if task.project is not None else None,
        "project_id": task.project_id,
        "title": task.title,
        "description": task.description,
        "status": task.status,
        "priority": task.priority,
        "owner": task.owner_agent_id,
        "legacy_owner": task.legacy_owner,
        "lease_expires_at": isoformat(task.lease_expires_at),
        "claim_active": bool(task.claim_token_hash),
        "claim_reissue_pending": task.claim_reissue_pending,
        "branch_name": task.branch_name,
        "delivery_commit": task.delivery_commit,
        "delivery_tests": task.delivery_tests,
        "delivery_notes": task.delivery_notes,
        "blocked_reason": task.blocked_reason,
        "status_reason": task.status_reason,
        "accepted_by": task.accepted_by,
        "accepted_at": isoformat(task.accepted_at),
        "scopes": [row.scope for row in task.scopes],
        "version": task.version,
        "created_at": isoformat(task.created_at),
        "updated_at": isoformat(task.updated_at),
    }


def agent_dict(agent: Agent, current_task_ids: list[str] | None = None) -> dict:
    return {
        "id": agent.id,
        "display_name": agent.display_name,
        "machine_name": agent.machine_name,
        "client_type": agent.client_type,
        "enabled": agent.enabled,
        "last_seen_at": isoformat(agent.last_seen_at),
        "current_task_ids": current_task_ids or [],
    }


def event_dict(event: TaskEvent) -> dict:
    return {
        "event_id": event.event_id,
        "task_id": event.task_id,
        "event_type": event.event_type,
        "actor_agent_id": event.actor_agent_id,
        "actor_user": event.actor_user,
        "old_status": event.old_status,
        "new_status": event.new_status,
        "old_version": event.old_version,
        "new_version": event.new_version,
        "request_id": event.request_id,
        "idempotency_key": event.idempotency_key,
        "payload_json": event.payload_json,
        "server_created_at": isoformat(event.server_created_at),
    }
