"""Fork-local store: what the agents cost to run, and what that cost avoided.

airbrx-only. Reads the per-session ``session_usage`` blobs that the
harnesses already write and turns them into a windowed, per-agent rollup
for the cost surface. It adds no table, no column and no migration —
the measurements exist; nothing was reporting them.

Two sources were deliberately NOT used, and the reasons are the whole
character of this module:

``user_daily_cost``
    Looks exactly like the right table. It is incremented "only when the
    session runs under at least one policy, so the table is never touched
    in deployments that have no policies configured" (see
    :class:`~omnigent.db.db_models.SqlUserDailyCost`). In a policy-free
    deployment it is empty, so a surface built on it renders "nothing was
    recorded" as ``$0.00``. A measured zero and an absent measurement are
    different answers.

:meth:`ConversationStore.usage_totals_for_user`
    Sums the same blobs, but ``float(usage.get("total_cost_usd") or 0.0)``
    turns an unpriced session into a zero-dollar one, an unparseable blob
    is skipped while still counting toward ``session_count``, and a count
    arriving as ``"7"`` is coerced rather than refused. Fine for an admin
    rollup; not something to build a money page on.

So this module carries its denominator in its return type: every figure
says how many of the sessions it actually covers, and a total with no
priced session behind it is ``None``, never ``0.0``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import Engine, select

from omnigent.db.db_models import (
    SqlConversation,
    SqlConversationMetadata,
    current_workspace_id,
)
from omnigent.db.utils import get_or_create_engine, make_named_managed_session_maker

#: Published LIST prices, for converting warehouse quantities to dollars.
#:
#: These are vendor list rates, not anyone's contract rate. Committed-use and
#: negotiated agreements are routinely below list, so a figure derived from
#: these is an UPPER BOUND on list terms and must be labelled as such wherever
#: it is shown. An operator who knows their real rate should override it.
#:
#: Snowflake: USD per credit, AWS US East (N. Virginia), on-demand, from the
#: Snowflake Service Consumption Table effective 2026-09-01.
SNOWFLAKE_LIST_USD_PER_CREDIT = {
    "standard": 2.00,
    "enterprise": 3.00,
    "business_critical": 4.00,
    "vps": 6.00,
}

#: Databricks: USD per DBU, AWS, Premium tier, on-demand. The SKU matters more
#: than the edition does -- SQL Serverless is roughly 3x SQL Classic -- so the
#: caller must name one rather than get a default that flatters or punishes.
DATABRICKS_LIST_USD_PER_DBU = {
    "jobs_light": 0.07,
    "jobs_compute": 0.15,
    "sql_classic": 0.22,
    "all_purpose": 0.55,
    "sql_pro": 0.55,
    "sql_serverless": 0.70,
}

#: Where the numbers above came from, carried into the API response so a reader
#: can check them rather than trust them.
WAREHOUSE_RATE_SOURCE = (
    "Vendor list prices: Snowflake Service Consumption Table (AWS US East, "
    "on-demand, effective 2026-09-01) and Databricks AWS Premium-tier published "
    "DBU rates. List, not contract: negotiated and committed-use rates are "
    "routinely lower, so any figure derived from these is an upper bound on "
    "list terms."
)


def warehouse_rate(vendor, plan):
    """USD per unit for *vendor*, or ``None`` when the plan is unrecognised.

    Refuses rather than defaults. Picking a plan on the caller's behalf is how
    a cost page ends up quoting SQL Serverless money for SQL Classic work.

    :param vendor: ``"snowflake"`` or ``"databricks"``.
    :param plan: Edition (Snowflake) or compute SKU (Databricks).
    :returns: USD per credit or per DBU, or ``None``.
    """
    table = {
        "snowflake": SNOWFLAKE_LIST_USD_PER_CREDIT,
        "databricks": DATABRICKS_LIST_USD_PER_DBU,
    }.get(vendor)
    if table is None:
        return None
    return table.get(plan)


#: Provider kinds whose token cost is NOT billed per token. A session run
#: under one of these still reports a ``total_cost_usd`` — the harness
#: computes it from catalog rates — but that figure is what the tokens
#: *would* have cost on metered billing, so it is avoided cost, not spend.
UNMETERED_KINDS = frozenset({"subscription"})


def _exact_number(value: object) -> float | None:
    """Accept a real number; refuse anything that merely looks like one.

    ``True`` is an ``int`` in Python and ``"7"`` is a plausible-looking
    count from a changed transport. Coercing either hides the change
    behind a number that reads fine, so both are refused.

    :param value: A decoded JSON value.
    :returns: The number, or ``None`` when *value* is not one.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


