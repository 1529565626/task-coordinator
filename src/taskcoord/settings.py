from __future__ import annotations

import os
import tomllib
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

PACKAGE_DIR = Path(__file__).resolve().parent
ROOT_DIR = PACKAGE_DIR.parents[1]


class Settings(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8787
    anonymous_read: bool = True
    anonymous_admin: bool = False
    database_url: str
    busy_timeout_ms: int = 5000
    lease_duration_minutes: int = 120
    heartbeat_minutes: int = 15
    lease_enabled: bool = False
    backup_directory: Path
    backup_retention_days: int = 30
    log_path: Path
    log_retention_days: int = 14
    admin_username: str = "admin"
    admin_password_hash: str = ""
    session_secret: str = ""
    manage_lock: bool = False
    root_dir: Path = Field(default=ROOT_DIR)

    @field_validator("lease_duration_minutes")
    @classmethod
    def _lease_window(cls, value: int) -> int:
        if not 30 <= value <= 480:
            raise ValueError("lease duration must be between 30 and 480 minutes")
        return value

    @field_validator("heartbeat_minutes")
    @classmethod
    def _heartbeat(cls, value: int) -> int:
        if value < 1:
            raise ValueError("heartbeat interval must be positive")
        return value

    @field_validator("port")
    @classmethod
    def _port(cls, value: int) -> int:
        if not 1 <= value <= 65535:
            raise ValueError("port out of range")
        return value


def _read_toml(path: Path) -> dict:
    if not path.is_file():
        return {}
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        os.environ.setdefault(name.strip(), value.strip())


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def load_settings() -> Settings:
    _load_dotenv(ROOT_DIR / ".env")
    config_path = Path(_env("TASKCOORD_CONFIG") or (ROOT_DIR / "config" / "service.toml"))
    if not config_path.is_file():
        example = ROOT_DIR / "config" / "service.toml.example"
        config_path = example if example.is_file() else config_path
    raw = _read_toml(config_path)
    server = raw.get("server", {})
    database = raw.get("database", {})
    leases = raw.get("leases", {})
    backup = raw.get("backup", {})
    logging_cfg = raw.get("logging", {})

    db_url = _env("TASKCOORD_DATABASE_URL") or database.get(
        "url", f"sqlite:///{(ROOT_DIR / 'data' / 'taskcoord.sqlite3').as_posix()}"
    )
    backup_dir = Path(
        _env("TASKCOORD_BACKUP_DIR")
        or backup.get("directory", str(ROOT_DIR / "data" / "backups"))
    )
    log_path = Path(_env("TASKCOORD_LOG_PATH") or logging_cfg.get("path", str(ROOT_DIR / "logs" / "taskcoord.log")))
    return Settings(
        host=_env("TASKCOORD_HOST") or server.get("host", "127.0.0.1"),
        port=int(_env("TASKCOORD_PORT") or server.get("port", 8787)),
        anonymous_read=_bool_env("TASKCOORD_ANONYMOUS_READ", server.get("anonymous_read", True)),
        anonymous_admin=_bool_env("TASKCOORD_ANONYMOUS_ADMIN", server.get("anonymous_admin", False)),
        database_url=db_url,
        busy_timeout_ms=int(_env("TASKCOORD_BUSY_TIMEOUT_MS") or database.get("busy_timeout_ms", 5000)),
        lease_duration_minutes=int(_env("TASKCOORD_LEASE_MINUTES") or leases.get("duration_minutes", 120)),
        heartbeat_minutes=int(_env("TASKCOORD_HEARTBEAT_MINUTES") or leases.get("heartbeat_minutes", 15)),
        lease_enabled=_bool_env("TASKCOORD_LEASE_ENABLED", leases.get("enabled", False)),
        backup_directory=backup_dir,
        backup_retention_days=int(backup.get("retention_days", 30)),
        log_path=log_path,
        log_retention_days=int(logging_cfg.get("retention_days", 14)),
        admin_username=_env("TASKCOORD_ADMIN_USERNAME", "admin") or "admin",
        admin_password_hash=_env("TASKCOORD_ADMIN_PASSWORD_HASH"),
        session_secret=_env("TASKCOORD_SESSION_SECRET"),
    )


def _bool_env(name: str, fallback: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return bool(fallback)
    return raw.strip().lower() in {"1", "true", "yes", "on"}
