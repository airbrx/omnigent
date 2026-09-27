"""Tally's workspace: state from recorded tool results, and the adapter routes.

The state tests use the item shapes Eva's deployment stores (bare server-prefixed
names, results sometimes wrapped as ``{"result": "<json>"}``, a reused
``call_id``). Fixtures follow the portal's real ``structuredContent`` shapes, small and
synthetic. A value that was not read, or not reported, is ``None`` with a
reason, never 0.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from omnigent.airbrx.tally.config import Binding
from omnigent.airbrx.tally.routes import create_tally_router
from omnigent.airbrx.tally.workspace import (
    REFRESH_PROMPT,
    bare_tool_name,
    completed_answer,
    refreshed_by,
    tool_results,
    workspace_state,
)

BINDING = Binding(
    users=("local",),
    host_id="",
    base_url="http://127.0.0.1:4318",
    token_ref="env:AIRBRX_TALLY_MCP_TOKEN",
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


def answer(text: str = "2 agents tracked") -> dict[str, Any]:
    return {
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": text}],
    }


SPRINT_MD = """# Sprint 14

## Decisions needed from Abram

- Pick the Superset host for the pilot dashboards
  - context: two candidates, both priced
- Approve the portal read-token rotation window
1. Decide whether Tally v2 gets a write boundary

## Blockers

| Item | Owner | Since |
|---|---|---|
| Portal token rotation | ops | 2026-09-20 |
| Superset SSO callback | platform | 2026-09-22 |

## Done

