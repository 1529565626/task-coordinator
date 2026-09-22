from __future__ import annotations

import json
import os
import sys
import tomllib
import uuid
from pathlib import Path

import httpx

from taskcoord.security import redact_text


class TaskctlError(Exception):
    def __init__(self, message: str, code: int = 1):
        super().__init__(message)
        self.code = code


def home_dir() -> Path:
    override = os.environ.get("TASKCOORD_HOME")
    path = Path(override) if override else Path.home() / ".taskcoord"
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_config() -> dict:
    path = home_dir() / "config.toml"
    data = tomllib.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    token = os.environ.get("TASKCOORD_API_TOKEN") or _read_secret("token")
    return {
        "server_url": (os.environ.get("TASKCOORD_SERVER") or data.get("server_url") or "http://127.0.0.1:8787").rstrip("/"),
        "agent_id": os.environ.get("TASKCOORD_AGENT_ID") or data.get("agent_id") or "",
        "project_key": os.environ.get("TASKCOORD_PROJECT_KEY") or data.get("project_key") or "",
        "token": token,
    }


def _read_secret(name: str) -> str:
    path = home_dir() / name
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8").strip()


def _claims() -> dict:
    path = home_dir() / "claims.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_claims(data: dict) -> None:
    path = home_dir() / "claims.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _claim_slot(config: dict, task_id: str) -> str:
    return f"{config['server_url']}|{config['agent_id']}|{task_id}"


def _store_claim(config: dict, task_id: str, token: str) -> None:
    data = _claims()
    data[_claim_slot(config, task_id)] = token
    _save_claims(data)


def _pop_claim(config: dict, task_id: str) -> None:
    data = _claims()
    data.pop(_claim_slot(config, task_id), None)
    _save_claims(data)


def _stored_claim(config: dict, task_id: str) -> str:
    return _claims().get(_claim_slot(config, task_id), "")


def _print(payload: dict, as_json: bool, summary: str) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(summary)


def _client(config: dict) -> httpx.Client:
    headers = {}
    if config.get("token"):
        headers["Authorization"] = f"Bearer {config['token']}"
    return httpx.Client(base_url=config["server_url"], headers=headers, timeout=10)


def _request(config: dict, method: str, path: str, *, body: dict | None = None, key: str | None = None, admin: bool = False) -> httpx.Response:
    headers = {}
    if key:
        headers["Idempotency-Key"] = key
    cookies = None
    if admin:
        session = _admin_session()
        headers["X-CSRF-Token"] = session.get("csrf_token", "")
        cookies = {"taskcoord_session": session.get("cookie", "")}
    elif config.get("token"):
        headers["Authorization"] = f"Bearer {config['token']}"
    try:
        with httpx.Client(base_url=config["server_url"], headers=headers, cookies=cookies, timeout=10) as client:
            return client.request(method, path, json=body)
    except httpx.HTTPError as exc:
        raise TaskctlError("服务不可用，已停止。不要把本地缓存当作认领成功。", 3) from exc


def ensure_ready(config: dict) -> None:
    try:
        with _client(config) as client:
            response = client.get("/api/v1/health/ready")
    except httpx.HTTPError as exc:
        raise TaskctlError("服务不可用，已停止认领。", 3) from exc
    if response.status_code != 200:
        raise TaskctlError("服务未就绪，已停止认领。", 3)


def _admin_session() -> dict:
    path = home_dir() / "admin.json"
    if not path.is_file():
        raise TaskctlError("请先执行 taskctl admin login", 2)
    return json.loads(path.read_text(encoding="utf-8"))


def _require_agent(config: dict) -> str:
    if not config["agent_id"] or not config["token"]:
        raise TaskctlError("缺少 agent_id 或 TASKCOORD_API_TOKEN", 2)
    return config["agent_id"]


def cmd_health(args, config) -> None:
    response = _request(config, "GET", "/api/v1/health/ready")
    payload = response.json()
    if response.status_code != 200:
        raise TaskctlError(payload.get("error", {}).get("message", "服务未就绪"), 3)
    _print(payload, args.json, "ready")


def cmd_next(args, config) -> None:
    ensure_ready(config)
    project = args.project or config["project_key"]
    if not project:
        raise TaskctlError("缺少 project", 2)
    response = _request(config, "GET", f"/api/v1/tasks/next?project={project}")
    payload = response.json()
    task = payload.get("task")
    summary = "没有可认领任务" if not task else f"{task['id']} {task['title']} v{task['version']}"
    _print(payload, args.json, summary)


