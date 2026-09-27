"""Authenticated workspace adapter over Omnigent's native session APIs."""

from __future__ import annotations

import asyncio
import importlib
import json
import math
import os
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from omnigent.airbrx.iris.account import collect_captures
from omnigent.airbrx.iris.config import bindings, session_binding
from omnigent.airbrx.iris.package import HERE, source_root
from omnigent.airbrx.iris.records import (
    bare_tool_name,
    reads_current_period,
    report_calls,
    report_references,  # noqa: F401 -- re-exported; tests import it from here
)
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

#: The first sentence of the refresh prompt. The workspace's history reload
#: recognises a refresh turn by it (docs/iris/WORKSPACE_V2.md, section 2), so
#: it is kept byte-for-byte.
REFRESH_FIRST_SENTENCE = "Call iris_overview and iris_audit for the selected tenant."
#: The second sentence leaves the window to the tool (QA re-walk N1,
#: 2026-09-26). Asked for "the last seven days", Iris worked the dates out from
#: the local date her harness gives her, which from 17:00 to midnight Pacific is
#: a day behind the UTC date that iris_overview and `reads_current_period` both
#: count in. Her 09-19..09-26 was refused as "not the current period", and each
#: page open collected again until the session's tool budget ran out. Called
#: without dates, the tool resolves the current period on its own UTC clock and
#: the adapter recognises the call as current without comparing any clocks, so
#: the host's time zone no longer enters into it.
REFRESH_WINDOW_SENTENCE = (
    "Call iris_overview with no arguments: leave out start_date and end_date, and do not "
    "work out dates yourself, because without them the tool reads the current period on "
    "its own clock."
)
REFRESH_PROMPT = (
    f"{REFRESH_FIRST_SENTENCE} {REFRESH_WINDOW_SENTENCE} "
    "Summarize the measured denominator and coverage. Do not propose changes."
)

#: How long a refresh that read no current-period overview holds off the next
#: one in the same session (QA re-walk N1). The page auto-collects whenever state
#: answers 409, so after a collect that missed, every page open ran another: four
#: opens spent the session's 60-call tool budget, and on a real tenant each is a
#: warehouse read. A repeat inside this window is refused without a turn. A chat
#: turn in between, or a refresh after the window, runs as usual.
REPEAT_COLLECT_COOLDOWN_SECONDS = 600
#: The 409 detail for that refusal, with `{wait}` filled in by
#: `repeat_collect_detail`. Distinct from `BUSY_DETAIL` and from both "no
#: overview" details (it must not start with "The collection"), so a caller can
#: tell "not started" from "ran and missed". It says "collect", as the page's
#: buttons do.
REPEAT_COLLECT_DETAIL = (
    "Iris did not start another collection, because the last one in this session read "
    "no current-period overview. Collect again in {wait}, or ask Iris anything in chat "
    "to lift the hold now. Open native chat to see what Iris did."
)


def repeat_collect_detail(seconds_left: float) -> str:
    """`REPEAT_COLLECT_DETAIL` for a hold with `seconds_left` to run, in whole minutes."""
    minutes = max(1, math.ceil(seconds_left / 60))
    return REPEAT_COLLECT_DETAIL.format(
        wait=f"about {minutes} minute" + ("" if minutes == 1 else "s")
    )


#: Tools whose reports a refresh bounds by its own turn marker. A refresh calls
#: exactly these, so an older report of either is the previous capture and must
#: not be passed off as the fresh one. `iris_investigate` and `iris_propose` are
#: deliberately absent: a refresh never calls them, so bounding them would blank
#: the investigation and the proposal on every refresh. Each carries its own
#: `report_times` entry instead, which says how old it is.
_REFRESH_BOUND = frozenset({"iris_overview", "iris_audit"})

#: Reports a state read carries when it can, and leaves null when it cannot.
#: Their fetch failing must not take the overview and audit down with it: the
#: workspace can still show the capture, and a null already means "absent".
#: A tenant mismatch is not a fetch failure and still refuses the whole read.
_OPTIONAL_REPORTS = frozenset({"iris_investigate", "iris_propose"})

