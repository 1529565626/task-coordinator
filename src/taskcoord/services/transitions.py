from __future__ import annotations

TERMINAL = {"done", "cancelled"}
STATUSES = {
    "pending_confirmation",
    "ready",
    "claimed",
    "review",
    "blocked",
    "done",
    "cancelled",
}
OCCUPYING = {"claimed", "review"}

_TRANSITIONS = {
    ("pending_confirmation", "confirm"): "ready",
    ("ready", "claim"): "claimed",
    ("claimed", "deliver"): "review",
    ("claimed", "release"): "ready",
    ("claimed", "expire"): "ready",
    ("claimed", "heartbeat"): "claimed",
    ("review", "accept"): "done",
    ("review", "reject_owner"): "claimed",
    ("review", "reject_ready"): "ready",
    ("blocked", "unblock"): "ready",
}


def next_status(status: str, action: str) -> str:
    if action in {"block", "cancel"}:
        if status in TERMINAL or (action == "block" and status == "blocked"):
            raise ValueError(action)
        return "blocked" if action == "block" else "cancelled"
    if action == "takeover":
        if status not in {"claimed", "review", "blocked"}:
            raise ValueError(action)
        return "claimed"
    try:
        return _TRANSITIONS[(status, action)]
    except KeyError as exc:
        raise ValueError(action) from exc
