from __future__ import annotations

from dataclasses import dataclass

from taskcoord.models import Agent, Session as UserSession, User


@dataclass
class Actor:
    kind: str
    agent: Agent | None = None
    user: User | None = None
    session: UserSession | None = None

    @property
    def idempotency_id(self) -> tuple[str, str]:
        if self.agent is not None:
            return "agent", self.agent.id
        if self.user is not None:
            return "user", str(self.user.id)
        return "anonymous", "anonymous"

    @property
    def label(self) -> str:
        if self.user is not None:
            return self.user.username
        if self.agent is not None:
            return self.agent.id
        return "anonymous"
