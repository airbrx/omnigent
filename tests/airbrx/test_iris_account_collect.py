"""The account collector reads only what the spec allows out of a session.

It walks the caller's Iris sessions, keeps the newest iris_overview per bound
tenant, and reads that one report's tenant_id and metrics. The rest of the
report never leaves this seam.
"""

import json

import httpx
import pytest
from fastapi import HTTPException

from omnigent.airbrx.iris.account import collect_captures
from omnigent.airbrx.iris.config import Binding


def binding(tenant_id, workspace, host_id="host-1"):
    return Binding(
        tenant_id=tenant_id,
        host_id=host_id,
        workspace=workspace,
        users=("ann@example.com",),
        pat_ref="keychain:x",
    )


HOT, COLD = binding("t-hot", "/w/hot"), binding("t-cold", "/w/cold")


def session(sid, workspace, host_id="host-1"):
    return {"id": sid, "agent_id": "ag_iris", "host_id": host_id, "workspace": workspace}


def turn(call_id, tool, file_id, created_at):
    """A recorded call and its output carrying a report.json download."""
    return [
        {"type": "function_call", "call_id": call_id, "name": f"mcp__omnigent__{tool}"},
        {
            "type": "function_call_output",
            "call_id": call_id,
            "created_at": created_at,
            "output": json.dumps({"downloads": [{"filename": "report.json", "file_id": file_id}]}),
        },
    ]


def page(data, has_more=False, last_id=None):
    return {
        "object": "list",
        "data": data,
        "has_more": has_more,
        "first_id": None,
        "last_id": last_id,
    }


REPORT = {
    "tenant_id": "t-hot",
    "metrics": {
        "requests": 10,
        "cache_hits": 8,
        "cache_misses": 2,
        "hit_rate": 0.8,
        "hit_rate_denominator": 10,
        "covered_days": 7,
        "requested_days": 7,
        "period_complete": True,
    },
    "evidence": [{"id": "e1", "data": {"rows": ["select secret"]}}],
    "findings": [{"id": "f1"}],
    "message": "Read-only evidence.",
}


class FakeApi:
    """Canned native API. `calls` records every path so tests can assert restraint."""

    def __init__(self, sessions, items, reports, statuses=None):
        self.sessions, self.items, self.reports = sessions, items, reports
        self.statuses = statuses or {}
        self.calls = []

    async def get(self, path, params=None):
        self.calls.append((path, dict(params or {})))
        if path in self.statuses:
            return httpx.Response(self.statuses[path], json={"detail": "nope"})
        if path == "/v1/sessions":
            after = (params or {}).get("after")
            pages = (
                self.sessions
                if isinstance(self.sessions, list)
                and self.sessions
                and isinstance(self.sessions[0], dict)
                and "data" in self.sessions[0]
                else [page(self.sessions)]
            )
            index = (
                0
                if after is None
                else next(i for i, p in enumerate(pages) if p["last_id"] == after) + 1
            )
            return httpx.Response(200, json=pages[index])
        if path.startswith("/v1/sessions/") and path.endswith("/items"):
            sid = path.split("/")[3]
            # The native API answers order=desc newest-first; mirror that.
            data = list(reversed(self.items.get(sid, [])))
            return httpx.Response(200, json=page(data))
        if "/resources/files/" in path:
            file_id = path.split("/")[-2]
            return httpx.Response(200, json=self.reports[file_id])
        raise AssertionError(f"unexpected path {path}")


async def test_the_newest_overview_per_tenant_wins_across_sessions():
    api = FakeApi(
        sessions=[session("s1", "/w/hot"), session("s2", "/w/hot"), session("s3", "/w/cold")],
        items={
            "s1": turn("c1", "iris_overview", "f-old", 100.0)
            + turn("c2", "iris_audit", "f-audit", 150.0),
            "s2": turn("c3", "iris_overview", "f-new", 200.0),
            "s3": [],
        },
        reports={
            "f-new": REPORT,
            "f-old": {**REPORT, "metrics": {}},
            "f-audit": {"tenant_id": "t-hot"},
        },
    )
    result = await collect_captures(api.get, agent_id="ag_iris", bindings=[HOT, COLD])
    assert set(result) == {"t-hot", "t-cold"}
    assert result["t-cold"] is None
    assert result["t-hot"] == {
        "tenant_id": "t-hot",
        "metrics": REPORT["metrics"],
        "captured_at": 200.0,
    }
    # Only the winning report was read, and the audit report never was.
    content = [p for p, _ in api.calls if "/resources/files/" in p]
    assert content == ["/v1/sessions/s2/resources/files/f-new/content"]


