"""Tally's catalog and readiness routes.

Readiness holds no token, so the most it may claim is that the portal's MCP
endpoint is mounted (405 to a GET). Whether her token is accepted is always
``null`` here, never ``true``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omnigent.airbrx.tally.routes import assess_mcp_probe, create_tally_router

USER = "aerickson@airbrx.com"
OTHER = "someone-else@airbrx.com"


class _Agent:
    id = "agent-tally-1"


class _AgentStore:
    def get_by_name(self, name: str) -> Any:
        return _Agent() if name == "tally" else None


LIVE = {
    "users": [USER],
    "base_url": "http://127.0.0.1:4318",
    "token_ref": "env:AIRBRX_TALLY_MCP_TOKEN",
}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    def _build(rows: list[dict[str, Any]], user: str | None = USER) -> TestClient:
        path = tmp_path / "tally.json"
        path.write_text(json.dumps(rows))
        monkeypatch.setenv("OMNIGENT_TALLY_CONFIG", str(path))
        monkeypatch.setattr(
            "omnigent.airbrx.tally.routes.require_user", lambda request, provider: user
        )
        app = FastAPI()
        app.include_router(
            create_tally_router(auth_provider=object(), agent_store=_AgentStore()),
            prefix="/v1",
        )
        return TestClient(app)

    return _build


def _patch_http(monkeypatch: pytest.MonkeyPatch, handler) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    transport = httpx.MockTransport(record)
    original = httpx.AsyncClient

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return original(*args, **kwargs)

    monkeypatch.setattr("omnigent.airbrx.tally.routes.httpx.AsyncClient", factory)
    return seen


def test_catalog_lists_only_this_users_bindings(client) -> None:
    body = client([LIVE, {**LIVE, "users": [OTHER], "label": "theirs"}]).get("/v1/tally").json()
    assert body["agent_id"] == "agent-tally-1"
    assert [b["label"] for b in body["bindings"]] == ["live"]
    assert body["bindings"][0]["mcp_url"] == "http://127.0.0.1:4318/mcp"
    assert body["bindings"][0]["host_id"] == ""


def test_catalog_never_exposes_the_token_reference(client) -> None:
    text = json.dumps(client([LIVE]).get("/v1/tally").json())
    assert "token_ref" not in text
    assert "AIRBRX_TALLY_MCP_TOKEN" not in text


def test_unauthenticated_callers_get_401(client) -> None:
    c = client([LIVE], user=None)
    assert c.get("/v1/tally").status_code == 401
    assert c.get("/v1/tally/readiness").status_code == 401


def test_a_405_means_the_endpoint_is_mounted_and_no_token_is_sent(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _patch_http(monkeypatch, lambda request: httpx.Response(405))
    body = client([LIVE]).get("/v1/tally/readiness").json()
    assert body["reachable"] is True
    assert body["mcp_mounted"] is True
    assert body["token_accepted"] is None
    (request,) = seen
    assert str(request.url) == "http://127.0.0.1:4318/mcp"
    assert request.method == "GET"
    assert "authorization" not in {k.lower() for k in request.headers}


def test_a_404_means_not_mounted(client, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_http(monkeypatch, lambda request: httpx.Response(404))
    body = client([LIVE]).get("/v1/tally/readiness").json()
    assert body["mcp_mounted"] is False


@pytest.mark.parametrize("status", [200, 401, 500, 307])
def test_anything_else_is_unknown_never_true(status: int) -> None:
    assert assess_mcp_probe(status)["mcp_mounted"] is None


def test_an_unreachable_portal_is_not_reachable(client, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    _patch_http(monkeypatch, boom)
    body = client([LIVE]).get("/v1/tally/readiness").json()
    assert body["reachable"] is False
    assert body["mcp_mounted"] is None
    assert body["token_accepted"] is None


def test_a_loopback_on_another_host_is_not_probed(client, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _patch_http(monkeypatch, lambda request: httpx.Response(405))
    body = client([{**LIVE, "host_id": "a" * 32}]).get("/v1/tally/readiness").json()
    assert seen == []
    assert body["reachable"] is None
    assert "curl" in body["detail"]


def test_readiness_refuses_to_guess_between_two_bindings(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_http(monkeypatch, lambda request: httpx.Response(405))
    two = [LIVE, {**LIVE, "label": "other"}]
    assert client(two).get("/v1/tally/readiness").status_code == 404
    assert client(two).get("/v1/tally/readiness?label=other").status_code == 200


def test_readiness_refuses_a_binding_this_user_may_not_open(client) -> None:
    assert client([{**LIVE, "users": [OTHER]}]).get("/v1/tally/readiness").status_code == 404
