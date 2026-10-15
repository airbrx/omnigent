"""Tally's authenticated catalog and readiness routes.

Shaped like ``airbrx/eva/routes.py``: who may open her, and whether the thing
she talks to is actually there.

Readiness here is narrower than Eva's on purpose. The coordinator routes never
hold Tally's bearer token (it travels as a reference and the runner's host
resolves it), so this cannot ask the portal an authenticated question. What it
can establish without a credential is whether the MCP endpoint is mounted: the
portal answers **405** to a GET at ``/mcp``. Whether her token is accepted is
reported as ``null``, never as ``true``, and the detail says where it is
actually checked: the first turn.
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request

from omnigent.airbrx.tally.config import Binding, bindings
from omnigent.server.routes._auth_helpers import require_user

#: The portal sidecar is on the same box. A few seconds is generous.
_PROBE_TIMEOUT = 5.0


def _public(binding: Binding) -> dict[str, Any]:
    """A binding as the UI may see it. ``token_ref`` is omitted, not redacted."""
    return {
        "label": binding.label,
        "host_id": binding.host_id,
        "base_url": binding.base_url,
        "mcp_url": binding.mcp_url(),
        "host_local": binding.is_host_local(),
        "fixture": binding.fixture,
        "workspace": binding.workspace or None,
    }


def assess_mcp_probe(status_code: int) -> dict[str, Any]:
    """Turn the status of an unauthenticated ``GET /mcp`` into readiness.

    Pure, so the same decision can be made from a ``curl`` on another host.
    ``405`` is the portal saying the endpoint exists and wants POST. Anything
    else leaves ``mcp_mounted`` false or unknown, never true.
    """
    if status_code == 405:
        return {
            "mcp_mounted": True,
            "detail": (
                "The portal's MCP endpoint is mounted. Whether Tally's token is "
                "accepted is not checked here; the first turn checks it."
            ),
        }
    if status_code == 404:
        return {
            "mcp_mounted": False,
            "detail": "The portal answered 404 at /mcp: the MCP endpoint is not mounted.",
        }
    return {
        "mcp_mounted": None,
        "detail": f"The portal answered {status_code} at /mcp, not the 405 it should.",
    }


def create_tally_router(*, auth_provider: Any, agent_store: Any) -> APIRouter:
    router = APIRouter()
    from omnigent.airbrx.tally.workspace import add_workspace_routes

    add_workspace_routes(router, auth_provider=auth_provider, agent_store=agent_store)

    @router.get("/tally")
    async def catalog(request: Request) -> dict[str, Any]:
        user = require_user(request, auth_provider)
        if user is None:
            raise HTTPException(401, "Tally requires host authentication")
        agent = agent_store.get_by_name("tally")
        return {
            "agent_id": agent.id if agent else None,
            "bindings": [_public(b) for b in bindings() if user in b.users],
        }

    @router.get("/tally/readiness")
    async def readiness(request: Request, label: str | None = None) -> dict[str, Any]:
        """Is the portal's MCP endpoint behind this binding actually there?

        ``reachable``   the portal responded at all.
        ``mcp_mounted`` ``GET /mcp`` answered 405, the portal's own signal.
        ``token_accepted`` always ``null`` here: this route holds no token.

        Anything this cannot establish is ``null`` and never ``true``.
        """
        user = require_user(request, auth_provider)
        if user is None:
            raise HTTPException(401, "Tally requires host authentication")

        candidates = [b for b in bindings() if user in b.users]
        if label is not None:
            candidates = [b for b in candidates if b.label == label]
        if len(candidates) != 1:
            raise HTTPException(404, "No single Tally binding for this user")
        binding = candidates[0]

        out: dict[str, Any] = {
            "label": binding.label,
            "host_id": binding.host_id,
            "base_url": binding.base_url,
            "mcp_url": binding.mcp_url(),
            "host_local": binding.is_host_local(),
            "fixture": binding.fixture,
            "reachable": None,
            "mcp_mounted": None,
            "token_accepted": None,
            "detail": "",
        }

        if binding.is_host_local():
            out["detail"] = (
                "This binding points at another execution host's loopback, which "
                "the coordinator cannot reach. Check on that host: curl -i "
                f"{binding.mcp_url()} (expect 405)"
            )
            return out

        try:
            async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT) as client:
                response = await client.get(binding.mcp_url())
        except (httpx.HTTPError, OSError) as exc:
            out["reachable"] = False
            out["detail"] = f"{type(exc).__name__}: the portal did not answer"
            return out

        out["reachable"] = True
        out.update(assess_mcp_probe(response.status_code))
        return out

    return router
