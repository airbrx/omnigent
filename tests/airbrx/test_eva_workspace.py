"""Eva's workspace: state from recorded tool results, and the adapter routes.

The state tests use the item shapes the deployment actually stores: bare
``outreach__`` names on calls, results sometimes wrapped as ``{"result": "<json>"}``,
a ``call_id`` reused for consecutive calls in one turn, and rows keyed ``id``
where the contract says ``lead_id``. All four were seen in real Eva sessions.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from omnigent.airbrx.eva.config import Binding
from omnigent.airbrx.eva.routes import create_eva_router
from omnigent.airbrx.eva.workspace import (
    REFRESH_PROMPT,
    bare_tool_name,
    completed_answer,
    tool_results,
    workspace_state,
)

LEAD = "d870d43d-380a-45a5-a566-d7744de76ddf"
DRAFT = "5b1f0c4e-8b8b-4c55-9d7e-0d4a1a2b3c4d"
BINDING = Binding(
    users=("local",),
    host_id="020a51244fbf461cbf6207e5d80bbc18",
    base_url="http://127.0.0.1:8000",
    token_ref="keychain:eva-outreach-token",
    workspace="/tmp/eva",
)


def call(name: str, args: dict[str, Any] | None = None, call_id: str = "c1") -> dict[str, Any]:
    return {
        "type": "function_call",
        "name": name,
        "arguments": json.dumps(args or {}),
        "call_id": call_id,
    }


def out(result: Any, call_id: str = "c1", at: int = 100, wrap: bool = False) -> dict[str, Any]:
    text = result if isinstance(result, str) else json.dumps(result)
    if wrap:
        text = json.dumps({"result": text})
    return {"type": "function_call_output", "call_id": call_id, "output": text, "created_at": at}


def answer(text: str = "115 in the pool") -> dict[str, Any]:
    return {
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": text}],
    }


POOL = {
    "leads": [{"id": LEAD, "name": "Brad Fair", "company": "High Performance Technologies"}],
    "next_cursor": "50",
    "total": 115,
}


# --------------------------------------------------------------------------
# Reading recorded results
# --------------------------------------------------------------------------


def test_names_lose_only_this_servers_prefixes() -> None:
    assert bare_tool_name("mcp__omnigent__outreach__list_pool") == "list_pool"
    assert bare_tool_name("outreach__list_pool") == "list_pool"
    # Another server's tool must not become one of Eva's by stripping.
    assert bare_tool_name("mcp__claude_ai_Slack__list_pool") == "mcp__claude_ai_Slack__list_pool"
    assert bare_tool_name(None) == ""


def test_a_reused_call_id_pairs_each_result_with_its_own_call() -> None:
    """ToolSearch and list_pool shared a call_id in a real turn."""
    items = [
        call("ToolSearch", {"query": "select:list_pool"}, "X"),
        out([{"type": "tool_reference"}], "X"),
        call("outreach__list_pool", {}, "X"),
        out(POOL, "X"),
    ]
    assert [r["tool"] for r in tool_results(items)] == ["ToolSearch", "list_pool"]


def test_a_wrapped_result_is_decoded() -> None:
    items = [call("outreach__list_pool"), out(POOL, wrap=True)]
    state = workspace_state(items, BINDING)
    assert state["pool"]["total"] == 115


def test_an_empty_session_says_so_and_invents_nothing() -> None:
    state = workspace_state([], BINDING)
    assert state["empty"] is True
    assert state["pool"] is None and state["mine"] is None
    assert state["leads"] == {} and state["drafts"] == []
    assert state["outreach_url"] == "http://127.0.0.1:8000"


def test_pool_and_my_leads_come_from_the_newest_result() -> None:
    older = {"leads": [], "total": 100}
    items = [
        call("outreach__list_pool", {}, "a"),
        out(older, "a", at=100),
        call("outreach__list_pool", {}, "b"),
        out(POOL, "b", at=200),
        call("outreach__list_my_leads", {}, "c"),
        out({"leads": [{"id": LEAD, "relationship": "claimant"}], "total": 1}, "c", at=210),
    ]
    state = workspace_state(items, BINDING)
    assert state["pool"]["total"] == 115
    assert state["mine"]["total"] == 1
    assert state["refreshed_at"] == 210
    assert state["empty"] is False


def test_get_lead_fills_detail_and_its_drafts() -> None:
    lead = {
        "profile": {"id": LEAD, "name": "Brad Fair", "company": "HPT"},
        "qualification": None,
        "touches": [],
        "drafts": [
            {
                "draft_id": DRAFT,
                "channel": "Email",
                "status": "draft",
                "version_no": 1,
                "subject": "Hi",
            }
        ],
    }
    state = workspace_state([call("outreach__get_lead", {"lead_id": LEAD}), out(lead)], BINDING)
    assert state["leads"][LEAD]["profile"]["name"] == "Brad Fair"
    (draft,) = state["drafts"]
    assert draft["draft_id"] == DRAFT
    assert draft["lead_id"] == LEAD
    assert draft["company"] == "HPT"


def test_submit_draft_carries_its_rule_results() -> None:
    result = {
        "draft_id": DRAFT,
        "version_no": 2,
        "status": "draft",
        "blocked": False,
        "rule_results": [{"key": "referral-terms", "severity": "warn", "status": "fail"}],
    }
    args = {"lead_id": LEAD, "subject": "Cache the repeat reads", "channel": "Email"}
    state = workspace_state([call("outreach__submit_draft", args), out(result)], BINDING)
    (draft,) = state["drafts"]
    assert draft["version_no"] == 2
    assert draft["subject"] == "Cache the repeat reads"
    assert draft["rule_results"][0]["key"] == "referral-terms"
    assert draft["blocked"] is False


def test_a_blocked_submission_is_a_blocked_draft_and_an_error() -> None:
    refused = {
        "error": {
            "code": "blocked_by_rule",
            "message": "1 blocking rule failed",
            "details": {
                "draft_id": DRAFT,
                "version_no": 3,
                "rule_results": [{"key": "no-em-dash", "severity": "block", "status": "fail"}],
            },
        }
    }
    state = workspace_state(
        [call("outreach__submit_draft", {"lead_id": LEAD}), out(refused)], BINDING
    )
    (draft,) = state["drafts"]
    assert draft["blocked"] is True
    assert draft["rule_results"][0]["key"] == "no-em-dash"
    assert state["errors"][-1]["tool"] == "submit_draft"


def test_request_approval_moves_a_draft_to_review() -> None:
    items = [
        call("outreach__submit_draft", {"lead_id": LEAD}, "a"),
        out(
            {"draft_id": DRAFT, "version_no": 1, "status": "draft", "rule_results": []}, "a", at=1
        ),
        call("outreach__request_approval", {"draft_id": DRAFT}, "b"),
        out(
            {"draft_id": DRAFT, "status": "in_review", "approval_requested_from": "Ben"}, "b", at=2
        ),
    ]
    (draft,) = workspace_state(items, BINDING)["drafts"]
    assert draft["status"] == "in_review"
    assert draft["approval_requested_from"] == "Ben"


def test_a_policy_denial_is_not_mistaken_for_data() -> None:
    denied = '[Denied by policy: eva-tool-boundary] {"reason": "nothing in this system sends"}'
    state = workspace_state(
        [call("outreach__mark_sent", {"draft_id": DRAFT}), out(denied)], BINDING
    )
    assert state["empty"] is True


def test_the_refresh_prompt_asks_only_for_reads() -> None:
    for read in ("list_pool", "list_my_leads", "get_lead"):
        assert read in REFRESH_PROMPT
    assert "Do not claim, draft, submit or write anything" in REFRESH_PROMPT
    assert "—" not in REFRESH_PROMPT


# --------------------------------------------------------------------------
# The boundary
# --------------------------------------------------------------------------


def test_a_turn_with_her_own_tools_is_accepted() -> None:
    result = completed_answer(
        [
            {"type": "function_call", "name": "ToolSearch"},
            {"type": "function_call", "name": "outreach__list_pool"},
            answer(),
        ]
    )
    assert result is not None
    assert result["tools"] == ["ToolSearch", "list_pool"]


@pytest.mark.parametrize(
    "name",
    [
        "outreach__mark_sent",
        "outreach__approve_draft",
        "mcp__claude_ai_Gmail__list_labels",
        "mcp__omnigent__sys_os_shell",
    ],
)
def test_a_turn_reaching_outside_her_boundary_is_rejected(name: str) -> None:
    with pytest.raises(HTTPException) as caught:
        completed_answer([{"type": "function_call", "name": name}, answer()])
    assert caught.value.status_code == 409


def test_an_unfinished_turn_has_no_answer() -> None:
    assert completed_answer([{"type": "function_call", "name": "outreach__list_pool"}]) is None


# --------------------------------------------------------------------------
# Routes, against a fake native session API in the same app
# --------------------------------------------------------------------------


class _Agent:
    id = "agent-eva"


class _AgentStore:
    def get_by_name(self, name: str) -> Any:
        return _Agent() if name == "eva" else None


def _app(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    items: list[dict[str, Any]],
    session: dict[str, Any] | None = None,
    user: str | None = "local",
    binding_row: dict[str, Any] | None = None,
) -> TestClient:
    row = binding_row or {
        "users": ["local"],
        "host_id": BINDING.host_id,
        "base_url": BINDING.base_url,
        "token_ref": BINDING.token_ref,
        "workspace": "/tmp/eva",
    }
    cfg = tmp_path / "eva.json"
    cfg.write_text(json.dumps([row]))
    monkeypatch.setenv("OMNIGENT_EVA_CONFIG", str(cfg))
    for module in ("omnigent.airbrx.eva.routes", "omnigent.airbrx.eva.workspace"):
        monkeypatch.setattr(f"{module}.require_user", lambda request, provider: user)
    snapshot = {
        "id": "s1",
        "agent_id": "agent-eva",
        "host_id": BINDING.host_id,
        "status": "idle",
        "permission_level": 4,
        **(session or {}),
    }
    app = FastAPI()
    app.include_router(
        create_eva_router(auth_provider=object(), agent_store=_AgentStore()), prefix="/v1"
    )

    @app.get("/v1/sessions/{session_id}")
    async def get_session(session_id: str) -> dict[str, Any]:
        return snapshot

    @app.get("/v1/sessions/{session_id}/items")
    async def get_items(session_id: str, order: str = "asc") -> dict[str, Any]:
        data = list(reversed(items)) if order == "desc" else items
        return {"data": data, "has_more": False}

    return TestClient(app)


def test_state_is_built_from_the_sessions_record(monkeypatch, tmp_path) -> None:
    client = _app(monkeypatch, tmp_path, items=[call("outreach__list_pool"), out(POOL)])
    body = client.get("/v1/eva/sessions/s1/ui/api/state").json()
    assert body["pool"]["total"] == 115
    assert body["outreach_url"] == "http://127.0.0.1:8000"


def test_an_unauthenticated_caller_gets_401(monkeypatch, tmp_path) -> None:
    client = _app(monkeypatch, tmp_path, items=[], user=None)
    assert client.get("/v1/eva/sessions/s1/ui/api/state").status_code == 401


def test_another_agents_session_is_not_eva(monkeypatch, tmp_path) -> None:
    client = _app(monkeypatch, tmp_path, items=[], session={"agent_id": "agent-iris"})
    assert client.get("/v1/eva/sessions/s1/ui/api/state").status_code == 404


def test_a_caller_without_a_binding_is_refused(monkeypatch, tmp_path) -> None:
    client = _app(monkeypatch, tmp_path, items=[], user="someone-else")
    assert client.get("/v1/eva/sessions/s1/ui/api/state").status_code == 403


def test_a_session_on_another_host_is_refused(monkeypatch, tmp_path) -> None:
    client = _app(monkeypatch, tmp_path, items=[], session={"host_id": "f" * 32})
    assert client.get("/v1/eva/sessions/s1/ui/api/state").status_code == 403


def test_read_only_permission_is_refused(monkeypatch, tmp_path) -> None:
    client = _app(monkeypatch, tmp_path, items=[], session={"permission_level": 1})
    assert client.get("/v1/eva/sessions/s1/ui/api/state").status_code == 403


def test_readiness_names_what_is_unverified_before_a_turn(monkeypatch, tmp_path) -> None:
    client = _app(monkeypatch, tmp_path, items=[])
    body = client.get("/v1/eva/sessions/s1/ui/api/readiness").json()
    assert body["turn_completed_here"] is False
    assert body["unverified"]
    assert body["outreach_url"] == "http://127.0.0.1:8000"


def test_the_app_and_only_its_assets_are_served(monkeypatch, tmp_path) -> None:
    client = _app(monkeypatch, tmp_path, items=[])
    page = client.get("/v1/eva/sessions/s1/ui/")
    assert page.status_code == 200
    assert "Eva" in page.text
    assert page.headers["x-frame-options"] == "SAMEORIGIN"
    for asset in ("app.js", "style.css", "assets/airbrx-logo.png", "assets/eva-portrait.png"):
        assert client.get(f"/v1/eva/sessions/s1/ui/{asset}").status_code == 200, asset
    for asset in ("../config.py", "bundle/config.yaml", "nope.js"):
        assert client.get(f"/v1/eva/sessions/s1/ui/{asset}").status_code == 404, asset


def test_the_portrait_needs_authentication(monkeypatch, tmp_path) -> None:
    assert _app(monkeypatch, tmp_path, items=[]).get("/v1/eva/portrait").status_code == 200
    anonymous = _app(monkeypatch, tmp_path, items=[], user=None)
    assert anonymous.get("/v1/eva/portrait").status_code == 401


def test_the_catalog_exposes_the_workspace_directory(monkeypatch, tmp_path) -> None:
    body = _app(monkeypatch, tmp_path, items=[]).get("/v1/eva").json()
    assert body["bindings"][0]["workspace"] == "/tmp/eva"


# --------------------------------------------------------------------------
# The shipped app
# --------------------------------------------------------------------------

UI = Path(__file__).resolve().parents[2] / "omnigent/airbrx/eva/ui"


def test_no_em_dash_anywhere_in_the_workspace() -> None:
    for path in [*UI.rglob("*.html"), *UI.rglob("*.css"), *UI.rglob("*.js")]:
        assert "—" not in path.read_text(), path


def test_the_app_never_renders_data_as_markup() -> None:
    """CRM text is data. The app builds DOM nodes and sets textContent only."""
    source = (UI / "app.js").read_text()
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
        assert sink not in source, sink


def test_the_app_has_no_approve_or_mark_sent_action() -> None:
    source = (UI / "app.js").read_text()
    assert "approve_draft" not in source
    assert "mark_sent" not in source
