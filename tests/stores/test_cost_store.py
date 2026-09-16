"""The cost store's contract is its denominator.

Every test here is really the same assertion from a different angle: a
figure on the money page must say how much of the window it covers, and
an absent measurement must never render as a measured zero.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from omnigent.db.compression import encode as encode_text
from omnigent.db.utils import get_or_create_engine
from omnigent.stores.cost_store import CostStore

# `conversations.created_at` is an INTEGER column: 32-bit on PostgreSQL, so an
# epoch bound above 2_147_483_647 is a DataError there while SQLite's dynamic
# typing accepts it silently. This window must stay inside int32.
WINDOW = (0, 2_000_000_000)

# ``agent_id`` is a Uuid16 column: 32-char hex, not a display name. Resolving
# an id back to a name is the route's job, not the store's.
IRIS = "a1" * 16
CHEAP = "b2" * 16
DEAR = "c3" * 16


@pytest.fixture()
def cost_store(db_uri: str) -> CostStore:
    return CostStore(db_uri)


def _set_created_at(db_uri: str, conversation_id: str, created_at: int) -> None:
    """Move a conversation in time.

    ``create_conversation`` stamps "now"; windowing cannot be tested
    without placing a row outside the window, and there is no public
    setter for ``created_at``.

    ``conversations.id`` is a :class:`~omnigent.db.db_models.Uuid16` — 16 raw
    bytes on disk, surfaced as 32 hex chars — so the hex string has to be
    unpacked to match. The ``rowcount`` assertion is not decoration: an
    ``UPDATE`` matching nothing is silent, and the first version of this
    helper matched nothing and quietly turned every windowing test into a
    test of a row that had never moved.
    """
    engine = get_or_create_engine(db_uri)
    with engine.begin() as connection:
        result = connection.execute(
            text("UPDATE conversations SET created_at = :ts WHERE id = :id"),
            {"ts": created_at, "id": bytes.fromhex(conversation_id)},
        )
    assert result.rowcount == 1, "helper updated no conversation row"


def _write_raw_usage(db_uri: str, conversation_id: str, raw: str) -> None:
    """Write a ``session_usage`` blob the public setter would never produce.

    The store has to survive a transport that changed shape, so the tests
    have to be able to write that shape.
    """
    engine = get_or_create_engine(db_uri)
    with engine.begin() as connection:
        result = connection.execute(
            text("UPDATE omnigent_conversation_metadata SET session_usage = :raw WHERE id = :id"),
            # session_usage is a CompressedText column: framed bytes, not
            # plain text. Go through the real encoder so these tests exercise
            # the storage format the store actually reads.
            {"raw": encode_text(raw), "id": bytes.fromhex(conversation_id)},
        )
    assert result.rowcount == 1, "helper updated no metadata row"


def test_priced_sessions_sum_and_report_full_coverage(cost_store, conversation_store):
    for _ in range(2):
        conversation = conversation_store.create_conversation(agent_id=IRIS)
        conversation_store.set_session_usage(
            conversation.id,
            {"total_tokens": 1_000, "total_cost_usd": 0.25},
        )

    window = cost_store.window(*WINDOW)
    (agent,) = window.agents
    assert agent.agent_id == IRIS
    assert agent.sessions == 2
    assert agent.priced_sessions == 2
    assert agent.cost_usd == pytest.approx(0.50)
    assert agent.priced_tokens == 2_000
    assert agent.complete is True


def test_an_unpriced_session_is_unknown_not_zero(cost_store, conversation_store):
    """A model missing from the pricing catalog yields tokens but no price."""
    priced = conversation_store.create_conversation(agent_id=IRIS)
    conversation_store.set_session_usage(priced.id, {"total_tokens": 100, "total_cost_usd": 1.0})
    unpriced = conversation_store.create_conversation(agent_id=IRIS)
    conversation_store.set_session_usage(unpriced.id, {"total_tokens": 900})

    (agent,) = cost_store.window(*WINDOW).agents
    assert agent.sessions == 2
    assert agent.priced_sessions == 1
    assert agent.unpriced_sessions == 1
    # The dollar figure covers one session of two, and says so.
    assert agent.cost_usd == pytest.approx(1.0)
    assert agent.complete is False
    # The unpriced tokens are not silently folded into the priced total.
    assert agent.priced_tokens == 100
    assert agent.unpriced_tokens == 900


def test_no_priced_session_reports_none_rather_than_zero_dollars(cost_store, conversation_store):
    """The distinction the whole module exists for."""
    conversation = conversation_store.create_conversation(agent_id=IRIS)
    conversation_store.set_session_usage(conversation.id, {"total_tokens": 500})

    (agent,) = cost_store.window(*WINDOW).agents
    assert agent.cost_usd is None
    assert agent.unpriced_sessions == 1
    assert cost_store.window(*WINDOW).cost_usd is None


def test_a_count_arriving_as_a_string_is_refused_not_coerced(
    cost_store, conversation_store, db_uri
):
    """``"7"`` is a changed transport, not a seven."""
    conversation = conversation_store.create_conversation(agent_id=IRIS)
    _write_raw_usage(db_uri, conversation.id, '{"total_tokens": "700", "total_cost_usd": 0.5}')

    (agent,) = cost_store.window(*WINDOW).agents
    assert agent.malformed_sessions == 1
    assert agent.priced_sessions == 0
    assert agent.cost_usd is None


def test_a_boolean_is_not_a_number(cost_store, conversation_store, db_uri):
    """``True`` is an ``int`` in Python; it is not a dollar amount."""
    conversation = conversation_store.create_conversation(agent_id=IRIS)
    _write_raw_usage(db_uri, conversation.id, '{"total_cost_usd": true}')

    (agent,) = cost_store.window(*WINDOW).agents
    assert agent.malformed_sessions == 1
    assert agent.cost_usd is None


def test_an_unparseable_blob_is_reported_not_skipped(cost_store, conversation_store, db_uri):
    conversation = conversation_store.create_conversation(agent_id=IRIS)
    _write_raw_usage(db_uri, conversation.id, "{not json at all")

    (agent,) = cost_store.window(*WINDOW).agents
    assert agent.sessions == 1
    assert agent.malformed_sessions == 1
    assert agent.cost_usd is None


def test_a_session_that_never_ran_is_unpriced_not_malformed(cost_store, conversation_store):
    """A fresh session has no usage yet; that is not a data defect."""
    conversation_store.create_conversation(agent_id=IRIS)

    (agent,) = cost_store.window(*WINDOW).agents
    assert agent.sessions == 1
    assert agent.malformed_sessions == 0
    assert agent.unpriced_sessions == 1


def test_sessions_outside_the_window_are_excluded(cost_store, conversation_store, db_uri):
    inside = conversation_store.create_conversation(agent_id=IRIS)
    conversation_store.set_session_usage(inside.id, {"total_tokens": 10, "total_cost_usd": 1.0})
    _set_created_at(db_uri, inside.id, 1_500)
    outside = conversation_store.create_conversation(agent_id=IRIS)
    conversation_store.set_session_usage(outside.id, {"total_tokens": 10, "total_cost_usd": 99.0})
    _set_created_at(db_uri, outside.id, 5_000)

    (agent,) = cost_store.window(1_000, 2_000).agents
    assert agent.sessions == 1
    assert agent.cost_usd == pytest.approx(1.0)


def test_sub_agent_sessions_are_not_counted_twice(cost_store, conversation_store):
    """A native harness folds sub-agent usage into the parent's blob."""
    parent = conversation_store.create_conversation(agent_id=IRIS)
    conversation_store.set_session_usage(parent.id, {"total_tokens": 100, "total_cost_usd": 2.0})
    child = conversation_store.create_conversation(
        kind="sub_agent",
        parent_conversation_id=parent.id,
        agent_id=IRIS,
        sub_agent_name="researcher",
    )
    conversation_store.set_session_usage(child.id, {"total_tokens": 100, "total_cost_usd": 2.0})

    window = cost_store.window(*WINDOW)
    (agent,) = window.agents
    assert agent.sessions == 1
    assert window.cost_usd == pytest.approx(2.0)


