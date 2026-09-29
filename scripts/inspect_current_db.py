"""Read-only inventory of the currently loaded database.

Parses the deployed .env for TASKCOORD_DATABASE_URL, connects, and prints
table names + row counts only. Never prints the URL or credentials.
"""
from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import create_engine, text

env_path = Path(r"E:\godot\task-coordinator\.env")
url = None
for line in env_path.read_text(encoding="utf-8").splitlines():
    if line.strip().startswith("TASKCOORD_DATABASE_URL="):
        url = line.split("=", 1)[1].strip()
        break

if not url:
    sys.exit("TASKCOORD_DATABASE_URL not found")

scheme = url.split(":", 1)[0]
host_part = url.split("@", 1)[-1].split("/", 1)[0]
print(f"backend scheme: {scheme}, host:port: {host_part} (credentials not shown)")

engine = create_engine(url, future=True)
with engine.connect() as conn:
    version = conn.execute(text("SELECT VERSION()")).scalar()
    print(f"mysql version: {version}")
    tables = [
        row[0]
        for row in conn.execute(
            text("SELECT table_name FROM information_schema.tables "
                 "WHERE table_schema = DATABASE() ORDER BY table_name")
        )
    ]
    total = 0
    for t in tables:
        n = conn.execute(text(f"SELECT COUNT(*) FROM `{t}`")).scalar()
        total += n
        print(f"  {t}: {n}")
    print(f"tables: {len(tables)}, total rows: {total}")
    print("alembic revision:", conn.execute(text("SELECT version_num FROM alembic_version")).scalars().all())
engine.dispose()
