"""Eva's authenticated catalog and readiness routes.

Much smaller than ``airbrx/iris/routes.py``, and for the same reason the rest of
this module is: Iris's workspace adapter has to proxy native session APIs
because her UI is a vendored single-page app that has to run somewhere. Eva's
user interface is the airbrx-outreach web app, which serves itself. What Omnigent
has to provide is the two things a hosted agent needs and cannot get from the app:
who may open her, and whether the thing she talks to is actually working.

The readiness route exists because of a specific failure on 2026-09-21. Abram
opened the outreach app, saw eight leads, and they were fabricated fixtures with
``@*.example`` addresses. The app reported itself healthy the whole time, because
it was: ``/readyz`` checks the database and the schema and says nothing about
whether a single row ever came from the CRM. ``sync_runs`` was zero and nothing
on any screen said so. A hosted agent pointed at that app would have repeated the
same reassurance to more people.

So readiness here is deliberately not a ping. It asks the question that was
actually wrong.
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request

from omnigent.airbrx.eva.config import Binding, bindings
from omnigent.server.routes._auth_helpers import require_user

#: Long enough for a cold serverless warehouse behind the app, short enough that
#: a hung upstream does not hold a request open. The outreach app's own home page
#: has been measured at 15s cold and about 2s warm.
_PROBE_TIMEOUT = 20.0


def _public(binding: Binding) -> dict[str, Any]:
    """A binding as the UI may see it.

    ``token_ref`` is omitted rather than redacted. A redacted field still tells a
    reader the shape and location of the secret, and nothing on a screen needs
    to know that a token exists.
    """
    return {
        "label": binding.label,
        "host_id": binding.host_id,
        "base_url": binding.base_url,
        "mcp_url": binding.mcp_url(),
        "host_local": binding.is_host_local(),
        "fixture": binding.fixture,
    }


def create_eva_router(*, auth_provider: Any, agent_store: Any) -> APIRouter:
    router = APIRouter()

    @router.get("/eva")
    async def catalog(request: Request) -> dict[str, Any]:
        user = require_user(request, auth_provider)
        if user is None:
            raise HTTPException(401, "Eva requires host authentication")
        agent = agent_store.get_by_name("eva")
        return {
            "agent_id": agent.id if agent else None,
            "bindings": [_public(b) for b in bindings() if user in b.users],
        }

    @router.get("/eva/readiness")
    async def readiness(request: Request, label: str | None = None) -> dict[str, Any]:
        """Is the outreach app behind this binding actually usable?

        Three answers, and the third is the one that matters:

        ``reachable``   the app responded at all.
        ``healthy``     its own ``/readyz`` says ok, with a repository bound.
        ``carries_crm_data``  a sync has actually run. **An app can be reachable
                        and healthy and still be showing invented rows.** That is
                        not hypothetical; it is what shipped.

        Anything this cannot establish is reported as ``null`` and never as
        ``true``. A readiness check that guesses optimistically is worse than
        none, because it is believed.
        """
        user = require_user(request, auth_provider)
        if user is None:
            raise HTTPException(401, "Eva requires host authentication")

        candidates = [b for b in bindings() if user in b.users]
        if label is not None:
            candidates = [b for b in candidates if b.label == label]
        if len(candidates) != 1:
            # Exactly one, or nothing. Same rule as `binding_for`: two bindings
            # make the selection ambiguous rather than redundant.
            raise HTTPException(404, "No single Eva binding for this user")
        binding = candidates[0]

        out: dict[str, Any] = {
            "label": binding.label,
            "host_id": binding.host_id,
            "base_url": binding.base_url,
            "host_local": binding.is_host_local(),
            "fixture": binding.fixture,
            "reachable": None,
            "healthy": None,
            "carries_crm_data": None,
            "detail": "",
        }

        if binding.is_host_local():
            # This route runs on the coordinator. The binding names a loopback
            # address on the EXECUTION HOST, which is a different machine, so
            # probing it from here would dial the coordinator's own loopback and
            # report "down" for an app that is running perfectly well.
            #
            # That failure would be worse than no answer, because it is a
            # confident wrong one. Everything stays null and the detail says
            # where the real check lives.
            out["detail"] = (
                "This binding points at the execution host's own loopback, which "
                "is where Eva's MCP client runs and is not reachable from the "
                "coordinator. Check readiness on that host: curl "
                f"{binding.base_url.rstrip('/')}/readyz"
            )
            return out

        try:
            async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT) as client:
                response = await client.get(binding.base_url.rstrip("/") + "/readyz")
        except (httpx.HTTPError, OSError) as exc:
            # Only reached for a binding the coordinator genuinely should be able
            # to dial. A host-local one returned above, so a refusal here is a
            # real "not reachable" rather than an artifact of asking the wrong
            # machine.
            out["reachable"] = False
            out["detail"] = f"{type(exc).__name__}: the outreach app did not answer"
            return out

        out["reachable"] = True
        if response.status_code != 200:
            out["healthy"] = False
            out["detail"] = f"/readyz answered {response.status_code}"
            return out

        try:
            body = response.json()
        except ValueError:
            out["healthy"] = False
            out["detail"] = "/readyz did not return JSON"
            return out

        out["healthy"] = body.get("status") == "ok"
        config = body.get("config") or {}
        out["sheets"] = config.get("google_sheets")
        out["sign_in"] = config.get("sign_in")

        # The CRM question. `/readyz` does not answer it today, so this asks for
        # a field that may not exist and refuses to invent one when it does not.
        # When the outreach app grows a sync summary, this starts answering
        # without a change here; until then it says "unknown", which is true.
        sync = body.get("sync")
        if isinstance(sync, dict) and "runs" in sync:
            runs = sync.get("runs")
            out["carries_crm_data"] = bool(runs)
            out["sync_runs"] = runs
            if not runs:
                out["detail"] = (
                    "The app is healthy and has never synced the CRM. "
                    "Any leads on screen are seed data, not your spreadsheet."
                )
        else:
            out["detail"] = out["detail"] or (
                "The app does not report sync state, so whether these leads came "
                "from the CRM cannot be established from here. Do not assume they did."
            )
        return out

    return router
