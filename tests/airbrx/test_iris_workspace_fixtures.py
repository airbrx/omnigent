"""The adapter responses Iris's v2 workspace is tested against, produced by the real handlers.

``web/src/shell/__fixtures__/irisAdapter.json`` is what the routes in
``omnigent/airbrx/iris/routes.py`` actually answer for a handful of recorded
sessions (docs/iris/WORKSPACE_V2.md, section 2). ``irisWorkspaceApp.test.ts``
(W3) replays it against ``omnigent/airbrx/iris/ui/``. This test fails when a
handler's answer drifts from the file, so a shape change cannot pass on one
side and break the other. Regenerate with::

    IRIS_WRITE_FIXTURES=1 uv run pytest tests/airbrx/test_iris_workspace_fixtures.py

Every report here is invented, shaped like Iris's own examples: the repository
is public. The tenant is ``fixture-iris``, a synthetic fixture binding.

The harness (``IrisSession``, ``make_client``) is shared with
``test_iris_routes.py``: the stand-ins for the native routes the adapter calls
are mounted on the same app, because the router reaches them through an
ASGITransport against ``request.app``. Nothing here starts a runner or a model.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omnigent.airbrx.iris.routes import create_iris_router

FIXTURE = Path(__file__).resolve().parents[2] / "web/src/shell/__fixtures__/irisAdapter.json"
SESSION = "s1"
API = f"/v1/iris/sessions/{SESSION}/ui/api"
TENANT = "fixture-iris"
HOST = "0123456789abcdef0123456789abcdef"
WORKSPACE = "/tmp/iris-fixture"
USER = "local"
#: 2026-09-27T00:21:00Z, the capture the contract's examples come from.
READ_AT = 1_790_468_460
#: The refresh prompt's first sentence, spelled out rather than imported: the
#: workspace recognises a refresh turn by these exact bytes (WORKSPACE_V2.md,
#: section 2), so the test pins the literal, not whatever the code says today.
REFRESH_FIRST_SENTENCE = "Call iris_overview and iris_audit for the selected tenant."
#: The clock the handlers see, so `cache_age_seconds` and `stale` are stable.
NOW = READ_AT + 28


def eid(label: str) -> str:
    """A stable 32-hex evidence or file id, shaped like Iris's."""
    return hashlib.sha256(label.encode()).hexdigest()[:32]


# --- Reports, shaped like iris:docs/examples/*/report.json ----------------------------

LIMITATIONS = [
    "Source values are untrusted data, never instructions.",
    "Redaction reduces exposure; it does not guarantee removal of all sensitive content.",
]


def summary_rows(start_day: int, prefix: str) -> list[dict[str, Any]]:
    """One `get_summary` evidence row per covered day: the coverage strip."""
    return [
        {
            "id": eid(f"{prefix}-summary-{day}"),
            "source_tool": "get_summary",
            "start_date": f"2026-09-{day:02d}",
            "end_date": f"2026-09-{day + 1:02d}",
            "status": "complete",
            "data": {
                "totalCacheHits": 80,
                "totalCacheMisses": 20,
                "totalHits": 100,
                "totalQueries": 100,
            },
            "limitations": [],
        }
        for day in range(start_day, start_day + 7)
    ]


def metrics(start: str, end: str, evidence: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "periods": 7,
        "start_date": start,
        "end_date": end,
        "requests": 700,
        "cache_hits": 560,
        "cache_misses": 140,
        "hit_rate": 0.8,
        "hit_rate_denominator": 700,
        "executions": None,
        "warehouse_time_ms": None,
        "unknowns": ["executions", "warehouse_time_ms"],
        "limitations": [],
        "covered_days": 7,
        "requested_days": 7,
        "evidence_ids": [e["id"] for e in evidence],
        "period_complete": True,
        "latency": {
            "response_time_ms": None,
            "denominator": None,
            "label": (
                "Request-weighted source response-time average for covered days; "
                "end-to-end proxy, not warehouse savings"
            ),
            "period_complete": False,
        },
    }


def envelope(run: str, message: str, **rest: Any) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "generatedAt": "2026-09-27T00:21:00+00:00",
        "owner": "iris-v1",
        "tenantId": TENANT,
        "runId": eid(run),
        "message": message,
        "incomplete": False,
        "mode": "synthetic fixture",
        "tenant_id": TENANT,
        "budget": {"calls": 10, "max_calls": 40},
        "limitations": LIMITATIONS,
        **rest,
    }


