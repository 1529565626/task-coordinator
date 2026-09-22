from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from taskcoord import __version__
from taskcoord.api.deps import db_session, ok
from taskcoord.errors import ApiError
from taskcoord.services.backup_service import backup_status, quick_check
from taskcoord.services.backup_service import _meta

router = APIRouter()


@router.get("/health/live")
def live() -> dict:
    return ok(status="live")


@router.get("/health/ready")
def ready(request: Request, session: Session = Depends(db_session)) -> dict:
    app = request.app
    if not getattr(app.state, "ready", False) or not getattr(app.state, "sweeper_ok", False):
        raise ApiError(503, "NOT_READY", getattr(app.state, "ready_error", None) or "服务未就绪")
    check = quick_check(session)
    if check != "ok":
        raise ApiError(503, "NOT_READY", "数据库完整性检查失败", {"integrity": check})
    from taskcoord.clock import utcnow

    _meta(session, "health_write", utcnow().isoformat())
    settings = app.state.settings
    return ok(
        status="ready",
        database="ok",
        sweeper="ok",
        schema_version=1,
        started_at=getattr(app.state, "started_at", None),
        admin_configured=bool(settings.admin_password_hash and settings.session_secret),
        leases={"duration_minutes": settings.lease_duration_minutes, "heartbeat_minutes": settings.heartbeat_minutes},
        backup=backup_status(session),
    )


@router.get("/version")
def version(request: Request) -> dict:
    settings = request.app.state.settings
    return ok(
        service="task-coordinator",
        version=__version__,
        schema_version=1,
        leases={"duration_minutes": settings.lease_duration_minutes, "heartbeat_minutes": settings.heartbeat_minutes},
    )
