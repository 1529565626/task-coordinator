from tests.conftest import admin, claim, create_agent, ready_task


def test_full_lifecycle_and_shared_version(client, csrf):
    token = create_agent(client, csrf)
    ready_task(client, csrf)
    claimed = claim(client, token)
    assert claimed.status_code == 200, claimed.text
    body = claimed.json()
    claim_token = body["claim_token"]
    version = body["task"]["version"]
    heartbeat = client.post(
        "/api/v1/tasks/TS-100/heartbeat",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "hb-1"},
        json={"agent_id": "codex-machine-a", "claim_token": claim_token},
    )
    assert heartbeat.status_code == 200, heartbeat.text
    delivered = client.post(
        "/api/v1/tasks/TS-100/deliver",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "del-1"},
        json={
            "agent_id": "codex-machine-a",
            "claim_token": claim_token,
            "branch_name": "task/TS-100-example",
            "commit": "a" * 40,
            "tests": "core 1/1",
            "notes": "no full regression",
        },
    )
    assert delivered.status_code == 200, delivered.text
    assert delivered.json()["task"]["status"] == "review"
    accepted = admin(client, csrf, "POST", "/api/v1/tasks/TS-100/accept")
    assert accepted.status_code == 200, accepted.text
    done = accepted.json()["task"]
    assert done["status"] == "done"
    assert done["accepted_by"] == "admin"
    assert done["accepted_at"]
    listed = client.get("/api/v1/tasks/TS-100")
    assert listed.json()["task"]["version"] == done["version"]
    page = client.get("/")
    assert page.status_code == 200
    assert "Task Coordinator" in page.text
    script = client.get("/assets/app.js")
    assert "/api/v1/tasks" in script.text
    assert version < done["version"]


