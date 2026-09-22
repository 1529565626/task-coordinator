from __future__ import annotations

import hashlib
import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from taskcoord.clock import utcnow
from taskcoord.errors import ApiError
from taskcoord.models import Agent, ImportBatch, Project, Task, TaskScope
from taskcoord.security import hash_token, new_token
from taskcoord.services.audit_service import append_event
from taskcoord.services.lease_service import lease_deadline
from taskcoord.services.transitions import STATUSES
from taskcoord.settings import Settings

_RESOLUTIONS = {"restore", "ready", "review"}


def parse_tasks_json(raw: bytes) -> list[dict]:
    try:
        text = raw.decode("utf-8-sig")
        document = json.loads(text)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ApiError(422, "IMPORT_INVALID", "tasks.json 不是合法的 UTF-8 JSON") from exc
    tasks = document.get("tasks") if isinstance(document, dict) else document
    if not isinstance(tasks, list):
        raise ApiError(422, "IMPORT_INVALID", "找不到 tasks 数组")
    normalized = []
    seen: set[str] = set()
    for item in tasks:
        if not isinstance(item, dict) or not item.get("id"):
            raise ApiError(422, "IMPORT_INVALID", "存在缺少 id 的任务")
        task_id = str(item["id"])
        if task_id in seen:
            raise ApiError(422, "IMPORT_INVALID", "源文件存在重复任务编号", {"task_id": task_id})
        seen.add(task_id)
        scopes = item.get("scope") or []
        if not isinstance(scopes, list) or not all(isinstance(scope, str) and scope.strip() for scope in scopes):
            raise ApiError(422, "IMPORT_INVALID", "scope 必须是非空字符串数组", {"task_id": task_id})
        delivery = item.get("delivery") or {}
        if delivery and not isinstance(delivery, dict):
            raise ApiError(422, "IMPORT_INVALID", "delivery 必须是对象", {"task_id": task_id})
        normalized.append(
            {
                "id": task_id,
                "project_key": str(item.get("project_id") or ""),
                "title": str(item.get("title") or ""),
                "description": str(item.get("description") or ""),
                "status": str(item.get("status") or ""),
                "owner": item.get("owner"),
                "scopes": list(dict.fromkeys(scope.strip() for scope in scopes)),
                "delivery_commit": (delivery.get("commit") if isinstance(delivery, dict) else None) or None,
                "delivery_tests": (delivery.get("tests") if isinstance(delivery, dict) else None) or None,
                "updated_at": item.get("updated_at"),
                "continues_from": item.get("continues_from") or [],
            }
        )
    return normalized


def import_id_for(raw: bytes) -> str:
    return "imp_" + hashlib.sha256(raw).hexdigest()[:32]


def preview_import(session: Session, raw: bytes) -> dict:
    import_id = import_id_for(raw)
    existing = session.get(ImportBatch, import_id)
    if existing is not None:
        report = json.loads(existing.report_json)
        report["import_id"] = import_id
        report["idempotent"] = True
        return report
    tasks = parse_tasks_json(raw)
    report = build_report(tasks)
    report["import_id"] = import_id
    report["idempotent"] = False
    session.add(
        ImportBatch(
            id=import_id,
            source_sha256=hashlib.sha256(raw).hexdigest(),
            report_json=json.dumps(report, ensure_ascii=False),
            payload_json=json.dumps(tasks, ensure_ascii=False),
            created_at=utcnow(),
        )
    )
    session.flush()
    return report


