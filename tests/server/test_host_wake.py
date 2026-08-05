"""Tests for wakeable hosts (``host_wake:`` config + ``POST /v1/hosts/{id}/wake``).

Two properties matter most and are asserted directly:

1. **Inert by default.** With no ``host_wake:`` section every host reads back
   ``wakeable: false`` and the wake endpoint refuses — a laptop-only install
   must behave exactly as it did before the feature existed.
2. **Fails loud on bad config.** An operator typo stops startup rather than
   surfacing later as a wake button that silently does nothing.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from omnigent.runtime.agent_cache import AgentCache
from omnigent.server.app import create_app
from omnigent.server.auth import RESERVED_USER_LOCAL
from omnigent.server.host_wake import (
    HostWakeError,
    HostWakeTarget,
    parse_host_wake_config,
)
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.artifact_store.local import LocalArtifactStore
from omnigent.stores.comment_store.sqlalchemy_store import SqlAlchemyCommentStore
from omnigent.stores.conversation_store.sqlalchemy_store import (
    SqlAlchemyConversationStore,
)
from omnigent.stores.file_store.sqlalchemy_store import SqlAlchemyFileStore
from omnigent.stores.host_store import HostStore

_HOST_ID = "c0ffee00deadbeef0123456789abcdef"
_HOST_NAME = "omnigent-devbox"


# ── config parsing ──────────────────────────────────────────────────────


def test_absent_section_disables_the_feature() -> None:
    """No ``host_wake:`` at all resolves to no targets."""
    assert parse_host_wake_config(None) == {}


def test_parses_a_valid_entry() -> None:
    """A well-formed entry becomes a target keyed by host name."""
    targets = parse_host_wake_config(
        [
            {
                "host_name": _HOST_NAME,
                "provider": "ec2",
                "instance_id": "i-099d66548b496d876",
                "region": "us-east-1",
            }
        ]
    )
    assert targets == {
        _HOST_NAME: HostWakeTarget(
            host_name=_HOST_NAME,
            provider="ec2",
            instance_id="i-099d66548b496d876",
            region="us-east-1",
        )
    }


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ({"host_name": "a"}, "mapping instead of list"),
        ([{"provider": "ec2", "instance_id": "i", "region": "r"}], "missing host_name"),
        ([{"host_name": "a", "instance_id": "i", "region": "r"}], "missing provider"),
        (
            [{"host_name": "a", "provider": "gcp", "instance_id": "i", "region": "r"}],
            "bad provider",
        ),
        ([{"host_name": "a", "provider": "ec2", "region": "r"}], "missing instance_id"),
        ([{"host_name": "a", "provider": "ec2", "instance_id": "i"}], "missing region"),
        (
            [{"host_name": "a", "provider": "ec2", "instance_id": "i", "region": "r", "typo": 1}],
            "unknown key",
        ),
        (
            [
                {"host_name": "a", "provider": "ec2", "instance_id": "i", "region": "r"},
                {"host_name": "a", "provider": "ec2", "instance_id": "j", "region": "r"},
            ],
            "duplicate host_name",
        ),
    ],
)
def test_malformed_config_fails_loud(raw: object, reason: str) -> None:
    """Every malformed shape raises at parse time, not at click time."""
    with pytest.raises(ValueError):
        parse_host_wake_config(raw)


# ── route behavior ──────────────────────────────────────────────────────


def _build_app(db_uri: str, tmp_path: Path, wake_targets: dict[str, HostWakeTarget] | None):
    """Build a host-aware app, optionally with wake targets configured."""
    artifact_store = LocalArtifactStore(str(tmp_path / "artifacts"))
    return create_app(
        agent_store=SqlAlchemyAgentStore(db_uri),
        file_store=SqlAlchemyFileStore(db_uri),
        conversation_store=SqlAlchemyConversationStore(db_uri),
        artifact_store=artifact_store,
        agent_cache=AgentCache(artifact_store=artifact_store, cache_dir=tmp_path / "cache"),
        comment_store=SqlAlchemyCommentStore(db_uri),
        host_store=HostStore(db_uri),
        host_wake_targets=wake_targets,
    )


@pytest_asyncio.fixture()
async def unconfigured_client(
    runtime_init: None, db_uri: str, tmp_path: Path
) -> AsyncIterator[httpx.AsyncClient]:
    """A host-aware app with NO host_wake config — the default deployment."""
    app = _build_app(db_uri, tmp_path, None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


@pytest_asyncio.fixture()
async def wake_client(
    runtime_init: None, db_uri: str, tmp_path: Path
) -> AsyncIterator[httpx.AsyncClient]:
    """A host-aware app that CAN wake ``_HOST_NAME``."""
    app = _build_app(
        db_uri,
        tmp_path,
        {
            _HOST_NAME: HostWakeTarget(
                host_name=_HOST_NAME,
                provider="ec2",
                instance_id="i-099d66548b496d876",
                region="us-east-1",
            )
        },
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


def _register_host(db_uri: str) -> None:
    """Register the host, then mark it offline (the state a wake targets)."""
    store = HostStore(db_uri)
    store.upsert_on_connect(host_id=_HOST_ID, name=_HOST_NAME, user_id=RESERVED_USER_LOCAL)
    store.set_offline(_HOST_ID)


async def test_hosts_are_not_wakeable_without_config(
    unconfigured_client: httpx.AsyncClient, db_uri: str
) -> None:
    """The whole feature is inert unless an operator opts in.

    This is the guarantee that a plain laptop install is untouched: the field
    is present but false, and the endpoint refuses.
    """
    _register_host(db_uri)
    listed = await unconfigured_client.get("/v1/hosts")
    assert listed.status_code == 200
    hosts = listed.json()["hosts"]
    assert [h["wakeable"] for h in hosts] == [False]

    refused = await unconfigured_client.post(f"/v1/hosts/{_HOST_ID}/wake")
    assert refused.status_code == 409
    assert "not configured as wakeable" in refused.json()["detail"]


async def test_configured_host_reports_wakeable(
    wake_client: httpx.AsyncClient, db_uri: str
) -> None:
    """A configured host advertises itself so the picker can offer to wake it."""
    _register_host(db_uri)
    listed = await wake_client.get("/v1/hosts")
    assert listed.json()["hosts"][0]["wakeable"] is True

    fetched = await wake_client.get(f"/v1/hosts/{_HOST_ID}")
    assert fetched.json()["wakeable"] is True


async def test_wake_unknown_host_is_404(wake_client: httpx.AsyncClient) -> None:
    """An id that was never registered is a 404, not a provider call."""
    resp = await wake_client.post("/v1/hosts/does_not_exist/wake")
    assert resp.status_code == 404


async def test_provider_failure_surfaces_as_502(
    wake_client: httpx.AsyncClient, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rejected start reports the provider's reason instead of a bare 500."""
    _register_host(db_uri)

    async def _boom(target: HostWakeTarget) -> str:
        raise HostWakeError(f"could not start {target.instance_id}: quota exceeded")

    monkeypatch.setattr("omnigent.server.routes.hosts.wake_host", _boom)
    resp = await wake_client.post(f"/v1/hosts/{_HOST_ID}/wake")
    assert resp.status_code == 502
    assert "quota exceeded" in resp.json()["detail"]


async def test_wake_starts_the_configured_instance(
    wake_client: httpx.AsyncClient, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A successful wake returns immediately rather than awaiting the host.

    The host takes ~40-60s to re-register; the endpoint must not hold the
    request open for it, so "waking" is the terminal response here.
    """
    _register_host(db_uri)
    seen: list[HostWakeTarget] = []

    async def _ok(target: HostWakeTarget) -> str:
        seen.append(target)
        return "pending"

    monkeypatch.setattr("omnigent.server.routes.hosts.wake_host", _ok)
    resp = await wake_client.post(f"/v1/hosts/{_HOST_ID}/wake")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "waking"
    assert body["provider_state"] == "pending"
    assert [t.instance_id for t in seen] == ["i-099d66548b496d876"]
