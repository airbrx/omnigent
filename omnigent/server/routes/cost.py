"""Fork-local route serving the cost and savings surface.

airbrx-only, and deliberately its own module: a file upstream does not
have can never conflict on a sync. The only upstream-owned line this
feature adds is the ``include_router`` call in ``app.py``.

The response shape is the honesty contract, not decoration. Three
questions are asked of this endpoint and they have three different
epistemic standings, so they are three different blocks and each one
carries its standing on its face:

``agent_token_cost``
    **Measured.** Summed from the usage the harnesses recorded, and it
    states how many of the window's sessions it actually covers.
``cost_avoided``
    **Modelled.** What the measured tokens would have cost on metered
    billing. It is not a bill and is never scaled to match one.
``warehouse``
    **Unavailable** without an operator-supplied cost basis, and it says
    so rather than showing a plausible number.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from omnigent.server.auth import AuthProvider
from omnigent.server.routes._auth_helpers import require_user
from omnigent.stores.agent_store import AgentStore
from omnigent.stores.cost_store import AgentTokenCost, CostStore, CostWindow

#: Widest window the endpoint will answer, in seconds (366 days). A
#: request for "everything" is a table scan of every session ever run;
#: it is refused with a stated limit rather than served slowly.
MAX_WINDOW_SECONDS = 366 * 24 * 60 * 60

#: Shown wherever the warehouse block appears. The two rates are contract
#: terms that live in nothing this server can read, and a list-price guess
#: on a money page is the one thing this surface must never ship.
WAREHOUSE_REQUIREMENT = (
    "Warehouse cost through Airbrx versus direct to Snowflake or Databricks "
    "needs an operator-supplied cost basis: USD per Snowflake credit and USD "
    "per Databricks DBU. Neither is derivable from anything this server can "
    "read, and a list-price estimate on a cost page reads as a measurement. "
    "Supply the rates to enable this figure."
)


def _invalid_input(message: str) -> JSONResponse:
    """Build the standard ``400`` error body used across this router."""
    return JSONResponse(
        {"error": {"code": "invalid_input", "message": message}},
        status_code=400,
    )


def _agent_block(agent: AgentTokenCost, names: dict[str, str]) -> dict[str, Any]:
    """Render one agent's row, denominator included.

    An id with no name behind it is reported as unresolved rather than
    rendered blank: a nameless row on a cost table reads as a bug, and
    an agent deleted after its sessions ran is not a bug.
    """
    return {
        "agent_id": agent.agent_id,
        "agent_name": names.get(agent.agent_id or "") if agent.agent_id else None,
        "agent_name_resolved": bool(agent.agent_id and agent.agent_id in names),
        "measured_usd": agent.cost_usd,
        "sessions": agent.sessions,
        "priced_sessions": agent.priced_sessions,
        "unpriced_sessions": agent.unpriced_sessions,
        "malformed_sessions": agent.malformed_sessions,
        "priced_tokens": agent.priced_tokens,
        "unpriced_tokens": agent.unpriced_tokens,
        "complete": agent.complete,
    }


def _coverage_note(window: CostWindow) -> str | None:
    """State what the measured figure is missing, or ``None`` when nothing.

    Every rate names its denominator; this is the sentence that does it
    in prose for the panel, so a reader does not have to divide two
    numbers to discover the figure is partial.
    """
    unpriced = sum(a.unpriced_sessions for a in window.agents)
    malformed = sum(a.malformed_sessions for a in window.agents)
    if not unpriced and not malformed:
        return None
    parts = []
    if unpriced:
        parts.append(
            f"{unpriced} of {window.sessions} sessions recorded usage that could "
            "not be priced, so their cost is unknown, not zero"
        )
    if malformed:
        parts.append(
            f"{malformed} of {window.sessions} sessions recorded usage this server could not read"
        )
    return "; ".join(parts) + "."


def create_cost_router(
    cost_store: CostStore,
    *,
    agent_store: AgentStore | None = None,
    provider_kind: str | None = None,
    auth_provider: AuthProvider | None = None,
) -> APIRouter:
    """Build the router for ``/v1/cost``.

    :param cost_store: Source of the measured usage rollup.
    :param agent_store: Used only to resolve agent ids to display names.
        ``None`` leaves every row's name unresolved and says so.
    :param provider_kind: The configured provider kind, e.g.
        ``"subscription"``. ``None`` leaves billed-versus-avoided
        indeterminate rather than assuming either.
    """
    router = APIRouter()

    @router.get("/cost")
    async def get_cost(request: Request, start: int, end: int) -> Any:
        require_user(request, auth_provider)
        if end <= start:
            return _invalid_input("end must be greater than start")
        if end - start > MAX_WINDOW_SECONDS:
            return _invalid_input(f"window must be at most {MAX_WINDOW_SECONDS} seconds")

        window = cost_store.window(start, end, provider_kind=provider_kind)

        names: dict[str, str] = {}
        if agent_store is not None:
            ids = [a.agent_id for a in window.agents if a.agent_id]
            if ids:
                names = agent_store.get_names(ids)

        return {
            "window": {"start_utc": window.start_utc, "end_utc": window.end_utc},
            "basis": {
                "provider_kind": window.provider_kind,
                "metered": window.metered,
                "determinate": window.metered is not None,
                "note": (
                    "Provider kind is unknown, so this server cannot say whether "
                    "token spend was billed per token or covered by a "
                    "subscription. Billed and avoided are withheld rather than "
                    "guessed."
                    if window.metered is None
                    else None
                ),
            },
            "agent_token_cost": {
                "standing": "measured",
                "measured_usd": window.cost_usd,
                "sessions": window.sessions,
                "priced_sessions": window.priced_sessions,
                "complete": window.sessions > 0 and window.priced_sessions == window.sessions,
                "coverage_note": _coverage_note(window),
                "by_agent": [_agent_block(a, names) for a in window.agents],
            },
            "cost_avoided": {
                "standing": "modelled",
                "modelled_usd": window.avoided_usd,
                "billed_usd": window.billed_usd,
                "explanation": (
                    "What the measured tokens would have cost on metered "
                    "per-token billing, computed by the harness from catalog "
                    "rates. This is a model, not an invoice: show it beside the "
                    "billed subscription fee and never scale it to match one."
                ),
            },
            "warehouse": {
                "standing": "unavailable",
                "available": False,
                "requires": [
                    "usd_per_snowflake_credit",
                    "usd_per_databricks_dbu",
                ],
                "explanation": WAREHOUSE_REQUIREMENT,
            },
        }

    return router
