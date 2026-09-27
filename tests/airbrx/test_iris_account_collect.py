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
            status = self.statuses[path]
            # A dict lets a test fail only one page of a paginated path, keyed
            # by the `after` param that request used (None for the first page).
            if isinstance(status, dict):
                status = status.get((params or {}).get("after"))
            if status is not None:
                return httpx.Response(status, json={"detail": "nope"})
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
            entry = self.items.get(sid, [])
            # A flat list is chronological and served as one desc page (mirrors
            # the native API's order=desc). A list of already-built `page(...)`
            # dicts lets a test serve several item pages keyed by `after`.
            pages = (
                entry
                if isinstance(entry, list)
                and entry
                and isinstance(entry[0], dict)
                and "data" in entry[0]
                else [page(list(reversed(entry)))]
            )
            after = (params or {}).get("after")
            index = (
                0
                if after is None
                else next(i for i, p in enumerate(pages) if p["last_id"] == after) + 1
            )
            return httpx.Response(200, json=pages[index])
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


async def test_no_bindings_means_no_call_at_all():
    api = FakeApi(sessions=[], items={}, reports={})
    result = await collect_captures(api.get, agent_id="ag_iris", bindings=[])
    assert result == {}
    assert api.calls == []


async def test_an_overview_on_a_later_items_page_is_still_found():
    api = FakeApi(
        sessions=[session("s1", "/w/hot")],
        items={
            "s1": [
                page(
                    list(reversed(turn("c-audit", "iris_audit", "f-audit", 300.0))),
                    has_more=True,
                    last_id="page-1",
                ),
                page(list(reversed(turn("c-ov", "iris_overview", "f1", 50.0)))),
            ]
        },
        reports={"f1": REPORT, "f-audit": {"tenant_id": "t-hot"}},
    )
    result = await collect_captures(api.get, agent_id="ag_iris", bindings=[HOT])
    assert result["t-hot"]["captured_at"] == 50.0
    item_calls = [p for p, _ in api.calls if p.endswith("/items")]
    assert item_calls == ["/v1/sessions/s1/items", "/v1/sessions/s1/items"]


async def test_a_call_and_its_output_split_across_a_page_boundary_are_still_paired():
    call_item = {"type": "function_call", "call_id": "c1", "name": "mcp__omnigent__iris_overview"}
    output_item = {
        "type": "function_call_output",
        "call_id": "c1",
        "created_at": 75.0,
        "output": json.dumps({"downloads": [{"filename": "report.json", "file_id": "f1"}]}),
    }
    api = FakeApi(
        sessions=[session("s1", "/w/hot")],
        # Newest first: the output (created later) is on page 1, its call is
        # on page 2 — a real pairing has to span the page boundary.
        items={
            "s1": [
                page([output_item], has_more=True, last_id="page-1"),
                page([call_item]),
            ]
        },
        reports={"f1": REPORT},
    )
    result = await collect_captures(api.get, agent_id="ag_iris", bindings=[HOT])
    assert result["t-hot"]["captured_at"] == 75.0
    item_calls = [p for p, _ in api.calls if p.endswith("/items")]
    assert item_calls == ["/v1/sessions/s1/items", "/v1/sessions/s1/items"]


async def test_paging_stops_as_soon_as_an_overview_is_found():
    api = FakeApi(
        sessions=[session("s1", "/w/hot")],
        items={
            "s1": [
                page(
                    list(reversed(turn("c-audit", "iris_audit", "f-audit", 300.0))),
                    has_more=True,
                    last_id="page-1",
                ),
                page(
                    list(reversed(turn("c-ov", "iris_overview", "f1", 50.0))),
                    has_more=True,
                    last_id="page-2",
                ),
                page(list(reversed(turn("c-old", "iris_audit", "f-old", 10.0)))),
            ]
        },
        reports={"f1": REPORT, "f-audit": {"tenant_id": "t-hot"}, "f-old": {"tenant_id": "t-hot"}},
    )
    result = await collect_captures(api.get, agent_id="ag_iris", bindings=[HOT])
    assert result["t-hot"]["captured_at"] == 50.0
    item_calls = [p for p, _ in api.calls if p.endswith("/items")]
    # The overview was found on page 2; page 3 must never be requested.
    assert item_calls == ["/v1/sessions/s1/items", "/v1/sessions/s1/items"]