def test_pending_cannot_be_claimed(client, csrf):
    create_agent(client, csrf)
    admin(client, csrf, "POST", "/api/v1/projects", json={"key": "map-build", "name": "Map", "description": ""})
    created = admin(
        client,
        csrf,
        "POST",
        "/api/v1/tasks",
        json={"id": "TS-101", "project_key": "map-build", "title": "t", "description": "", "priority": 1, "scopes": ["ui"]},
    )
    assert created.json()["task"]["status"] == "pending_confirmation"
    agent = create_agent(client, csrf, "cursor-machine-a", "cursor")
    response = client.post(
        "/api/v1/tasks/TS-101/claim",
        headers={"Authorization": f"Bearer {agent}", "Idempotency-Key": "pending"},
        json={"agent_id": "cursor-machine-a", "branch_name": "task/TS-101", "continue_from": []},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ILLEGAL_TRANSITION"


def test_scope_conflict_and_continue_from(client, csrf):
    token_a = create_agent(client, csrf, "codex-machine-a", "codex")
    token_b = create_agent(client, csrf, "codex-machine-b", "codex")
    ready_task(client, csrf, "TS-110", ["shared"])
    ready_task(client, csrf, "TS-111", ["shared"])
    first = claim(client, token_a, "TS-110", "task/TS-110")
    assert first.status_code == 200, first.text
    second = client.post(
        "/api/v1/tasks/TS-111/claim",
        headers={"Authorization": f"Bearer {token_b}", "Idempotency-Key": "scope-b"},
        json={"agent_id": "codex-machine-b", "branch_name": "task/TS-111", "continue_from": ["TS-110"]},
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] in {"SCOPE_CONFLICT", "CONTINUE_FROM_INVALID"}
    delivered = client.post(
        "/api/v1/tasks/TS-110/deliver",
        headers={"Authorization": f"Bearer {token_a}", "Idempotency-Key": "del-110"},
        json={
            "agent_id": "codex-machine-a",
            "claim_token": first.json()["claim_token"],
            "branch_name": "task/TS-110",
            "commit": "b" * 40,
            "tests": "ok",
        },
    )
    assert delivered.status_code == 200, delivered.text
    continued = claim(client, token_a, "TS-111", "task/TS-111", ["TS-110"])
    assert continued.status_code == 200, continued.text
    assert any(event["event_type"] == "task.continued_from" for event in client.get("/api/v1/tasks/TS-111/events").json()["events"])


def test_version_conflict_and_stale_token(client, csrf):
    token = create_agent(client, csrf)
    task = ready_task(client, csrf, "TS-120", ["camera"])
    stale = admin(
        client,
        csrf,
        "PATCH",
        "/api/v1/tasks/TS-120",
        json={"version": task["version"] - 1, "title": "nope"},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "VERSION_CONFLICT"
    claimed = claim(client, token, "TS-120", "task/TS-120")
    other = create_agent(client, csrf, "cursor-machine-a", "cursor")
    stolen = client.post(
        "/api/v1/tasks/TS-120/heartbeat",
        headers={"Authorization": f"Bearer {other}", "Idempotency-Key": "stolen"},
        json={"agent_id": "cursor-machine-a", "claim_token": claimed.json()["claim_token"]},
    )
    assert stolen.status_code == 403
    assert stolen.json()["error"]["code"] == "NOT_OWNER"


def test_idempotency_replay_and_conflict(client, csrf):
    token = create_agent(client, csrf)
    ready_task(client, csrf, "TS-130", ["path"])
    body = {"agent_id": "codex-machine-a", "branch_name": "task/TS-130", "continue_from": []}
    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": "same-key"}
    first = client.post("/api/v1/tasks/TS-130/claim", headers=headers, json=body)
    second = client.post("/api/v1/tasks/TS-130/claim", headers=headers, json=body)
    assert first.status_code == 200 and second.status_code == 200
    assert first.json() == second.json()
    changed = client.post(
        "/api/v1/tasks/TS-130/claim",
        headers=headers,
        json={**body, "branch_name": "task/other"},
    )
    assert changed.status_code == 409
    assert changed.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    events = client.get("/api/v1/tasks/TS-130/events").json()["events"]
    assert len([event for event in events if event["event_type"] == "task.claimed"]) == 1


def test_release_takeover_and_block_are_audited(client, csrf):
    token = create_agent(client, csrf)
    other = create_agent(client, csrf, "cursor-machine-a", "cursor")
    ready_task(client, csrf, "TS-180", ["audit"])
    claimed = claim(client, token, "TS-180", "task/TS-180")
    assert claimed.status_code == 200, claimed.text
    released = client.post(
        "/api/v1/tasks/TS-180/release",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "rel-180"},
        json={"agent_id": "codex-machine-a", "claim_token": claimed.json()["claim_token"], "reason": "需求需要重新确认"},
    )
    assert released.status_code == 200, released.text
    assert released.json()["task"]["status"] == "ready"
    assert released.json()["task"]["owner"] is None
    claimed = claim(client, token, "TS-180", "task/TS-180-again")
    assert claimed.status_code == 200, claimed.text
    takeover = admin(
        client,
        csrf,
        "POST",
        "/api/v1/tasks/TS-180/takeover",
        json={"agent_id": "cursor-machine-a", "reason": "原负责人中断"},
    )
    assert takeover.status_code == 200, takeover.text
    assert takeover.json()["task"]["owner"] == "cursor-machine-a"
    assert takeover.json()["task"]["claim_reissue_pending"] is True
    assert "claim_token" not in takeover.json()
    reissued = client.post(
        "/api/v1/tasks/TS-180/reissue-claim-token",
        headers={"Authorization": f"Bearer {other}", "Idempotency-Key": "reissue-180"},
        json={},
    )
    assert reissued.status_code == 200, reissued.text
    blocked = admin(client, csrf, "POST", "/api/v1/tasks/TS-180/block", json={"reason": "等待外部决定"})
    assert blocked.status_code == 200, blocked.text
    assert blocked.json()["task"]["status"] == "blocked"
    kinds = {event["event_type"] for event in client.get("/api/v1/tasks/TS-180/events").json()["events"]}
    assert {"task.released", "task.taken_over", "task.claim_token_reissued", "task.blocked"} <= kinds


def test_lease_expiry_releases_owner(client, csrf):
    from datetime import timedelta

    from sqlalchemy import select

    from taskcoord.clock import utcnow
    from taskcoord.database import session_scope
    from taskcoord.models import Task
    from taskcoord.services.lease_service import expire_due

    token = create_agent(client, csrf)
    create_agent(client, csrf, "cursor-machine-a", "cursor")
    ready_task(client, csrf, "TS-140", ["lease"])
    claimed = claim(client, token, "TS-140", "task/TS-140")
    assert claimed.status_code == 200
    with session_scope(client.app.state.session_factory) as session:
        task = session.scalar(select(Task).where(Task.id == "TS-140"))
        task.lease_expires_at = utcnow() - timedelta(minutes=1)
    with session_scope(client.app.state.session_factory) as session:
        expired = expire_due(session, utcnow())
    assert expired == ["TS-140"]
    other = client.post(
        "/api/v1/tasks/TS-140/claim",
        headers={"Authorization": "Bearer " + _token_for(client, csrf, "cursor-machine-a"), "Idempotency-Key": "after-expiry"},
        json={"agent_id": "cursor-machine-a", "branch_name": "task/TS-140-b", "continue_from": []},
    )
    assert other.status_code == 200, other.text
    assert other.json()["task"]["owner"] == "cursor-machine-a"


def _token_for(client, csrf, agent_id):
    listed = client.get("/api/v1/agents")
    assert any(agent["id"] == agent_id for agent in listed.json()["agents"])
    rotated = admin(client, csrf, "POST", f"/api/v1/agents/{agent_id}/rotate-token")
    assert rotated.status_code == 200, rotated.text
    return rotated.json()["api_token"]