#: Where the v2 workspace (docs/iris/WORKSPACE_V2.md, D1) lives in this package.
UI_ROOT = HERE / "ui"
#: The v2 app files served from `UI_ROOT`. A pattern, not a directory listing,
#: so nothing else placed under `ui/` becomes reachable, and `views/` cannot be
#: escaped with `..` or a nested path.
_V2_APP_FILES = re.compile(r"(?:app\.js|style\.css|views/[A-Za-z0-9_-]+\.js)")
#: The two images still read from the pinned archive under either UI.
_PINNED_IMAGES = frozenset({"assets/iris-portrait.png", "assets/airbrx-logo.png"})


def ui_version() -> str:
    """Which workspace the asset route serves, read on every request.

    `OMNIGENT_IRIS_UI=v2` serves the new UI plus the shared kernel. Anything
    else, including unset, serves the pinned UI with `host.js` injected, as
    before, so production is unchanged until the default is flipped (W5).
    """
    return "v2" if os.environ.get("OMNIGENT_IRIS_UI") == "v2" else "pinned"


def kernel_file(name: str) -> Path | None:
    """The shared workspace kernel file `name`, or None when it is not served.

    The kernel (`omnigent.airbrx.workspace`) owns its allowlist; this only
    refuses anything outside it. Imported here rather than at module load so a
    host without the kernel still serves the pinned UI.
    """
    try:
        # By name: the kernel lands in its own change (W2), and this module
        # must import, type-check and serve the pinned UI without it.
        kernel = importlib.import_module("omnigent.airbrx.workspace.assets")
    except ImportError:
        return None
    if name not in getattr(kernel, "KERNEL_ASSETS", ()):
        return None
    try:
        path = kernel.kernel_asset(name)
    except (KeyError, ValueError, OSError):
        return None
    if path is None:
        return None
    path = Path(path)
    return path if path.is_file() else None


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


#: The 409 detail when a turn is already running in the session (this adapter
#: holds its lock, or the native session is running or waiting). The kernel's
#: `api()` surfaces a 4xx as `status` plus this `detail` string and nothing else,
#: so this exact string is how a caller tells "busy" from the two "no overview"
#: 409s of `read_state`: a reload during the first auto-collect gets it while
#: the orphaned refresh is still running. Kept byte-for-byte; a test pins it.
BUSY_DETAIL = "Iris is busy; cancel or wait for the current turn"


def _user_text(item: dict) -> str | None:
    if item.get("type") != "message" or item.get("role") != "user":
        return None
    return "".join(
        c.get("text", "") for c in item.get("content") or [] if c.get("type") == "input_text"
    )


def missed_collect_at(items: list[dict]) -> float | None:
    """When the session's latest turn, a refresh, read no current-period overview.

    `items` in chronological order. Returns the `created_at` of that refresh's
    message, or None when the latest user turn is not a refresh or it found the
    current period (by `reads_current_period`, the rule `read_state` and the
    Accounts ranking use).
    """
    marker = next((i for i in reversed(items) if _user_text(i) is not None), None)
    if marker is None or not _user_text(marker).startswith(REFRESH_FIRST_SENTENCE):
        return None
    at = marker.get("created_at", 0)
    for tool, _, created_at, arguments, called_at in report_calls(items):
        if (
            tool == "iris_overview"
            and created_at >= at
            and reads_current_period(arguments, called_at)
        ):
            return None
    return at