async def test_a_failed_later_items_page_fails_that_tenant_closed():
    api = FakeApi(
        sessions=[session("s1", "/w/hot")],
        items={
            "s1": [
                page(
                    list(reversed(turn("c-audit", "iris_audit", "f-audit", 300.0))),
                    has_more=True,
                    last_id="page-1",
                ),
                page(list(reversed(turn("c-ov", "iris_overview", "f1", 50.0)))),
            ]
        },
        reports={"f1": REPORT, "f-audit": {"tenant_id": "t-hot"}},
        # The first page (after=None) succeeds; the second page (after="page-1"),
        # which is where the overview lives, fails.
        statuses={"/v1/sessions/s1/items": {"page-1": 500}},
    )
    result = await collect_captures(api.get, agent_id="ag_iris", bindings=[HOT])
    assert result["t-hot"] == {"error": "a session's record could not be read (HTTP 500)"}


async def test_a_capture_that_names_another_tenant_is_passed_through_for_rank_to_refuse():
    # The collector does not judge; rank() quarantines this as tenant_mismatch.
    api = FakeApi(
        sessions=[session("s1", "/w/cold")],
        items={"s1": turn("c1", "iris_overview", "f1", 1.0)},
        reports={"f1": REPORT},
    )
    result = await collect_captures(api.get, agent_id="ag_iris", bindings=[COLD])
    assert result["t-cold"]["tenant_id"] == "t-hot"


# --------------------------------------------------- the borrowed call_id --
# The hosted item log writes each Iris result twice and gives the duplicate an
# ADJACENT call_id, so one call_id can carry two different tools with a
# different output each. Building {call_id: name} and letting the last writer
# win misattributes the FIRST tool's output to the second, and that tool's
# report is then never found. See omnigent.airbrx.iris.records.paired.


def borrowed(call_id, first, second, first_file, second_file, at):
    """Two tools sharing one call_id, each with its own output.

    The shape measured on 2026-09-22 in session df952db8…, where iris_audit
    and iris_propose shared toolu_01FzPy with no ToolSearch involved.
    """
    return [
        {"type": "function_call", "call_id": call_id, "name": f"mcp__omnigent__{first}"},
        {"type": "function_call", "call_id": call_id, "name": f"mcp__omnigent__{second}"},
        {
            "type": "function_call_output",
            "call_id": call_id,
            "created_at": at,
            "output": json.dumps(
                {"downloads": [{"filename": "report.json", "file_id": first_file}]}
            ),
        },
        {
            "type": "function_call_output",
            "call_id": call_id,
            "created_at": at + 1,
            "output": json.dumps(
                {"downloads": [{"filename": "report.json", "file_id": second_file}]}
            ),
        },
    ]


def test_two_tools_sharing_one_call_id_keep_their_own_outputs():
    from omnigent.airbrx.iris.records import report_references

    # ORDER MATTERS, and getting it backwards makes this test prove nothing.
    # With the OVERVIEW's call first and a later tool borrowing its id,
    # last-writer-wins names the id `iris_audit` and the overview's output is
    # attributed to audit — so the overview is lost. Written the other way
    # round (audit first) the old rule already picks `iris_overview` and the
    # test passes with or without the fix.
    items = borrowed("shared", "iris_overview", "iris_audit", "f_overview", "f_audit", 100)
    assert report_references(items) == [
        ("iris_overview", "f_overview", 100),
        ("iris_audit", "f_audit", 101),
    ]


