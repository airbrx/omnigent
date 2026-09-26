"""The adapter responses Eva's workspace app is tested against, produced by the real handlers.

``web/src/shell/__fixtures__/evaAdapter.json`` is what the adapter routes in
``omnigent/airbrx/eva/workspace.py`` actually answer for a handful of recorded
sessions, and ``web/src/shell/evaWorkspaceApp.test.ts`` feeds it to
``omnigent/airbrx/eva/ui/app.js``. This test fails when a handler's answer
drifts from the file, so a shape change cannot pass on one side and break the
other. Regenerate with::

    EVA_WRITE_FIXTURES=1 uv run pytest tests/airbrx/test_eva_workspace_fixtures.py

Every name here is invented: the repository is public.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omnigent.airbrx.eva.routes import create_eva_router

FIXTURE = Path(__file__).resolve().parents[2] / "web/src/shell/__fixtures__/evaAdapter.json"
API = "/v1/eva/sessions/s1/ui/api"
HOST = "0123456789abcdef0123456789abcdef"
#: The clock the handlers see, so ``cache_age_seconds`` and ``stale`` are stable.
NOW = 1_790_000_060
READ_AT = 1_790_000_000
LEAD = "00000000-0000-4000-8000-000000000001"
DRAFT = "00000000-0000-4000-8000-000000000002"


def call(name: str, args: dict[str, Any] | None = None, call_id: str = "c1") -> dict[str, Any]:
    return {
        "type": "function_call",
        "name": name,
        "arguments": json.dumps(args or {}),
        "call_id": call_id,
    }


def out(result: Any, call_id: str = "c1", at: int = READ_AT) -> dict[str, Any]:
    return {
        "type": "function_call_output",
        "call_id": call_id,
        "output": json.dumps(result),
        "created_at": at,
    }


def answer(text: str) -> dict[str, Any]:
    return {
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": text}],
    }


USER_TURN = {"id": "u1", "type": "message", "role": "user", "created_at": READ_AT - 5}
POOL = {
    "leads": [
        {
            "id": LEAD,
            "name": "Pat Example",
            "title": "Head of Data",
            "company": "Example Analytics Co",
            "warehouse": "Snowflake",
            "lead_status": "New",
            "priority": 2,
        }
    ],
    "total": 1,
}
MINE = {"leads": [], "total": 0}
LEAD_DETAIL = {
    "profile": {**POOL["leads"][0], "city": "Springfield"},
    "qualification": {"score": 3, "warehouse_fit": "good", "rationale": "Invented for a test."},
    "touches": [],
    "drafts": [
        {
            "draft_id": DRAFT,
            "channel": "Email",
            "status": "draft",
            "version_no": 1,
            "subject": "An invented subject",
        }
    ],
}
#: A session where Eva read the pool, her leads and one lead, then answered.
READ_SESSION = [
    USER_TURN,
    call("outreach__list_pool", call_id="a"),
    out(POOL, "a"),
    call("outreach__list_my_leads", call_id="b"),
    out(MINE, "b"),
    call("outreach__get_lead", {"lead_id": LEAD, "include_drafts": True}, "c"),
    out(LEAD_DETAIL, "c"),
    answer("One lead in the pool: Pat Example at Example Analytics Co."),
]


class _Agent:
    id = "agent-eva"


class _AgentStore:
    def get_by_name(self, name: str) -> Any:
        return _Agent() if name == "eva" else None


def _client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, items: list[dict]) -> TestClient:
    cfg = tmp_path / "eva.json"
    cfg.write_text(
        json.dumps(
            [
                {
                    "users": ["local"],
                    "host_id": HOST,
                    "base_url": "http://127.0.0.1:8000",
                    "token_ref": "keychain:eva-outreach-token",
                    "workspace": "/tmp/eva",
                }
            ]
        )
    )
    monkeypatch.setenv("OMNIGENT_EVA_CONFIG", str(cfg))
    for module in ("omnigent.airbrx.eva.routes", "omnigent.airbrx.eva.workspace"):
        monkeypatch.setattr(f"{module}.require_user", lambda request, provider: "local")
    # Only the handlers' own `time` name, so nothing else in the process sees it.
    monkeypatch.setattr(
        "omnigent.airbrx.eva.workspace.time",
        SimpleNamespace(time=lambda: NOW, monotonic=time.monotonic),
    )
    app = FastAPI()
    app.include_router(
        create_eva_router(auth_provider=object(), agent_store=_AgentStore()), prefix="/v1"
    )

    @app.get("/v1/sessions/{session_id}")
    async def get_session(session_id: str) -> dict[str, Any]:
        return {
            "id": "s1",
            "agent_id": "agent-eva",
            "host_id": HOST,
            "status": "idle",
            "permission_level": 4,
        }

    @app.get("/v1/sessions/{session_id}/items")
    async def get_items(session_id: str, order: str = "asc") -> dict[str, Any]:
        return {"data": list(reversed(items)) if order == "desc" else items, "has_more": False}

    @app.post("/v1/sessions/{session_id}/events")
    async def post_event(session_id: str) -> dict[str, Any]:
        return {"queued": True, "item_id": "u1"}

    return TestClient(app)


def _answer(client: TestClient, method: str, route: str) -> dict[str, Any]:
    if method == "GET":
        response = client.get(f"{API}/{route}")
    elif route == "chat":
        response = client.post(
            f"{API}/chat", json={"history": [{"role": "user", "content": "Who is in the pool?"}]}
        )
    else:
        response = client.post(f"{API}/{route}", json={})
    return {"status": response.status_code, "body": response.json()}


#: Scenario name to (recorded session, the routes whose answers are captured).
SCENARIOS: dict[str, tuple[list[dict[str, Any]], list[tuple[str, str]]]] = {
    # Nothing has run: state is a 409, readiness names what is unverified.
    "fresh_session": ([], [("GET", "readiness"), ("GET", "state")]),
    # Eva has read the pipeline and answered a question.
    "read_session": (
        READ_SESSION,
        [("GET", "readiness"), ("GET", "state"), ("POST", "chat"), ("POST", "refresh")],
    ),
    # An earlier read exists, but the refresh turn itself read nothing: 409.
    "refresh_read_nothing": (
        [
            call("outreach__list_pool", call_id="old"),
            out(POOL, "old", at=READ_AT - 600),
            USER_TURN,
            answer("I could not reach the outreach app."),
        ],
        [("GET", "state"), ("POST", "refresh")],
    ),
}


def _produce(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    produced: dict[str, Any] = {}
    for name, (items, routes) in SCENARIOS.items():
        client = _client(monkeypatch, tmp_path, items)
        produced[name] = {route: _answer(client, method, route) for method, route in routes}
    return produced


def test_the_workspace_fixtures_are_what_the_handlers_answer(monkeypatch, tmp_path) -> None:
    produced = _produce(monkeypatch, tmp_path)
    text = json.dumps(produced, indent=2, sort_keys=True) + "\n"
    if os.environ.get("EVA_WRITE_FIXTURES") == "1":
        FIXTURE.write_text(text)
    assert FIXTURE.exists(), "Generate the fixture: EVA_WRITE_FIXTURES=1, see this module."
    assert json.loads(FIXTURE.read_text()) == produced, (
        "The Eva adapter's answers changed. Regenerate the fixture (see this module's "
        "docstring) and make web/src/shell/evaWorkspaceApp.test.ts pass against it."
    )


def test_the_fixtures_cover_both_409s_the_app_must_handle(monkeypatch, tmp_path) -> None:
    produced = _produce(monkeypatch, tmp_path)
    assert produced["fresh_session"]["state"]["status"] == 409
    assert produced["refresh_read_nothing"]["refresh"]["status"] == 409
    assert produced["read_session"]["refresh"]["status"] == 200
