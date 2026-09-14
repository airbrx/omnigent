"""Round-trip and scoping for the fork-local agent avatar store."""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from omnigent.stores.agent_avatar_store import AgentAvatarStore
from omnigent.stores.artifact_store.local import LocalArtifactStore


@pytest.fixture
def store(tmp_path, db_uri):
    return AgentAvatarStore(db_uri, LocalArtifactStore(str(tmp_path / "artifacts")))


def test_put_then_get_round_trips(store):
    meta = store.put("researcher", b"\x89PNG-bytes", "image/png")
    assert meta.agent_name == "researcher"
    assert meta.content_type == "image/png"
    got = store.get("researcher")
    assert got is not None
    data, meta2 = got
    assert data == b"\x89PNG-bytes"
    assert meta2.updated_at == meta.updated_at


def test_get_unknown_agent_is_none(store):
    assert store.get("nope") is None


def test_put_twice_replaces_and_advances_updated_at(store):
    first = store.put("researcher", b"one", "image/png")
    second = store.put("researcher", b"two", "image/jpeg")
    assert store.get("researcher")[0] == b"two"
    assert second.content_type == "image/jpeg"
    assert second.updated_at >= first.updated_at
    assert len(store.list_all()) == 1


def test_put_twice_upserts_via_the_immediate_session_maker(store):
    """put()'s check-then-insert must go through the immediate session maker.

    ``host_store.py`` protects its analogous upsert-on-connect path from a
    check-then-insert race with a second, ``immediate=True`` session maker
    (``BEGIN IMMEDIATE`` on SQLite, ``SELECT ... FOR UPDATE`` on Postgres) —
    without it, two concurrent ``put()`` calls for the same
    ``(workspace_id, agent_name)`` (e.g. a doubled-submit avatar upload)
    can both see no row and both INSERT, and the second raises
    ``IntegrityError`` instead of upserting. A true concurrency test would be
    flaky, so this instead proves two things sequentially: the upsert path
    still replaces cleanly rather than raising, and it is the *immediate*
    session maker — not the plain one used by get/list_all/delete — that
    actually ran the check-then-insert.
    """
    calls: list[str] = []
    original_lifecycle_session = store._lifecycle_session

    @contextmanager
    def spying_lifecycle_session(query_name):
        calls.append(query_name)
        with original_lifecycle_session(query_name) as session:
            yield session

    store._lifecycle_session = spying_lifecycle_session

    first = store.put("researcher", b"one", "image/png")
    second = store.put("researcher", b"two", "image/jpeg")

    assert calls == ["put_agent_avatar", "put_agent_avatar"]
    assert store.get("researcher")[0] == b"two"
    assert second.content_type == "image/jpeg"
    assert second.updated_at >= first.updated_at
    assert len(store.list_all()) == 1


def test_delete_removes_row_and_blob(store):
    store.put("researcher", b"one", "image/png")
    assert store.delete("researcher") is True
    assert store.get("researcher") is None
    assert store.delete("researcher") is False


def test_avatars_are_scoped_to_their_workspace(store):
    """One workspace must not read another's avatar.

    Every query in this store filters on ``current_workspace_id()`` and the
    artifact key embeds the workspace id, so the same agent NAME in two
    workspaces is two different pictures. Without this the drawer would
    leak images across tenants — the agent name is not unique globally.
    """
    from omnigent.db.db_models import workspace_scope

    with workspace_scope(1):
        store.put("researcher", b"workspace-one", "image/png")
    with workspace_scope(2):
        assert store.get("researcher") is None
        assert store.list_all() == []
        store.put("researcher", b"workspace-two", "image/png")
        assert store.get("researcher")[0] == b"workspace-two"
    with workspace_scope(1):
        assert store.get("researcher")[0] == b"workspace-one"