def test_a_borrowed_id_does_not_lose_the_overview_behind_toolsearch():
    # ToolSearch claiming the id first is the other observed shape. The Iris
    # result under that id is real and must still be found.
    from omnigent.airbrx.iris.records import report_references

    items = [
        {"type": "function_call", "call_id": "c1", "name": "ToolSearch"},
        {"type": "function_call", "call_id": "c1", "name": "mcp__omnigent__iris_overview"},
        {"type": "function_call_output", "call_id": "c1", "created_at": 10, "output": "[]"},
        {
            "type": "function_call_output",
            "call_id": "c1",
            "created_at": 11,
            "output": json.dumps({"downloads": [{"filename": "report.json", "file_id": "f1"}]}),
        },
    ]
    assert report_references(items) == [("iris_overview", "f1", 11)]


def test_a_call_with_no_output_yet_is_not_a_pairing_error():
    # A cancelled or still-running turn leaves a call unanswered.
    from omnigent.airbrx.iris.records import paired

    items = [
        {"type": "function_call", "call_id": "c1", "name": "mcp__omnigent__iris_overview"},
        {"type": "function_call", "call_id": "c1", "name": "mcp__omnigent__iris_audit"},
        {"type": "function_call_output", "call_id": "c1", "created_at": 5, "output": "{}"},
    ]
    assert [name for _, name in paired(items)] == ["iris_overview"]


def test_pairs_come_back_in_time_order_across_call_ids():
    # Callers take the newest reference per tool, so order is load-bearing.
    from omnigent.airbrx.iris.records import paired

    items = [
        *turn("late", "iris_overview", "f_late", 900),
        *turn("early", "iris_overview", "f_early", 100),
    ]
    assert [item["created_at"] for item, _ in paired(items)] == [100, 900]


def test_the_newest_capture_survives_a_borrowed_call_id():
    # A session whose overview's call_id is borrowed by a LATER tool still
    # reports that tenant, rather than reading as one that never collected.
    # The overview goes first: that is the order the old rule loses.
    from omnigent.airbrx.iris.records import report_references

    items = borrowed("shared", "iris_overview", "iris_audit", "f_overview", "f_audit", 500)
    newest = {}
    for tool, file_id, at in report_references(items):
        if tool == "iris_overview" and at >= newest.get("at", 0):
            newest = {"file_id": file_id, "at": at}
    assert newest == {"file_id": "f_overview", "at": 500}


# ------------------------------------------------- counting EXECUTIONS ------
# `paired` says which tool a recorded result belongs to. `executions` says how
# many times a tool RAN, which is the question an acceptance gate asks. The
# rule — two byte-identical payloads are one run — had no test anywhere,
# because it lived inline in scripts/iris/verify_host.py, which has no test
# file. It was the one rule with nothing checking it, on a gate whose whole job
# is to notice a second dispatch.


def output(call_id, payload, at=0):
    return {
        "type": "function_call_output",
        "call_id": call_id,
        "created_at": at,
        "output": json.dumps(payload),
    }


def call(call_id, tool):
    return {"type": "function_call", "call_id": call_id, "name": f"mcp__omnigent__{tool}"}


OVERVIEW = {"message": "Tenant overview", "evidence": [{"id": "e1"}], "budget": {"calls": 10}}


def test_the_same_payload_recorded_twice_is_one_execution():
    from omnigent.airbrx.iris.records import executions

    items = [
        call("a", "iris_overview"),
        output("a", OVERVIEW, 1),
        call("b", "iris_overview"),
        output("b", OVERVIEW, 2),  # the log's copy
    ]
    assert executions(items) == ["iris_overview"]


def test_a_genuine_second_run_is_two_executions():
    # A real second run mints a fresh evidence id and advances the budget, so
    # it cannot be byte-equal to the first. This is the case the rule must NOT
    # collapse, or a double dispatch goes unnoticed.
    from omnigent.airbrx.iris.records import executions

    second = {"message": "Tenant overview", "evidence": [{"id": "e2"}], "budget": {"calls": 28}}
    items = [
        call("a", "iris_overview"),
        output("a", OVERVIEW, 1),
        call("b", "iris_overview"),
        output("b", second, 2),
    ]
    assert executions(items) == ["iris_overview", "iris_overview"]