async def test_nothing_but_tenant_id_and_metrics_leaves_the_seam():
    api = FakeApi(
        sessions=[session("s1", "/w/hot")],
        items={"s1": turn("c1", "iris_overview", "f1", 1.0)},
        reports={"f1": REPORT},
    )
    result = await collect_captures(api.get, agent_id="ag_iris", bindings=[HOT])
    assert set(result["t-hot"]) == {"tenant_id", "metrics", "captured_at"}
    assert "select secret" not in json.dumps(result)


async def test_the_session_list_is_filtered_to_iris_and_includes_archived():
    api = FakeApi(sessions=[], items={}, reports={})
    await collect_captures(api.get, agent_id="ag_iris", bindings=[HOT])
    [(path, params)] = api.calls
    assert path == "/v1/sessions"
    assert params["agent_id"] == "ag_iris"
    assert params["include_archived"] == "true"
    assert params["limit"] == 1000


async def test_session_pages_are_followed_to_the_end():
    api = FakeApi(
        sessions=[
            page([session("s1", "/w/hot")], has_more=True, last_id="s1"),
            page([session("s2", "/w/cold")]),
        ],
        items={"s1": [], "s2": turn("c1", "iris_overview", "f1", 5.0)},
        reports={"f1": {**REPORT, "tenant_id": "t-cold"}},
    )
    result = await collect_captures(api.get, agent_id="ag_iris", bindings=[HOT, COLD])
    assert result["t-hot"] is None
    assert result["t-cold"]["captured_at"] == 5.0
    assert [p for p, _ in api.calls if p == "/v1/sessions"] == ["/v1/sessions", "/v1/sessions"]


async def test_a_session_that_matches_no_binding_is_skipped_without_reading_it():
    api = FakeApi(
        sessions=[session("stranger", "/w/other")],
        items={"stranger": turn("c1", "iris_overview", "f1", 1.0)},
        reports={"f1": REPORT},
    )
    result = await collect_captures(api.get, agent_id="ag_iris", bindings=[HOT])
    assert result == {"t-hot": None}
    assert all("stranger" not in p for p, _ in api.calls)


async def test_a_binding_on_another_host_with_the_same_workspace_does_not_match():
    api = FakeApi(
        sessions=[session("s1", "/w/hot", host_id="host-2")],
        items={"s1": turn("c1", "iris_overview", "f1", 1.0)},
        reports={"f1": REPORT},
    )
    result = await collect_captures(api.get, agent_id="ag_iris", bindings=[HOT])
    assert result == {"t-hot": None}


async def test_an_unreadable_report_is_an_error_for_that_tenant_only():
    api = FakeApi(
        sessions=[session("s1", "/w/hot"), session("s2", "/w/cold")],
        items={
            "s1": turn("c1", "iris_overview", "f1", 1.0),
            "s2": turn("c2", "iris_overview", "f2", 1.0),
        },
        reports={"f2": {**REPORT, "tenant_id": "t-cold"}},
        statuses={"/v1/sessions/s1/resources/files/f1/content": 502},
    )
    result = await collect_captures(api.get, agent_id="ag_iris", bindings=[HOT, COLD])
    assert result["t-hot"] == {"error": "the newest report could not be read (HTTP 502)"}
    assert result["t-cold"]["tenant_id"] == "t-cold"


async def test_unreadable_items_fail_that_tenant_closed_even_if_another_session_had_a_capture():
    api = FakeApi(
        sessions=[session("s1", "/w/hot"), session("s2", "/w/hot")],
        items={"s1": turn("c1", "iris_overview", "f1", 1.0), "s2": []},
        reports={"f1": REPORT},
        statuses={"/v1/sessions/s2/items": 500},
    )
    result = await collect_captures(api.get, agent_id="ag_iris", bindings=[HOT])
    assert result["t-hot"] == {"error": "a session's record could not be read (HTTP 500)"}


async def test_a_failed_session_list_fails_the_whole_account():
    api = FakeApi(sessions=[], items={}, reports={}, statuses={"/v1/sessions": 503})
    with pytest.raises(HTTPException) as caught:
        await collect_captures(api.get, agent_id="ag_iris", bindings=[HOT])
    assert caught.value.status_code == 502


async def test_a_capture_that_names_another_tenant_is_passed_through_for_rank_to_refuse():
    # The collector does not judge; rank() quarantines this as tenant_mismatch.
    api = FakeApi(
        sessions=[session("s1", "/w/cold")],
        items={"s1": turn("c1", "iris_overview", "f1", 1.0)},
        reports={"f1": REPORT},
    )
    result = await collect_captures(api.get, agent_id="ag_iris", bindings=[COLD])
    assert result["t-cold"]["tenant_id"] == "t-hot"