- Shipped the analytics overview
"""
BOARD = {"markdown": SPRINT_MD, "truncated": False}
AGENT_ROW = {
    "agent_id": "iris",
    "label": "iris",
    "run_count": 2,
    "input_tokens": 1383,
    "output_tokens": 692,
    "cache_read_tokens": 0,
    "cache_write_tokens": 1443,
    "api_equivalent_usd": 0.023187,
    "actual_charges_usd": None,
    "subscription_fee_usd": None,
    "databricks_list_price_usd": None,
    "avoided_data_cost_usd": None,
    "event_count": 2,
}
ANALYTICS = {
    "mode": "live_evidence",
    "updated_at": "2026-09-26T10:00:00Z",
    "summary": {
        "run_count": 3,
        "input_tokens": 2000,
        "output_tokens": 900,
        "api_equivalent_usd": 0.031,
        "actual_charges_usd": None,
        "databricks_list_price_usd": None,
        "avoided_data_cost_usd": None,
    },
    "agents": [AGENT_ROW, {**AGENT_ROW, "agent_id": "eva", "label": "eva", "run_count": 1}],
    "gateway": {},
    "limitations": ["actual charges are not reported yet"],
    "organization": {},
    "hosts": [],
    "source_coverage": {},
    "airbrx_mcp": {},
    "airbrx_pricing": {},
    "model_gateway": {},
    "pricing": {},
    "run_count": 3,
}
HEALTH = {"status": "ok", "mode": "live_evidence_with_fixtures"}
POLICY = {
    "desired": {"version": 3},
    "applied": {"version": 3},
    "observed": {"version": 2},
    "audit": [{"at": "2026-09-25T09:00:00Z", "action": "apply"}],
    "storage": "append-only event log",
}


# --------------------------------------------------------------------------
# Reading recorded results
# --------------------------------------------------------------------------


def test_names_lose_only_this_servers_prefixes() -> None:
    assert bare_tool_name("mcp__omnigent__portal__get_health") == "get_health"
    assert bare_tool_name("portal__get_health") == "get_health"
    assert bare_tool_name("mcp__claude_ai_Slack__get_health") == "mcp__claude_ai_Slack__get_health"
    assert bare_tool_name(None) == ""


def test_a_reused_call_id_pairs_each_result_with_its_own_call() -> None:
    items = [
        call("ToolSearch", {"query": "select:get_health"}, "X"),
        out([{"type": "tool_reference"}], "X"),
        call("portal__get_health", {}, "X"),
        out(HEALTH, "X"),
    ]
    assert [r["tool"] for r in tool_results(items)] == ["ToolSearch", "get_health"]


def test_an_empty_session_says_so_and_every_kpi_is_unread() -> None:
    state = workspace_state([], BINDING)
    assert state["empty"] is True
    assert state["portal_url"] == "/gateway/app/"
    for kpi in state["kpis"].values():
        assert kpi["value"] is None
        assert kpi["reason"] == "not read yet"


def _all_reads() -> list[dict[str, Any]]:
    return [
        call("portal__get_sprint_board", {}, "a"),
        out(BOARD, "a", at=100),
        call("portal__get_analytics_overview", {}, "b"),
        out(ANALYTICS, "b", at=110, wrap=True),
        call("portal__get_health", {}, "c"),
        out(HEALTH, "c", at=120),
    ]


def test_kpis_come_from_the_portals_real_shapes() -> None:
    state = workspace_state(_all_reads(), BINDING)
    k = state["kpis"]
    # Two top-level decisions and one numbered; the nested context line is not one.
    assert k["decisions_waiting"]["value"] == 3
    assert k["blockers"]["value"] == 2
    assert k["agents_tracked"]["value"] == 2
    assert k["data_freshness"]["value"] == "2026-09-26T10:00:00Z"
    assert state["blockers"] == ["Portal token rotation", "Superset SSO callback"]
    assert state["decisions"][0] == "Pick the Superset host for the pilot dashboards"
    assert state["refreshed_at"] == 120
    assert state["empty"] is False


def test_api_equivalent_spend_is_labelled_an_estimate_and_charges_stay_null() -> None:
    spend = workspace_state(_all_reads(), BINDING)["kpis"]["api_equivalent_usd"]
    assert spend["value"] == 0.031
    assert spend["basis"] == "estimated"
    assert spend["actual_charges_usd"] is None


def test_a_structured_content_envelope_is_unwrapped() -> None:
    envelope = {"content": [{"type": "text", "text": "..."}], "structuredContent": HEALTH}
    state = workspace_state([call("portal__get_health"), out(envelope)], BINDING)
    assert state["health"]["data"] == HEALTH


def test_a_missing_section_is_unavailable_not_zero() -> None:
    board = {"markdown": "# Sprint 14\n\n## Done\n\n- a thing\n", "truncated": True}
    k = workspace_state([call("portal__get_sprint_board"), out(board)], BINDING)["kpis"]
    for name in ("decisions_waiting", "blockers"):
        assert k[name]["value"] is None
        assert "no" in k[name]["reason"] and "truncated" in k[name]["reason"]


def test_an_empty_blockers_table_is_a_real_zero() -> None:
    md = "## Decisions needed from Abram\n\nNone this week.\n\n## Blockers\n\n| Item |\n|---|\n"
    k = workspace_state(
        [call("portal__get_sprint_board"), out({"markdown": md, "truncated": False})], BINDING
    )["kpis"]
    assert k["blockers"]["value"] == 0
    assert k["decisions_waiting"]["value"] == 0


def test_a_blockers_section_without_a_table_is_unavailable() -> None:
    md = "## Blockers\n\nSee the thread.\n"
    k = workspace_state(
        [call("portal__get_sprint_board"), out({"markdown": md, "truncated": False})], BINDING
    )["kpis"]
    assert k["blockers"]["value"] is None


def test_values_the_portal_did_not_report_are_unavailable_not_zero() -> None:
    items = [
        call("portal__get_sprint_board", {}, "a"),
        out({"truncated": False}, "a"),
        call("portal__get_analytics_overview", {}, "b"),
        out({"mode": "live_evidence", "summary": {"api_equivalent_usd": None}}, "b"),
    ]
    k = workspace_state(items, BINDING)["kpis"]
    for name in (
        "decisions_waiting",
        "blockers",
        "agents_tracked",
        "data_freshness",
        "api_equivalent_usd",
    ):
        assert k[name]["value"] is None, name
        assert k[name]["reason"] and k[name]["reason"] != "not read yet", name


def test_a_boolean_is_not_a_number() -> None:
    bad = {**ANALYTICS, "summary": {"api_equivalent_usd": True}}
    state = workspace_state([call("portal__get_analytics_overview"), out(bad)], BINDING)
    assert state["kpis"]["api_equivalent_usd"]["value"] is None


def test_policies_are_kept_per_agent_and_the_newest_wins() -> None:
    items = [
        call("portal__get_agent_policy", {"agent": "iris"}, "a"),
        out({**POLICY, "desired": {"version": 1}}, "a", at=1),
        call("portal__get_agent_policy", {"agent": "eva"}, "b"),
        out({"version": 7}, "b", at=2),
        call("portal__get_agent_policy", {"agent": "iris"}, "c"),
        out(POLICY, "c", at=3),
    ]
    policies = workspace_state(items, BINDING)["policies"]
    assert policies["iris"]["data"]["storage"] == "append-only event log"
    assert policies["eva"]["data"] == {"version": 7}


def test_an_error_result_is_an_error_not_data() -> None:
    items = [call("portal__get_health"), out({"error": {"message": "portal down"}})]
    state = workspace_state(items, BINDING)
    assert state["empty"] is True
    assert state["errors"][-1] == {"tool": "get_health", "message": "portal down", "at": 100}


def test_a_policy_denial_is_not_mistaken_for_data() -> None:
    denied = '[Denied by policy: tally-tool-boundary] {"reason": "read-only"}'
    state = workspace_state([call("portal__save_policy"), out(denied)], BINDING)
    assert state["empty"] is True


def test_a_foreign_tool_result_is_ignored() -> None:
    state = workspace_state([call("mcp__claude_ai_Slack__get_health"), out(HEALTH)], BINDING)
    assert state["empty"] is True


def test_refreshed_by_needs_a_successful_read_after_the_marker() -> None:
    before = [call("portal__get_health", call_id="a"), out(HEALTH, "a")]
    marker = {"id": "u1", "type": "message", "role": "user"}
    assert refreshed_by([*before, marker, answer()], "u1") is False
    after = [marker, call("portal__get_health", call_id="b"), out(HEALTH, "b")]
    assert refreshed_by([*before, *after], "u1") is True


def test_the_refresh_prompt_asks_only_for_reads() -> None:
    for read in ("get_health", "get_analytics_overview", "get_sprint_board", "get_agent_policy"):
        assert read in REFRESH_PROMPT
    assert "Do not change, publish or write anything" in REFRESH_PROMPT
    assert "—" not in REFRESH_PROMPT


# --------------------------------------------------------------------------
# The boundary
# --------------------------------------------------------------------------


def test_a_turn_with_her_own_tools_is_accepted() -> None:
    result = completed_answer(
        [
            {"type": "function_call", "name": "ToolSearch"},
            {"type": "function_call", "name": "portal__get_health"},
            {"type": "function_call", "name": "mcp__omnigent__portal__get_agent_policy"},
            answer(),
        ]
    )
    assert result is not None
    assert result["tools"] == ["ToolSearch", "get_health", "get_agent_policy"]


@pytest.mark.parametrize(
    "name",
    [
        "portal__save_policy",
        "portal__publish_config_revision",
        "mcp__claude_ai_Gmail__send_message",
        "mcp__claude_ai_Slack__slack_send_message",
        "mcp__omnigent__sys_os_shell",
        "outreach__list_pool",
        "Bash",
        "Skill",
    ],
)
def test_a_turn_reaching_outside_her_boundary_is_rejected(name: str) -> None:
    with pytest.raises(HTTPException) as caught:
        completed_answer([{"type": "function_call", "name": name}, answer()])
    assert caught.value.status_code == 409


def test_an_unfinished_turn_has_no_answer() -> None:
    assert completed_answer([{"type": "function_call", "name": "portal__get_health"}]) is None


# --------------------------------------------------------------------------
# Routes, against a fake native session API in the same app
# --------------------------------------------------------------------------


class _Agent:
    id = "agent-tally"


class _AgentStore:
    def get_by_name(self, name: str) -> Any:
        return _Agent() if name == "tally" else None


def _app(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    items: list[dict[str, Any]],
    session: dict[str, Any] | None = None,
    user: str | None = "local",
    rows: list[dict[str, Any]] | None = None,
) -> TestClient:
    cfg = tmp_path / "tally.json"
    cfg.write_text(
        json.dumps(
            rows
            or [
                {
                    "users": ["local"],
                    "base_url": BINDING.base_url,
                    "token_ref": BINDING.token_ref,
                }
            ]
        )
    )
    monkeypatch.setenv("OMNIGENT_TALLY_CONFIG", str(cfg))
    for module in ("omnigent.airbrx.tally.routes", "omnigent.airbrx.tally.workspace"):
        monkeypatch.setattr(f"{module}.require_user", lambda request, provider: user)
    snapshot = {
        "id": "s1",
        "agent_id": "agent-tally",
        "host_id": None,
        "status": "idle",
        "permission_level": 4,
        **(session or {}),
    }
    app = FastAPI()
    app.include_router(
        create_tally_router(auth_provider=object(), agent_store=_AgentStore()), prefix="/v1"
    )

    @app.get("/v1/sessions/{session_id}")
    async def get_session(session_id: str) -> dict[str, Any]:
        return snapshot

    @app.get("/v1/sessions/{session_id}/items")
    async def get_items(session_id: str, order: str = "asc") -> dict[str, Any]:
        data = list(reversed(items)) if order == "desc" else items
        return {"data": data, "has_more": False}

    @app.post("/v1/sessions/{session_id}/events")
    async def post_event(session_id: str) -> dict[str, Any]:
        return {"queued": True, "item_id": "u1"}

    return TestClient(app)


IRIS_CHAT_KEYS = {"text", "tools", "failed", "item_id"}
IRIS_READINESS_KEYS = {
    "tenant_id",
    "name",
    "fixture",
    "session_status",
    "turn_completed_here",
    "last_task_failed",
    "verified",
    "unverified",
}
USER_TURN = {"id": "u1", "type": "message", "role": "user", "created_at": 90}


def test_state_is_built_from_the_sessions_record(monkeypatch, tmp_path) -> None:
    client = _app(monkeypatch, tmp_path, items=[call("portal__get_health"), out(HEALTH)])
    body = client.get("/v1/tally/sessions/s1/ui/api/state").json()
    assert body["health"]["data"]["status"] == "ok"
    assert body["portal_url"] == "/gateway/app/"


def test_chat_answers_with_iris_keys_exactly(monkeypatch, tmp_path) -> None:
    client = _app(
        monkeypatch, tmp_path, items=[USER_TURN, call("portal__get_health"), out(HEALTH), answer()]
    )
    response = client.post(
        "/v1/tally/sessions/s1/ui/api/chat",
        json={"history": [{"role": "user", "content": "Is the portal healthy?"}]},
    )
    assert response.status_code == 200
    body = response.json()
    assert set(body) == IRIS_CHAT_KEYS
    assert body["failed"] is None and body["item_id"] == "u1"


def test_a_turn_that_used_a_write_tool_is_rejected(monkeypatch, tmp_path) -> None:
    client = _app(
        monkeypatch, tmp_path, items=[USER_TURN, call("portal__save_policy"), out("x"), answer()]
    )
    response = client.post(
        "/v1/tally/sessions/s1/ui/api/chat",
        json={"history": [{"role": "user", "content": "Tighten Eva's policy"}]},
    )
    assert response.status_code == 409


def test_readiness_carries_every_iris_key(monkeypatch, tmp_path) -> None:
    body = (
        _app(monkeypatch, tmp_path, items=[]).get("/v1/tally/sessions/s1/ui/api/readiness").json()
    )
    assert set(body) >= IRIS_READINESS_KEYS
    assert body["tenant_id"] == "live" and body["fixture"] is False
    assert body["portal_url"] == "/gateway/app/"
    assert body["turn_completed_here"] is False
    assert body["unverified"]


def test_empty_state_is_a_409(monkeypatch, tmp_path) -> None:
    response = _app(monkeypatch, tmp_path, items=[]).get("/v1/tally/sessions/s1/ui/api/state")
    assert response.status_code == 409
    assert "Refresh" in response.json()["detail"]


def test_refresh_answers_with_the_state_itself(monkeypatch, tmp_path) -> None:
    items = [USER_TURN, call("portal__get_health"), out(HEALTH), answer()]
    client = _app(monkeypatch, tmp_path, items=items)
    response = client.post("/v1/tally/sessions/s1/ui/api/refresh", json={})
    assert response.status_code == 200
    assert response.json()["health"]["data"]["status"] == "ok"


def test_a_refresh_that_read_nothing_new_is_a_409(monkeypatch, tmp_path) -> None:
    earlier = [call("portal__get_health", call_id="c0"), out(HEALTH, call_id="c0", at=10)]
    client = _app(monkeypatch, tmp_path, items=[*earlier, USER_TURN, answer()])
    assert client.post("/v1/tally/sessions/s1/ui/api/refresh", json={}).status_code == 409


def test_an_unauthenticated_caller_gets_401(monkeypatch, tmp_path) -> None:
    client = _app(monkeypatch, tmp_path, items=[], user=None)
    assert client.get("/v1/tally/sessions/s1/ui/api/state").status_code == 401


def test_another_agents_session_is_not_tally(monkeypatch, tmp_path) -> None:
    client = _app(monkeypatch, tmp_path, items=[], session={"agent_id": "agent-eva"})
    assert client.get("/v1/tally/sessions/s1/ui/api/state").status_code == 404


def test_a_caller_without_a_binding_is_refused(monkeypatch, tmp_path) -> None:
    client = _app(monkeypatch, tmp_path, items=[], user="someone-else")
    assert client.get("/v1/tally/sessions/s1/ui/api/state").status_code == 403


def test_a_hostless_binding_authorizes_a_session_on_any_host(monkeypatch, tmp_path) -> None:
    """Eva's rule: an empty host_id does not pin the session to a host."""
    client = _app(
        monkeypatch,
        tmp_path,
        items=[call("portal__get_health"), out(HEALTH)],
        session={"host_id": "f" * 32},
    )
    assert client.get("/v1/tally/sessions/s1/ui/api/state").status_code == 200


