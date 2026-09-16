"""Authenticated workspace adapter over Omnigent's native session APIs."""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from omnigent.airbrx.iris.config import bindings, session_binding
from omnigent.airbrx.iris.package import HERE, source_root
from omnigent.airbrx.iris.runtime import TOOLS
from omnigent.server.routes._auth_helpers import require_user
from omnigent.server.routes._content_type import require_json_content_type


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str
    content: str = Field(max_length=16000)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    history: list[ChatMessage] = Field(min_length=1, max_length=24)
    deadline: int = Field(default=300, ge=1, le=300)


@asynccontextmanager
async def session_client(request: Request):
    headers = {
        k: v
        for k, v in request.headers.items()
        if k.lower() in {"authorization", "cookie", "origin", "host", "x-forwarded-email"}
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=request.app),
        base_url=str(request.base_url),
        headers=headers,
        timeout=330,
    ) as client:
        yield client


async def checked(response):
    if response.status_code >= 400:
        raise HTTPException(response.status_code, "Native session operation failed")
    return response.json()


#: Iris tools are registered bare (see :data:`omnigent.airbrx.iris.runtime.TOOLS`)
#: and dispatched bare, but the model reaches them through the SDK's MCP
#: namespace, so a recorded session item spells the call
#: ``mcp__omnigent__iris_overview``. Comparing the recorded spelling against the
#: registration spelling rejected every correct turn with a 409.
#:
#: Only this server's own prefix is removed. A tool served by any other MCP
#: server -- ``mcp__airbrx__*``, ``mcp__claude_ai_Slack__*`` -- is outside the
#: Iris boundary and must still be refused, so the guard keeps its meaning.
_OMNIGENT_TOOL_PREFIX = "mcp__omnigent__"


def bare_tool_name(name):
    """Return the registration name for a recorded tool call."""
    if not isinstance(name, str):
        return name
    return name.removeprefix(_OMNIGENT_TOOL_PREFIX)


#: Harness tools a recorded Iris turn may contain without breaching the
#: boundary. ``ToolSearch`` only loads tool *schemas*; it executes nothing
#: against a tenant, and any tool it surfaces still has to be called as its own
#: ``function_call``, which the check below sees and refuses. Iris dispatch
#: itself is gated separately by :func:`omnigent.airbrx.iris.runtime.invoke`
#: against :data:`~omnigent.airbrx.iris.runtime.TOOLS`, which is why this set is
#: kept out of ``TOOLS``: widening the record-side guard must not widen what can
#: actually be dispatched.
_HARNESS_TOOLS = frozenset({"ToolSearch"})

#: Everything a recorded turn is allowed to contain.
_ALLOWED_IN_A_TURN = TOOLS | _HARNESS_TOOLS


def completed_answer(items: list[dict]) -> dict | None:
    tools = [bare_tool_name(i.get("name")) for i in items if i.get("type") == "function_call"]
    if set(tools) - _ALLOWED_IN_A_TURN:
        raise HTTPException(409, "Unexpected Iris tool boundary; turn rejected")
    answers = [
        i
        for i in items
        if i.get("type") == "message"
        and i.get("role") == "assistant"
        and i.get("status") == "completed"
    ]
    if not answers:
        return None
    text = "\n".join(
        c.get("text", "") for c in answers[-1].get("content", []) if c.get("type") == "output_text"
    )
    return {"text": text, "tools": tools, "failed": None} if text.strip() else None


def report_references(items):
    calls = {
        i.get("call_id"): bare_tool_name(i.get("name"))
        for i in items
        if i.get("type") == "function_call"
    }
    references = []
    for item in items:
        if (
            item.get("type") != "function_call_output"
            or calls.get(item.get("call_id")) not in TOOLS
        ):
            continue
        try:
            result = json.loads(item.get("output", ""))
        except (ValueError, TypeError):
            continue
        if isinstance(result, dict) and not result.get("error") and not result.get("error_code"):
            for download in result.get("downloads", []):
                if download.get("filename") == "report.json":
                    references.append(
                        (calls[item["call_id"]], download["file_id"], item.get("created_at", 0))
                    )
    return references


