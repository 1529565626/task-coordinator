from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from taskcoord.errors import ApiError, SchemaTooNewError
from taskcoord.models import Base, ServiceMeta
from taskcoord.settings import ROOT_DIR, Settings

log = logging.getLogger(__name__)


def create_db_engine(settings: Settings) -> Engine:
    url = settings.database_url
    connect_args = {}
    if url.startswith("sqlite"):
        connect_args["timeout"] = max(settings.busy_timeout_ms / 1000, 0.1)
        _ensure_sqlite_parent(url)
    engine_kwargs = {"future": True, "connect_args": connect_args}
    if url.startswith("sqlite"):
        engine_kwargs["isolation_level"] = "AUTOCOMMIT"
    engine = create_engine(url, **engine_kwargs)
    if url.startswith("sqlite"):
        _install_sqlite_hooks(engine, settings.busy_timeout_ms)
    return engine


def _ensure_sqlite_parent(url: str) -> None:
    path = sqlite_path_from_url(url)
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)


def sqlite_path_from_url(url: str) -> Path | None:
    if not url.startswith("sqlite:///"):
        return None
    raw = url.removeprefix("sqlite:///")
    if raw == ":memory:" or raw.startswith("file:"):
        return None
    return Path(raw)


def _install_sqlite_hooks(engine: Engine, busy_timeout_ms: int) -> None:
    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

    @event.listens_for(engine, "begin")
    def _begin_immediate(connection) -> None:
        connection.exec_driver_sql("BEGIN IMMEDIATE")


def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    session = factory()
    try:
        yield session
        session.commit()
    except OperationalError as exc:
        session.rollback()
        message = str(exc).lower()
        if "locked" in message or "busy" in message:
            raise ApiError(503, "DATABASE_BUSY", "数据库正忙，请使用同一 Idempotency-Key 重试", {}) from exc
        raise ApiError(503, "DATABASE_UNAVAILABLE", "数据库不可用", {}) from exc
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def alembic_config(settings: Settings) -> Config:
    cfg = Config(str(ROOT_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT_DIR / "migrations"))
    cfg.set_main_option("sqlalchemy.url", settings.database_url)
    return cfg


def prepare_database(settings: Settings, engine: Engine) -> None:
    if settings.database_url.startswith("sqlite"):
        _ensure_sqlite_parent(settings.database_url)
    _refuse_unknown_revision(settings, engine)
    command.upgrade(alembic_config(settings), "head")
    _assert_integrity(engine)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO service_meta(key, value) VALUES ('schema_version', '1') "
                "ON CONFLICT(key) DO UPDATE SET value='1'"
            )
        )


def _refuse_unknown_revision(settings: Settings, engine: Engine) -> None:
    script = ScriptDirectory.from_config(alembic_config(settings))
    known = {rev.revision for rev in script.walk_revisions()}
    with engine.connect() as connection:
        rows = connection.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='alembic_version'")).fetchall()
        if not rows:
            return
        current = {row[0] for row in connection.execute(text("SELECT version_num FROM alembic_version"))}
    unknown = current - known
    if unknown:
        raise SchemaTooNewError(f"database schema {sorted(unknown)} is newer than this build")


def _assert_integrity(engine: Engine) -> None:
    with engine.connect() as connection:
        row = connection.execute(text("PRAGMA integrity_check")).fetchone()
    if row is None or row[0] != "ok":
        raise RuntimeError(f"sqlite integrity check failed: {row}")


def create_all_for_tests(engine: Engine) -> None:
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE IF NOT EXISTS service_meta (key VARCHAR(80) PRIMARY KEY, value TEXT NOT NULL)")
        )
        connection.execute(
            text("INSERT INTO service_meta(key, value) VALUES ('schema_version', '1') ON CONFLICT(key) DO NOTHING")
        )