def cmd_show(args, config) -> None:
    ensure_ready(config)
    headers_claim = _stored_claim(config, args.task_id)
    with _client(config) as client:
        headers = {"X-Claim-Token": headers_claim} if headers_claim else {}
        try:
            response = client.get(f"/api/v1/tasks/{args.task_id}", headers=headers)
        except httpx.HTTPError as exc:
            raise TaskctlError("服务不可用，不能确认任务状态。", 3) from exc
    payload = response.json()
    if response.status_code != 200:
        raise TaskctlError(payload.get("error", {}).get("code", "请求失败"), response.status_code)
    task = payload["task"]
    _print(payload, args.json, f"{task['id']} {task['status']} owner={task.get('owner')} v{task['version']}")


def cmd_list(args, config) -> None:
    ensure_ready(config)
    params = []
    if args.project or config["project_key"]:
        params.append("project=" + (args.project or config["project_key"]))
    if args.status:
        params.append("status=" + args.status)
    response = _request(config, "GET", "/api/v1/tasks" + (("?" + "&".join(params)) if params else ""))
    payload = response.json()
    lines = [f"{task['id']} {task['status']} {task.get('owner') or '-'}" for task in payload.get("tasks", [])]
    _print(payload, args.json, "\n".join(lines) or "空")


def cmd_claim(args, config) -> None:
    ensure_ready(config)
    agent_id = _require_agent(config)
    key = args.idempotency_key or str(uuid.uuid4())
    body = {"agent_id": agent_id, "branch_name": args.branch, "continue_from": args.continue_from or []}
    response = _request(config, "POST", f"/api/v1/tasks/{args.task_id}/claim", body=body, key=key)
    payload = response.json()
    if response.status_code != 200:
        code = payload.get("error", {}).get("code", "CLAIM_FAILED")
        raise TaskctlError(f"{code} key={key}", 4 if response.status_code == 409 else 1)
    _store_claim(config, args.task_id, payload["claim_token"])
    task = payload["task"]
    public = {"request_id": payload["request_id"], "task": task, "claim_token_saved": True}
    _print(public if not args.json else payload, args.json, f"claimed {task['id']} v{task['version']}")


def cmd_heartbeat(args, config) -> None:
    ensure_ready(config)
    _owner_post(config, args, "heartbeat", {})


def cmd_release(args, config) -> None:
    ensure_ready(config)
    _owner_post(config, args, "release", {"reason": args.reason})
    _pop_claim(config, args.task_id)


def cmd_deliver(args, config) -> None:
    ensure_ready(config)
    agent_id = _require_agent(config)
    token = _stored_claim(config, args.task_id)
    if not token:
        raise TaskctlError("本地没有 claim token，已停止交付。", 2)
    key = args.idempotency_key or str(uuid.uuid4())
    body = {
        "agent_id": agent_id,
        "claim_token": token,
        "branch_name": args.branch,
        "commit": args.commit,
        "tests": args.tests,
        "notes": args.notes or "",
    }
    response = _request(config, "POST", f"/api/v1/tasks/{args.task_id}/deliver", body=body, key=key)
    payload = response.json()
    if response.status_code != 200:
        raise TaskctlError(payload.get("error", {}).get("code", "DELIVER_FAILED"), 1)
    _pop_claim(config, args.task_id)
    task = payload["task"]
    _print(payload, args.json, f"review {task['id']} {task.get('delivery_commit')}")


def cmd_reissue(args, config) -> None:
    ensure_ready(config)
    _require_agent(config)
    key = args.idempotency_key or str(uuid.uuid4())
    response = _request(config, "POST", f"/api/v1/tasks/{args.task_id}/reissue-claim-token", body={}, key=key)
    payload = response.json()
    if response.status_code != 200:
        raise TaskctlError(payload.get("error", {}).get("code", "REISSUE_FAILED"), 1)
    _store_claim(config, args.task_id, payload["claim_token"])
    _print(payload, args.json, f"claim token 已保存 {args.task_id}")


def _owner_post(config: dict, args, action: str, extra: dict) -> None:
    agent_id = _require_agent(config)
    token = _stored_claim(config, args.task_id)
    if not token:
        raise TaskctlError("本地没有 claim token，已停止。", 2)
    key = args.idempotency_key or str(uuid.uuid4())
    body = {"agent_id": agent_id, "claim_token": token, **extra}
    response = _request(config, "POST", f"/api/v1/tasks/{args.task_id}/{action}", body=body, key=key)
    payload = response.json()
    if response.status_code != 200:
        raise TaskctlError(payload.get("error", {}).get("code", "REQUEST_FAILED"), 1)
    task = payload["task"]
    _print(payload, args.json, f"{action} {task['id']} {task['status']} v{task['version']}")


def cmd_admin_login(args, config) -> None:
    password = os.environ.get("TASKCOORD_ADMIN_PASSWORD", "")
    if not password:
        raise TaskctlError("请通过环境变量 TASKCOORD_ADMIN_PASSWORD 提供管理员密码", 2)
    try:
        with httpx.Client(base_url=config["server_url"], timeout=10) as client:
            response = client.post("/api/v1/auth/login", json={"username": args.username, "password": password})
    except httpx.HTTPError as exc:
        raise TaskctlError("服务不可用，登录未完成。", 3) from exc
    if response.status_code != 200:
        raise TaskctlError("登录失败", 1)
    cookie = response.cookies.get("taskcoord_session", "")
    (home_dir() / "admin.json").write_text(
        json.dumps({"cookie": cookie, "csrf_token": response.json()["csrf_token"]}),
        encoding="utf-8",
    )
    print("admin session saved")


