"""The cost endpoint's contract is what it refuses to claim.

These tests are mostly about absence: a figure with no basis is withheld
rather than guessed, an unpriced session is not folded into a total, and
the warehouse comparison says it cannot be computed instead of showing a
number built on a list-price guess.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from omnigent.server.routes.cost import create_cost_router
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.conversation_store.sqlalchemy_store import (
    SqlAlchemyConversationStore,
)
from omnigent.stores.cost_store import CostStore

pytestmark = pytest.mark.asyncio

# Conversations are stamped "now" on create, and the endpoint refuses a
# window wider than a year, so bracket the present rather than all time.
_NOW = int(time.time())
WINDOW = {"start": _NOW - 86_400, "end": _NOW + 86_400}


@pytest.fixture()
def cost_store(db_uri: str) -> CostStore:
    return CostStore(db_uri)


@pytest.fixture()
def conversations(db_uri: str) -> SqlAlchemyConversationStore:
    return SqlAlchemyConversationStore(db_uri)


@pytest.fixture()
def agents(db_uri: str) -> SqlAlchemyAgentStore:
    return SqlAlchemyAgentStore(db_uri)


def _app(cost_store: CostStore, **kwargs) -> FastAPI:
    app = FastAPI()
    app.include_router(create_cost_router(cost_store, **kwargs), prefix="/v1")
    return app


@pytest_asyncio.fixture()
async def client(cost_store: CostStore) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=_app(cost_store))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _get(client: httpx.AsyncClient, **params) -> dict:
    response = await client.get("/v1/cost", params={**WINDOW, **params})
    assert response.status_code == 200, response.text
    return response.json()


async def test_an_empty_window_reports_no_cost_rather_than_zero(client):
    """Nothing ran, so there is nothing to report — which is not $0.00 spent."""
    body = await _get(client)
    assert body["agent_token_cost"]["measured_usd"] is None
    assert body["agent_token_cost"]["sessions"] == 0
    assert body["agent_token_cost"]["by_agent"] == []


async def test_measured_cost_carries_its_denominator(client, conversations):
    priced = conversations.create_conversation(agent_id="a1" * 16)
    conversations.set_session_usage(priced.id, {"total_tokens": 100, "total_cost_usd": 2.0})
    unpriced = conversations.create_conversation(agent_id="a1" * 16)
    conversations.set_session_usage(unpriced.id, {"total_tokens": 900})

    block = (await _get(client))["agent_token_cost"]
    assert block["standing"] == "measured"
    assert block["measured_usd"] == pytest.approx(2.0)
    assert block["sessions"] == 2
    assert block["priced_sessions"] == 1
    assert block["complete"] is False
    # The partiality is stated in prose, not left as arithmetic for the reader.
    assert "unknown, not zero" in block["coverage_note"]


async def test_a_fully_priced_window_says_so_and_has_no_caveat(client, conversations):
    conversation = conversations.create_conversation(agent_id="a1" * 16)
    conversations.set_session_usage(conversation.id, {"total_tokens": 10, "total_cost_usd": 1.0})

    block = (await _get(client))["agent_token_cost"]
    assert block["complete"] is True
    assert block["coverage_note"] is None


async def test_an_unknown_provider_withholds_billed_and_avoided(client, conversations):
    """Fail closed: with no basis, neither figure is asserted."""
    conversation = conversations.create_conversation(agent_id="a1" * 16)
    conversations.set_session_usage(conversation.id, {"total_tokens": 10, "total_cost_usd": 3.0})

    body = await _get(client)
    assert body["basis"]["metered"] is None
    assert body["basis"]["determinate"] is False
    assert body["basis"]["note"]
    assert body["cost_avoided"]["modelled_usd"] is None
    assert body["cost_avoided"]["billed_usd"] is None
    # The measurement itself is still reported; only its split is withheld.
    assert body["agent_token_cost"]["measured_usd"] == pytest.approx(3.0)


async def test_subscription_routing_reports_avoided_cost_as_modelled(cost_store, conversations):
    conversation = conversations.create_conversation(agent_id="a1" * 16)
    conversations.set_session_usage(conversation.id, {"total_tokens": 10, "total_cost_usd": 4.0})
    transport = httpx.ASGITransport(app=_app(cost_store, provider_kind="subscription"))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        body = await _get(c)

    assert body["basis"]["metered"] is False
    assert body["cost_avoided"]["standing"] == "modelled"
    assert body["cost_avoided"]["modelled_usd"] == pytest.approx(4.0)
    assert body["cost_avoided"]["billed_usd"] == pytest.approx(0.0)
    assert "never scale it to match" in body["cost_avoided"]["explanation"]


async def test_metered_routing_reports_spend_and_no_avoidance(cost_store, conversations):
    conversation = conversations.create_conversation(agent_id="a1" * 16)
    conversations.set_session_usage(conversation.id, {"total_tokens": 10, "total_cost_usd": 4.0})
    transport = httpx.ASGITransport(app=_app(cost_store, provider_kind="key"))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        body = await _get(c)

    assert body["basis"]["metered"] is True
    assert body["cost_avoided"]["billed_usd"] == pytest.approx(4.0)
    assert body["cost_avoided"]["modelled_usd"] == pytest.approx(0.0)


async def test_warehouse_comparison_states_its_requirement_and_shows_nothing(client):
    """The panel that cannot be computed tonight must say why."""
    warehouse = (await _get(client))["warehouse"]
    assert warehouse["available"] is False
    assert warehouse["standing"] == "unavailable"
    assert warehouse["requires"] == [
        "usd_per_snowflake_credit",
        "usd_per_databricks_dbu",
    ]
    assert "cost basis" in warehouse["explanation"]
    # No dollar figure may appear anywhere in this block.
    assert not any(key.endswith("_usd") for key in warehouse), (
        "the warehouse block must not carry a dollar figure it cannot support"
    )


async def test_an_unresolvable_agent_is_marked_not_blanked(client, conversations):
    """An agent deleted after its sessions ran is not a rendering bug."""
    conversation = conversations.create_conversation(agent_id="dd" * 16)
    conversations.set_session_usage(conversation.id, {"total_tokens": 1, "total_cost_usd": 1.0})

    (row,) = (await _get(client))["agent_token_cost"]["by_agent"]
    assert row["agent_id"] == "dd" * 16
    assert row["agent_name_resolved"] is False


async def test_a_known_agent_is_named(cost_store, conversations, agents):
    agent = agents.create(agent_id="ee" * 16, name="iris", bundle_location="k")
    conversation = conversations.create_conversation(agent_id=agent.id)
    conversations.set_session_usage(conversation.id, {"total_tokens": 1, "total_cost_usd": 1.0})
    transport = httpx.ASGITransport(app=_app(cost_store, agent_store=agents))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        body = await _get(c)

    (row,) = body["agent_token_cost"]["by_agent"]
    assert row["agent_name"] == "iris"
    assert row["agent_name_resolved"] is True


async def test_an_inverted_window_is_refused(client):
    response = await client.get("/v1/cost", params={"start": 100, "end": 100})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_input"


async def test_an_unbounded_window_is_refused_with_a_stated_limit(client):
    response = await client.get("/v1/cost", params={"start": 0, "end": 10**10})
    assert response.status_code == 400
    assert "at most" in response.json()["error"]["message"]