def create_iris_router(*, auth_provider, agent_store):
    router = APIRouter()
    locks: dict[str, asyncio.Lock] = {}

    async def authorize(request, session_id, client):
        user = require_user(request, auth_provider)
        # This adapter is never an unauthenticated developer bridge.
        if user is None:
            raise HTTPException(401, "Iris requires host authentication")
        session = await checked(await client.get(f"/v1/sessions/{session_id}"))
        agent = agent_store.get_by_name("iris")
        if agent is None or session.get("agent_id") != agent.id:
            raise HTTPException(404, "Not a registered Iris session")
        if (session.get("permission_level") or 0) < 2:
            raise HTTPException(403, "Iris requires session edit permission")
        try:
            binding = session_binding(session, user)
        except ValueError as exc:
            raise HTTPException(403, str(exc)) from None
        return session, binding

    async def turn(request, session_id, client, message, deadline=300):
        session, _ = await authorize(request, session_id, client)
        lock = locks.setdefault(session_id, asyncio.Lock())
        if lock.locked() or session["status"] in {"running", "waiting"}:
            raise HTTPException(409, "Iris is busy; cancel or wait for the current turn")
        async with lock:
            posted = await checked(
                await client.post(
                    f"/v1/sessions/{session_id}/events",
                    json={
                        "type": "message",
                        "data": {
                            "role": "user",
                            "content": [{"type": "input_text", "text": message}],
                        },
                    },
                )
            )
            cursor = posted.get("item_id")
            if not posted.get("queued") or not cursor:
                raise HTTPException(409, "Native session did not accept the turn")
            expires = time.monotonic() + deadline
            try:
                while time.monotonic() < expires:
                    if await request.is_disconnected():
                        raise asyncio.CancelledError()
                    snapshot, _ = await authorize(request, session_id, client)
                    page = await checked(
                        await client.get(
                            f"/v1/sessions/{session_id}/items",
                            params={"after": cursor, "limit": 1000},
                        )
                    )
                    if page.get("has_more"):
                        raise HTTPException(
                            409, "Turn exceeds workspace history limit; open native chat"
                        )
                    if snapshot["status"] == "failed" or snapshot.get("last_task_error"):
                        raise HTTPException(409, "Iris turn failed; open native chat to recover")
                    answer = completed_answer(page["data"])
                    if (
                        answer
                        and snapshot["status"] == "idle"
                        and not snapshot.get("active_response_id")
                    ):
                        return {**answer, "item_id": cursor}
                    await asyncio.sleep(0.5)
                raise HTTPException(504, "Iris turn timed out and was cancelled")
            except (asyncio.CancelledError, HTTPException):
                await client.post(
                    f"/v1/sessions/{session_id}/events", json={"type": "interrupt", "data": {}}
                )
                raise

    @router.get("/iris")
    async def catalog(request: Request):
        user = require_user(request, auth_provider)
        if user is None:
            raise HTTPException(401)
        agent = agent_store.get_by_name("iris")
        return {
            "agent_id": agent.id if agent else None,
            "revision": json.loads((HERE / "source.json").read_text())["revision"],
            "bindings": [
                {
                    "tenant_id": b.tenant_id,
                    "host_id": b.host_id,
                    "workspace": b.workspace,
                    "fixture": b.fixture,
                }
                for b in bindings()
                if user in b.users
            ],
        }

    @router.get("/iris/portrait", include_in_schema=False)
    async def portrait(request: Request):
        """Iris's portrait, read from her pinned package, for the agent drawer.

        The drawer's avatar store is an upload surface and nothing has been
        uploaded to it, so without this the one agent who actually ships a
        portrait would sit in the roster as two grey initials. Served from the
        verified archive rather than a copy committed into the web assets, so
        it cannot drift from the package it belongs to, and behind the same
        authentication as every other route in this module.
        """
        if require_user(request, auth_provider) is None:
            raise HTTPException(401, "Iris requires host authentication")
        return FileResponse(
            source_root() / "ui/assets/iris-portrait.png",
            media_type="image/png",
            headers={"Cache-Control": "private, max-age=3600"},
        )

    @router.post(
        "/iris/sessions/{session_id}/ui/api/chat",
        dependencies=[Depends(require_json_content_type)],
    )
    async def chat(request: Request, session_id: str, body: ChatRequest):
        if body.history[-1].role != "user" or not body.history[-1].content.strip():
            raise HTTPException(422, "The last message must be a nonempty user question")
        # Only the last question is forwarded; native history owns prior actions.
        async with session_client(request) as client:
            return await turn(request, session_id, client, body.history[-1].content, body.deadline)

    @router.post(
        "/iris/sessions/{session_id}/ui/api/cancel",
        dependencies=[Depends(require_json_content_type)],
    )
    async def cancel(request: Request, session_id: str):
        async with session_client(request) as client:
            await authorize(request, session_id, client)
            return await checked(
                await client.post(
                    f"/v1/sessions/{session_id}/events", json={"type": "interrupt", "data": {}}
                )
            )

    async def read_state(request: Request, session_id: str, fresh: bool = False):
        async with session_client(request) as client:
            _, binding = await authorize(request, session_id, client)
            params = {"limit": 1000, "order": "desc"}
            if fresh:
                fresh_turn = await turn(
                    request,
                    session_id,
                    client,
                    (
                        "Call iris_overview and iris_audit for the selected tenant. "
                        "Summarize the measured denominator and coverage. Do not propose changes."
                    ),
                )
                params["after"] = fresh_turn["item_id"]
            page = await checked(
                await client.get(f"/v1/sessions/{session_id}/items", params=params)
            )
            refs = report_references(list(reversed(page["data"])))
            reports = {}
            cache_age = 0
            for tool, file_id, created_at in reversed(refs):
                if tool in reports:
                    continue
                report = await checked(
                    await client.get(
                        f"/v1/sessions/{session_id}/resources/files/{file_id}/content"
                    )
                )
                if report.get("tenant_id") != binding.tenant_id:
                    raise HTTPException(403, "Report tenant does not match session")
                reports[tool] = report
                if tool == "iris_overview":
                    cache_age = max(0, time.time() - created_at)
            if "iris_overview" not in reports:
                raise HTTPException(409, "No session overview yet; use Refresh from host")
            rules, meta = [], {}
            for evidence in reports["iris_overview"].get("evidence", []):
                if evidence.get("source_tool") == "get_rule_effectiveness":
                    data = evidence.get("data") or {}
                    rules = data.get("rules") or []
                    meta = {k: data.get(k) for k in ("year", "generatedAt", "totalQueries")}
            return {
                "overview": reports["iris_overview"],
                "audit": reports.get("iris_audit", {"findings": []}),
                "rules": rules,
                "rule_effectiveness_meta": meta,
                "stale": cache_age > 300,
                "cache_age_seconds": round(cache_age),
                "monitoring": None,
            }

    @router.get("/iris/sessions/{session_id}/ui/api/readiness")
    async def readiness(request: Request, session_id: str):
        """What this host has actually checked about running a turn here.

        Deliberately not a prediction. Whether the execution host can reach the
        model is not knowable from the server side until a turn runs, so this
        reports the preconditions it did verify, whether a turn has ever
        completed in this session, and whether the session recorded a failure —
        and names the rest as unverified rather than letting the workspace show
        a hopeful spinner over an unproven connection.

        The host's own error text is deliberately not forwarded: the rest of
        this module refuses to reflect execution diagnostics, which can carry
        credential material. The workspace is told a failure happened and where
        the authoritative record is, which is what it needs to stop claiming.
        """
        async with session_client(request) as client:
            session, binding = await authorize(request, session_id, client)
            page = await checked(
                await client.get(
                    f"/v1/sessions/{session_id}/items",
                    params={"limit": 1000, "order": "desc"},
                )
            )
            completed = any(
                item.get("type") == "message"
                and item.get("role") == "assistant"
                and item.get("status") == "completed"
                for item in page["data"]
            )
            scope = " (synthetic fixture)" if binding.fixture else " (read-only)"
            return {
                "tenant_id": binding.tenant_id,
                "fixture": binding.fixture,
                "session_status": session.get("status"),
                "turn_completed_here": completed,
                "last_task_failed": bool(session.get("last_task_error")),
                "verified": [
                    "you are authenticated to this Omnigent host",
                    "Iris is a registered agent on this host",
                    "this session belongs to Iris and you may run turns in it",
                    f"tenant {binding.tenant_id} is bound to this session's workspace{scope}",
                ],
                "unverified": (
                    []
                    if completed
                    else [
                        "no turn has completed in this session, so whether the execution "
                        "host can reach the model is unknown; it cannot be known from here "
                        "until a turn actually runs"
                    ]
                ),
            }

    @router.get("/iris/sessions/{session_id}/ui/api/state")
    async def state(request: Request, session_id: str):
        return await read_state(request, session_id)

    @router.post(
        "/iris/sessions/{session_id}/ui/api/refresh",
        dependencies=[Depends(require_json_content_type)],
    )
    async def refresh(request: Request, session_id: str):
        return await read_state(request, session_id, fresh=True)

    @router.get("/iris/sessions/{session_id}/ui/{asset:path}", include_in_schema=False)
    async def asset(request: Request, session_id: str, asset: str):
        async with session_client(request) as client:
            await authorize(request, session_id, client)
        if not asset or asset == "index.html":
            html = (source_root() / "ui/index.html").read_text()
            html = html.replace(
                '<script src="theme.js">', '<script src="host.js"></script><script src="theme.js">'
            )
            return HTMLResponse(
                html, headers={"Cache-Control": "no-store", "X-Frame-Options": "SAMEORIGIN"}
            )
        if asset == "host.js":
            # No-store, unlike the pinned assets below: this adapter is part of
            # the host build, not the verified package, and a cached copy would
            # keep reporting a readiness contract the server has moved past.
            return FileResponse(
                HERE / "host.js",
                media_type="text/javascript",
                headers={"Cache-Control": "no-store"},
            )
        allowed = {
            "app.js",
            "theme.js",
            "style.css",
            "demo-state.json",
            "assets/iris-portrait.png",
            "assets/airbrx-logo.png",
        }
        if asset not in allowed:
            raise HTTPException(404)
        return FileResponse(
            source_root() / "ui" / asset, headers={"Cache-Control": "private, max-age=3600"}
        )

    return router
