"""Eva's workspace: the everyday surface over her native Omnigent session.

Shaped like Iris's (``omnigent/airbrx/iris/routes.py``) on purpose: a small
branded app served per session, framed by the Omnigent web app at
``/eva/:sessionId``, talking to five adapter routes. See docs/eva/WORKSPACE.md.

Two things differ, and both are about where the data lives.

**The coordinator never talks to the outreach app.** For a binding on an
execution host's loopback it cannot, and the workspace must work the same
locally and on omnigent.airbrx.ai. So :func:`workspace_state` builds the whole
view, deterministically and with no model call, from Eva's own tool results
already recorded in the session. A later result replaces an earlier one for the
same lead or draft.

**Refresh is a turn.** It asks Eva to call ``list_pool``, ``list_my_leads`` and
``get_lead`` for each lead she holds, and to write nothing. Until one has run,
the state is empty and says so. It never falls back to sample data: Iris's
workspace once opened on a complete synthetic report behind a small chip, and a
rep reading it could not tell.

The outreach app remains the deeper surface, for configuration, approvals and
edits. The state carries the binding's ``base_url`` so the workspace can link
there; it is the app as the execution host sees it, which on a single Mac is
also what the browser sees.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from omnigent.airbrx.eva.config import Binding, bindings
from omnigent.airbrx.eva.package import portrait_path
from omnigent.airbrx.eva.policy import EVA_TOOLS
from omnigent.server.routes._auth_helpers import require_user
from omnigent.server.routes._content_type import require_json_content_type

UI_ROOT = Path(__file__).parent / "ui"

#: Harness tools a recorded turn may contain without breaching the boundary.
#: ``ToolSearch`` only loads schemas; anything it surfaces still has to be
#: called as its own ``function_call``, which the check below sees.
_HARNESS_TOOLS = frozenset({"ToolSearch"})
_ALLOWED_IN_A_TURN = EVA_TOOLS | _HARNESS_TOOLS

#: Files the framed app may fetch, and where each one lives. Nothing else is
#: served. Her portrait is the packaged one (``package.portrait_path``), the
#: same file the agent drawer shows, so there is one picture of her.
_ASSETS: dict[str, tuple[Path | None, str]] = {
    "app.js": (UI_ROOT / "app.js", "text/javascript"),
    "style.css": (UI_ROOT / "style.css", "text/css"),
    "assets/airbrx-logo.png": (UI_ROOT / "assets/airbrx-logo.png", "image/png"),
    "assets/eva-portrait.png": (None, "image/png"),
}

#: The Refresh turn. Read-only by instruction, and read-only in effect: nothing
#: here can write, because every write tool needs arguments this prompt does
#: not give her.
REFRESH_PROMPT = (
    "Workspace refresh. Call list_pool with limit 50, then list_my_leads. Then call "
    "get_lead for every lead list_my_leads returned, with include profile, "
    "qualification, touches and drafts. Do not claim, draft, submit or write anything. "
    "When done, reply with one line: how many leads are in the pool, how many you hold, "
    "and how many drafts you saw."
)


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str
    content: str = Field(max_length=16000)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    history: list[ChatMessage] = Field(min_length=1, max_length=24)
    deadline: int = Field(default=300, ge=1, le=300)


# --------------------------------------------------------------------------
# Reading recorded tool results
# --------------------------------------------------------------------------


def bare_tool_name(name: str | None) -> str:
    """``mcp__omnigent__outreach__list_pool`` and ``outreach__list_pool`` to ``list_pool``."""
    name = name or ""
    for prefix in ("mcp__omnigent__", "outreach__"):
        if name.startswith(prefix):
            name = name[len(prefix) :]
    return name


def _decode(output: Any) -> Any:
    """A tool result as data, or ``None`` when it is not JSON.

    Results arrive as JSON text, sometimes wrapped as ``{"result": "<json>"}``
    by the harness. A policy denial is plain text and decodes to ``None``.
    """
    value = output
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    if isinstance(value, dict) and set(value) == {"result"} and isinstance(value["result"], str):
        try:
            return json.loads(value["result"])
        except ValueError:
            return None
    return value


def _lead_id(row: dict[str, Any]) -> str | None:
    """The contract says ``lead_id``; the live app has also answered with ``id``."""
    value = row.get("lead_id") or row.get("id")
    return str(value) if value else None


def tool_results(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pair each recorded call with its result, oldest first.

    Pairing is by position as well as ``call_id``: the same ``call_id`` has been
    seen reused for consecutive calls in one turn, so a result belongs to the
    most recent call with its id.
    """
    pending: dict[str, dict[str, Any]] = {}
    out: list[dict[str, Any]] = []
    for item in items:
        kind = item.get("type")
        if kind == "function_call":
            try:
                arguments = json.loads(item.get("arguments") or "{}")
            except ValueError:
                arguments = {}
            pending[str(item.get("call_id"))] = {
                "tool": bare_tool_name(item.get("name")),
                "arguments": arguments if isinstance(arguments, dict) else {},
            }
        elif kind == "function_call_output":
            call = pending.pop(str(item.get("call_id")), None)
            if call is None:
                continue
            out.append(
                {**call, "result": _decode(item.get("output")), "at": item.get("created_at")}
            )
    return out