@dataclass(frozen=True)
class AgentTokenCost:
    """One agent's measured token cost over the window, with its denominator.

    :param agent_id: The agent the sessions belong to; ``None`` groups
        sessions that have no agent binding.
    :param sessions: Sessions in the window attributed to this agent —
        the denominator for every other field here.
    :param priced_sessions: Sessions whose usage carried a usable
        ``total_cost_usd``. Only these are behind :attr:`cost_usd`.
    :param unpriced_sessions: Sessions that recorded usage but no price,
        which is what a model missing from the pricing catalog produces.
        Their tokens are real and their cost is unknown — not zero.
    :param malformed_sessions: Sessions whose ``session_usage`` did not
        parse, or carried a non-numeric where a number belongs. Reported
        rather than skipped, because a transport that started sending
        strings should be visible.
    :param cost_usd: Summed USD over :attr:`priced_sessions` only, or
        ``None`` when no session was priced. Never ``0.0`` to mean
        "nothing was measured".
    :param priced_tokens: Tokens belonging to priced sessions.
    :param unpriced_tokens: Tokens known to have been spent but not
        priceable. The gap between the two token figures is the honest
        size of what :attr:`cost_usd` is missing.
    """

    agent_id: str | None
    sessions: int
    priced_sessions: int
    unpriced_sessions: int
    malformed_sessions: int
    cost_usd: float | None
    priced_tokens: int
    unpriced_tokens: int

    @property
    def complete(self) -> bool:
        """Whether :attr:`cost_usd` covers every session in the window."""
        return self.sessions > 0 and self.priced_sessions == self.sessions


@dataclass(frozen=True)
class CostWindow:
    """The cost surface's answer for one window.

    :param start_utc: Inclusive window start, Unix epoch seconds.
    :param end_utc: Exclusive window end, Unix epoch seconds.
    :param agents: Per-agent rollups, largest measured cost first.
    :param provider_kind: The configured provider kind the figures are
        read against, e.g. ``"subscription"`` or ``"key"``. ``None`` when
        the caller could not determine it, which makes
        :attr:`metered` indeterminate and is reported as such.
    :param metered: ``True`` when token spend is billed per token,
        ``False`` when it is covered by a subscription, ``None`` when
        :attr:`provider_kind` is unknown. Fails closed: an unknown
        provider is not assumed to be either.
    """

    start_utc: int
    end_utc: int
    agents: tuple[AgentTokenCost, ...]
    provider_kind: str | None
    metered: bool | None

    @property
    def sessions(self) -> int:
        """Sessions in the window, across every agent."""
        return sum(a.sessions for a in self.agents)

    @property
    def priced_sessions(self) -> int:
        """Sessions with a usable price, across every agent."""
        return sum(a.priced_sessions for a in self.agents)

    @property
    def cost_usd(self) -> float | None:
        """Total measured USD, or ``None`` when nothing was priced."""
        priced = [a.cost_usd for a in self.agents if a.cost_usd is not None]
        return sum(priced) if priced else None

    @property
    def billed_usd(self) -> float | None:
        """Metered spend: the part of :attr:`cost_usd` actually billed per token.

        ``None`` when the provider kind is unknown — an indeterminate
        basis does not get to pass as a dollar figure.
        """
        if self.metered is None:
            return None
        return self.cost_usd if self.metered else 0.0

    @property
    def avoided_usd(self) -> float | None:
        """Modelled cost avoided by running on an unmetered provider.

        This is what the measured tokens WOULD have cost on metered
        billing, computed by the harness from catalog rates. It is a
        modelled figure and must be presented beside the billed
        subscription fee, never scaled to match it. ``None`` when the
        basis is unknown, and ``0.0`` only when spend really was metered.
        """
        if self.metered is None:
            return None
        return 0.0 if self.metered else self.cost_usd