SUMMARY = summary_rows(19, "now")
RULE_EFFECTIVENESS = {
    "id": eid("rule-effectiveness"),
    "source_tool": "get_rule_effectiveness",
    "start_date": None,
    "end_date": None,
    "status": "complete",
    "data": {
        "rules": [
            {"ruleId": "report-cache", "cacheHits": 80, "cacheMisses": 20, "totalExecutions": 100}
        ],
        "year": None,
        "generatedAt": None,
        "totalQueries": None,
    },
    "limitations": [],
}
OPPORTUNITIES = {
    "id": eid("opportunities"),
    "source_tool": "get_cache_opportunities",
    "start_date": "2026-09-19",
    "end_date": "2026-09-26",
    "status": "complete",
    "data": {"repeatMisses": [{"ruleId": "report-cache", "misses": 20, "distinctQueries": 4}]},
    "limitations": [],
}
REPEAT_MISS = {
    "id": eid("finding-repeat-miss"),
    "tenant_id": TENANT,
    "kind": "repeat_miss",
    "severity": "warning",
    "confidence": "medium",
    "rule_id": "report-cache",
    "evidence_ids": [OPPORTUNITIES["id"]],
    "explanation": "The same four queries miss the report-cache rule repeatedly.",
    "next_step": "Investigate whether the cache key includes a per-request element.",
}


def overview_report() -> dict[str, Any]:
    evidence = [*SUMMARY, RULE_EFFECTIVENESS, OPPORTUNITIES]
    return envelope(
        "overview",
        "Tenant overview. Exact counts cover disjoint completed UTC days.",
        findings=[REPEAT_MISS],
        evidence=evidence,
        metrics=metrics("2026-09-19", "2026-09-26", SUMMARY),
    )


TENANT_RULES = {
    "id": eid("tenant-rules"),
    "source_tool": "get_tenant_rules",
    "start_date": None,
    "end_date": None,
    "status": "complete",
    "data": {"baseline_hash": eid("baseline") * 2, "rule_count": 3},
    "limitations": [],
}


def audit_report() -> dict[str, Any]:
    return envelope(
        "audit",
        "Rule configuration audit. Read-only.",
        findings=[
            {
                "id": "unknown_sensitivity:report-cache",
                "tenant_id": TENANT,
                "kind": "unknown_sensitivity",
                "severity": "warning",
                "confidence": "low",
                "rule_id": "report-cache",
                "evidence_ids": [TENANT_RULES["id"]],
                "explanation": "Whether report-cache results carry per-user data is unknown.",
                "next_step": "Confirm the rule's tables hold no per-user rows.",
            }
        ],
        evidence=[TENANT_RULES],
    )


PREVIOUS_SUMMARY = summary_rows(12, "previous")


def investigate_report() -> dict[str, Any]:
    evidence = [*SUMMARY, *PREVIOUS_SUMMARY]
    return envelope(
        "investigate",
        "Compared two complete seven-day periods. Read-only.",
        findings=[
            {
                "id": eid("finding-investigation"),
                "tenant_id": TENANT,
                "kind": "investigation",
                "severity": "info",
                "confidence": "medium",
                "rule_id": None,
                "evidence_ids": [SUMMARY[0]["id"], PREVIOUS_SUMMARY[0]["id"]],
                "explanation": "Hit rate is unchanged between the two periods.",
                "next_step": "No change needed on this evidence.",
            }
        ],
        evidence=evidence,
        current=metrics("2026-09-19", "2026-09-26", SUMMARY),
        previous=metrics("2026-09-12", "2026-09-19", PREVIOUS_SUMMARY),
    )


PREVIEW = {
    "id": eid("preview"),
    "source_tool": "preview_rule_change",
    "start_date": None,
    "end_date": None,
    "status": "complete",
    "data": {
        "written": False,
        "validation": {"valid": True, "errors": [], "warnings": []},
        "diff": {"summary": {"destructive": False}},
        "warning": None,
        "fixture": True,
    },
    "limitations": [],
}


