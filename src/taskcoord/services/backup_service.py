from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from taskcoord.clock import isoformat, utcnow
from taskcoord.database import sqlite_path_from_url
from taskcoord.errors import ApiError
from taskcoord.models import ServiceMeta
from taskcoord.settings import Settings


def backup_database(settings: Settings, session: Session | None = None) -> dict:
    source = sqlite_path_from_url(settings.database_url)
    if source is None or not source.exists():
        raise ApiError(503, "DATABASE_UNAVAILABLE", "找不到 SQLite 数据库文件")
    settings.backup_directory.mkdir(parents=True, exist_ok=True)
    stamp = utcnow().strftime("%Y%m%d-%H%M%S")
    destination = settings.backup_directory / f"taskcoord-{stamp}.sqlite3"
    _online_backup(source, destination)
    integrity = integrity_of(destination)
    if integrity != "ok":
        destination.unlink(missing_ok=True)
        raise ApiError(503, "BACKUP_FAILED", "备份完整性检查失败")
    removed = prune_backups(settings)
    if session is not None:
        _meta(session, "last_backup_at", utcnow().isoformat())
        _meta(session, "last_backup_path", str(destination))
    return {
        "path": str(destination),
        "integrity": integrity,
        "pruned": removed,
        "created_at": utcnow().isoformat(),
    }


def maybe_scheduled_backup(settings: Settings, session: Session) -> dict | None:
    if not settings.database_url.startswith("sqlite"):
        return None
    last = _get_meta(session, "last_backup_at")
    if last:
        try:
            from datetime import datetime

            stamp = datetime.fromisoformat(last)
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=utcnow().tzinfo)
            if utcnow() - stamp < timedelta(hours=24):
                return None
        except ValueError:
            pass
    return backup_database(settings, session)


def integrity_of(path: Path) -> str:
    connection = sqlite3.connect(path)
    try:
        row = connection.execute("PRAGMA integrity_check").fetchone()
    finally:
        connection.close()
    return row[0] if row else "fail"


def inspect_backup(path: Path) -> dict:
    if not path.is_file():
        raise ApiError(404, "BACKUP_NOT_FOUND", "备份文件不存在")
    integrity = integrity_of(path)
    connection = sqlite3.connect(path)
    try:
        tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        task_count = connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] if "tasks" in tables else 0
    finally:
        connection.close()
    return {"path": str(path), "integrity": integrity, "tables": tables, "task_count": task_count, "dry_run": True}


def restore_database(settings: Settings, backup: Path, *, apply: bool, lock_path: Path) -> dict:
    report = inspect_backup(backup)
    if report["integrity"] != "ok":
        raise ApiError(409, "BACKUP_INVALID", "备份未通过完整性检查", report)
    if not apply:
        return report
    if lock_path.exists():
        raise ApiError(409, "SERVER_RUNNING", "恢复前必须停止服务进程")
    source = sqlite_path_from_url(settings.database_url)
    if source is None:
        raise ApiError(503, "DATABASE_UNAVAILABLE", "当前数据库不是本地 SQLite 文件")
    source.parent.mkdir(parents=True, exist_ok=True)
    _online_backup(backup, source)
    report["dry_run"] = False
    report["restored_to"] = str(source)
    return report


def prune_backups(settings: Settings) -> int:
    if not settings.backup_directory.exists():
        return 0
    cutoff = utcnow() - timedelta(days=settings.backup_retention_days)
    removed = 0
    for path in settings.backup_directory.glob("taskcoord-*.sqlite3"):
        modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        if modified < cutoff:
            path.unlink()
            removed += 1
    return removed


def quick_check(session: Session) -> str:
    if session.bind is not None and session.bind.dialect.name != "sqlite":
        session.execute(text("SELECT 1"))
        return "ok"
    row = session.execute(text("PRAGMA quick_check")).fetchone()
    return row[0] if row else "fail"


def _online_backup(source: Path, destination: Path) -> None:
    src = sqlite3.connect(source)
    dst = sqlite3.connect(destination)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def _meta(session: Session, key: str, value: str) -> None:
    row = session.get(ServiceMeta, key)
    if row is None:
        session.add(ServiceMeta(key=key, value=value))
    else:
        row.value = value


def _get_meta(session: Session, key: str) -> str | None:
    row = session.get(ServiceMeta, key)
    return row.value if row else None


def backup_status(session: Session) -> dict:
    return {
        "last_backup_at": _get_meta(session, "last_backup_at"),
        "last_backup_path": _get_meta(session, "last_backup_path"),
        "last_export_at": _get_meta(session, "last_export_at"),
    }


def remember_export(session: Session, path: str) -> None:
    _meta(session, "last_export_at", isoformat(utcnow()) or "")
    _meta(session, "last_export_path", path)
