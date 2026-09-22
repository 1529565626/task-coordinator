"""One-shot sync: start from empty DB, create agents, preview + commit import."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from taskcoord.security import redact_text  # noqa: E402

SOURCE_DEFAULT = Path(r"A:\godot\map_build\TinySwordsPM\data\tasks.json")


def _home() -> Path:
    override = os.environ.get("TASKCOORD_HOME")
    return Path(override) if override else Path.home() / ".taskcoord"


def _server() -> str:
    return (os.environ.get("TASKCOORD_SERVER") or "http://127.0.0.1:8787").rstrip("/")


def _ensure_admin_login() -> None:
    session_path = _home() / "admin.json"
    if session_path.is_file():
        return
    password = os.environ.get("TASKCOORD_ADMIN_PASSWORD", "").strip()
    if not password:
        bootstrap_file = ROOT / "data" / "migration" / ".initial-admin-password"
        if bootstrap_file.is_file():
            password = bootstrap_file.read_text(encoding="ascii").strip()
    if not password:
        raise RuntimeError("缺少 admin 会话。请设置 TASKCOORD_ADMIN_PASSWORD、先 taskctl admin login，或重新 bootstrap")
    body = json.dumps({"username": os.environ.get("TASKCOORD_ADMIN_USERNAME", "admin"), "password": password}).encode("utf-8")
    request = urllib.request.Request(
        _server() + "/api/v1/auth/login",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    cookie = ""
    for header in response.headers.get_all("Set-Cookie") or []:
        if header.startswith("taskcoord_session="):
            cookie = header.split(";", 1)[0].split("=", 1)[1]
            break
    if not cookie:
        raise RuntimeError("登录成功但未收到 session cookie")
    _home().mkdir(parents=True, exist_ok=True)
    session_path.write_text(json.dumps({"cookie": cookie, "csrf_token": payload["csrf_token"]}, ensure_ascii=False), encoding="utf-8")


def _admin_request(method: str, path: str, body: bytes | None = None, content_type: str = "application/json") -> dict:
    session_path = _home() / "admin.json"
    if not session_path.is_file():
        raise RuntimeError("缺少 admin 会话，请先 taskctl admin login")
    session = json.loads(session_path.read_text(encoding="utf-8"))
    headers = {
        "X-CSRF-Token": session["csrf_token"],
        "Cookie": f"taskcoord_session={session['cookie']}",
    }
    if body is not None:
        headers["Content-Type"] = content_type
        headers["Idempotency-Key"] = hashlib.sha256(body).hexdigest()[:32]
    request = urllib.request.Request(_server() + path, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(detail) from exc


def _ensure_agents(owner_map: dict[str, str]) -> None:
    existing = {row["id"] for row in _admin_request("GET", "/api/v1/agents").get("agents", [])}
    for legacy, agent_id in sorted(owner_map.items(), key=lambda item: item[1]):
        if agent_id in existing:
            continue
        client = "cursor" if agent_id == "sasaki" else "codex"
        body = json.dumps(
            {
                "id": agent_id,
                "display_name": legacy,
                "machine_name": "imported",
                "client_type": client,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        _admin_request("POST", "/api/v1/agents", body)
        existing.add(agent_id)
        print(f"agent created: {agent_id} ({legacy})")


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Sync TinySwordsPM tasks.json into Task Coordinator.")
    parser.add_argument("--source", type=Path, default=SOURCE_DEFAULT)
    parser.add_argument("--decisions", type=Path, default=ROOT / "data" / "migration" / "sync-decisions.json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if not args.source.is_file():
        print(redact_text(f"找不到源文件: {args.source}"), file=sys.stderr)
        return 2
    if not args.decisions.is_file():
        print(f"缺少决策文件，请先运行: python scripts/prepare_sync_decisions.py --source {args.source} --out {args.decisions}", file=sys.stderr)
        return 2

    try:
        health = urllib.request.urlopen(_server() + "/api/v1/health/ready", timeout=10)
        if health.status != 200:
            print("服务未就绪", file=sys.stderr)
            return 3
    except Exception as exc:
        print(redact_text(f"服务不可用: {exc}"), file=sys.stderr)
        return 3

    _ensure_admin_login()

    decisions = json.loads(args.decisions.read_text(encoding="utf-8"))
    owner_map = decisions["owner_map"]
    claimed_resolutions = decisions["claimed_resolutions"]

    _ensure_agents(owner_map)

    raw = args.source.read_bytes()
    preview = _admin_request("POST", "/api/v1/admin/import/preview", raw, "application/json")
    report = preview["report"]
    print(json.dumps({"preview": report}, ensure_ascii=False, indent=2))
    if args.dry_run:
        return 0

    pending = report.get("claimed_pending") or []
    missing = [task_id for task_id in pending if claimed_resolutions.get(task_id) not in {"restore", "ready", "review"}]
    if missing:
        print("claimed 任务缺少决策: " + ", ".join(missing[:20]), file=sys.stderr)
        return 2

    commit_body = json.dumps(
        {
            "import_id": report["import_id"],
            "owner_map": owner_map,
            "claimed_resolutions": claimed_resolutions,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    result = _admin_request("POST", "/api/v1/admin/import/commit", commit_body)
    print(json.dumps(result, ensure_ascii=False, indent=2))

    tasks = _admin_request("GET", "/api/v1/tasks?limit=1000")
    by_status: dict[str, int] = {}
    for task in tasks.get("tasks", []):
        by_status[task["status"]] = by_status.get(task["status"], 0) + 1
    print(json.dumps({"imported_task_count": len(tasks.get("tasks", [])), "by_status": by_status}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