def test_a_duplicate_under_a_borrowed_call_id_is_still_one_execution():
    # The shape actually observed: the copy borrows the PRECEDING tool's id.
    from omnigent.airbrx.iris.records import executions

    items = [
        call("x", "ToolSearch"),
        output("x", [{"type": "tool_reference"}], 1),
        call("x", "iris_overview"),
        output("x", OVERVIEW, 2),
        call("y", "iris_overview"),
        output("y", OVERVIEW, 3),
    ]
    assert executions(items) == ["iris_overview"]


def test_harness_tools_are_not_counted_as_iris_runs():
    from omnigent.airbrx.iris.records import executions

    items = [call("x", "ToolSearch"), output("x", [{"type": "tool_reference"}], 1)]
    assert executions(items) == []


async def test_a_borrowed_call_id_still_yields_the_newest_capture_end_to_end():
    # Chief's case (d), through collect_captures rather than report_references:
    # an iris_overview whose call shares an id with a neighbouring tool is
    # still found, and still the newest. Before the pairing fix the account
    # route would have reported this tenant as never_collected.
    older = turn("solo", "iris_overview", "f_old", 100)
    # Overview's call FIRST, audit borrowing its id after. Under
    # last-writer-wins the id resolves to `iris_audit`, the overview's output
    # is attributed to audit, the newest capture is never found, and this
    # tenant reads as holding its older capture. That is the shape that fails
    # without paired(); the reverse order passes either way and proves nothing.
    shared = borrowed("dup", "iris_overview", "iris_audit", "f_new", "f_audit", 500)
    api = FakeApi(
        sessions=[session("s1", "/w/hot")],
        items={"s1": older + shared},
        reports={
            "f_new": REPORT,
            "f_old": {**REPORT, "metrics": {**REPORT["metrics"], "requests": 1}},
            "f_audit": {"tenant_id": "t-hot"},
        },
    )
    result = await collect_captures(api.get, agent_id="ag_iris", bindings=[HOT])
    assert result["t-hot"] == {
        "tenant_id": "t-hot",
        "metrics": REPORT["metrics"],
        "captured_at": 500,
    }


# ------------------------------------------- the tenant's CURRENT period ----
# iris_overview takes optional start_date/end_date. Without them it reads the
# tenant's current period: the seven completed UTC days before the day it runs.
# A mid-session comparison ("how did the week before compare?") calls it again
# with an older window, and that report is newer than the capture. The account
# ranking must not rank a tenant on its comparison window, which is what
# "newest overview whatever its window" did. Same rule as the workspace state:
# records.reads_current_period over records.report_calls.

COMPARISON = {"start_date": "2026-09-12", "end_date": "2026-09-19"}


def dated_turn(call_id, tool, file_id, created_at, arguments, called_at=None):
    """`turn`, with the call's recorded arguments and its own clock."""
    run = turn(call_id, tool, file_id, created_at)
    run[0]["arguments"] = arguments if isinstance(arguments, str) else json.dumps(arguments)
    run[0]["created_at"] = created_at if called_at is None else called_at
    return run


#: 2026-09-27T00:21:00Z, so the current period is 09-20 up to 09-27, end exclusive.
AT = 1_790_468_460


def metrics_of(requests, period_complete=True, covered_days=7):
    return {
        **REPORT["metrics"],
        "requests": requests,
        "covered_days": covered_days,
        "period_complete": period_complete,
    }


async def test_a_newer_comparison_window_does_not_become_the_accounts_capture():
    api = FakeApi(
        sessions=[session("s1", "/w/hot")],
        items={
            "s1": turn("c1", "iris_overview", "f-current", AT)
            + dated_turn("c2", "iris_overview", "f-compare", AT + 60, COMPARISON)
        },
        reports={
            "f-current": {**REPORT, "metrics": metrics_of(700)},
            "f-compare": {**REPORT, "metrics": metrics_of(12)},
        },
    )
    result = await collect_captures(api.get, agent_id="ag_iris", bindings=[HOT])
    assert result["t-hot"] == {
        "tenant_id": "t-hot",
        "metrics": metrics_of(700),
        "captured_at": AT,
    }
    content = [p for p, _ in api.calls if "/resources/files/" in p]
    assert content == ["/v1/sessions/s1/resources/files/f-current/content"]


