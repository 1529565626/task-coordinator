from __future__ import annotations

import os
import sys
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine, engine_from_config, pool

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from taskcoord.models import Base  # noqa: E402

config = context.config

target_metadata = Base.metadata


def _database_url() -> str | None:
    """数据库 URL 优先级：环境变量 TASKCOORD_DATABASE_URL > alembic.ini 的 sqlalchemy.url。

    与服务运行时（database.prepare_database → alembic_config）保持一致：
    部署时只需设置环境变量，无需手改 alembic.ini。
    """
    return os.environ.get("TASKCOORD_DATABASE_URL") or config.get_main_option("sqlalchemy.url")


def run_migrations_offline() -> None:
    url = _database_url()
    if not url:
        raise SystemExit("数据库 URL 未配置：设置 TASKCOORD_DATABASE_URL 环境变量，或填写 alembic.ini 的 sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url = _database_url()
    if url:
        connectable = create_engine(url, poolclass=pool.NullPool)
    else:
        connectable = engine_from_config(
            config.get_section(config.config_ini_section, {}),
            prefix="sqlalchemy.",
            poolclass=pool.NullPool,
        )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
