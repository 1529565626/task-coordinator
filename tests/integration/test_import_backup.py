import json
from pathlib import Path

from taskcoord.database import prepare_database
from taskcoord.errors import SchemaTooNewError
from taskcoord.main import create_app
from taskcoord.services.backup_service import inspect_backup, restore_database
from tests.conftest import admin, create_agent, ready_task


def test_restart_keeps_claim_and_audit(settings, tmp_path):
    app = create_app(settings)
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        csrf = client.post("/api/v1/auth/login", json={"username": "admin", "password": "test-admin-password"}).json()["csrf_token"]
        token = create_agent(client, csrf)
        ready_task(client, csrf, "TS-150", ["persist"])
        claimed = client.post(
            "/api/v1/tasks/TS-150/claim",
            headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "persist"},
            json={"agent_id": "codex-machine-a", "branch_name": "task/TS-150", "continue_from": []},
        )
        assert claimed.status_code == 200, claimed.text
    app.state.engine.dispose()
    restarted = create_app(settings)
    with TestClient(restarted) as client:
        task = client.get("/api/v1/tasks/TS-150").json()["task"]
        events = client.get("/api/v1/tasks/TS-150/events").json()["events"]
    assert task["status"] == "claimed"
    assert task["owner"] == "codex-machine-a"
    assert any(event["event_type"] == "task.claimed" for event in events)


def test_backup_roundtrip_and_restore_guard(client, csrf, settings):
    create_agent(client, csrf)
    ready_task(client, csrf, "TS-160", ["backup"])
    backup = admin(client, csrf, "POST", "/api/v1/admin/backup", json={})
    assert backup.status_code == 200, backup.text
    path = Path(backup.json()["backup"]["path"])
    report = inspect_backup(path)
    assert report["integrity"] == "ok"
    assert report["task_count"] == 1
    lock = settings.root_dir / "data" / "server.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("1", encoding="utf-8")
    try:
        restore_database(settings, path, apply=True, lock_path=lock)
        raise AssertionError("restore should refuse while the lock exists")
    except Exception as exc:
        assert getattr(exc, "code", "") == "SERVER_RUNNING"
    lock.unlink()
    restored = settings.model_copy(
        update={"database_url": "sqlite:///" + str((path.parent / "restored.sqlite3").as_posix())}
    )
    applied = restore_database(restored, path, apply=True, lock_path=lock)
    assert applied["dry_run"] is False
    check = inspect_backup(path.parent / "restored.sqlite3")
    assert check["integrity"] == "ok"
    assert check["task_count"] == 1


def test_import_preview_commit_is_idempotent(client, csrf, tmp_path):
    source = tmp_path / "tasks.json"
    source.write_text(
        json.dumps(
            {
                "version": 1,
                "tasks": [
                    {
                        "id": "TS-170",
                        "project_id": "project_0001",
                        "title": "导入",
                        "status": "review",
                        "owner": "legacy-owner",
                        "scope": ["forest"],
                        "description": "keep",
                        "delivery": {"commit": "c" * 40, "tests": "kept"},
                        "updated_at": "2026-09-05T14:44:05+00:00",
                    },
                    {
                        "id": "TS-171",
                        "project_id": "project_0001",
                        "title": "认领中",
                        "status": "claimed",
                        "owner": "legacy-owner",
                        "scope": ["forest"],
                        "description": "choose",
                        "updated_at": "2026-09-05T14:44:05+00:00",
                    },
                ],
            }
        ),
        encoding="utf-8-sig",
    )
    raw = source.read_bytes()
    preview = admin(client, csrf, "POST", "/api/v1/admin/import/preview", content=raw, headers={"Content-Type": "application/json"})
    assert preview.status_code == 200, preview.text
    report = preview.json()["report"]
    assert report["task_count"] == 2
    assert report["claimed_pending"] == ["TS-171"]
    assert report["scope_conflicts"]
    import_id = report["import_id"]
    missing = admin(
        client,
        csrf,
        "POST",
        "/api/v1/admin/import/commit",
        json={"import_id": import_id, "owner_map": {}, "claimed_resolutions": {}},
    )
    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "IMPORT_DECISIONS_REQUIRED"
    committed = admin(
        client,
        csrf,
        "POST",
        "/api/v1/admin/import/commit",
        json={"import_id": import_id, "owner_map": {}, "claimed_resolutions": {"TS-171": "ready"}},
    )
    assert committed.status_code == 200, committed.text
    again = admin(
        client,
        csrf,
        "POST",
        "/api/v1/admin/import/commit",
        json={"import_id": import_id, "owner_map": {}, "claimed_resolutions": {"TS-171": "review"}},
    )
    assert again.status_code == 200
    assert again.json()["result"]["idempotent"] is True
    assert again.json()["result"]["imported"] == 2
    ready = client.get("/api/v1/tasks/TS-171").json()["task"]
    review = client.get("/api/v1/tasks/TS-170").json()["task"]
    assert ready["status"] == "ready"
    assert ready["owner"] is None
    assert review["status"] == "review"
    assert review["delivery_commit"] == "c" * 40
    assert review["scopes"] == ["forest"]
    assert source.read_text(encoding="utf-8-sig").startswith("{")


def test_schema_too_new_refuses_startup(settings):
    from sqlalchemy import text

    from taskcoord.database import create_db_engine

    engine = create_db_engine(settings)
    prepare_database(settings, engine)
    with engine.begin() as connection:
        connection.execute(text("UPDATE alembic_version SET version_num='9999_future'"))
    try:
        prepare_database(settings, engine)
        raise AssertionError("newer schema should refuse startup")
    except SchemaTooNewError:
        pass
    finally:
        engine.dispose()