def test_a_binding_named_for_another_host_is_refused(monkeypatch, tmp_path) -> None:
    rows = [
        {
            "users": ["local"],
            "host_id": "a" * 32,
            "base_url": BINDING.base_url,
            "token_ref": BINDING.token_ref,
        }
    ]
    client = _app(monkeypatch, tmp_path, items=[], rows=rows, session={"host_id": "f" * 32})
    assert client.get("/v1/tally/sessions/s1/ui/api/state").status_code == 403


def test_read_only_permission_is_refused(monkeypatch, tmp_path) -> None:
    client = _app(monkeypatch, tmp_path, items=[], session={"permission_level": 1})
    assert client.get("/v1/tally/sessions/s1/ui/api/state").status_code == 403


def test_the_app_and_only_its_assets_are_served(monkeypatch, tmp_path) -> None:
    client = _app(monkeypatch, tmp_path, items=[])
    page = client.get("/v1/tally/sessions/s1/ui/")
    assert page.status_code == 200
    assert "Tally" in page.text
    assert page.headers["x-frame-options"] == "SAMEORIGIN"
    for asset in ("app.js", "style.css", "assets/airbrx-logo.png", "assets/tally-portrait.png"):
        assert client.get(f"/v1/tally/sessions/s1/ui/{asset}").status_code == 200, asset
    for asset in ("../config.py", "bundle/config.yaml", "nope.js", "assets/eva-portrait.png"):
        assert client.get(f"/v1/tally/sessions/s1/ui/{asset}").status_code == 404, asset