def cmd_admin_create_agent(args, config) -> None:
    key = args.idempotency_key or str(uuid.uuid4())
    body = {"id": args.id, "display_name": args.name, "machine_name": args.machine, "client_type": args.client}
    response = _request(config, "POST", "/api/v1/agents", body=body, key=key, admin=True)
    payload = response.json()
    if response.status_code != 200:
        raise TaskctlError(payload.get("error", {}).get("code", "AGENT_CREATE_FAILED"), 1)
    _print(payload, True, "")


def cmd_admin_backup(args, config) -> None:
    key = args.idempotency_key or str(uuid.uuid4())
    response = _request(config, "POST", "/api/v1/admin/backup", body={}, key=key, admin=True)
    payload = response.json()
    if response.status_code != 200:
        raise TaskctlError(payload.get("error", {}).get("code", "BACKUP_FAILED"), 1)
    _print(payload, args.json, payload.get("backup", {}).get("path", "backup ok"))


def cmd_admin_restore(args, config) -> None:
    if not args.dry_run:
        raise TaskctlError("恢复必须先带 --dry-run 检查。真正替换数据库前要先停止服务。", 2)
    from taskcoord.services.backup_service import inspect_backup

    report = inspect_backup(Path(args.file))
    _print(report, args.json, f"integrity={report['integrity']} tasks={report['task_count']}")
    if report["integrity"] != "ok":
        raise TaskctlError("备份完整性检查失败", 1)


def build_parser():
    parser = argparse_parser()
    parser.add_argument("--json", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    health = sub.add_parser("health")
    health.set_defaults(func=cmd_health)

    nxt = sub.add_parser("next")
    nxt.add_argument("--project")
    nxt.set_defaults(func=cmd_next)

    show = sub.add_parser("show")
    show.add_argument("task_id")
    show.set_defaults(func=cmd_show)

    listing = sub.add_parser("list")
    listing.add_argument("--project")
    listing.add_argument("--status")
    listing.set_defaults(func=cmd_list)

    claim = sub.add_parser("claim")
    claim.add_argument("task_id")
    claim.add_argument("--branch", required=True)
    claim.add_argument("--continue-from", dest="continue_from", action="append")
    claim.add_argument("--idempotency-key")
    claim.set_defaults(func=cmd_claim)

    for name, func, extra in (
        ("heartbeat", cmd_heartbeat, False),
        ("release", cmd_release, True),
    ):
        command = sub.add_parser(name)
        command.add_argument("task_id")
        command.add_argument("--idempotency-key")
        if extra:
            command.add_argument("--reason", required=True)
        command.set_defaults(func=func)

    deliver = sub.add_parser("deliver")
    deliver.add_argument("task_id")
    deliver.add_argument("--branch", required=True)
    deliver.add_argument("--commit", required=True)
    deliver.add_argument("--tests", required=True)
    deliver.add_argument("--notes", default="")
    deliver.add_argument("--idempotency-key")
    deliver.set_defaults(func=cmd_deliver)

    reissue = sub.add_parser("reissue")
    reissue.add_argument("task_id")
    reissue.add_argument("--idempotency-key")
    reissue.set_defaults(func=cmd_reissue)

    admin = sub.add_parser("admin")
    admin_sub = admin.add_subparsers(dest="admin_command", required=True)
    login = admin_sub.add_parser("login")
    login.add_argument("--username", default="admin")
    login.set_defaults(func=cmd_admin_login)
    create_agent = admin_sub.add_parser("create-agent")
    create_agent.add_argument("--id", required=True)
    create_agent.add_argument("--name", required=True)
    create_agent.add_argument("--machine", default="")
    create_agent.add_argument("--client", required=True)
    create_agent.add_argument("--idempotency-key")
    create_agent.set_defaults(func=cmd_admin_create_agent)
    backup = admin_sub.add_parser("backup")
    backup.add_argument("--idempotency-key")
    backup.set_defaults(func=cmd_admin_backup)
    restore = admin_sub.add_parser("restore")
    restore.add_argument("--file", required=True)
    restore.add_argument("--dry-run", action="store_true")
    restore.set_defaults(func=cmd_admin_restore)
    return parser


def argparse_parser():
    import argparse

    return argparse.ArgumentParser(prog="taskctl")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = load_config()
    try:
        args.func(args, config)
    except TaskctlError as exc:
        print(redact_text(str(exc)), file=sys.stderr)
        return exc.code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
