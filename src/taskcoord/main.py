from __future__ import annotations

import asyncio
import logging
import os
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from taskcoord import __version__
from taskcoord.api.v1 import admin, auth, claims, events, health, projects, tasks
from taskcoord.clock import isoformat, utcnow
from taskcoord.context import request_id_var
from taskcoord.database import create_db_engine, prepare_database, session_factory, session_scope
from taskcoord.errors import ApiError, SchemaTooNewError
from taskcoord.logging_config import configure_logging
from taskcoord.models import User
from taskcoord.services.backup_service import maybe_scheduled_backup
from taskcoord.services.lease_service import expire_due
from taskcoord.settings import PACKAGE_DIR, Settings, load_settings

log = logging.getLogger(__name__)
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    engine = create_db_engine(settings)
    factory = session_factory(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.ready = False
        app.state.ready_error = "starting"
        app.state.sweeper_ok = False
        app.state.started_at = isoformat(utcnow())
        lock_path = settings.root_dir / "data" / "server.lock"
        try:
            prepare_database(settings, engine)
            with session_scope(factory) as session:
                _seed_admin(session, settings)
                expire_due(session, utcnow())
            app.state.ready = True
            app.state.ready_error = None
            app.state.sweeper_ok = True
            if settings.manage_lock:
                lock_path.parent.mkdir(parents=True, exist_ok=True)
                lock_path.write_text(str(os.getpid()), encoding="utf-8")
        except SchemaTooNewError:
            raise
        except Exception as exc:
            app.state.ready = False
            app.state.ready_error = str(exc)
            log.exception("startup prepare failed")
        sweeper = asyncio.create_task(_sweep(app))
        try:
            yield
        finally:
            sweeper.cancel()
            try:
                await sweeper
            except asyncio.CancelledError:
                pass
            if settings.manage_lock and lock_path.exists():
                lock_path.unlink()
            engine.dispose()

    app = FastAPI(title="Task Coordinator", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = factory
    app.state.ready = False
    app.state.sweeper_ok = False
    app.state.ready_error = "starting"
    app.state.started_at = None

    @app.middleware("http")
    async def cache_body(request, call_next):
        request.state.raw_body = await request.body()
        return await call_next(request)

    @app.middleware("http")
    async def request_context(request, call_next):
        request_id = "req_" + uuid.uuid4().hex
        token = request_id_var.set(request_id)
        try:
            if (
                request.method in WRITE_METHODS
                and request.url.path.startswith("/api/")
                and not request.url.path.startswith("/api/v1/health")
                and not app.state.ready
            ):
                return JSONResponse(
                    status_code=503,
                    content={
                        "request_id": request_id,
                        "error": {
                            "code": "NOT_READY",
                            "message": app.state.ready_error or "服务未就绪",
                            "details": {},
                        },
                    },
                )
            response = await call_next(request)
            response.headers["X-Request-Id"] = request_id
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
            return response
        finally:
            request_id_var.reset(token)

    @app.exception_handler(ApiError)
    async def handle_api_error(_request, exc: ApiError):
        return JSONResponse(
            status_code=exc.status,
            content={
                "request_id": request_id_var.get(),
                "error": {"code": exc.code, "message": exc.message, "details": exc.details},
            },
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation(_request, exc: RequestValidationError):
        details = []
        for item in exc.errors():
            details.append({key: value for key, value in item.items() if key != "input"})
        return JSONResponse(
            status_code=422,
            content={
                "request_id": request_id_var.get(),
                "error": {"code": "VALIDATION_ERROR", "message": "请求字段不合法", "details": {"errors": details}},
            },
        )

    for module in (health, auth, projects, tasks, claims, events, admin):
        app.include_router(module.router, prefix="/api/v1")

    web_dir = PACKAGE_DIR / "web"
    web_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/assets", StaticFiles(directory=web_dir), name="assets")

    @app.get("/")
    def index():
        page = web_dir / "index.html"
        if not page.exists():
            return JSONResponse(status_code=404, content={"error": {"code": "NOT_FOUND", "message": "页面尚未安装"}})
        return FileResponse(page)

    return app


async def _sweep(app: FastAPI) -> None:
    while True:
        await asyncio.sleep(30)
        try:
            with session_scope(app.state.session_factory) as session:
                expire_due(session, utcnow())
            if app.state.ready:
                with session_scope(app.state.session_factory) as session:
                    maybe_scheduled_backup(app.state.settings, session)
            app.state.sweeper_ok = True
        except Exception:
            app.state.sweeper_ok = False
            log.exception("background sweep failed")


def _seed_admin(session, settings: Settings) -> None:
    if not settings.admin_password_hash:
        return
    from sqlalchemy import select

    user = session.scalar(select(User).where(User.username == settings.admin_username))
    if user is None:
        session.add(
            User(
                username=settings.admin_username,
                password_hash=settings.admin_password_hash,
                created_at=utcnow(),
            )
        )
        return
    user.password_hash = settings.admin_password_hash


def main() -> None:
    settings = load_settings().model_copy(update={"manage_lock": True})
    configure_logging(settings)
    engine = create_db_engine(settings)
    try:
        prepare_database(settings, engine)
    except SchemaTooNewError as exc:
        log.error("%s", exc)
        raise SystemExit(1) from exc
    except Exception:
        log.exception("database is not ready; write endpoints will stay closed")
    finally:
        engine.dispose()
    import uvicorn

    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_config=None)


if __name__ == "__main__":
    main()