def create_iris_router(*, auth_provider, agent_store, hosts_online=None):
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
            raise HTTPException(409, BUSY_DETAIL)
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
            # The client going away is NOT a Stop. A page reload aborts this
            # request's fetch, so the server sees a disconnect, and this loop
            # used to answer it with an interrupt: a reload mid-chat cancelled
            # Iris's turn, when the reloaded page means to read the answer back
            # from history (WORKSPACE_V2.md, section 7 gate). Only `api/cancel`
            # (the user's explicit Stop) interrupts on the user's behalf.
            #
            # So a disconnected caller does not end the watch either: it runs to
            # the answer or the deadline, which keeps the tool-boundary refusal
            # and the deadline below enforced for a turn nobody is watching.
            # Neither is a CancelledError grounds for an interrupt: that is this
            # task being torn down (a disconnect, on a stack that cancels
            # handlers for it, or a server shutdown), not a user asking to stop.
            # On such a stack the watch simply ends, so the deadline below is
            # not enforced for that turn; today's uvicorn stack does not cancel
            # handlers on disconnect (review of #115), so it is.
            try:
                while time.monotonic() < expires:
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
            except HTTPException:
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
        mine = [b for b in bindings() if user in b.users]
        # A binding whose execution host is asleep is not a tenant anyone can
        # open: session creation refuses it with a 400 before the workspace
        # loads. Listing it as freely selectable makes the drawer look healthy
        # and moves the failure to the first click. Say so here instead.
        #
        # None, not False, when the server cannot tell: unknown is not offline,
        # and a caller that cannot distinguish them would grey out every tenant
        # the moment this lookup went missing.
        online = None
        if hosts_online is not None:
            online = hosts_online(sorted({b.host_id for b in mine}))
        return {
            "agent_id": agent.id if agent else None,
            "revision": json.loads((HERE / "source.json").read_text())["revision"],
            "bindings": [
                {
                    "tenant_id": b.tenant_id,
                    "name": b.name,
                    "host_id": b.host_id,
                    "workspace": b.workspace,
                    "fixture": b.fixture,
                    "host_online": None if online is None else b.host_id in online,
                }
                for b in mine
            ],
        }

    @router.get("/iris/account")
    async def account(request: Request):
        """The caller's bound tenants, ranked by the value at stake, or quarantined with a reason.

        Deterministic: no model runs here. The tenant set is the caller's
        bindings — the coordinator holds no PAT, so it cannot ask the
        Airbrx manifest, and a tenant with no binding could not be drilled
        into anyway. Captures come from the caller's own Iris sessions
        (`collect_captures`), and only `tenant_id` and `metrics` are read out
        of a report. Ranking is `iris.account.rank`, pinned in the archive,
        so what a figure *means* is versioned and hash-verified with the rest
        of Iris.
        """
        user = require_user(request, auth_provider)
        if user is None:
            raise HTTPException(401, "Iris requires host authentication")
        agent = agent_store.get_by_name("iris")
        if agent is None:
            raise HTTPException(404, "Iris is not registered on this host")
        try:
            mine = [b for b in bindings() if user in b.users]
        except (ValueError, OSError):
            # Operator configuration, not user input; the whole list fails
            # rather than rendering a partial account as complete.
            raise HTTPException(
                500, "The Iris binding configuration on this host is invalid or unreadable"
            ) from None
        source_root()
        from iris.account import rank

        async with session_client(request) as client:
            captures = await collect_captures(client.get, agent_id=agent.id, bindings=mine)
        tenants = [{"tenant_id": b.tenant_id, "name": b.name or None, "note": None} for b in mine]
        now = time.time()
        return {"generated_at": now, "tenants": len(tenants), **rank(tenants, captures, now=now)}

    @router.get("/iris/portrait", include_in_schema=False)
    async def portrait(request: Request):
        """Iris's portrait, read from her pinned package, for the agent drawer.

        The avatar store can hold a picture for her and on at least one host it
        does: an operator uploaded one at 16:36 on 2026-09-15 whose SHA-256 is
        byte-for-byte this same packaged file. That upload still wins — the
        drawer prefers a stored avatar — but it is a hand-made duplicate of a
        file the package already pins, and nothing keeps the two in step. When
        the package's portrait changes, the copy in the store goes quietly
        stale and no one is told.

        So this is the source of truth and the store is the override. It also
        means a host where nobody has uploaded anything still shows her face
        instead of two grey initials. Read from the verified archive rather
        than a copy committed into the web assets, and behind the same
        authentication as every other route here.
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

    async def refuse_a_repeat_collect(session_id, session, client):
        """409 instead of a turn when the last refresh just missed (QA re-walk N1).

        A busy session is left to `turn`, so a reload during the first collect
        still gets `BUSY_DETAIL`.
        """
        lock = locks.get(session_id)
        if (lock is not None and lock.locked()) or session["status"] in {"running", "waiting"}:
            return
        page = await checked(
            await client.get(
                f"/v1/sessions/{session_id}/items", params={"limit": 1000, "order": "desc"}
            )
        )
        at = missed_collect_at(list(reversed(page["data"])))
        left = REPEAT_COLLECT_COOLDOWN_SECONDS - (time.time() - at) if at is not None else 0
        if left > 0:
            raise HTTPException(409, repeat_collect_detail(left))

    async def read_state(request: Request, session_id: str, fresh: bool = False):
        async with session_client(request) as client:
            session, binding = await authorize(request, session_id, client)
            params = {"limit": 1000, "order": "desc"}
            cursor = None
            if fresh:
                await refuse_a_repeat_collect(session_id, session, client)
                fresh_turn = await turn(
                    request,
                    session_id,
                    client,
                    REFRESH_PROMPT,
                )
                # Deliberately NOT `params["after"] = fresh_turn["item_id"]`.
                #
                # `after` combined with `order=desc` returns an empty page —
                # descending, "after" the turn's own user message means items
                # OLDER than it, and the reports we just collected are newer.
                # Measured against a live session:
                #
                #   after only                items=14, report found
                #   order=desc only           items=15, report found
                #   order=desc + after        items= 0, report NOT found
                #
                # So this endpoint ran a real turn — a minute of warehouse
                # work — then filtered its own results out and answered
                # "No session overview yet; use Refresh from host", telling
                # the operator to press the button that had just worked.
                #
                # The ordering cannot simply be dropped: the double reversal
                # below is what makes the NEWEST report per tool win, and
                # removing `order=desc` silently inverts it to the oldest.
                # Bound by the turn's own message instead, on the same clock
                # as the reports.
                cursor = fresh_turn["item_id"]
            page = await checked(
                await client.get(f"/v1/sessions/{session_id}/items", params=params)
            )
            ordered = list(reversed(page["data"]))
            collected_after = 0.0
            if cursor is not None:
                marker = next((i for i in ordered if i.get("id") == cursor), None)
                if marker is None:
                    # Without the marker there is no way to tell this turn's
                    # reports from a previous turn's. Say so rather than
                    # presenting a stale capture as a fresh collection.
                    raise HTTPException(
                        409,
                        "The collection completed but its position in this session could not be "
                        "established; open native chat to review the turn.",
                    )
                collected_after = marker.get("created_at", 0)
            refs = report_calls(ordered)
            reports = {}
            report_times = dict.fromkeys(sorted(TOOLS))
            cache_age = 0
            captured_at = None
            unreadable = set()
            other_windows = False
            for tool, file_id, created_at, arguments, called_at in reversed(refs):
                if tool in reports or tool in unreadable:
                    continue
                # On a fresh collection, only this turn's overview and audit
                # count. A refresh that returns the previous capture is worse
                # than one that admits it collected nothing. The investigation
                # and the proposal are the newest in the session either way:
                # see _REFRESH_BOUND. Checked before the window below, so an
                # older comparison does not turn "produced no overview" into
                # "read no current-period overview".
                if tool in _REFRESH_BOUND and created_at < collected_after:
                    continue
                # `overview` is the tenant's current period. An overview Iris
                # read over another window mid-session (to compare against) is
                # newer than the capture, and was being shown as the capture
                # after a reload. It is skipped, not fetched: the contract has
                # no slot for it, and the comparison itself is what
                # `investigation` carries. The window is judged on the day the
                # call was made, not the day its answer was recorded.
                if tool == "iris_overview" and not reads_current_period(arguments, called_at):
                    other_windows = True
                    continue
                try:
                    report = await checked(
                        await client.get(
                            f"/v1/sessions/{session_id}/resources/files/{file_id}/content"
                        )
                    )
                    if not isinstance(report, dict):
                        raise ValueError("report is not an object")
                except (HTTPException, ValueError, httpx.HTTPError):
                    if tool not in _OPTIONAL_REPORTS:
                        raise
                    # Null, as if absent, and no older report in its place:
                    # the newest is the one the session stands behind.
                    unreadable.add(tool)
                    continue
                if report.get("tenant_id") != binding.tenant_id:
                    raise HTTPException(403, "Report tenant does not match session")
                reports[tool] = report
                report_times[tool] = float(created_at)
                if tool == "iris_overview":
                    captured_at = float(created_at)
                    cache_age = max(0, time.time() - created_at)
            if "iris_overview" not in reports:
                # Two different situations; the wording used to send both to
                # "use Refresh from host", which on the fresh path pointed at
                # the button that had just run.
                if other_windows:
                    raise HTTPException(
                        409,
                        "The collection read no current-period overview; open native chat to "
                        "see what Iris did."
                        if fresh
                        else "No current-period overview yet; use Refresh from host",
                    )
                raise HTTPException(
                    409,
                    "The collection produced no overview; open native chat to see what Iris did."
                    if fresh
                    else "No session overview yet; use Refresh from host",
                )
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
                # The clock `cache_age_seconds` is measured on: when the item
                # carrying the overview report was recorded.
                "captured_at": captured_at,
                "investigation": reports.get("iris_investigate"),
                "proposal": reports.get("iris_propose"),
                "report_times": report_times,
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
                "name": binding.name,
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
                        # This said "Refresh runs Iris's four tools without invoking
                        # the model". That is false, and it was measured false on
                        # 2026-09-22: a hosted session where nothing but Refresh from
                        # host was pressed holds two COMPLETED assistant messages, and
                        # this very endpoint flipped to "a model turn has completed"
                        # straight afterwards. Refresh goes through `turn`, which asks
                        # the model to call the tools.
                        #
                        # It also said an imported report fills this view without the
                        # host running anything. Iris now collects rather than imports
                        # (iris #29) and the v2 workspace has no import path, so the
                        # line says what does happen: the first open collects an
                        # overview, which is a model turn. The workspace shows this
                        # text verbatim.
                        "no model turn has completed in this session yet, so whether the "
                        "execution host can reach the model is unknown. The first time "
                        "this workspace opens, it collects a fresh overview from the host "
                        "automatically, and that runs a model turn. Asking Iris a question "
                        "runs one too. This line goes away once a turn has completed."
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

    def v2_asset(asset: str):
        """The v2 workspace (WORKSPACE_V2.md, section 2, "Assets"). Everything else is 404.

        No `host.js`, no injection, and none of the pinned app's files: v2 is
        the new UI and the kernel, plus the two pinned images. `iris-state.json`
        and `demo-state.json` stay 404 for the reason given in `asset` below.
        """
        if not asset or asset == "index.html":
            if not (UI_ROOT / "index.html").is_file():
                raise HTTPException(404)
            return FileResponse(
                UI_ROOT / "index.html",
                media_type="text/html",
                headers={"Cache-Control": "no-store", "X-Frame-Options": "SAMEORIGIN"},
            )
        if _V2_APP_FILES.fullmatch(asset):
            path = UI_ROOT / asset
            if path.is_file():
                return FileResponse(path, headers={"Cache-Control": "no-store"})
            raise HTTPException(404)
        if asset.startswith("kernel/"):
            path = kernel_file(asset.removeprefix("kernel/"))
            if path is None:
                raise HTTPException(404)
            return FileResponse(path, headers={"Cache-Control": "no-store"})
        if asset in _PINNED_IMAGES:
            return FileResponse(
                source_root() / "ui" / asset, headers={"Cache-Control": "private, max-age=3600"}
            )
        raise HTTPException(404)

    @router.get("/iris/sessions/{session_id}/ui/{asset:path}", include_in_schema=False)
    async def asset(request: Request, session_id: str, asset: str):
        async with session_client(request) as client:
            await authorize(request, session_id, client)
        if ui_version() == "v2":
            return v2_asset(asset)
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
        # The app, not captures. `iris-state.json` is already withheld because
        # it is someone's captured tenant evidence; `demo-state.json` is
        # withheld for a subtler reason that is the same reason.
        #
        # app.js boots through a fallback chain: `api/state`, then
        # `iris-state.json`, then `demo-state.json`. On a hosted mount the
        # first 409s until a turn has produced an overview and the second is
        # 404. Serving the third meant a fresh tenant-bound session opened
        # showing a complete, entirely synthetic cache report - hit rate,
        # findings, a tenant line - behind nothing but a small "Synthetic
        # demo" chip, and `ask()` then answered questions from it locally
        # without ever calling the host. A session shows its own evidence or
        # it shows nothing and says so.
        allowed = {
            "app.js",
            "theme.js",
            "style.css",
            "assets/iris-portrait.png",
            "assets/airbrx-logo.png",
        }
        if asset not in allowed:
            raise HTTPException(404)
        return FileResponse(
            source_root() / "ui" / asset, headers={"Cache-Control": "private, max-age=3600"}
        )

    return router