def test_agents_are_grouped_and_ordered_by_measured_cost(cost_store, conversation_store):
    cheap = conversation_store.create_conversation(agent_id=CHEAP)
    conversation_store.set_session_usage(cheap.id, {"total_tokens": 1, "total_cost_usd": 0.01})
    dear = conversation_store.create_conversation(agent_id=DEAR)
    conversation_store.set_session_usage(dear.id, {"total_tokens": 1, "total_cost_usd": 5.00})

    window = cost_store.window(*WINDOW)
    assert [a.agent_id for a in window.agents] == [DEAR, CHEAP]
    assert window.cost_usd == pytest.approx(5.01)
    assert window.sessions == 2


def test_an_inverted_window_is_refused(cost_store):
    with pytest.raises(ValueError):
        cost_store.window(2_000, 1_000)
    with pytest.raises(ValueError):
        cost_store.window(1_000, 1_000)


class TestMeteredBasis:
    """Billed versus avoided turns entirely on the provider kind."""

    def _one_priced_session(self, conversation_store) -> None:
        conversation = conversation_store.create_conversation(agent_id=IRIS)
        conversation_store.set_session_usage(
            conversation.id, {"total_tokens": 1_000, "total_cost_usd": 3.0}
        )

    def test_subscription_spend_is_avoided_not_billed(self, cost_store, conversation_store):
        self._one_priced_session(conversation_store)
        window = cost_store.window(*WINDOW, provider_kind="subscription")
        assert window.metered is False
        assert window.billed_usd == pytest.approx(0.0)
        assert window.avoided_usd == pytest.approx(3.0)

    def test_api_key_spend_is_billed_not_avoided(self, cost_store, conversation_store):
        self._one_priced_session(conversation_store)
        window = cost_store.window(*WINDOW, provider_kind="key")
        assert window.metered is True
        assert window.billed_usd == pytest.approx(3.0)
        assert window.avoided_usd == pytest.approx(0.0)

    def test_an_unknown_provider_is_indeterminate_not_assumed(
        self, cost_store, conversation_store
    ):
        """Fail closed: an unknown basis produces no dollar verdict."""
        self._one_priced_session(conversation_store)
        window = cost_store.window(*WINDOW)
        assert window.provider_kind is None
        assert window.metered is None
        assert window.billed_usd is None
        assert window.avoided_usd is None
        # The measured cost itself is still known; only its split is not.
        assert window.cost_usd == pytest.approx(3.0)


def test_a_bound_outside_int32_is_refused_in_terms_of_the_input(cost_store):
    """PostgreSQL's created_at is 32-bit; SQLite's is not.

    Without this the same call is a silent success on one backend and an
    opaque psycopg NumericValueOutOfRange on the other -- which is exactly
    how it reached CI green locally and red on PostgreSQL.
    """
    with pytest.raises(ValueError, match="32-bit epoch seconds"):
        cost_store.window(0, 4_000_000_000)
