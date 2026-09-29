from __future__ import annotations

import time
from collections import defaultdict, deque
from datetime import timedelta

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from taskcoord.api.deps import SESSION_COOKIE, db_session, ok, require_csrf
from taskcoord.auth import Actor
from taskcoord.clock import utcnow
from taskcoord.errors import ApiError
from taskcoord.models import Session as UserSession, User
from taskcoord.schemas import LoginIn
from taskcoord.security import hash_token, new_token, verify_password

router = APIRouter()
COOKIE = SESSION_COOKIE

# 登录防暴力破解：同一来源 60 秒内最多 5 次失败，成功登录不计数。
# 状态挂在 app.state 上：多实例互不影响，测试间也不会泄漏。
# 注意：uvicorn 多 worker 时每个进程独立计数，单实例部署足够；如需全局限流放在反代层。
_LOGIN_WINDOW_SECONDS = 60
_LOGIN_MAX_FAILURES = 5


def _client_key(request: Request) -> str:
    host = request.client.host if request.client else "unknown"
    return request.headers.get("x-forwarded-for", "").split(",")[0].strip() or host


def _failure_store(request: Request) -> dict[str, deque[float]]:
    store = getattr(request.app.state, "login_failures", None)
    if store is None:
        store = defaultdict(deque)
        request.app.state.login_failures = store
    return store


def _check_login_rate(request: Request) -> None:
    key = _client_key(request)
    now = time.monotonic()
    recent = _failure_store(request)[key]
    while recent and now - recent[0] > _LOGIN_WINDOW_SECONDS:
        recent.popleft()
    if len(recent) >= _LOGIN_MAX_FAILURES:
        raise ApiError(429, "RATE_LIMITED", "登录失败次数过多，请稍后重试", {"retry_after_seconds": _LOGIN_WINDOW_SECONDS})


def _record_login_failure(request: Request) -> None:
    _failure_store(request)[_client_key(request)].append(time.monotonic())


@router.post("/auth/login")
def login(payload: LoginIn, request: Request, response: Response, session: Session = Depends(db_session)) -> dict:
    _check_login_rate(request)
    settings = request.app.state.settings
    if not settings.session_secret or not settings.admin_password_hash:
        raise ApiError(503, "CONFIG_MISSING", "管理员密码或 session secret 未配置")
    user = session.scalar(select(User).where(User.username == payload.username))
    if user is None or not verify_password(payload.password, user.password_hash):
        _record_login_failure(request)
        raise ApiError(401, "AUTH_REQUIRED", "用户名或密码错误")
    token = new_token()
    csrf = new_token()
    ttl = timedelta(hours=settings.session_ttl_hours)
    session.add(
        UserSession(
            user_id=user.id,
            token_hash=hash_token(token),
            csrf_token=csrf,
            expires_at=utcnow() + ttl,
            created_at=utcnow(),
        )
    )
    response.set_cookie(COOKIE, token, httponly=True, samesite="lax", path="/", max_age=int(ttl.total_seconds()))
    return ok(username=user.username, csrf_token=csrf)


@router.post("/auth/logout")
def logout(request: Request, response: Response, session: Session = Depends(db_session), actor: Actor = Depends(require_csrf)) -> dict:
    if actor.session is not None:
        stored = session.get(UserSession, actor.session.id)
        if stored is not None:
            session.delete(stored)
    response.delete_cookie(COOKIE, path="/")
    return ok(status="logged_out")


@router.get("/auth/me")
def me(actor: Actor = Depends(require_csrf)) -> dict:
    if actor.user is None:
        if actor.kind == "admin":
            return ok(username="local", csrf_token=None)
        raise ApiError(401, "AUTH_REQUIRED", "需要管理员登录")
    return ok(username=actor.user.username, csrf_token=actor.session.csrf_token if actor.session else None)
