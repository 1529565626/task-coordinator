from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.error
import urllib.request

from taskcoord.security import redact_text


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Import TinySwordsPM tasks.json through the Task Coordinator API.")
    parser.add_argument("--source", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--commit", action="store_true")
    parser.add_argument("--decisions")
    parser.add_argument("--server")
    args = parser.parse_args(argv)
    if args.dry_run == args.commit:
        print("请只选择 --dry-run 或 --commit", file=sys.stderr)
        return 2
    source = open(args.source, "rb")
    raw = source.read()
    source.close()
    server = (args.server or os.environ.get("TASKCOORD_SERVER") or "http://127.0.0.1:8787").rstrip("/")
    try:
        report = _request(server, "POST", "/api/v1/admin/import/preview", raw, "application/json")
    except Exception as exc:
        print(redact_text(f"无法连接任务服务，导入已停止：{exc}"), file=sys.stderr)
        return 3
    print(json.dumps(report["report"], ensure_ascii=False, indent=2))
    if args.dry_run:
        return 0
    decisions = {"owner_map": {}, "claimed_resolutions": {}}
    if args.decisions:
        with open(args.decisions, "r", encoding="utf-8-sig") as handle:
            decisions.update(json.load(handle))
    pending = report["report"].get("claimed_pending") or []
    missing = [task_id for task_id in pending if decisions["claimed_resolutions"].get(task_id) not in {"restore", "ready", "review"}]
    if missing:
        print("claimed 任务还没有逐项决定：" + ", ".join(missing), file=sys.stderr)
        return 2
    result = _request(
        server,
        "POST",
        "/api/v1/admin/import/commit",
        json.dumps(
            {
                "import_id": report["report"]["import_id"],
                "owner_map": decisions["owner_map"],
                "claimed_resolutions": decisions["claimed_resolutions"],
            }
        ).encode("utf-8"),
        "application/json",
    )
    print(json.dumps(result["result"], ensure_ascii=False, indent=2))
    return 0


def _request(server: str, method: str, path: str, body: bytes, content_type: str) -> dict:
    home = os.environ.get("TASKCOORD_HOME") or os.path.join(os.path.expanduser("~"), ".taskcoord")
    admin_path = os.path.join(home, "admin.json")
    with open(admin_path, "r", encoding="utf-8") as handle:
        session = json.load(handle)
    request = urllib.request.Request(
        server + path,
        data=body,
        method=method,
        headers={
            "Content-Type": content_type,
            "X-CSRF-Token": session["csrf_token"],
            "Idempotency-Key": hashlib.sha256(body).hexdigest(),
            "Cookie": "taskcoord_session=" + session["cookie"],
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(detail) from exc


if __name__ == "__main__":
    raise SystemExit(main())
