from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from taskcoord.api.deps import db_session, ok, require_csrf
from taskcoord.auth import Actor
from taskcoord.clock import utcnow
from taskcoord.errors import ApiError
from taskcoord.models import Session as UserSession, User
from taskcoord.schemas import LoginIn
from taskcoord.security import hash_token, new_token, verify_password

router = APIRouter()
COOKIE = "taskcoord_session"


@router.post("/auth/login")
def login(payload: LoginIn, request: Request, response: Response, session: Session = Depends(db_session)) -> dict:
    settings = request.app.state.settings
    if not settings.session_secret or not settings.admin_password_hash:
        raise ApiError(503, "CONFIG_MISSING", "管理员密码或 session secret 未配置")
    user = session.scalar(select(User).where(User.username == payload.username))
    if user is None or not verify_password(payload.password, user.password_hash):
        raise ApiError(401, "AUTH_REQUIRED", "用户名或密码错误")
    token = new_token()
    csrf = new_token()
    session.add(
        UserSession(
            user_id=user.id,
            token_hash=hash_token(token),
            csrf_token=csrf,
            expires_at=utcnow() + timedelta(hours=12),
            created_at=utcnow(),
        )
    )
    response.set_cookie(COOKIE, token, httponly=True, samesite="lax", path="/", max_age=12 * 3600)
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