def workspace_state(
    items: Iterable[dict[str, Any]], binding: Binding | None = None
) -> dict[str, Any]:
    """Everything the workspace shows, from the session's own record. No model runs."""
    pool: dict[str, Any] | None = None
    mine: dict[str, Any] | None = None
    leads: dict[str, dict[str, Any]] = {}
    drafts: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, Any]] = []

    def draft(draft_id: Any, **fields: Any) -> None:
        if not draft_id:
            return
        key = str(draft_id)
        row = drafts.setdefault(key, {"draft_id": key})
        row.update({k: v for k, v in fields.items() if v is not None})

    for call in tool_results(items):
        tool, args, result, at = call["tool"], call["arguments"], call["result"], call["at"]
        if isinstance(result, dict) and "error" in result:
            err = result["error"]
            if (
                isinstance(err, dict)
                and err.get("code") == "blocked_by_rule"
                and tool == "submit_draft"
            ):
                details = err.get("details") or {}
                draft(
                    details.get("draft_id"),
                    lead_id=args.get("lead_id"),
                    version_no=details.get("version_no"),
                    subject=args.get("subject"),
                    channel=args.get("channel"),
                    blocked=True,
                    rule_results=details.get("rule_results") or [],
                    updated_at=at,
                )
            message = err.get("message") if isinstance(err, dict) else str(err)
            errors.append({"tool": tool, "message": message, "at": at})
            continue
        if not isinstance(result, dict):
            continue
        if tool == "list_pool":
            pool = {"leads": result.get("leads") or [], "total": result.get("total"), "at": at}
        elif tool == "list_my_leads":
            mine = {"leads": result.get("leads") or [], "total": result.get("total"), "at": at}
        elif tool == "get_lead":
            profile = result.get("profile") or {}
            lead_id = _lead_id(profile) or args.get("lead_id")
            if not lead_id:
                continue
            leads[str(lead_id)] = {**result, "lead_id": str(lead_id), "at": at}
            for d in result.get("drafts") or []:
                draft(
                    d.get("draft_id"),
                    lead_id=str(lead_id),
                    company=profile.get("company"),
                    name=profile.get("name"),
                    channel=d.get("channel"),
                    status=d.get("status"),
                    version_no=d.get("version_no"),
                    subject=d.get("subject"),
                    updated_at=d.get("updated_at") or at,
                )
        elif tool == "submit_draft":
            draft(
                result.get("draft_id"),
                lead_id=args.get("lead_id"),
                version_no=result.get("version_no"),
                status=result.get("status"),
                subject=args.get("subject"),
                channel=args.get("channel"),
                blocked=bool(result.get("blocked")),
                rule_results=result.get("rule_results") or [],
                updated_at=at,
            )
        elif tool == "request_approval":
            draft(
                result.get("draft_id"),
                status=result.get("status"),
                approval_requested_from=result.get("approval_requested_from"),
                requested_at=result.get("requested_at"),
                updated_at=at,
            )
        elif tool == "claim_lead" and result.get("lead_id"):
            lead = leads.get(str(result["lead_id"]))
            if lead is not None:
                profile = dict(lead.get("profile") or {})
                profile["claim"] = {
                    "rep": result.get("rep"),
                    "expires_at": result.get("expires_at"),
                }
                lead["profile"] = profile

    refreshed = [x["at"] for x in (pool, mine) if x and x.get("at")]
    return {
        "pool": pool,
        "mine": mine,
        "leads": leads,
        "drafts": sorted(drafts.values(), key=lambda d: d.get("updated_at") or 0, reverse=True),
        "refreshed_at": max(refreshed) if refreshed else None,
        "errors": errors[-5:],
        "empty": pool is None and mine is None and not leads and not drafts,
        "outreach_url": binding.base_url.rstrip("/") if binding else None,
        "binding_label": binding.label if binding else None,
    }


