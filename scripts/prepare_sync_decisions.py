"""Build owner_map and claimed_resolutions for tasks.json import. Read-only on source."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

_AGENT = re.compile(r"^[a-z0-9][a-z0-9-]{0,78}$")


def legacy_to_agent_id(owner: str) -> str:
    value = owner.strip().lower().replace("_", "-")
    if not _AGENT.fullmatch(value):
        raise ValueError(f"cannot map owner to agent id: {owner!r}")
    if value in {"codex", "cursor", "agent"}:
        value = f"legacy-{value}"
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument(
        "--claimed",
        choices=("restore", "ready", "review"),
        default="restore",
        help="How to import tasks that are still claimed in tasks.json",
    )
    args = parser.parse_args()
    raw = Path(args.source).read_bytes()
    document = json.loads(raw.decode("utf-8-sig"))
    tasks = document["tasks"]
    owners = sorted({t.get("owner") for t in tasks if t.get("owner")})
    owner_map = {owner: legacy_to_agent_id(owner) for owner in owners}
    claimed_resolutions = {
        t["id"]: args.claimed for t in tasks if t.get("status") == "claimed"
    }
    payload = {
        "owner_map": owner_map,
        "claimed_resolutions": claimed_resolutions,
        "meta": {
            "source": str(Path(args.source).resolve()),
            "owner_count": len(owner_map),
            "claimed_count": len(claimed_resolutions),
            "claimed_policy": args.claimed,
        },
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload["meta"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
