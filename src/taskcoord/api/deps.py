from __future__ import annotations

import json
from datetime import timedelta

import hmac

from fastapi import Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from taskcoord.auth import Actor
from taskcoord.clock import ensure_utc, utcnow
from taskcoord.context import idempotency_key_var, request_id_var
from taskcoord.database import session_scope
from taskcoord.errors import ApiError
from taskcoord.models import Agent, Session as UserSession, User
from taskcoord.security import hash_token, request_fingerprint
from taskcoord.services.rules import run_idempotent


def db_session(request: Request):
    factory = request.app.state.session_factory
    with session_scope(factory) as session:
        yield session


def ok(**payload) -> dict:
    return {"request_id": request_id_var.get(), **payload}


def resolve_actor(request: Request, session: Session = Depends(db_session)) -> Actor:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        token = header.split(" ", 1)[1].strip()
        agent = session.scalar(select(Agent).where(Agent.token_hash == hash_token(token)))
        if agent is None:
            raise ApiError(401, "AUTH_REQUIRED", "Agent token 无效")
        if not agent.enabled:
            raise ApiError(403, "AGENT_DISABLED", "Agent 已停用")
        _touch_agent(agent)
        return Actor(kind="agent", agent=agent)
    cookie = request.cookies.get("taskcoord_session")
    if cookie:
        row = session.scalar(select(UserSession).where(UserSession.token_hash == hash_token(cookie)))
        if row is not None and ensure_utc(row.expires_at) > utcnow():
            user = session.get(User, row.user_id)
            if user is not None:
                return Actor(kind="admin", user=user, session=row)
        # Stale cookies fall through to anonymous so read-only pages still work.
    if request.app.state.settings.anonymous_admin:
        return Actor(kind="admin")
    return Actor(kind="anonymous")


def require_admin(actor: Actor = Depends(resolve_actor)) -> Actor:
    if actor.kind != "admin":
        raise ApiError(401 if actor.kind == "anonymous" else 403, "FORBIDDEN" if actor.kind == "agent" else "AUTH_REQUIRED", "需要管理员登录")
    return actor


def require_agent(actor: Actor = Depends(resolve_actor)) -> Actor:
    if actor.agent is None:
        raise ApiError(401, "AUTH_REQUIRED", "需要 Agent token")
    return actor


def require_reader(request: Request, actor: Actor = Depends(resolve_actor)) -> Actor:
    if request.app.state.settings.anonymous_read or actor.kind != "anonymous":
        return actor
    raise ApiError(401, "AUTH_REQUIRED", "需要登录或 Agent token")


def require_csrf(request: Request, actor: Actor = Depends(resolve_actor)) -> Actor:
    if actor.user is None and actor.kind == "admin":
        return actor
    if actor.kind != "admin":
        return actor
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return actor
    provided = request.headers.get("x-csrf-token", "")
    expected = actor.session.csrf_token if actor.session is not None else ""
    if not expected or len(provided) != len(expected) or not hmac.compare_digest(provided, expected):
        raise ApiError(403, "CSRF_FAILED", "缺少或无效的 CSRF token")
    return actor


def perform(request: Request, session: Session, actor: Actor, work):
    key = request.headers.get("idempotency-key")
    token = idempotency_key_var.set(key or "")
    try:
        raw = getattr(request.state, "raw_body", b"") or b""
        path = request.url.path
        if request.url.query:
            path = f"{path}?{request.url.query}"
        status, payload = run_idempotent(
            session,
            *actor.idempotency_id,
            key,
            request_fingerprint(request.method, path, raw),
            work,
        )
    finally:
        idempotency_key_var.reset(token)
    return JSONResponse(status_code=status, content=payload)


def ensure_same_agent(actor: Actor, agent_id: str) -> None:
    if actor.agent is None or actor.agent.id != agent_id:
        raise ApiError(403, "FORBIDDEN", "agent_id 与 token 不一致")


def _touch_agent(agent: Agent) -> None:
    seen = ensure_utc(agent.last_seen_at)
    if seen is None or seen < utcnow() - timedelta(seconds=60):
        agent.last_seen_at = utcnow()


def error_body(status: int, code: str, message: str, details: dict | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"request_id": request_id_var.get(), "error": {"code": code, "message": message, "details": details or {}}},
    )


def parse_json(raw: bytes) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ApiError(400, "VALIDATION_ERROR", "请求体不是合法 JSON") from exc
    if not isinstance(data, dict):
        raise ApiError(400, "VALIDATION_ERROR", "请求体必须是 JSON 对象")
    return data
