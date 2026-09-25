"""Non-secret, operator-owned bindings for Eva.

Mirrors ``airbrx/iris/config.py``, including ``host_id``, and the reason that
field is here is worth stating because an earlier version of this file left it
out on purpose and was wrong.

Eva's tools are remote MCP calls to the airbrx-outreach app, so it looked like
she needed no execution host: the coordinator could call the app directly. That
is true only if the app is reachable from the coordinator, which means a public
hostname, a certificate and a container on the EC2 box. None of that exists, and
Iris does not need any of it.

**Iris has no public hostname.** Her tools run on an execution host that dials
out to the coordinator, so it can reach whatever that machine can reach.
``omnigent/runner/mcp_manager.py`` connects MCP servers on the **runner**, which
for a host-bound session is that same machine. So a host-bound Eva reaches
``http://127.0.0.1:8000/mcp`` on the operator's own Mac, and needs no DNS, no
certificate and no container anywhere.

Removing ``host_id`` removed exactly the mechanism that makes Iris work without
public infrastructure. It is back.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

_ALLOWED = {"users", "host_id", "base_url", "token_ref", "label", "fixture", "workspace"}


@dataclass(frozen=True)
class Binding:
    #: Omnigent user ids allowed to open this Eva. The authorization decision.
    users: tuple[str, ...]
    #: The execution host that runs Eva's turns, from ``/v1/hosts``, or empty.
    #:
    #: This field answers one question: **whose loopback is ``base_url``?**
    #: Named, and her MCP client runs on that host, so the app is resolved from
    #: THAT machine. Empty, and the coordinator dials the app itself, which is
    #: the case when the two run on the same box.
    #:
    #: Both are real deployments, and this file has been wrong about that in
    #: both directions. It first omitted the field, which removed the mechanism
    #: that lets Iris work without public infrastructure. It then required the
    #: field, which refuses the hosted shape where no execution host exists.
    #: Bind one host per label: the selection must be unambiguous, not redundant.
    host_id: str
    #: The outreach app as the EXECUTION HOST sees it. Normally
    #: ``http://127.0.0.1:8000`` because the app runs on that same Mac. A public
    #: URL is allowed and is not required.
    base_url: str
    #: How to resolve the outreach MCP bearer token, e.g.
    #: ``keychain:eva-outreach-token``. Resolved per invocation on the host that
    #: makes the call. Empty for a fixture binding.
    token_ref: str
    #: What the workspace calls this binding when more than one exists.
    label: str = "live"
    #: A visibly synthetic binding for acceptance runs. Never real data.
    fixture: bool = False
    #: Absolute directory on the execution host for the runner's working
    #: directory, the same field Iris's binding has. Creating a session on a
    #: host requires one, so the workspace cannot start Eva without it. Empty
    #: is allowed, and the workspace then says the binding needs one rather
    #: than failing on the first click.
    workspace: str = ""

    def mcp_url(self) -> str:
        """The MCP endpoint, with the trailing slash the transport requires.

        The outreach app mounts streamable HTTP at ``/mcp/`` and answers 307 to
        ``/mcp``. A POST that does not follow that redirect fails in a way that
        reads as an authentication problem, which is how an hour went missing
        once already.
        """
        return self.base_url.rstrip("/") + "/mcp/"

    def is_host_local(self) -> bool:
        """Is ``base_url`` only meaningful on the execution host?

        Used by the readiness route, which runs on the coordinator and therefore
        cannot probe a loopback address belonging to another machine. It reports
        that honestly rather than reporting a connection failure as if the app
        were down.

        A loopback address is only unreachable from here when it belongs to
        another machine, and ``host_id`` is what says that it does. Without one
        the loopback is the coordinator's own and the probe is the real answer,
        so deciding from the URL alone would make readiness go blind on exactly
        the deployment where it works best.
        """
        if not self.host_id:
            return False
        return any(h in self.base_url for h in ("127.0.0.1", "localhost", "::1"))


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
        # Optional on purpose; see the field's note. Empty means the
        # coordinator reaches the app itself.
        host_id = str(row.get("host_id", "")).strip()
        base_url = str(row["base_url"]).strip()
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("Eva binding base_url must be an absolute http(s) URL")
        fixture = bool(row.get("fixture", False))
        token_ref = str(row.get("token_ref", ""))
        if not fixture and not token_ref:
            raise ValueError("A live Eva binding needs a token_ref")
        workspace = str(row.get("workspace", "")).strip()
        if workspace and not workspace.startswith("/"):
            raise ValueError("Eva binding workspace must be an absolute path on the host")
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