class CostStore:
    """Windowed, per-agent LLM cost read from existing ``session_usage`` blobs.

    :param storage_location: SQLAlchemy database URI.
    """

    def __init__(self, storage_location: str) -> None:
        self._engine: Engine = get_or_create_engine(storage_location)
        self._session = make_named_managed_session_maker(
            self._engine,
            query_name_prefix="omnigent.cost_store",
        )

    def window(
        self,
        start_utc: int,
        end_utc: int,
        *,
        provider_kind: str | None = None,
    ) -> CostWindow:
        """Roll up token cost per agent for sessions created in the window.

        Sub-agent conversations are excluded and their cost is not lost:
        a native harness accumulates a sub-agent's usage into its parent
        session's blob, so counting the children too would double it.

        :param start_utc: Inclusive start, Unix epoch seconds.
        :param end_utc: Exclusive end, Unix epoch seconds.
        :param provider_kind: The configured provider kind, from
            :mod:`omnigent.onboarding.provider_config`. ``None`` leaves
            the metered/unmetered split indeterminate rather than
            guessing one.
        :returns: A :class:`CostWindow`.
        :raises ValueError: When the window is empty or inverted. A
            window that cannot be interpreted is refused, not silently
            widened to something that returns rows.
        """
        if end_utc <= start_utc:
            raise ValueError("Cost window must be a non-empty [start, end) range")
        # `conversations.created_at` is an INTEGER column, which is 32-bit on
        # PostgreSQL. A bound outside that range reaches the driver as an opaque
        # NumericValueOutOfRange rather than anything a caller can act on, and
        # SQLite accepts it silently, so the two backends disagree about whether
        # the same query is valid. Refuse it here, in terms of the input.
        if not (-(2**31) <= start_utc <= 2**31 - 1 and -(2**31) <= end_utc <= 2**31 - 1):
            raise ValueError(
                "Cost window bounds must be 32-bit epoch seconds; "
                f"got start={start_utc}, end={end_utc}"
            )
        with self._session("cost_window") as session:
            rows = session.execute(
                select(
                    SqlConversation.agent_id,
                    SqlConversationMetadata.session_usage,
                )
                .join(
                    SqlConversationMetadata,
                    SqlConversationMetadata.id == SqlConversation.id,
                )
                .where(
                    SqlConversation.workspace_id == current_workspace_id(),
                    SqlConversationMetadata.workspace_id == current_workspace_id(),
                    SqlConversation.parent_conversation_id.is_(None),
                    SqlConversation.created_at >= start_utc,
                    SqlConversation.created_at < end_utc,
                )
            ).all()

        tallies: dict[str | None, dict[str, float]] = {}
        for agent_id, raw in rows:
            tally = tallies.setdefault(
                agent_id,
                {
                    "sessions": 0,
                    "priced": 0,
                    "unpriced": 0,
                    "malformed": 0,
                    "cost": 0.0,
                    "priced_tokens": 0,
                    "unpriced_tokens": 0,
                },
            )
            tally["sessions"] += 1
            self._tally_session(raw, tally)

        agents = tuple(
            sorted(
                (
                    AgentTokenCost(
                        agent_id=agent_id,
                        sessions=int(t["sessions"]),
                        priced_sessions=int(t["priced"]),
                        unpriced_sessions=int(t["unpriced"]),
                        malformed_sessions=int(t["malformed"]),
                        cost_usd=t["cost"] if t["priced"] else None,
                        priced_tokens=int(t["priced_tokens"]),
                        unpriced_tokens=int(t["unpriced_tokens"]),
                    )
                    for agent_id, t in tallies.items()
                ),
                key=lambda a: (-(a.cost_usd or 0.0), a.agent_id or ""),
            )
        )
        return CostWindow(
            start_utc=start_utc,
            end_utc=end_utc,
            agents=agents,
            provider_kind=provider_kind,
            metered=None if provider_kind is None else provider_kind not in UNMETERED_KINDS,
        )

    @staticmethod
    def _tally_session(raw: str | None, tally: dict[str, float]) -> None:
        """Fold one session's ``session_usage`` blob into *tally*.

        A session with no usage at all (never ran a turn) is neither
        priced nor malformed — it simply has nothing to report, and
        counting it as malformed would cry wolf on every fresh session.
        """
        if not raw:
            tally["unpriced"] += 1
            return
        try:
            usage = json.loads(raw)
        except (TypeError, ValueError):
            tally["malformed"] += 1
            return
        if not isinstance(usage, dict):
            tally["malformed"] += 1
            return

        tokens = _exact_number(usage.get("total_tokens"))
        if usage.get("total_tokens") is not None and tokens is None:
            # A number arrived as something that is not a number. Say so.
            tally["malformed"] += 1
            return
        cost = _exact_number(usage.get("total_cost_usd"))
        if usage.get("total_cost_usd") is not None and cost is None:
            tally["malformed"] += 1
            return

        if cost is None:
            tally["unpriced"] += 1
            tally["unpriced_tokens"] += int(tokens or 0)
            return
        tally["priced"] += 1
        tally["cost"] += cost
        tally["priced_tokens"] += int(tokens or 0)