def completed_answer(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The finished answer of one turn, or None while it is still running.

    Refuses a turn that recorded a call to any tool outside Eva's allow list,
    the same line Iris's workspace draws. Her policy already denies such a
    call; this makes the workspace say so instead of rendering around it.
    """
    tools = [bare_tool_name(i.get("name")) for i in items if i.get("type") == "function_call"]
    if set(tools) - _ALLOWED_IN_A_TURN:
        raise HTTPException(409, "Eva reached for a tool outside her boundary; turn rejected")
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
    return {"text": text, "tools": tools} if text.strip() else None


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------


@asynccontextmanager
async def _session_client(request: Request) -> AsyncIterator[httpx.AsyncClient]:
    """Call this server's own session API as the caller, in process."""
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


async def _checked(response: httpx.Response) -> Any:
    if response.status_code >= 400:
        raise HTTPException(response.status_code, "Native session operation failed")
    return response.json()


def add_workspace_routes(router: APIRouter, *, auth_provider: Any, agent_store: Any) -> None:
    locks: dict[str, asyncio.Lock] = {}

    async def authorize(
        request: Request, session_id: str, client: httpx.AsyncClient
    ) -> tuple[dict[str, Any], Binding]:
        user = require_user(request, auth_provider)
        if user is None:
            raise HTTPException(401, "Eva requires host authentication")
        session = await _checked(await client.get(f"/v1/sessions/{session_id}"))
        agent = agent_store.get_by_name("eva")
        if agent is None or session.get("agent_id") != agent.id:
            raise HTTPException(404, "Not a registered Eva session")
        if (session.get("permission_level") or 0) < 2:
            raise HTTPException(403, "Eva requires session edit permission")
        host = session.get("host_id")
        mine = [b for b in bindings() if user in b.users and (not b.host_id or b.host_id == host)]
        if len(mine) != 1:
            raise HTTPException(403, "No single Eva binding authorizes you on this session's host")
        return session, mine[0]

    async def turn(
        request: Request,
        session_id: str,
        client: httpx.AsyncClient,
        message: str,
        deadline: int = 300,
    ) -> dict[str, Any]:
        session, _ = await authorize(request, session_id, client)
        lock = locks.setdefault(session_id, asyncio.Lock())
        if lock.locked() or session["status"] in {"running", "waiting"}:
            raise HTTPException(409, "Eva is busy; cancel or wait for the current turn")
        async with lock:
            posted = await _checked(
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
                    page = await _checked(
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
                        raise HTTPException(409, "Eva's turn failed; open native chat to recover")
                    answer = completed_answer(page["data"])
                    if (
                        answer
                        and snapshot["status"] == "idle"
                        and not snapshot.get("active_response_id")
                    ):
                        return {**answer, "item_id": cursor}
                    await asyncio.sleep(0.5)
                raise HTTPException(504, "Eva's turn timed out and was cancelled")
            except (asyncio.CancelledError, HTTPException):
                await client.post(
                    f"/v1/sessions/{session_id}/events", json={"type": "interrupt", "data": {}}
                )
                raise

    async def read_state(request: Request, session_id: str) -> dict[str, Any]:
        async with _session_client(request) as client:
            _, binding = await authorize(request, session_id, client)
            page = await _checked(
                await client.get(
                    f"/v1/sessions/{session_id}/items", params={"limit": 1000, "order": "desc"}
                )
            )
        return workspace_state(list(reversed(page["data"])), binding)

    @router.get("/eva/portrait", include_in_schema=False)
    async def portrait(request: Request) -> FileResponse:
        """Eva's packaged portrait, the drawer's fallback when the avatar store is empty.

        Iris has the same route. The store normally wins, because server start
        registers this same file there; this keeps a host where it did not still
        showing her face rather than initials.
        """
        if require_user(request, auth_provider) is None:
            raise HTTPException(401, "Eva requires host authentication")
        return FileResponse(
            portrait_path(),
            media_type="image/png",
            headers={"Cache-Control": "private, max-age=3600"},
        )

    @router.post(
        "/eva/sessions/{session_id}/ui/api/chat",
        dependencies=[Depends(require_json_content_type)],
    )
    async def chat(request: Request, session_id: str, body: ChatRequest) -> dict[str, Any]:
        if body.history[-1].role != "user" or not body.history[-1].content.strip():
            raise HTTPException(422, "The last message must be a nonempty user question")
        # Only the last message is forwarded; the native session owns history.
        async with _session_client(request) as client:
            answer = await turn(
                request, session_id, client, body.history[-1].content, body.deadline
            )
        return {**answer, "state": await read_state(request, session_id)}

    @router.post(
        "/eva/sessions/{session_id}/ui/api/cancel",
        dependencies=[Depends(require_json_content_type)],
    )
    async def cancel(request: Request, session_id: str) -> Any:
        async with _session_client(request) as client:
            await authorize(request, session_id, client)
            return await _checked(
                await client.post(
                    f"/v1/sessions/{session_id}/events", json={"type": "interrupt", "data": {}}
                )
            )

    @router.get("/eva/sessions/{session_id}/ui/api/state")
    async def state(request: Request, session_id: str) -> dict[str, Any]:
        return await read_state(request, session_id)

    @router.post(
        "/eva/sessions/{session_id}/ui/api/refresh",
        dependencies=[Depends(require_json_content_type)],
    )
    async def refresh(request: Request, session_id: str) -> dict[str, Any]:
        async with _session_client(request) as client:
            answer = await turn(request, session_id, client, REFRESH_PROMPT)
        return {**answer, "state": await read_state(request, session_id)}

    @router.get("/eva/sessions/{session_id}/ui/api/readiness")
    async def readiness(request: Request, session_id: str) -> dict[str, Any]:
        """What has actually been checked about this session. Not a prediction."""
        async with _session_client(request) as client:
            session, binding = await authorize(request, session_id, client)
            page = await _checked(
                await client.get(
                    f"/v1/sessions/{session_id}/items", params={"limit": 1000, "order": "desc"}
                )
            )
        completed = any(
            i.get("type") == "message"
            and i.get("role") == "assistant"
            and i.get("status") == "completed"
            for i in page["data"]
        )
        return {
            "label": binding.label,
            "session_status": session.get("status"),
            "turn_completed_here": completed,
            "last_task_failed": bool(session.get("last_task_error")),
            "outreach_url": binding.base_url.rstrip("/"),
            "verified": [
                "you are authenticated to this Omnigent host",
                "Eva is a registered agent on this host",
                "this session belongs to Eva and you may run turns in it",
                "your Eva binding authorizes you on this session's host",
            ],
            "unverified": (
                []
                if completed
                else [
                    "no turn has completed in this session, so whether Eva's host can reach "
                    "the model and the outreach app is unknown until Refresh or a question runs"
                ]
            ),
        }

    @router.get("/eva/sessions/{session_id}/ui/{asset:path}", include_in_schema=False)
    async def asset(request: Request, session_id: str, asset: str) -> Any:
        async with _session_client(request) as client:
            await authorize(request, session_id, client)
        if not asset or asset == "index.html":
            return HTMLResponse(
                (UI_ROOT / "index.html").read_text(),
                headers={"Cache-Control": "no-store", "X-Frame-Options": "SAMEORIGIN"},
            )
        if asset not in _ASSETS:
            raise HTTPException(404)
        path, media_type = _ASSETS[asset]
        return FileResponse(
            path or portrait_path(),
            media_type=media_type,
            headers={
                "Cache-Control": "no-store"
                if asset.endswith((".js", ".css"))
                else "private, max-age=3600"
            },
        )