async def test_a_comparison_in_a_newer_session_does_not_beat_the_capture_in_an_older_one():
    api = FakeApi(
        sessions=[session("s-new", "/w/hot"), session("s-old", "/w/hot")],
        items={
            "s-new": dated_turn("c2", "iris_overview", "f-compare", AT + 600, COMPARISON),
            "s-old": turn("c1", "iris_overview", "f-current", AT),
        },
        reports={
            "f-current": {**REPORT, "metrics": metrics_of(700)},
            "f-compare": {**REPORT, "metrics": metrics_of(12)},
        },
    )
    result = await collect_captures(api.get, agent_id="ag_iris", bindings=[HOT])
    assert result["t-hot"]["captured_at"] == AT
    assert result["t-hot"]["metrics"]["requests"] == 700


async def test_a_partial_current_capture_still_counts_as_current():
    """3 of 7 days, period_complete false: current by what was asked, not by what was found.

    `rank` then quarantines it as incomplete_period, by coverage; the collector
    does not judge coverage.
    """
    partial = metrics_of(300, period_complete=False, covered_days=3)
    api = FakeApi(
        sessions=[session("s1", "/w/hot")],
        items={
            "s1": turn("c1", "iris_overview", "f-full", AT)
            + turn("c2", "iris_overview", "f-partial", AT + 30)
            + dated_turn("c3", "iris_overview", "f-compare", AT + 60, COMPARISON)
        },
        reports={
            "f-full": {**REPORT, "metrics": metrics_of(700)},
            "f-partial": {**REPORT, "metrics": partial},
            "f-compare": {**REPORT, "metrics": metrics_of(12)},
        },
    )
    result = await collect_captures(api.get, agent_id="ag_iris", bindings=[HOT])
    assert result["t-hot"] == {"tenant_id": "t-hot", "metrics": partial, "captured_at": AT + 30}


async def test_explicit_dates_naming_the_current_period_count_as_current():
    api = FakeApi(
        sessions=[session("s1", "/w/hot")],
        items={
            "s1": turn("c1", "iris_overview", "f-default", AT)
            + dated_turn(
                "c2",
                "iris_overview",
                "f-explicit",
                AT + 30,
                {"start_date": "2026-09-20", "end_date": "2026-09-27"},
            )
        },
        reports={
            "f-default": {**REPORT, "metrics": metrics_of(700)},
            "f-explicit": {**REPORT, "metrics": metrics_of(701)},
        },
    )
    result = await collect_captures(api.get, agent_id="ag_iris", bindings=[HOT])
    assert result["t-hot"]["captured_at"] == AT + 30


async def test_unreadable_call_arguments_are_not_the_current_period():
    api = FakeApi(
        sessions=[session("s1", "/w/hot")],
        items={
            "s1": turn("c1", "iris_overview", "f-current", AT)
            + dated_turn("c2", "iris_overview", "f-garbled", AT + 60, "{not json")
        },
        reports={
            "f-current": {**REPORT, "metrics": metrics_of(700)},
            "f-garbled": {**REPORT, "metrics": metrics_of(12)},
        },
    )
    result = await collect_captures(api.get, agent_id="ag_iris", bindings=[HOT])
    assert result["t-hot"]["captured_at"] == AT


async def test_a_tenant_holding_only_comparison_windows_has_no_current_capture():
    """None, as for a tenant that never collected: `rank` quarantines it as never_collected."""
    api = FakeApi(
        sessions=[session("s1", "/w/hot")],
        items={"s1": dated_turn("c1", "iris_overview", "f-compare", AT, COMPARISON)},
        reports={"f-compare": REPORT},
    )
    result = await collect_captures(api.get, agent_id="ag_iris", bindings=[HOT])
    assert result == {"t-hot": None}
    assert not [p for p, _ in api.calls if "/resources/files/" in p]