def propose_report() -> dict[str, Any]:
    return envelope(
        "propose",
        (
            "Validated proposal. Proposal not applied. Preview compares configuration; "
            "historical impact has not been simulated."
        ),
        findings=[],
        evidence=[TENANT_RULES, PREVIEW],
        proposal_status="validated",
        proposal_view={
            "rule_id": "report-cache",
            "baseline_hash": eid("baseline") * 2,
            "diff": (
                "--- baseline\n+++ candidate\n@@ -13,7 +13,7 @@\n"
                '         "cache": {\n-          "ttlSeconds": 60\n+          "ttlSeconds": 120\n'
                "         },"
            ),
            "validation": {"valid": True, "errors": [], "warnings": [], "fixture": True},
            "preview": PREVIEW["data"],
        },
    )


# --- Recorded session items ------------------------------------------------------------


def tool_run(
    tool: str, file_id: str, at: float, call_id: str | None = None
) -> list[dict[str, Any]]:
    """A recorded Iris tool call and its output, spelled as the host stores them."""
    call_id = call_id or f"call-{file_id[:8]}"
    downloads = [
        {"filename": "report.json", "file_id": file_id},
        {"filename": "report.md", "file_id": eid(f"{file_id}-md")},
    ]
    return [
        {
            "id": f"fc-{call_id}",
            "type": "function_call",
            "name": f"mcp__omnigent__{tool}",
            "arguments": "{}",
            "call_id": call_id,
            "created_at": at,
        },
        {
            "id": f"fo-{call_id}",
            "type": "function_call_output",
            "call_id": call_id,
            "output": json.dumps({"summary": "ok", "downloads": downloads}),
            "created_at": at,
        },
    ]


def user(text: str, at: float, item_id: str) -> dict[str, Any]:
    return {
        "id": item_id,
        "type": "message",
        "role": "user",
        "status": "completed",
        "content": [{"type": "input_text", "text": text}],
        "created_at": at,
    }


def answer(text: str, at: float, item_id: str = "a") -> dict[str, Any]:
    return {
        "id": item_id,
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": text}],
        "created_at": at,
    }


class IrisSession:
    """A native Iris session: its items, report files, and what a posted turn appends."""

    def __init__(
        self,
        items: list[dict[str, Any]] | None = None,
        files: dict[str, dict[str, Any]] | None = None,
        on_turn: Callable[[str, float], list[dict[str, Any]]] | None = None,
        status: str = "idle",
    ) -> None:
        self.items = list(items or [])
        self.files = dict(files or {})
        self.on_turn = on_turn
        self.status = status
        self.posted: list[dict[str, Any]] = []

    def post(self, event: dict[str, Any]) -> dict[str, Any]:
        self.posted.append(event)
        if event.get("type") != "message":
            return {"queued": True}
        text = event["data"]["content"][0]["text"]
        marker = f"turn-{len(self.posted)}"
        at = NOW - 1
        self.items.append(user(text, at, marker))
        if self.on_turn is not None:
            self.items.extend(self.on_turn(text, at))
        return {"queued": True, "item_id": marker}


def binding_config(tmp_path: Path, tenant: str = TENANT) -> Path:
    path = tmp_path / "iris-bindings.json"
    path.write_text(
        json.dumps(
            [
                {
                    "tenant_id": tenant,
                    "name": "Iris fixture tenant",
                    "host_id": HOST,
                    "workspace": WORKSPACE,
                    "users": [USER],
                    "pat_ref": "",
                    "fixture": True,
                }
            ]
        )
    )
    return path


class _Agent:
    id = "agent-iris"


class _AgentStore:
    def get_by_name(self, name: str) -> Any:
        return _Agent() if name == "iris" else None


