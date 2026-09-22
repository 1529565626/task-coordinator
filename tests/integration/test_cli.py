import json

from taskcoord.cli.taskctl import main


def test_cli_fail_closed_without_server(tmp_path, monkeypatch):
    monkeypatch.setenv("TASKCOORD_HOME", str(tmp_path))
    monkeypatch.setenv("TASKCOORD_SERVER", "http://127.0.0.1:9")
    monkeypatch.setenv("TASKCOORD_AGENT_ID", "codex-machine-a")
    monkeypatch.setenv("TASKCOORD_API_TOKEN", "not-a-real-token")
    code = main(["claim", "TS-100", "--branch", "task/TS-100"])
    assert code == 3
    assert not (tmp_path / "claims.json").exists()


def test_cli_claim_deliver_json(live_server, tmp_path, monkeypatch):
    import httpx

    from tests.concurrency.test_claim_race import _admin, _agent, _login, _ready

    csrf, cookies = _login(live_server)
    token = _agent(live_server, csrf, cookies, "codex-machine-a", "codex")
    _ready(live_server, csrf, cookies, "TS-230", ["cli"])
    monkeypatch.setenv("TASKCOORD_HOME", str(tmp_path))
    monkeypatch.setenv("TASKCOORD_SERVER", live_server)
    monkeypatch.setenv("TASKCOORD_AGENT_ID", "codex-machine-a")
    monkeypatch.setenv("TASKCOORD_API_TOKEN", token)
    monkeypatch.setenv("TASKCOORD_PROJECT_KEY", "map-build")
    assert main(["--json", "health"]) == 0
    assert main(["--json", "next", "--project", "map-build"]) == 0
    assert main(["--json", "claim", "TS-230", "--branch", "task/TS-230-example"]) == 0
    stored = json.loads((tmp_path / "claims.json").read_text(encoding="utf-8"))
    assert any(value for value in stored.values())
    assert main(["--json", "heartbeat", "TS-230"]) == 0
    commit = "d" * 40
    assert main(["--json", "deliver", "TS-230", "--branch", "task/TS-230-example", "--commit", commit, "--tests", "28/28 passed"]) == 0
    assert json.loads((tmp_path / "claims.json").read_text(encoding="utf-8")) == {}
    shown = httpx.get(live_server + "/api/v1/tasks/TS-230", timeout=10)
    body = shown.json()["task"]
    assert body["status"] == "review"
    assert body["delivery_commit"] == commit
    listed = main(["--json", "list", "--status", "review"])
    assert listed == 0
    monkeypatch.setenv("TASKCOORD_ADMIN_PASSWORD", "test-admin-password")
    assert main(["admin", "login"]) == 0
    backup_code = main(["--json", "admin", "backup"])
    assert backup_code == 0
