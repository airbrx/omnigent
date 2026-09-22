"""Non-secret, operator-owned bindings for Eva.

Deliberately narrower than ``airbrx/iris/config.py``. Iris binds a tenant to an
execution *host*, because her tools are local Python that must run somewhere
specific with a PAT in that machine's keychain. Every one of Eva's tools is a
remote MCP call to the airbrx-outreach app, so there is nothing to place on a
host and no ``host_id`` here. If a ``host_id`` ever appears in this file, a local
tool has been added and Iris's whole host apparatus comes back with it.

What a binding carries: who may open Eva, where the outreach app is, and a
*reference* to the MCP bearer token. Never the token itself.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

_ALLOWED = {"users", "base_url", "token_ref", "label", "fixture"}


@dataclass(frozen=True)
class Binding:
    #: Omnigent user ids allowed to open this Eva. The authorization decision.
    users: tuple[str, ...]
    #: Base URL of the airbrx-outreach app, e.g. https://eva.airbrx.ai.
    #: ``/mcp`` is appended by :func:`mcp_url`; a binding naming the endpoint
    #: directly would make the two ways of writing it disagree eventually.
    base_url: str
    #: How to resolve the outreach MCP bearer token, e.g.
    #: ``keychain:eva-outreach-token`` or ``env:OUTREACH_MCP_TOKEN``. Resolved
    #: per invocation on the process that makes the call. Empty for a fixture.
    token_ref: str
    #: What the workspace calls this binding when more than one exists.
    label: str = "live"
    #: A visibly synthetic binding for acceptance runs. Never points at real data.
    fixture: bool = False

    def mcp_url(self) -> str:
        return self.base_url.rstrip("/") + "/mcp"


def bindings() -> tuple[Binding, ...]:
    """Parse ``OMNIGENT_EVA_CONFIG``. Absent means Eva is entirely inert.

    Unknown keys are rejected rather than ignored, the same way Iris does it: a
    typo in an operator's authorization file must stop startup, not silently
    widen or narrow who can open the agent.
    """
    path = os.environ.get("OMNIGENT_EVA_CONFIG")
    if not path:
        return ()
    rows = json.loads(Path(path).read_text())
    if not isinstance(rows, list):
        raise ValueError("Eva config must be a list of bindings")
    result: list[Binding] = []
    for row in rows:
        unknown = set(row) - _ALLOWED
        if unknown:
            raise ValueError(f"Unknown Eva binding setting: {', '.join(sorted(unknown))}")
        users = tuple(row["users"])
        if not users:
            raise ValueError("An Eva binding with no users can be opened by nobody")
        base_url = str(row["base_url"]).strip()
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("Eva binding base_url must be an absolute http(s) URL")
        fixture = bool(row.get("fixture", False))
        token_ref = str(row.get("token_ref", ""))
        if not fixture and not token_ref:
            raise ValueError("A live Eva binding needs a token_ref")
        result.append(
            Binding(
                users=users,
                base_url=base_url,
                token_ref=token_ref,
                label=str(row.get("label", "live")),
                fixture=fixture,
            )
        )
    labels = [b.label for b in result]
    if len(set(labels)) != len(labels):
        raise ValueError("Eva bindings must have distinct labels")
    return tuple(result)


def binding_for(user_id: str, label: str | None = None) -> Binding | None:
    """The binding this user may open, or None.

    With no label and several bindings, this returns nothing rather than
    guessing. Iris's verifier takes the same line: exactly one match, because
    two bindings for one subject make the selection ambiguous rather than
    redundant.
    """
    candidates = [b for b in bindings() if user_id in b.users]
    if label is not None:
        candidates = [b for b in candidates if b.label == label]
    if len(candidates) != 1:
        return None
    return candidates[0]
