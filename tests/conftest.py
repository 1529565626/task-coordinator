from __future__ import annotations

import socket
import threading
import time
import uuid

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

from taskcoord.main import create_app
from taskcoord.security import hash_password
from taskcoord.settings import Settings

ADMIN_PASSWORD = "test-admin-password"


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        database_url="sqlite:///" + (tmp_path / "taskcoord.sqlite3").as_posix(),
        backup_directory=tmp_path / "backups",
        log_path=tmp_path / "taskcoord.log",
        admin_password_hash=hash_password(ADMIN_PASSWORD),
        session_secret="test-session-secret",
        lease_duration_minutes=30,
        heartbeat_minutes=15,
        busy_timeout_ms=15000,
        root_dir=tmp_path,
    )


@pytest.fixture
def client(settings):
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def csrf(client) -> str:
    response = client.post("/api/v1/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def admin(client, csrf, method, path, **kwargs):
    headers = {"X-CSRF-Token": csrf, "Idempotency-Key": kwargs.pop("idempotency", str(uuid.uuid4()))}
    headers.update(kwargs.pop("headers", {}))
    return client.request(method, path, headers=headers, **kwargs)


def create_agent(client, csrf, agent_id="codex-machine-a", client_type="codex") -> str:
    response = admin(
        client,
        csrf,
        "POST",
        "/api/v1/agents",
        json={"id": agent_id, "display_name": agent_id, "machine_name": "test", "client_type": client_type},
    )
    assert response.status_code == 200, response.text
    return response.json()["api_token"]


def ready_task(client, csrf, task_id="TS-100", scopes=None, project="map-build", priority=100):
    created = admin(
        client,
        csrf,
        "POST",
        "/api/v1/projects",
        json={"key": project, "name": project, "description": ""},
    )
    if created.status_code not in {200, 409}:
        raise AssertionError(created.text)
    response = admin(
        client,
        csrf,
        "POST",
        "/api/v1/tasks",
        json={
            "id": task_id,
            "project_key": project,
            "title": task_id,
            "description": "need",
            "priority": priority,
            "scopes": scopes or ["combat"],
        },
    )
    assert response.status_code == 200, response.text
    confirmed = admin(client, csrf, "POST", f"/api/v1/tasks/{task_id}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    return confirmed.json()["task"]


def claim(client, token, task_id="TS-100", branch="task/TS-100-example", continue_from=None, key=None):
    return client.post(
        f"/api/v1/tasks/{task_id}/claim",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": key or str(uuid.uuid4())},
        json={"agent_id": "codex-machine-a", "branch_name": branch, "continue_from": continue_from or []},
    )


@pytest.fixture
def live_server(settings):
    app = create_app(settings)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            response = httpx.get(base + "/api/v1/health/live", timeout=0.3)
            if response.status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.05)
    else:
        server.should_exit = True
        raise RuntimeError("server did not start")
    yield base
    server.should_exit = True
    thread.join(timeout=5)