def make_client(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, session: IrisSession
) -> TestClient:
    monkeypatch.setenv("OMNIGENT_IRIS_CONFIG", str(binding_config(tmp_path)))
    monkeypatch.setattr("omnigent.airbrx.iris.routes.require_user", lambda request, provider: USER)
    # Only the handlers' own `time` name, so nothing else in the process sees it.
    monkeypatch.setattr(
        "omnigent.airbrx.iris.routes.time",
        SimpleNamespace(time=lambda: NOW, monotonic=time.monotonic),
    )
    app = FastAPI()
    app.include_router(
        create_iris_router(auth_provider=object(), agent_store=_AgentStore()), prefix="/v1"
    )

    @app.get("/v1/sessions/{session_id}")
    async def get_session(session_id: str) -> dict[str, Any]:
        return {
            "id": session_id,
            "agent_id": "agent-iris",
            "host_id": HOST,
            "workspace": WORKSPACE,
            "status": session.status,
            "permission_level": 4,
            "last_task_error": None,
            "active_response_id": None,
        }

    @app.get("/v1/sessions/{session_id}/items")
    async def get_items(
        session_id: str, order: str = "asc", after: str | None = None
    ) -> dict[str, Any]:
        items = session.items
        if after is not None:
            ids = [i.get("id") for i in items]
            items = items[ids.index(after) + 1 :] if after in ids else []
        return {"data": list(reversed(items)) if order == "desc" else items, "has_more": False}

    @app.get("/v1/sessions/{session_id}/resources/files/{file_id}/content")
    async def get_file(session_id: str, file_id: str) -> dict[str, Any]:
        return session.files[file_id]

    @app.post("/v1/sessions/{session_id}/events")
    async def post_event(session_id: str, event: dict[str, Any]) -> dict[str, Any]:
        return session.post(event)

    return TestClient(app)


# --- Scenarios ---------------------------------------------------------------------------

OVERVIEW_ID = eid("file-overview")
AUDIT_ID = eid("file-audit")
INVESTIGATE_ID = eid("file-investigate")
PROPOSE_ID = eid("file-propose")
FRESH_OVERVIEW_ID = eid("file-overview-fresh")
FRESH_AUDIT_ID = eid("file-audit-fresh")

FILES = {
    OVERVIEW_ID: overview_report(),
    AUDIT_ID: audit_report(),
    INVESTIGATE_ID: investigate_report(),
    PROPOSE_ID: propose_report(),
    FRESH_OVERVIEW_ID: overview_report(),
    FRESH_AUDIT_ID: audit_report(),
}

REFRESH_ANSWER = "Hit rate 80.0% over 700 requests, 7 of 7 days covered."
CHAT_ANSWER = "Hit rate is **80.0%** over 700 requests in the covered period."


def captured(at: float = READ_AT) -> list[dict[str, Any]]:
    """A refresh turn that read the tenant at `at`: overview and audit, then an answer."""
    return [
        user(f"{REFRESH_FIRST_SENTENCE} Summarize the coverage.", at - 10, "u-capture"),
        *tool_run("iris_overview", OVERVIEW_ID, at),
        *tool_run("iris_audit", AUDIT_ID, at + 2),
        answer(REFRESH_ANSWER, at + 4, "a-capture"),
    ]


def worked() -> list[dict[str, Any]]:
    """After the capture, Iris compared periods and drafted a validated proposal."""
    return [
        *captured(),
        user("Investigate finding repeat_miss, then propose a change.", READ_AT + 6, "u-work"),
        *tool_run("iris_investigate", INVESTIGATE_ID, READ_AT + 8),
        *tool_run("iris_propose", PROPOSE_ID, READ_AT + 10),
        answer(
            "Proposal validated. Not applied; it needs external approval.", READ_AT + 12, "a-w"
        ),
    ]


def respond(text: str, at: float) -> list[dict[str, Any]]:
    """What a turn posted to the fixture session appends: a refresh reads, a question answers."""
    if text.startswith(REFRESH_FIRST_SENTENCE):
        return [
            *tool_run("iris_overview", FRESH_OVERVIEW_ID, at, "fresh-o"),
            *tool_run("iris_audit", FRESH_AUDIT_ID, at, "fresh-a"),
            answer(REFRESH_ANSWER, at, "a-fresh"),
        ]
    return [answer(CHAT_ANSWER, at, "a-chat")]


def read_nothing(text: str, at: float) -> list[dict[str, Any]]:
    """A turn where Iris answered without collecting anything."""
    return [answer("I could not reach the tenant's gateway.", at, "a-nothing")]


