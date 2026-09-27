"""Non-secret, operator-owned bindings for Tally.

Copied from ``airbrx/eva/config.py`` on purpose, field for field, so the two
agents are configured, authorized and reasoned about the same way. Read that
module's docstring for why ``host_id`` exists and why it is optional.

Tally's one tool server is the Airbrx portal sidecar's MCP endpoint, which runs
on the coordinator at ``http://127.0.0.1:4318/mcp``. So ``base_url`` is normally
the coordinator's own loopback, and the readiness route can probe it directly.

**Where her turns run.** A binding with no ``host_id`` is the shape Abram chose:
Tally belongs next to the portal, on the coordinator. What that means in this
code, verified rather than assumed, is written up in docs/tally/RUNBOOK.md
("Where Tally runs"). In short: a session created with no host and no managed
sandbox has no runner bound to it, and ``launch_env`` is only applied when a
host launches a runner. Naming the coordinator's own execution host in
``host_id`` is the shape that runs turns today.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

_ALLOWED = {"users", "host_id", "base_url", "token_ref", "label", "fixture", "workspace"}

#: The environment variable naming the JSON bindings file.
CONFIG_ENV = "OMNIGENT_TALLY_CONFIG"


@dataclass(frozen=True)
class Binding:
    #: Omnigent user ids allowed to open this Tally. The authorization decision.
    users: tuple[str, ...]
    #: The execution host that runs Tally's turns, from ``/v1/hosts``, or empty.
    #: Same meaning as Eva's field: it says whose loopback ``base_url`` is.
    host_id: str
    #: The portal sidecar as the runner sees it. Normally
    #: ``http://127.0.0.1:4318``, the coordinator's own loopback.
    base_url: str
    #: How to resolve the portal MCP bearer token, e.g.
    #: ``env:AIRBRX_TALLY_MCP_TOKEN``. Resolved on the host that launches the
    #: runner, never by the coordinator's routes. Empty for a fixture binding.
    token_ref: str
    #: What the workspace calls this binding when more than one exists.
    label: str = "live"
    #: A visibly synthetic binding for acceptance runs. Never real data.
    fixture: bool = False
    #: Absolute directory on the execution host for the runner's working
    #: directory. Required to create a session on a host, as Eva's is.
    workspace: str = ""

    def mcp_url(self) -> str:
        """The portal's MCP endpoint: exactly ``/mcp``, no trailing slash.

        The opposite of Eva's outreach app, which mounts at ``/mcp/``. The
        portal answers POST JSON-RPC at ``/mcp`` and 405 to a GET there, so a
        trailing slash would be a different, unmounted path.
        """
        return self.base_url.rstrip("/") + "/mcp"

    def is_host_local(self) -> bool:
        """Is ``base_url`` only meaningful on a different execution host?

        The same rule as Eva's: a loopback address belongs to another machine
        only when ``host_id`` names one. Without a host the loopback is the
        coordinator's own and the readiness probe is the real answer.
        """
        if not self.host_id:
            return False
        return any(h in self.base_url for h in ("127.0.0.1", "localhost", "::1"))


def bindings() -> tuple[Binding, ...]:
    """Parse ``OMNIGENT_TALLY_CONFIG``. Absent means Tally is entirely inert.

    Unknown keys are rejected rather than ignored, as Iris and Eva do: a typo
    in an authorization file must stop startup, not silently widen or narrow
    who can open the agent.
    """
    path = os.environ.get(CONFIG_ENV)
    if not path:
        return ()
    rows = json.loads(Path(path).read_text())
    if not isinstance(rows, list):
        raise ValueError("Tally config must be a list of bindings")
    result: list[Binding] = []
    for row in rows:
        unknown = set(row) - _ALLOWED
        if unknown:
            raise ValueError(f"Unknown Tally binding setting: {', '.join(sorted(unknown))}")
        users = tuple(row["users"])
        if not users:
            raise ValueError("A Tally binding with no users can be opened by nobody")
        host_id = str(row.get("host_id", "")).strip()
        base_url = str(row["base_url"]).strip()
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("Tally binding base_url must be an absolute http(s) URL")
        fixture = bool(row.get("fixture", False))
        token_ref = str(row.get("token_ref", ""))
        if not fixture and not token_ref:
            raise ValueError("A live Tally binding needs a token_ref")
        workspace = str(row.get("workspace", "")).strip()
        if workspace and not workspace.startswith("/"):
            raise ValueError("Tally binding workspace must be an absolute path on the host")
        result.append(
            Binding(
                users=users,
                host_id=host_id,
                base_url=base_url,
                token_ref=token_ref,
                label=str(row.get("label", "live")),
                fixture=fixture,
                workspace=workspace,
            )
        )
    labels = [b.label for b in result]
    if len(set(labels)) != len(labels):
        raise ValueError("Tally bindings must have distinct labels")
    return tuple(result)


def binding_for(user_id: str, label: str | None = None) -> Binding | None:
    """The binding this user may open, or None.

    Exactly one match or nothing, the same rule as Eva's: two bindings for one
    user make the selection ambiguous rather than redundant.
    """
    candidates = [b for b in bindings() if user_id in b.users]
    if label is not None:
        candidates = [b for b in candidates if b.label == label]
    if len(candidates) != 1:
        return None
    return candidates[0]