async def test_paging_goes_past_a_comparison_window_to_the_current_capture():
    """The early stop is on the newest CURRENT overview, not the newest overview."""
    api = FakeApi(
        sessions=[session("s1", "/w/hot")],
        items={
            "s1": [
                page(
                    list(
                        reversed(
                            dated_turn("c-cmp", "iris_overview", "f-compare", AT + 60, COMPARISON)
                        )
                    ),
                    has_more=True,
                    last_id="page-1",
                ),
                page(list(reversed(turn("c-ov", "iris_overview", "f-current", AT)))),
            ]
        },
        reports={
            "f-current": {**REPORT, "metrics": metrics_of(700)},
            "f-compare": {**REPORT, "metrics": metrics_of(12)},
        },
    )
    result = await collect_captures(api.get, agent_id="ag_iris", bindings=[HOT])
    assert result["t-hot"]["captured_at"] == AT
    item_calls = [p for p, _ in api.calls if p.endswith("/items")]
    assert item_calls == ["/v1/sessions/s1/items", "/v1/sessions/s1/items"]


async def test_the_window_is_decided_on_the_calls_own_clock_across_midnight():
    """Called at 23:59:58 UTC on 09-26 for 09-19..09-26, answered at 00:00:04 on 09-27."""
    midnight = AT - 21 * 60  # 2026-09-27T00:00:00Z
    api = FakeApi(
        sessions=[session("s1", "/w/hot")],
        items={
            "s1": dated_turn(
                "c1",
                "iris_overview",
                "f-straddle",
                midnight + 4,
                {"start_date": "2026-09-19", "end_date": "2026-09-26"},
                called_at=midnight - 2,
            )
        },
        reports={"f-straddle": REPORT},
    )
    result = await collect_captures(api.get, agent_id="ag_iris", bindings=[HOT])
    assert result["t-hot"]["captured_at"] == midnight + 4


# --- N1: the Accounts ranking reads the same current period on any host clock ------------


@pytest.fixture
def pacific_host(monkeypatch):
    """The execution host runs in America/Los_Angeles."""
    import time

    monkeypatch.setenv("TZ", "America/Los_Angeles")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


#: 2026-09-27T00:30:00Z: 17:30 on 2026-09-26 in America/Los_Angeles.
PACIFIC_1730 = AT + 9 * 60


async def test_a_capture_at_1730_pacific_without_dates_is_ranked(pacific_host):
    """A refresh at 17:30 PDT calls iris_overview with no dates; a newer comparison follows."""
    narrowed = {
        **metrics_of(300, period_complete=False, covered_days=3),
        "start_date": "2026-09-20",
        "end_date": "2026-09-22",
    }
    api = FakeApi(
        sessions=[session("s1", "/w/hot")],
        items={
            "s1": dated_turn("c1", "iris_overview", "f-partial", PACIFIC_1730, {})
            + dated_turn("c2", "iris_overview", "f-compare", PACIFIC_1730 + 60, COMPARISON)
        },
        reports={
            "f-partial": {**REPORT, "metrics": narrowed},
            "f-compare": {**REPORT, "metrics": metrics_of(12)},
        },
    )
    result = await collect_captures(api.get, agent_id="ag_iris", bindings=[HOT])
    assert result["t-hot"] == {
        "tenant_id": "t-hot",
        "metrics": narrowed,
        "captured_at": PACIFIC_1730,
    }


async def test_only_an_older_window_at_1730_pacific_is_still_never_collected(pacific_host):
    api = FakeApi(
        sessions=[session("s1", "/w/hot")],
        items={"s1": dated_turn("c1", "iris_overview", "f-compare", PACIFIC_1730, COMPARISON)},
        reports={"f-compare": {**REPORT, "metrics": metrics_of(12)}},
    )
    result = await collect_captures(api.get, agent_id="ag_iris", bindings=[HOT])
    assert result["t-hot"] is None
