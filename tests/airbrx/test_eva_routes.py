"""Eva's catalog and readiness routes.

The readiness tests carry the weight. The defect they exist for is that an
outreach app can be reachable, healthy, and showing eight invented leads, and
every check anyone had said "ok".
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omnigent.airbrx.eva.routes import create_eva_router

USER = "aerickson@airbrx.com"
OTHER = "someone-else@airbrx.com"


class _Agent:
    id = "agent-eva-1"


class _AgentStore:
    def get_by_name(self, name: str) -> Any:
        return _Agent() if name == "eva" else None


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """An app with Eva mounted and one live binding, as `USER`."""

    def _build(rows: list[dict[str, Any]], user: str | None = USER) -> TestClient:
        path = tmp_path / "eva.json"
        path.write_text(json.dumps(rows))
        monkeypatch.setenv("OMNIGENT_EVA_CONFIG", str(path))
        monkeypatch.setattr(
            "omnigent.airbrx.eva.routes.require_user", lambda request, provider: user
        )
        app = FastAPI()
        app.include_router(
            create_eva_router(auth_provider=object(), agent_store=_AgentStore()),
            prefix="/v1",
        )
        return TestClient(app)

    return _build


LIVE = {
    "users": [USER],
    "host_id": "882128953d2a4e178ddbd48d70b298a1",
    "base_url": "https://eva.airbrx.test",
    "token_ref": "keychain:eva-outreach-token",
}


def _readyz(payload: dict[str, Any], status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload)

    return handler


def _patch_http(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return original(*args, **kwargs)

    monkeypatch.setattr("omnigent.airbrx.eva.routes.httpx.AsyncClient", factory)


# --------------------------------------------------------------------------
# Catalog
# --------------------------------------------------------------------------


def test_catalog_lists_only_this_user_s_bindings(client) -> None:
    c = client([LIVE, {**LIVE, "users": [OTHER], "label": "theirs"}])
    body = c.get("/v1/eva").json()
    assert body["agent_id"] == "agent-eva-1"
    assert [b["label"] for b in body["bindings"]] == ["live"]


def test_catalog_never_exposes_the_token_reference(client) -> None:
    """Omitted, not redacted. A redacted field still describes the secret."""
    body = client([LIVE]).get("/v1/eva").json()
    assert "token_ref" not in json.dumps(body)
    assert "keychain" not in json.dumps(body)


def test_an_unauthenticated_caller_gets_401(client) -> None:
    assert client([LIVE], user=None).get("/v1/eva").status_code == 401


def test_readiness_refuses_an_unauthenticated_caller(client) -> None:
    assert client([LIVE], user=None).get("/v1/eva/readiness").status_code == 401


# --------------------------------------------------------------------------
# Readiness. The point of the module.
# --------------------------------------------------------------------------


def test_the_eight_september_fixtures_are_reported_as_not_carrying_crm_data(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exact shape of the 2026-09-21 defect.

    status ok, a bound repository, 25 tables, eight leads and every one of them
    an ``@*.example`` fixture. Everything anyone checked said healthy. This must
    not, and it must not start to once the question changes from "has a sync
    run" to "is the data real".
    """
    _patch_http(
        monkeypatch,
        _readyz(
            {
                "status": "ok",
                "config": {"google_sheets": "not configured", "sign_in": "google oidc"},
                "sync": {"runs": 0},
                "leads": {"total": 8, "fixture": 8},
            }
        ),
    )
    body = client([LIVE]).get("/v1/eva/readiness").json()
    assert body["reachable"] is True
    assert body["healthy"] is True
    assert body["carries_crm_data"] is False
    assert body["leads"] == {"total": 8, "fixture": 8}
    assert body["sync_runs"] == 0
    assert "8 of 8" in body["detail"]
    assert "fixture" in body["detail"]