def test_the_portrait_needs_authentication(monkeypatch, tmp_path) -> None:
    assert _app(monkeypatch, tmp_path, items=[]).get("/v1/tally/portrait").status_code == 200
    anonymous = _app(monkeypatch, tmp_path, items=[], user=None)
    assert anonymous.get("/v1/tally/portrait").status_code == 401


# --------------------------------------------------------------------------
# The shipped app
# --------------------------------------------------------------------------

UI = Path(__file__).resolve().parents[2] / "omnigent/airbrx/tally/ui"


def test_no_em_dash_anywhere_in_the_workspace() -> None:
    for path in [*UI.rglob("*.html"), *UI.rglob("*.css"), *UI.rglob("*.js")]:
        assert "—" not in path.read_text(), path


def test_the_app_never_renders_data_as_markup() -> None:
    source = (UI / "app.js").read_text()
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
        assert sink not in source, sink


def test_the_app_names_no_write_tool() -> None:
    source = (UI / "app.js").read_text()
    for write in ("save_policy", "publish_config_revision", "update_"):
        assert write not in source, write


def test_the_tabs_frame_the_portal_through_the_gateway_proxy() -> None:
    source = (UI / "app.js").read_text()
    for path in ("/gateway/app/#overview", "/gateway/app/#agents", "/gateway/app/#board"):
        assert path in source
    assert "/eva/app" not in source
    assert "tallyHostTheme" in source


def test_the_spend_tile_is_labelled_an_estimate() -> None:
    page = (UI / "index.html").read_text()
    assert 'id="kpi-spend"' in page
    assert "estimated at published rates, not a charge" in page
    source = (UI / "app.js").read_text()
    assert "api_equivalent" in source and "actual_charges" in source