def commit_import(
    session: Session,
    settings: Settings,
    import_id: str,
    *,
    owner_map: dict[str, str],
    claimed_resolutions: dict[str, str],
    actor_user: str,
) -> dict:
    batch = session.get(ImportBatch, import_id)
    if batch is None:
        raise ApiError(404, "IMPORT_NOT_FOUND", "请先预检，再使用同一个 import id 提交")
    if batch.committed_result_json:
        stored = json.loads(batch.committed_result_json)
        stored["idempotent"] = True
        return stored
    tasks = json.loads(batch.payload_json)
    bad_status = [task["id"] for task in tasks if task["status"] not in STATUSES]
    if bad_status:
        raise ApiError(422, "IMPORT_INVALID", "存在无法识别的状态", {"task_ids": bad_status})
    claimed_ids = [task["id"] for task in tasks if task["status"] == "claimed"]
    missing = [task_id for task_id in claimed_ids if claimed_resolutions.get(task_id) not in _RESOLUTIONS]
    if missing:
        raise ApiError(
            422,
            "IMPORT_DECISIONS_REQUIRED",
            "claimed 任务必须逐项选择 restore、ready 或 review",
            {"task_ids": missing},
        )
    for task_id, decision in claimed_resolutions.items():
        if decision not in _RESOLUTIONS:
            raise ApiError(422, "VALIDATION_ERROR", "无法识别的 claimed 处理方式", {"task_id": task_id})
    existing_ids = [task["id"] for task in tasks if session.get(Task, task["id"]) is not None]
    if existing_ids:
        raise ApiError(409, "IMPORT_CONFLICT", "数据库里已有相同任务编号", {"task_ids": existing_ids})

    imported = 0
    for item in tasks:
        project = session.scalar(select(Project).where(Project.key == item["project_key"]))
        if project is None:
            if not item["project_key"]:
                raise ApiError(422, "IMPORT_INVALID", "任务缺少 project_id", {"task_id": item["id"]})
            project = Project(
                key=item["project_key"],
                name=item["project_key"],
                description="Imported from tasks.json",
                active=True,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
            session.add(project)
            session.flush()
        status, owner_id, reissue, lease = _resolve_row(session, settings, item, owner_map, claimed_resolutions)
        if item["status"] == "claimed" and claimed_resolutions[item["id"]] == "restore" and owner_id is None:
            raise ApiError(
                422,
                "UNKNOWN_OWNER",
                "恢复认领前必须把 owner 映射到已存在的 agent",
                {"task_id": item["id"], "owner": item.get("owner")},
            )
        stamp = _parse_time(item.get("updated_at"))
        task = Task(
            id=item["id"],
            project_id=project.id,
            title=item["title"] or item["id"],
            description=item["description"],
            status=status,
            priority=100,
            owner_agent_id=owner_id,
            legacy_owner=item.get("owner"),
            lease_expires_at=lease,
            claim_token_hash=hash_token(new_token()) if reissue else None,
            claim_reissue_pending=reissue,
            delivery_commit=item.get("delivery_commit"),
            delivery_tests=item.get("delivery_tests"),
            version=1,
            created_at=stamp,
            updated_at=stamp,
        )
        session.add(task)
        session.flush()
        for scope in item["scopes"]:
            session.add(TaskScope(task_id=task.id, scope=scope))
        append_event(
            session,
            event_type="task.imported",
            task_id=task.id,
            old_status=None,
            new_status=status,
            old_version=None,
            new_version=1,
            actor_user=actor_user,
            payload={
                "source_status": item["status"],
                "legacy_owner": item.get("owner"),
                "continues_from": item.get("continues_from") or [],
                "import_id": import_id,
            },
        )
        imported += 1
    result = {
        "import_id": import_id,
        "imported": imported,
        "idempotent": False,
        "claimed_resolutions": claimed_resolutions,
    }
    batch.committed_result_json = json.dumps(result, ensure_ascii=False)
    session.flush()
    return result


def build_report(tasks: list[dict]) -> dict:
    by_status: dict[str, int] = {}
    owners: dict[str, int] = {}
    projects: dict[str, int] = {}
    unknown_status = []
    for task in tasks:
        by_status[task["status"]] = by_status.get(task["status"], 0) + 1
        if task["status"] not in STATUSES:
            unknown_status.append(task["id"])
        if task.get("owner"):
            owners[task["owner"]] = owners.get(task["owner"], 0) + 1
        projects[task["project_key"]] = projects.get(task["project_key"], 0) + 1
    conflicts = []
    active = [task for task in tasks if task["status"] in {"claimed", "review"}]
    for index, left in enumerate(active):
        left_scopes = set(left["scopes"])
        for right in active[index + 1 :]:
            if left["project_key"] != right["project_key"]:
                continue
            overlap = sorted(left_scopes & set(right["scopes"]))
            if overlap:
                conflicts.append({"tasks": [left["id"], right["id"]], "scopes": overlap, "project_key": left["project_key"]})
    return {
        "task_count": len(tasks),
        "by_status": by_status,
        "projects": projects,
        "owners": owners,
        "unknown_status": unknown_status,
        "claimed_pending": [task["id"] for task in tasks if task["status"] == "claimed"],
        "unknown_owners": sorted(owners),
        "scope_conflicts": conflicts,
        "delivery_count": sum(1 for task in tasks if task.get("delivery_commit") or task.get("delivery_tests")),
    }


def _resolve_row(session: Session, settings: Settings, item: dict, owner_map: dict[str, str], resolutions: dict[str, str]):
    source_status = item["status"]
    decision = resolutions.get(item["id"])
    status = source_status
    if source_status == "claimed":
        status = "claimed" if decision == "restore" else decision
    owner_name = owner_map.get(item.get("owner") or "", item.get("owner"))
    owner = session.get(Agent, owner_name) if owner_name else None
    owner_id = owner.id if owner is not None else None
    reissue = status == "claimed"
    lease = lease_deadline(settings, utcnow()) if reissue else None
    if status not in {"claimed", "review", "done"}:
        owner_id = None
    return status, owner_id, reissue, lease


def _parse_time(value: str | None) -> datetime:
    if not value:
        return utcnow()
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError:
        return utcnow()
    if stamp.tzinfo is None:
        from datetime import timezone

        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp
