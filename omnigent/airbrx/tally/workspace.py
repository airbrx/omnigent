"""Tally's workspace: the everyday surface over her native Omnigent session.

A copy of Eva's (``omnigent/airbrx/eva/workspace.py``) with Tally's content: a
small branded app served per session, framed by the Omnigent web app at
``/tally/:sessionId``, talking to five adapter routes whose response keys match
Iris's and Eva's.

**The view is built from Tally's own recorded tool results**, deterministically
and with no model call on read, the way Eva's is. A later result replaces an
earlier one for the same read. Until a read has run, the state is empty and
says so, and it never falls back to sample data.

**Missing is never zero.** Every KPI carries a value or ``None``, and ``None``
comes with the reason: not read yet, or read and not reported by the portal.
The shapes read here are the portal's own ``structuredContent`` (docs/tally/
RUNBOOK.md, "Tool results"); anything outside them is read defensively.

**Refresh is a turn** that asks Tally to make her four reads and write nothing.

The portal's own pages are framed through the existing same-origin proxy at
``/gateway/app/`` (``omnigent/airbrx/gateway/proxy.py``), so the state links
there.
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

from omnigent.airbrx.gateway.proxy import PREFIX as GATEWAY_APP_PATH
from omnigent.airbrx.tally.config import Binding, bindings
from omnigent.airbrx.tally.package import portrait_path
from omnigent.airbrx.tally.policy import DISCOVERY_TOOLS, PORTAL_SERVER, TALLY_TOOLS
from omnigent.server.routes._auth_helpers import require_user
from omnigent.server.routes._content_type import require_json_content_type

UI_ROOT = Path(__file__).parent / "ui"

#: The portal as the browser reaches it, through the gateway proxy.
PORTAL_URL = GATEWAY_APP_PATH + "/"

#: Iris's threshold: state older than this is flagged ``stale``.
STALE_AFTER_SECONDS = 300

#: Every tool a recorded turn may contain without breaching the boundary.
_ALLOWED_IN_A_TURN = TALLY_TOOLS | DISCOVERY_TOOLS

#: Files the framed app may fetch. Nothing else is served. The portrait is the
#: packaged one, the same file the agent drawer shows.
_ASSETS: dict[str, tuple[Path | None, str]] = {
    "app.js": (UI_ROOT / "app.js", "text/javascript"),
    "style.css": (UI_ROOT / "style.css", "text/css"),
    "assets/airbrx-logo.png": (UI_ROOT / "assets/airbrx-logo.png", "image/png"),
    "assets/tally-portrait.png": (None, "image/png"),
}

#: The Refresh turn. Read-only by instruction and in effect: she holds no write.
REFRESH_PROMPT = (
    "Workspace refresh. Call get_health, get_analytics_overview and get_sprint_board, "
    "then get_agent_policy with agent iris and again with agent eva. Do not change, "
    "publish or write anything. When done, reply with one line: how many agents are "
    "tracked, how many decisions are waiting, how many blockers are open, and anything "
    "the portal did not report."
)

#: The sprint board sections the KPIs come from, matched on the heading text.
DECISIONS_HEADING = "Decisions needed from Abram"
BLOCKERS_HEADING = "Blockers"


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
    """``mcp__omnigent__portal__get_health`` and ``portal__get_health`` to ``get_health``."""
    name = name or ""
    for prefix in ("mcp__omnigent__", f"{PORTAL_SERVER}__"):
        if name.startswith(prefix):
            name = name[len(prefix) :]
    return name


def _decode(output: Any) -> Any:
    """A tool result as data, or ``None`` when it is not JSON (a policy denial)."""
    value = output
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    if isinstance(value, dict) and set(value) == {"result"} and isinstance(value["result"], str):
        try:
            value = json.loads(value["result"])
        except ValueError:
            return None
    # An MCP CallToolResult recorded whole: the data is its structuredContent.
    if isinstance(value, dict) and isinstance(value.get("structuredContent"), dict):
        return value["structuredContent"]
    return value


def tool_results(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pair each recorded call with its result, oldest first.

    Pairing is by position as well as ``call_id``, as Eva's is: a ``call_id``
    has been seen reused for consecutive calls in one turn.
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


def _count(value: Any) -> int | None:
    """A non-negative integer, or None. ``True`` is not a count."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _number(value: Any) -> float | int | None:
    """A number, or None. ``null`` in the portal means unavailable, never zero."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def markdown_section(markdown: str, heading: str) -> list[str] | None:
    """The lines under ``## <heading>`` up to the next ``#``/``##`` heading.

    ``None`` when the heading is absent, which is "unavailable", not zero.
    Deeper headings (``###``) stay inside the section.
    """
    lines = markdown.replace("\r\n", "\n").split("\n")
    for n, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("## ") and stripped[3:].strip().lower() == heading.lower():
            body: list[str] = []
            for rest in lines[n + 1 :]:
                marker = rest.lstrip()
                if marker.startswith(("# ", "## ")):
                    break
                body.append(rest)
            return body
    return None


_LIST_ITEM = ("- ", "* ", "+ ")


def decision_items(markdown: str) -> list[str] | None:
    """The top-level list items under "Decisions needed from Abram".

    Nested items belong to the item above them and are not counted.
    """
    section = markdown_section(markdown, DECISIONS_HEADING)
    if section is None:
        return None
    items: list[str] = []
    for line in section:
        if not line or line[0].isspace():
            continue
        head, _, rest = line.partition(" ")
        if line.startswith(_LIST_ITEM):
            items.append(line[2:].strip())
        elif head.endswith((".", ")")) and head[:-1].isdigit() and rest.strip():
            items.append(rest.strip())
    return items


def _cells(row: str) -> list[str]:
    inner = row.strip()
    if inner.startswith("|"):
        inner = inner[1:]
    if inner.endswith("|"):
        inner = inner[:-1]
    return [c.strip() for c in inner.split("|")]


def _is_separator(row: str) -> bool:
    cells = _cells(row)
    return bool(cells) and all(c and set(c) <= set(":- ") and "-" in c for c in cells)


def blocker_rows(markdown: str) -> list[str] | None:
    """The first-column item of each row of the table under "Blockers".

    ``None`` when the section is missing or holds no table (header and
    separator), because then the board did not say. A table with a header and
    no rows is a real zero.
    """
    section = markdown_section(markdown, BLOCKERS_HEADING)
    if section is None:
        return None
    rows = [line for line in section if line.strip().startswith("|")]
    if len(rows) < 2 or not _is_separator(rows[1]):
        return None
    items: list[str] = []
    for row in rows[2:]:
        cells = _cells(row)
        if any(cells):
            items.append(cells[0])
    return items


def board_summary(board: Any) -> dict[str, Any] | None:
    """Decisions and blockers parsed out of the sprint board's markdown."""
    if not isinstance(board, dict) or not isinstance(board.get("markdown"), str):
        return None
    markdown = board["markdown"]
    return {
        "decisions": decision_items(markdown),
        "blockers": blocker_rows(markdown),
        "truncated": bool(board.get("truncated")),
    }


def _kpi(read: dict[str, Any] | None, value: Any, source: str, missing: str) -> dict[str, Any]:
    """One KPI: a value with its source, or None with the reason."""
    if read is None:
        return {"value": None, "source": None, "reason": "not read yet", "at": None}
    if value is None:
        return {"value": None, "source": None, "reason": missing, "at": read["at"]}
    return {"value": value, "source": source, "reason": None, "at": read["at"]}


def _analytics(read: dict[str, Any] | None) -> dict[str, Any]:
    data = read["data"] if read else None
    return data if isinstance(data, dict) else {}


def workspace_state(
    items: Iterable[dict[str, Any]], binding: Binding | None = None
) -> dict[str, Any]:
    """Everything the workspace shows, from the session's own record. No model runs."""
    reads: dict[str, dict[str, Any] | None] = {
        "get_analytics_overview": None,
        "get_sprint_board": None,
        "get_health": None,
    }
    policies: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, Any]] = []

    for call in tool_results(items):
        tool, args, result, at = call["tool"], call["arguments"], call["result"], call["at"]
        if tool not in TALLY_TOOLS:
            continue
        if isinstance(result, dict) and "error" in result:
            err = result["error"]
            message = err.get("message") if isinstance(err, dict) else str(err)
            errors.append({"tool": tool, "message": message, "at": at})
            continue
        if result is None:
            continue
        if tool == "get_agent_policy":
            agent = args.get("agent") or (
                result.get("agent") if isinstance(result, dict) else None
            )
            if isinstance(agent, str) and agent:
                policies[agent] = {"data": result, "at": at}
        else:
            reads[tool] = {"data": result, "at": at}

    analytics = reads["get_analytics_overview"]
    board = reads["get_sprint_board"]
    health = reads["get_health"]

    overview = _analytics(analytics)
    summary = overview.get("summary") if isinstance(overview.get("summary"), dict) else {}
    agents = overview.get("agents")
    updated_at = overview.get("updated_at")
    parsed = board_summary(board["data"]) if board else None
    truncated = " (the board was truncated)" if parsed and parsed["truncated"] else ""
    decisions = parsed["decisions"] if parsed else None
    blockers = parsed["blockers"] if parsed else None
    kpis = {
        "decisions_waiting": _kpi(
            board,
            len(decisions) if decisions is not None else None,
            f"sprint board, {DECISIONS_HEADING}",
            f'the sprint board has no "{DECISIONS_HEADING}" section{truncated}',
        ),
        "blockers": _kpi(
            board,
            len(blockers) if blockers is not None else None,
            f"sprint board, {BLOCKERS_HEADING} table",
            f'the sprint board has no "{BLOCKERS_HEADING}" table{truncated}',
        ),
        "agents_tracked": _kpi(
            analytics,
            len(agents) if isinstance(agents, list) else None,
            "analytics, agents",
            "the analytics overview did not report agents",
        ),
        "data_freshness": _kpi(
            analytics,
            updated_at if isinstance(updated_at, str) and updated_at else None,
            "analytics, updated_at",
            "the analytics overview did not report when its data is from",
        ),
        # An ESTIMATE at published rates, not a charge. actual_charges_usd is
        # the only measured charge and is reported beside it, null or not.
        "api_equivalent_usd": {
            **_kpi(
                analytics,
                _number(summary.get("api_equivalent_usd")),
                "analytics, summary.api_equivalent_usd",
                "the analytics overview did not report API-equivalent spend",
            ),
            "basis": "estimated",
            "actual_charges_usd": _number(summary.get("actual_charges_usd")),
        },
    }

    stamps = [r["at"] for r in (analytics, board, health) if r and r.get("at")]
    stamps += [p["at"] for p in policies.values() if p.get("at")]
    refreshed_at = max(stamps) if stamps else None
    age = None if refreshed_at is None else max(0, round(time.time() - refreshed_at))
    return {
        "analytics": analytics,
        "board": board,
        "health": health,
        "policies": policies,
        "decisions": decisions,
        "blockers": blockers,
        "kpis": kpis,
        "refreshed_at": refreshed_at,
        "errors": errors[-5:],
        "empty": analytics is None and board is None and health is None and not policies,
        "stale": age is None or age > STALE_AFTER_SECONDS,
        "cache_age_seconds": age,
        "portal_url": PORTAL_URL if binding else None,
        "binding_label": binding.label if binding else None,
    }


def refreshed_by(items: list[dict[str, Any]], marker_id: str) -> bool:
    """Did the turn that starts at ``marker_id`` record a successful portal read?"""
    start = next((n for n, i in enumerate(items) if i.get("id") == marker_id), None)
    if start is None:
        return False
    return any(
        call["tool"] in TALLY_TOOLS
        and call["result"] is not None
        and not (isinstance(call["result"], dict) and "error" in call["result"])
        for call in tool_results(items[start:])
    )


def completed_answer(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The finished answer of one turn, or None while it is still running.

    Refuses a turn that recorded a call to any tool outside Tally's allow list.
    Her policy already denies such a call; this makes the workspace say so
    instead of rendering around it.
    """
    tools = [bare_tool_name(i.get("name")) for i in items if i.get("type") == "function_call"]
    if set(tools) - _ALLOWED_IN_A_TURN:
        raise HTTPException(409, "Tally reached for a tool outside her boundary; turn rejected")
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
            raise HTTPException(401, "Tally requires host authentication")
        session = await _checked(await client.get(f"/v1/sessions/{session_id}"))
        agent = agent_store.get_by_name("tally")
        if agent is None or session.get("agent_id") != agent.id:
            raise HTTPException(404, "Not a registered Tally session")
        if (session.get("permission_level") or 0) < 2:
            raise HTTPException(403, "Tally requires session edit permission")
        host = session.get("host_id")
        mine = [b for b in bindings() if user in b.users and (not b.host_id or b.host_id == host)]
        if len(mine) != 1:
            raise HTTPException(
                403, "No single Tally binding authorizes you on this session's host"
            )
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
            raise HTTPException(409, "Tally is busy; cancel or wait for the current turn")
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
                        raise HTTPException(
                            409, "Tally's turn failed; open native chat to recover"
                        )
                    answer = completed_answer(page["data"])
                    if (
                        answer
                        and snapshot["status"] == "idle"
                        and not snapshot.get("active_response_id")
                    ):
                        return {**answer, "item_id": cursor}
                    await asyncio.sleep(0.5)
                raise HTTPException(504, "Tally's turn timed out and was cancelled")
            except (asyncio.CancelledError, HTTPException):
                await client.post(
                    f"/v1/sessions/{session_id}/events", json={"type": "interrupt", "data": {}}
                )
                raise

    async def read_state(request: Request, session_id: str, fresh: bool = False) -> dict[str, Any]:
        async with _session_client(request) as client:
            _, binding = await authorize(request, session_id, client)
            marker = None
            if fresh:
                marker = (await turn(request, session_id, client, REFRESH_PROMPT))["item_id"]
            page = await _checked(
                await client.get(
                    f"/v1/sessions/{session_id}/items", params={"limit": 1000, "order": "desc"}
                )
            )
        items = list(reversed(page["data"]))
        if marker is not None and not refreshed_by(items, marker):
            raise HTTPException(
                409, "The refresh read nothing; open native chat to see what Tally did."
            )
        state = workspace_state(items, binding)
        if state["empty"]:
            raise HTTPException(409, "No workspace state yet; use Refresh")
        return state

    @router.get("/tally/portrait", include_in_schema=False)
    async def portrait(request: Request) -> FileResponse:
        """Tally's packaged portrait, the drawer's fallback when the avatar store is empty."""
        if require_user(request, auth_provider) is None:
            raise HTTPException(401, "Tally requires host authentication")
        return FileResponse(
            portrait_path(),
            media_type="image/png",
            headers={"Cache-Control": "private, max-age=3600"},
        )

    @router.post(
        "/tally/sessions/{session_id}/ui/api/chat",
        dependencies=[Depends(require_json_content_type)],
    )
    async def chat(request: Request, session_id: str, body: ChatRequest) -> dict[str, Any]:
        if body.history[-1].role != "user" or not body.history[-1].content.strip():
            raise HTTPException(422, "The last message must be a nonempty user question")
        # Only the last message is forwarded; the native session owns history.
        async with _session_client(request) as client:
            return await turn(request, session_id, client, body.history[-1].content, body.deadline)

    @router.post(
        "/tally/sessions/{session_id}/ui/api/cancel",
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

    @router.get("/tally/sessions/{session_id}/ui/api/state")
    async def state(request: Request, session_id: str) -> dict[str, Any]:
        return await read_state(request, session_id)

    @router.post(
        "/tally/sessions/{session_id}/ui/api/refresh",
        dependencies=[Depends(require_json_content_type)],
    )
    async def refresh(request: Request, session_id: str) -> dict[str, Any]:
        return await read_state(request, session_id, fresh=True)

    @router.get("/tally/sessions/{session_id}/ui/api/readiness")
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
            # Iris's keys. Tally has no tenant; her binding label stands in.
            "tenant_id": binding.label,
            "name": binding.label,
            "fixture": binding.fixture,
            "session_status": session.get("status"),
            "turn_completed_here": completed,
            "last_task_failed": bool(session.get("last_task_error")),
            # Tally's additions.
            "label": binding.label,
            "portal_url": PORTAL_URL,
            "verified": [
                "you are authenticated to this Omnigent host",
                "Tally is a registered agent on this host",
                "this session belongs to Tally and you may run turns in it",
                "your Tally binding authorizes you on this session's host",
            ],
            "unverified": (
                []
                if completed
                else [
                    "no turn has completed in this session, so whether Tally can reach "
                    "the model and the portal is unknown until Refresh or a question runs"
                ]
            ),
        }

    @router.get("/tally/sessions/{session_id}/ui/{asset:path}", include_in_schema=False)
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