#: Scenario name to (session factory, the routes whose answers are captured).
SCENARIOS: dict[str, tuple[Callable[[], IrisSession], list[tuple[str, str]]]] = {
    # Nothing has run: state is a 409, readiness names what is unverified.
    "fresh_session": (
        lambda: IrisSession(on_turn=read_nothing),
        [("GET", "readiness"), ("GET", "state")],
    ),
    # The first-ever auto-collect ran a turn that produced no overview: 409.
    "first_collect_produced_nothing": (
        lambda: IrisSession(on_turn=read_nothing),
        [("POST", "refresh")],
    ),
    # A capture 28 s old; a question; a refresh that reads again.
    "captured": (
        lambda: IrisSession(captured(), FILES, respond),
        [("GET", "readiness"), ("GET", "state"), ("POST", "chat"), ("POST", "refresh")],
    ),
    # The same capture read 15 minutes later: stale.
    "stale_capture": (
        lambda: IrisSession(captured(at=NOW - 900), FILES, respond),
        [("GET", "state"), ("POST", "refresh")],
    ),
    # Investigation and proposal exist; a refresh keeps both.
    "investigated_and_proposed": (
        lambda: IrisSession(worked(), FILES, respond),
        [("GET", "state"), ("POST", "refresh")],
    ),
    # An earlier capture exists, but the refresh turn itself read nothing: 409.
    "refresh_read_nothing": (
        lambda: IrisSession(captured(), FILES, read_nothing),
        [("GET", "state"), ("POST", "refresh")],
    ),
    # A turn is already running: chat and refresh are refused, not queued.
    "busy_session": (
        lambda: IrisSession(captured(), FILES, respond, status="running"),
        [("POST", "chat"), ("POST", "refresh")],
    ),
}


def ask(client: TestClient, method: str, route: str) -> dict[str, Any]:
    if method == "GET":
        response = client.get(f"{API}/{route}")
    elif route == "chat":
        response = client.post(
            f"{API}/chat",
            json={
                "history": [{"role": "user", "content": "Explain this capture's performance"}],
                "deadline": 300,
            },
        )
    else:
        response = client.post(f"{API}/{route}", json={})
    return {"status": response.status_code, "body": response.json()}


def produce(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    produced: dict[str, Any] = {}
    for name, (factory, routes) in SCENARIOS.items():
        answers = {}
        for method, route in routes:
            # A fresh session per route, so a refresh does not change what the
            # state read beside it answered, whichever order they run in.
            client = make_client(monkeypatch, tmp_path, factory())
            answers[route] = ask(client, method, route)
        produced[name] = answers
    return produced


def test_the_workspace_fixtures_are_what_the_handlers_answer(monkeypatch, tmp_path) -> None:
    produced = produce(monkeypatch, tmp_path)
    text = json.dumps(produced, indent=2, sort_keys=True) + "\n"
    if os.environ.get("IRIS_WRITE_FIXTURES") == "1":
        FIXTURE.write_text(text)
    assert FIXTURE.exists(), "Generate the fixture: IRIS_WRITE_FIXTURES=1, see this module."
    assert json.loads(FIXTURE.read_text()) == produced, (
        "The Iris adapter's answers changed. Regenerate the fixture (see this module's "
        "docstring) and make web/src/shell/irisWorkspaceApp.test.ts pass against it."
    )


def test_the_fixtures_cover_what_the_app_must_handle(monkeypatch, tmp_path) -> None:
    produced = produce(monkeypatch, tmp_path)
    assert produced["fresh_session"]["state"]["status"] == 409
    assert produced["first_collect_produced_nothing"]["refresh"]["status"] == 409
    assert produced["refresh_read_nothing"]["refresh"]["status"] == 409
    assert produced["busy_session"]["chat"]["status"] == 409
    assert produced["captured"]["state"]["body"]["stale"] is False
    assert produced["stale_capture"]["state"]["body"]["stale"] is True
    worked_state = produced["investigated_and_proposed"]["state"]["body"]
    assert worked_state["investigation"] is not None
    assert worked_state["proposal"]["proposal_status"] == "validated"
    assert produced["investigated_and_proposed"]["refresh"]["body"]["proposal"] is not None


def test_every_evidence_link_in_the_fixture_resolves(monkeypatch, tmp_path) -> None:
    """`findings[].evidence_ids` resolve against the union of every report's evidence.

    WORKSPACE_V2.md section 2, "Evidence ids". A fixture whose links dangle
    would let the Evidence tab pass its tests while pointing at nothing.
    """
    body = produce(monkeypatch, tmp_path)["investigated_and_proposed"]["state"]["body"]
    reports = [body["overview"], body["audit"], body["investigation"], body["proposal"]]
    known = {e["id"] for r in reports for e in r.get("evidence", [])}
    linked = {i for r in reports for f in r.get("findings", []) for i in f["evidence_ids"]}
    assert linked, "no finding links any evidence"
    assert linked <= known, f"dangling evidence ids: {sorted(linked - known)}"
