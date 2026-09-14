"""Non-secret, operator-owned tenant bindings shared by server and execution host."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Binding:
    tenant_id: str
    host_id: str
    workspace: str
    users: tuple[str, ...]
    pat_ref: str
    fixture: bool = False


def bindings() -> tuple[Binding, ...]:
    path = os.environ.get("OMNIGENT_IRIS_CONFIG")
    if not path:
        return ()
    rows = json.loads(Path(path).read_text())
    result = []
    for row in rows:
        if set(row) - {"tenant_id", "host_id", "workspace", "users", "pat_ref", "fixture"}:
            raise ValueError("Unknown Iris binding setting")
        binding = Binding(
            tenant_id=row["tenant_id"],
            host_id=row["host_id"],
            workspace=row["workspace"],
            users=tuple(row["users"]),
            pat_ref=row["pat_ref"],
            fixture=row.get("fixture", False),
        )
        if (
            not binding.tenant_id
            or not binding.host_id
            or not binding.users
            or not Path(binding.workspace).is_absolute()
            or type(binding.fixture) is not bool
            or (not binding.fixture and not binding.pat_ref.startswith("keychain:"))
        ):
            raise ValueError(
                "Iris requires an explicit tenant, host, workspace, users and secret reference"
            )
        if any(b.workspace == binding.workspace and b.host_id == binding.host_id for b in result):
            raise ValueError("An Iris host workspace must bind exactly one tenant")
        result.append(binding)
    return tuple(result)


def session_binding(session: dict, user: str | None = None) -> Binding:
    matches = [
        b
        for b in bindings()
        if b.host_id == session.get("host_id")
        and b.workspace == session.get("workspace")
        and (user is None or user in b.users)
    ]
    if len(matches) != 1:
        raise ValueError("This session has no authorized Iris tenant binding")
    return matches[0]
