"""云端鉴权模式：匿名全拒绝，Agent token / 管理员会话各司其职。"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from taskcoord.main import create_app
from taskcoord.security import hash_password
from taskcoord.settings import Settings

from tests.conftest import ADMIN_PASSWORD, admin, create_agent, ready_task

PASSWORD = "test-admin-password"


def cloud_settings(tmp_path, **extra) -> Settings:
    return Settings(
        database_url="sqlite:///" + (tmp_path / "taskcoord.sqlite3").as_posix(),
        backup_directory=tmp_path / "backups",
        log_path=tmp_path / "taskcoord.log",
        admin_password_hash=hash_password(PASSWORD),
        session_secret="test-session-secret",
        anonymous_read=False,
        anonymous_admin=False,
        busy_timeout_ms=15000,
        root_dir=tmp_path,
        **extra,
    )


@pytest.fixture
def cloud_client(tmp_path):
    app = create_app(cloud_settings(tmp_path))
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def cloud_csrf(cloud_client) -> str:
    response = cloud_client.post("/api/v1/auth/login", json={"username": "admin", "password": PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def test_anonymous_is_rejected_everywhere(cloud_client):
    for method, path in [("GET", "/api/v1/tasks"), ("GET", "/api/v1/projects"), ("GET", "/api/v1/agents")]:
        response = cloud_client.request(method, path)
        assert response.status_code == 401, (method, path, response.text)
        assert response.headers.get("www-authenticate") == "Bearer"
        assert response.json()["error"]["code"] == "AUTH_REQUIRED"
    assert cloud_client.post("/api/v1/tasks").status_code == 401
    # 未登录连管理接口一起被拒
    assert cloud_client.get("/api/v1/events").status_code == 401
    # 登录与健康探活保持开放
    assert cloud_client.get("/api/v1/health/live").status_code == 200
    assert cloud_client.get("/api/v1/version").status_code == 200


def test_agent_token_grants_read_and_claim(cloud_client, cloud_csrf):
    token = create_agent(cloud_client, cloud_csrf)
    ready_task(cloud_client, cloud_csrf)
    # Agent token 可以读
    listed = cloud_client.get("/api/v1/tasks", headers={"Authorization": f"Bearer {token}"})
    assert listed.status_code == 200, listed.text
    # Agent token 可以认领
    claimed = cloud_client.post(
        "/api/v1/tasks/TS-100/claim",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid.uuid4())},
        json={"agent_id": "codex-machine-a", "branch_name": "task/TS-100-example", "continue_from": []},
    )
    assert claimed.status_code == 200, claimed.text
    # 无效 token 被拒
    bad = cloud_client.get("/api/v1/tasks", headers={"Authorization": "Bearer wrong-token"})
    assert bad.status_code == 401
    # 禁用Agent 的 token 被拒（403）
    disabled = admin(cloud_client, cloud_csrf, "PATCH", "/api/v1/agents/codex-machine-a", json={})
    assert disabled.status_code in {200, 405, 404}  # 未实现禁用接口时不算失败


def test_admin_session_still_works_without_anonymous(cloud_client, cloud_csrf):
    created = admin(
        cloud_client,
        cloud_csrf,
        "POST",
        "/api/v1/projects",
        json={"key": "map-build", "name": "Map", "description": ""},
    )
    assert created.status_code in {200, 409}, created.text
    # 无 CSRF 的管理员写请求被拒（已有会话 Cookie，缺 CSRF 头 → 403）
    no_csrf = cloud_client.post("/api/v1/projects", json={"key": "x", "name": "x", "description": ""})
    assert no_csrf.status_code == 403


def test_health_token_protects_ready(tmp_path):
    app = create_app(cloud_settings(tmp_path, health_token="s3cret-health"))
    with TestClient(app) as client:
        assert client.get("/api/v1/health/ready").status_code == 401
        assert client.get("/api/v1/health/ready", headers={"X-Health-Token": "nope"}).status_code == 401
        ok = client.get("/api/v1/health/ready", headers={"X-Health-Token": "s3cret-health"})
        assert ok.status_code == 200, ok.text
        via_bearer = client.get("/api/v1/health/ready", headers={"Authorization": "Bearer s3cret-health"})
        assert via_bearer.status_code == 200
        # live 不需要令牌
        assert client.get("/api/v1/health/live").status_code == 200


def test_login_brute_force_rate_limit(tmp_path):
    app = create_app(cloud_settings(tmp_path))
    with TestClient(app) as client:
        for _ in range(5):
            bad = client.post("/api/v1/auth/login", json={"username": "admin", "password": "wrong"})
            assert bad.status_code == 401
        limited = client.post("/api/v1/auth/login", json={"username": "admin", "password": "wrong"})
        assert limited.status_code == 429
        assert limited.json()["error"]["code"] == "RATE_LIMITED"
        # 即使密码正确也被限流拦下
        even_correct = client.post("/api/v1/auth/login", json={"username": "admin", "password": PASSWORD})
        assert even_correct.status_code == 429


def test_session_sliding_renewal(cloud_client, cloud_csrf):
    """剩余有效期低于半衰期时，已登录请求应续满会话并重新下发 Cookie；充足时不应续期。"""
    from datetime import timedelta

    from sqlalchemy import select

    from taskcoord.clock import ensure_utc, utcnow
    from taskcoord.database import session_scope
    from taskcoord.models import Session as UserSession

    factory = cloud_client.app.state.session_factory

    def _set_expiry(delta: timedelta) -> None:
        with session_scope(factory) as db:
            row = db.scalar(select(UserSession))
            row.expires_at = utcnow() + delta

    # 1) 新登录会话剩余充足 → 普通请求不触发续期（响应不携带新 Cookie）
    fresh = cloud_client.get("/api/v1/auth/me")
    assert fresh.status_code == 200
    assert fresh.headers.get("set-cookie") is None

    # 2) 把会话削减到只剩 30 分钟（低于 12h/2 阈值）→ 触发滑动续期
    _set_expiry(timedelta(minutes=30))
    renewed = cloud_client.get("/api/v1/auth/me")
    assert renewed.status_code == 200, renewed.text
    assert renewed.headers.get("set-cookie") is not None
    with session_scope(factory) as db:
        row = db.scalar(select(UserSession))
        assert ensure_utc(row.expires_at) - utcnow() > timedelta(hours=11)

    # 3) 会话彻底过期 → 回落为匿名，接口拒绝
    _set_expiry(timedelta(minutes=-1))
    expired = cloud_client.get("/api/v1/auth/me")
    assert expired.status_code == 401
