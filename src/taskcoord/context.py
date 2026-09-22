from __future__ import annotations

import contextvars

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")
idempotency_key_var: contextvars.ContextVar[str] = contextvars.ContextVar("idempotency_key", default="")
