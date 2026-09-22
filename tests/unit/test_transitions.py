from taskcoord.services.transitions import next_status
import pytest


LEGAL = [
    ("pending_confirmation", "confirm", "ready"),
    ("ready", "claim", "claimed"),
    ("claimed", "deliver", "review"),
    ("claimed", "release", "ready"),
    ("claimed", "expire", "ready"),
    ("review", "accept", "done"),
    ("review", "reject_owner", "claimed"),
    ("review", "reject_ready", "ready"),
    ("claimed", "block", "blocked"),
    ("ready", "cancel", "cancelled"),
    ("blocked", "unblock", "ready"),
    ("review", "takeover", "claimed"),
]


@pytest.mark.parametrize("status,action,expected", LEGAL)
def test_legal_transitions(status, action, expected):
    assert next_status(status, action) == expected


@pytest.mark.parametrize(
    "status,action",
    [
        ("pending_confirmation", "claim"),
        ("ready", "deliver"),
        ("done", "claim"),
        ("done", "block"),
        ("done", "cancel"),
        ("cancelled", "confirm"),
        ("review", "release"),
        ("blocked", "block"),
        ("ready", "takeover"),
    ],
)
def test_illegal_transitions(status, action):
    with pytest.raises(ValueError):
        next_status(status, action)