def test_real_leads_with_no_fixtures_carry_crm_data_even_when_no_sync_has_run(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Abram's requirement is real data, not a sync run.

    115 leads arrived by CSV import on 2026-09-22 with ``sync_runs`` still 0.
    The old proxy answered false for that app. The count answers true, and
    reports the zero sync runs beside it as its own fact.
    """
    _patch_http(
        monkeypatch,
        _readyz(
            {
                "status": "ok",
                "config": {"google_sheets": "not configured"},
                "sync": {"runs": 0},
                "leads": {"total": 115, "fixture": 0},
            }
        ),
    )
    body = client([LIVE]).get("/v1/eva/readiness").json()
    assert body["carries_crm_data"] is True
    assert body["sync_runs"] == 0
    assert body["leads"] == {"total": 115, "fixture": 0}
    assert "import" in body["detail"]
    assert "never run" in body["detail"]


def test_a_mixed_app_is_worse_than_an_empty_one(client, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_http(
        monkeypatch,
        _readyz({"status": "ok", "sync": {"runs": 2}, "leads": {"total": 115, "fixture": 3}}),
    )
    body = client([LIVE]).get("/v1/eva/readiness").json()
    assert body["carries_crm_data"] is False
    assert body["sync_runs"] == 2
    assert "3 of 115" in body["detail"]


def test_an_empty_app_does_not_carry_crm_data(client, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_http(monkeypatch, _readyz({"status": "ok", "leads": {"total": 0, "fixture": 0}}))
    body = client([LIVE]).get("/v1/eva/readiness").json()
    assert body["carries_crm_data"] is False
    assert "no leads" in body["detail"]


def test_a_sync_count_alone_establishes_nothing(client, monkeypatch: pytest.MonkeyPatch) -> None:
    """``sync.runs`` was the old proxy. On its own it no longer answers.

    A sync could have run against a sheet that was later purged, or written
    nothing. The runs are reported, the CRM question stays open.
    """
    _patch_http(
        monkeypatch,
        _readyz({"status": "ok", "config": {"google_sheets": "configured"}, "sync": {"runs": 3}}),
    )
    body = client([LIVE]).get("/v1/eva/readiness").json()
    assert body["carries_crm_data"] is None
    assert body["sync_runs"] == 3
    assert "cannot be established" in body["detail"]


def test_a_boolean_or_negative_count_is_not_a_count(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_http(monkeypatch, _readyz({"status": "ok", "leads": {"total": True, "fixture": -1}}))
    body = client([LIVE]).get("/v1/eva/readiness").json()
    assert body["carries_crm_data"] is None


def test_an_app_that_does_not_report_sync_state_is_unknown_not_true(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An outreach /readyz with neither a `leads` nor a `sync` block.

    The honest answer is null. Reporting true here would recreate the defect in
    the very check built to catch it.
    """
    _patch_http(monkeypatch, _readyz({"status": "ok", "config": {}}))
    body = client([LIVE]).get("/v1/eva/readiness").json()
    assert body["healthy"] is True
    assert body["carries_crm_data"] is None
    assert "cannot be established" in body["detail"]
    assert "Do not assume" in body["detail"]


def test_an_unreachable_app_is_not_healthy_and_not_carrying_data(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    _patch_http(monkeypatch, boom)
    body = client([LIVE]).get("/v1/eva/readiness").json()
    assert body["reachable"] is False
    assert body["healthy"] is None
    assert body["carries_crm_data"] is None


def test_a_non_200_readyz_is_unhealthy(client, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_http(monkeypatch, _readyz({"status": "degraded"}, status=503))
    body = client([LIVE]).get("/v1/eva/readiness").json()
    assert body["reachable"] is True
    assert body["healthy"] is False
    assert body["carries_crm_data"] is None


def test_readiness_refuses_to_guess_between_two_bindings(client) -> None:
    two = [LIVE, {**LIVE, "base_url": "https://other.test", "label": "other"}]
    assert client(two).get("/v1/eva/readiness").status_code == 404
    # Named explicitly, it answers.
    assert client(two).get("/v1/eva/readiness?label=other").status_code == 200


def test_readiness_refuses_a_binding_this_user_may_not_open(client) -> None:
    assert client([{**LIVE, "users": [OTHER]}]).get("/v1/eva/readiness").status_code == 404
