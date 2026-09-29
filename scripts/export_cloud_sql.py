"""Export the currently loaded MySQL database to a cloud-init SQL script.

Read-only on the source. The connection URL is parsed from the deployed
.env (or TASKCOORD_SOURCE_URL env var) and is never printed or logged.

Output: data/migration/cloud_init.sql
  - schema (SHOW CREATE TABLE) for all tables
  - full data for business tables
  - sessions / idempotency_records created empty (transient data)
  - alembic_version seeded with the current revision

Usage:
  .venv/Scripts/python.exe scripts/export_cloud_sql.py [--source <mysql_url>]
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

import pymysql
from sqlalchemy.engine import make_url

ROOT_DIR = Path(__file__).resolve().parents[1]
DEPLOYED_ENV = Path(r"E:\godot\task-coordinator\.env")

# Data tables in FK-safe insert order.
DATA_TABLES = [
    "users",
    "projects",
    "agents",
    "tasks",
    "task_scopes",
    "task_events",
    "import_batches",
    "service_meta",
]
# Transient tables: schema only, no data.
EMPTY_TABLES = ["sessions", "idempotency_records"]
DDL_TABLES = DATA_TABLES + EMPTY_TABLES


def resolve_source_url(cli_url: str | None) -> str:
    if cli_url:
        return cli_url
    if os_url := __import__("os").environ.get("TASKCOORD_SOURCE_URL"):
        return os_url
    for line in DEPLOYED_ENV.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("TASKCOORD_DATABASE_URL="):
            return line.split("=", 1)[1].strip()
    sys.exit("no source URL: pass --source, set TASKCOORD_SOURCE_URL, or ensure deployed .env exists")


def connect(url: str) -> pymysql.connections.Connection:
    u = make_url(url)
    return pymysql.connect(
        host=u.host,
        port=u.port or 3306,
        user=unquote(u.username or ""),
        password=unquote(u.password or ""),
        database=u.database,
        charset="utf8mb4",
    )


def safe_create(conn, table: str) -> str:
    with conn.cursor() as cur:
        cur.execute(f"SHOW CREATE TABLE `{table}`")
        row = cur.fetchone()
    ddl = row[1]
    ddl = ddl.replace(f"CREATE TABLE `{table}`", f"CREATE TABLE `{table}`", 1)
    return ddl + ";"


def dump_rows(conn, table: str, chunk: int = 100) -> tuple[int, list[str]]:
    statements: list[str] = []
    count = 0
    with conn.cursor(pymysql.cursors.SSDictCursor) as cur:
        cur.execute(f"SELECT * FROM `{table}`")
        batch: list[str] = []
        columns: list[str] | None = None
        for row in cur:
            if columns is None:
                columns = list(row.keys())
            batch.append("(" + ", ".join(conn.escape(v) for v in row.values()) + ")")
            count += 1
            if len(batch) >= chunk:
                statements.append(_insert_stmt(table, columns, batch))
                batch = []
        if batch and columns is not None:
            statements.append(_insert_stmt(table, columns, batch))
    return count, statements


def _insert_stmt(table: str, columns: list[str], values: list[str]) -> str:
    cols = ", ".join(f"`{c}`" for c in columns)
    rows = ",\n  ".join(values)
    return f"INSERT INTO `{table}` ({cols}) VALUES\n  {rows};"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=None, help="mysql:// source URL (default: deployed .env)")
    parser.add_argument("--output", default=str(ROOT_DIR / "data" / "migration" / "cloud_init.sql"))
    args = parser.parse_args()

    url = resolve_source_url(args.source)
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    conn = connect(url)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT VERSION()")
            mysql_version = cur.fetchone()[0]
            cur.execute("SELECT version_num FROM alembic_version")
            revisions = [r[0] for r in cur.fetchall()]
            cur.execute("SELECT DATABASE()")
            database_name = cur.fetchone()[0]

        lines = [
            f"-- Task Coordinator cloud init script",
            f"-- Source host : {make_url(url).host} (credentials not included)",
            f"-- Source db   : {database_name}",
            f"-- MySQL server version: {mysql_version}",
            f"-- Generated   : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"-- Data tables : {', '.join(DATA_TABLES)}",
            f"-- Empty tables: {', '.join(EMPTY_TABLES)} (transient data not migrated)",
            "",
            "SET NAMES utf8mb4;",
            "SET FOREIGN_KEY_CHECKS = 0;",
            "",
        ]

        print(f"source database: {database_name} on {make_url(url).host}")
        for table in DDL_TABLES:
            lines.append(f"-- ---------- {table} ----------")
            lines.append(f"DROP TABLE IF EXISTS `{table}`;")
            lines.append(safe_create(conn, table))
            lines.append("")

        total = 0
        for table in DATA_TABLES:
            count, stmts = dump_rows(conn, table)
            total += count
            print(f"  {table}: {count} rows")
            lines.append(f"-- data: {table} ({count} rows)")
            lines.extend(stmts)
            lines.append("")

        lines.append(f"INSERT INTO `alembic_version` (version_num) VALUES ({conn.escape(revisions[0])});")
        lines.append("SET FOREIGN_KEY_CHECKS = 1;")
        lines.append("")
        print(f"  alembic revision: {revisions}")

        out_path.write_text("\n".join(lines), encoding="utf-8")
        size_kb = out_path.stat().st_size / 1024
        print(f"wrote {out_path} ({size_kb:.1f} KB, {total} data rows total)")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
