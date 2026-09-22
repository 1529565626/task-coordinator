import threading
import uuid

import httpx

from tests.conftest import ADMIN_PASSWORD


def _login(base):
    response = httpx.post(base + "/api/v1/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD}, timeout=10)
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"], response.cookies


def _admin(base, csrf, cookies, method, path, **kwargs):
    headers = {"X-CSRF-Token": csrf, "Idempotency-Key": str(uuid.uuid4())}
    response = httpx.request(method, base + path, headers=headers, cookies=cookies, timeout=20, **kwargs)
    assert response.status_code == 200, response.text
    return response


def _agent(base, csrf, cookies, agent_id, client_type):
    return _admin(
        base,
        csrf,
        cookies,
        "POST",
        "/api/v1/agents",
        json={"id": agent_id, "display_name": agent_id, "machine_name": "box", "client_type": client_type},
    ).json()["api_token"]


def _ready(base, csrf, cookies, task_id, scopes, project="map-build"):
    created = httpx.post(
        base + "/api/v1/projects",
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": str(uuid.uuid4())},
        cookies=cookies,
        json={"key": project, "name": project, "description": ""},
        timeout=20,
    )
    assert created.status_code in {200, 409}
    _admin(
        base,
        csrf,
        cookies,
        "POST",
        "/api/v1/tasks",
        json={"id": task_id, "project_key": project, "title": task_id, "description": "", "priority": 10, "scopes": scopes},
    )
    _admin(base, csrf, cookies, "POST", f"/api/v1/tasks/{task_id}/confirm", json={})


def _claim(base, token, agent_id, task_id, key=None):
    return httpx.post(
        base + f"/api/v1/tasks/{task_id}/claim",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": key or str(uuid.uuid4())},
        json={"agent_id": agent_id, "branch_name": f"task/{task_id}", "continue_from": []},
        timeout=20,
    )


def test_twenty_clients_cannot_double_claim(live_server):
    base = live_server
    csrf, cookies = _login(base)
    token = _agent(base, csrf, cookies, "codex-machine-a", "codex")
    _ready(base, csrf, cookies, "TS-200", ["arena"])
    results = []

    def once():
        results.append(_claim(base, token, "codex-machine-a", "TS-200"))

    threads = [threading.Thread(target=once) for _ in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    codes = sorted(response.status_code for response in results)
    assert codes.count(200) == 1, [(response.status_code, response.text) for response in results]
    assert codes.count(409) == 19
    owners = {response.json().get("task", {}).get("owner") for response in results if response.status_code == 200}
    assert owners == {"codex-machine-a"}


def test_same_scope_loses_and_distinct_scope_wins(live_server):
    base = live_server
    csrf, cookies = _login(base)
    token_a = _agent(base, csrf, cookies, "codex-machine-a", "codex")
    token_b = _agent(base, csrf, cookies, "cursor-machine-b", "cursor")
    _ready(base, csrf, cookies, "TS-210", ["shared-scope"])
    _ready(base, csrf, cookies, "TS-211", ["shared-scope"])
    _ready(base, csrf, cookies, "TS-212", ["other-scope"])
    pair = []

    def claim_211():
        pair.append(_claim(base, token_b, "cursor-machine-b", "TS-211"))

    def claim_210():
        pair.append(_claim(base, token_a, "codex-machine-a", "TS-210"))

    threads = [threading.Thread(target=claim_210), threading.Thread(target=claim_211)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(response.status_code for response in pair) == [200, 409]
    other = _claim(base, token_b, "cursor-machine-b", "TS-212")
    assert other.status_code == 200, other.text


def test_same_idempotency_key_creates_one_event(live_server):
    base = live_server
    csrf, cookies = _login(base)
    token = _agent(base, csrf, cookies, "codex-machine-a", "codex")
    _ready(base, csrf, cookies, "TS-220", ["once"])
    results = []

    def once():
        results.append(_claim(base, token, "codex-machine-a", "TS-220", key="shared-attempt"))

    threads = [threading.Thread(target=once) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert {response.status_code for response in results} == {200}
    assert len({response.json()["request_id"] for response in results}) == 1
    events = httpx.get(base + "/api/v1/tasks/TS-220/events", timeout=10).json()["events"]
    assert len([event for event in events if event["event_type"] == "task.claimed"]) == 1
