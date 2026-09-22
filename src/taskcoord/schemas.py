from __future__ import annotations

from pydantic import BaseModel, Field


class ProjectIn(BaseModel):
    key: str
    name: str
    description: str = ""
    active: bool = True


class ProjectPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    active: bool | None = None


class TaskIn(BaseModel):
    id: str
    project_key: str
    title: str
    description: str = ""
    priority: int = 100
    scopes: list[str] = Field(default_factory=list)


class TaskPatch(BaseModel):
    version: int
    title: str | None = None
    description: str | None = None
    priority: int | None = None
    scopes: list[str] | None = None


class ReasonIn(BaseModel):
    reason: str


class RejectIn(BaseModel):
    reason: str
    return_to: str


class ClaimIn(BaseModel):
    agent_id: str
    branch_name: str
    continue_from: list[str] = Field(default_factory=list)


class TokenIn(BaseModel):
    agent_id: str = ""
    claim_token: str = ""
    reason: str = ""


class DeliverIn(BaseModel):
    agent_id: str
    claim_token: str
    branch_name: str
    commit: str
    tests: str
    notes: str = ""


class TakeoverIn(BaseModel):
    agent_id: str
    reason: str


class AgentIn(BaseModel):
    id: str
    display_name: str
    machine_name: str = ""
    client_type: str


class LoginIn(BaseModel):
    username: str
    password: str


class ImportCommitIn(BaseModel):
    import_id: str
    owner_map: dict[str, str] = Field(default_factory=dict)
    claimed_resolutions: dict[str, str] = Field(default_factory=dict)


class RestoreDryRunIn(BaseModel):
    path: str
