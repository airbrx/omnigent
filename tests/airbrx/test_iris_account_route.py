"""GET /v1/iris/account: the caller's bound tenants, ranked, with no report body.

The iris router reaches the native API through an ASGITransport against
`request.app`, so stand-ins for the three native routes it calls are mounted
on the same test app. Nothing here starts a runner or a model.
"""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omnigent.airbrx.iris.routes import create_iris_router
from omnigent.errors import OmnigentError

USER = "ann@example.com"


class Auth:
    def get_user_id(self, request):
        return request.headers.get("x-forwarded-email")


class Agent:
    id = "ag_iris"


class Agents:
    def __init__(self, registered=True):
        self.registered = registered

    def get_by_name(self, name):
        return Agent() if (name == "iris" and self.registered) else None


def report(tenant_id, **metrics):
    base = {
        "requests": 100,
        "cache_hits": 60,
        "cache_misses": 40,
        "hit_rate": 0.6,
        "hit_rate_denominator": 100,
        "covered_days": 7,
        "requested_days": 7,
        "period_complete": True,
    }
    base.update(metrics)
    return {
        "tenant_id": tenant_id,
        "metrics": base,
        "evidence": [{"id": "e1", "data": {"rows": ["select secret from t"]}}],
        "findings": [{"id": "f1", "explanation": "LEAKED FINDING"}],
        "message": "Read-only evidence.",
    }


def turn(call_id, file_id, created_at):
    return [
        {"type": "function_call", "call_id": call_id, "name": "mcp__omnigent__iris_overview"},
        {
            "type": "function_call_output",
            "call_id": call_id,
            "created_at": created_at,
            "output": json.dumps({"downloads": [{"filename": "report.json", "file_id": file_id}]}),
        },
    ]


@pytest.fixture
def app(tmp_path, monkeypatch):
    config = tmp_path / "iris-bindings.json"
    config.write_text(
        json.dumps(
            [
                {
                    "tenant_id": "t-hot",
                    "host_id": "h",
                    "workspace": "/w/hot",
                    "users": [USER],
                    "pat_ref": "",
                    "fixture": True,
                },
                {
                    "tenant_id": "t-cold",
                    "host_id": "h",
                    "workspace": "/w/cold",
                    "users": [USER],
                    "pat_ref": "",
                    "fixture": True,
                },
                {
                    "tenant_id": "t-bobs",
                    "host_id": "h",
                    "workspace": "/w/bobs",
                    "users": ["bob@example.com"],
                    "pat_ref": "",
                    "fixture": True,
                },
            ]
        )
    )
    monkeypatch.setenv("OMNIGENT_IRIS_CONFIG", str(config))

    application = FastAPI()
    application.state.native = {
        "sessions": [
            {"id": "s-hot", "agent_id": "ag_iris", "host_id": "h", "workspace": "/w/hot"},
            {"id": "s-bobs", "agent_id": "ag_iris", "host_id": "h", "workspace": "/w/bobs"},
        ],
        "items": {
            "s-hot": list(reversed(turn("c1", "f-hot", 1_700_000_000.0))),
            "s-bobs": list(reversed(turn("c2", "f-bobs", 1_700_000_001.0))),
        },
        "reports": {"f-hot": report("t-hot"), "f-bobs": report("t-bobs")},
        "list_status": 200,
        "seen": [],
    }
    native = application.state.native

    @application.get("/v1/sessions")
    async def list_sessions():
        native["seen"].append("/v1/sessions")
        if native["list_status"] != 200:
            from fastapi.responses import JSONResponse

            return JSONResponse({"detail": "down"}, status_code=native["list_status"])
        return {
            "object": "list",
            "data": native["sessions"],
            "has_more": False,
            "first_id": None,
            "last_id": None,
        }

    @application.get("/v1/sessions/{sid}/items")
    async def list_items(sid: str):
        native["seen"].append(f"/v1/sessions/{sid}/items")
        return {
            "object": "list",
            "data": native["items"].get(sid, []),
            "has_more": False,
            "first_id": None,
            "last_id": None,
        }

    @application.get("/v1/sessions/{sid}/resources/files/{fid}/content")
    async def content(sid: str, fid: str):
        native["seen"].append(f"/v1/sessions/{sid}/resources/files/{fid}/content")
        return native["reports"][fid]

    application.include_router(
        create_iris_router(auth_provider=Auth(), agent_store=Agents()), prefix="/v1"
    )
    return application


def test_the_callers_tenants_are_ranked_or_quarantined_and_nothing_else_is_listed(app):
    response = TestClient(app).get("/v1/iris/account", headers={"X-Forwarded-Email": USER})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tenants"] == 2
    assert [r["tenant_id"] for r in body["ranked"]] == ["t-hot"]
    assert [(r["tenant_id"], r["reason"]) for r in body["quarantined"]] == [
        ("t-cold", "never_collected")
    ]
    assert body["ranked"][0]["cache_misses"] == 40
    assert body["ranked"][0]["age_seconds"] > 0
    # Bob's session was never opened, let alone his report.
    assert not any("s-bobs" in path for path in app.state.native["seen"])


def test_no_report_body_reaches_the_response(app):
    response = TestClient(app).get("/v1/iris/account", headers={"X-Forwarded-Email": USER})
    text = response.text
    for forbidden in (
        "evidence",
        "findings",
        "select secret",
        "LEAKED FINDING",
        "Read-only evidence",
    ):
        assert forbidden not in text


def test_an_unauthenticated_request_never_reaches_the_session_store(app):
    with pytest.raises(OmnigentError):
        TestClient(app).get("/v1/iris/account")
    assert app.state.native["seen"] == []


def test_a_failed_session_list_is_an_error_not_a_short_list(app):
    app.state.native["list_status"] = 503
    response = TestClient(app).get("/v1/iris/account", headers={"X-Forwarded-Email": USER})
    assert response.status_code == 502
    assert "ranked" not in response.json()


def test_an_unregistered_iris_is_404(tmp_path, monkeypatch):
    monkeypatch.delenv("OMNIGENT_IRIS_CONFIG", raising=False)
    application = FastAPI()
    application.include_router(
        create_iris_router(auth_provider=Auth(), agent_store=Agents(registered=False)),
        prefix="/v1",
    )
    response = TestClient(application).get("/v1/iris/account", headers={"X-Forwarded-Email": USER})
    assert response.status_code == 404


def test_invalid_binding_config_is_a_500_with_no_json_parser_text(tmp_path, monkeypatch):
    config = tmp_path / "iris-bindings.json"
    config.write_text("{not json")
    monkeypatch.setenv("OMNIGENT_IRIS_CONFIG", str(config))

    application = FastAPI()
    application.include_router(
        create_iris_router(auth_provider=Auth(), agent_store=Agents()), prefix="/v1"
    )
    response = TestClient(application).get("/v1/iris/account", headers={"X-Forwarded-Email": USER})
    assert response.status_code == 500
    assert response.json() == {
        "detail": "The Iris binding configuration on this host is invalid or unreadable"
    }
    assert "Expecting" not in response.text


def test_a_caller_with_no_bindings_gets_an_empty_account_not_an_error(app):
    response = TestClient(app).get(
        "/v1/iris/account", headers={"X-Forwarded-Email": "nobody@example.com"}
    )
    assert response.status_code == 200
    assert response.json() == {**response.json(), "tenants": 0, "ranked": [], "quarantined": []}
