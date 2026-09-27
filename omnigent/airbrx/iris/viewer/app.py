"""The standalone Iris viewer: saved runs on disk, served to the one Iris app.

STANDALONE_VIEWER.md, section 4.2. The app under `ui/` is byte-identical to
the one framed in Omnigent; this module only answers the seams (section 2):
`api/host` says the agent is not connected and the data is saved runs, and
`api/state`, `api/items` and `api/files` read one run's verified files.
Nothing here can ask Iris anything or collect anything: `api/chat`,
`api/refresh` and `api/cancel` exist only to refuse (rule V1).

Imports are limited to the stdlib, fastapi/starlette and leaf modules of
`omnigent.airbrx.iris`, never `omnigent.server` or the adapter routes, so a
slim deploy stays possible (section 1; a test pins it).
"""

from __future__ import annotations

import html
import json
import mimetypes
import os
import time
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Query, Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from omnigent.airbrx.iris import runs
from omnigent.airbrx.iris.package import HERE, source_root
from omnigent.airbrx.iris.runs import RunUnreadable, SavedRun, SavedTenant
from omnigent.airbrx.iris.ui_assets import NO_STORE, ui_response
from omnigent.airbrx.iris.viewer.identity import LOOPBACK, Identity, IdentityProvider

APP_NAME = "iris-viewer"
#: The checkout this module runs from; dev.sh compares it to stay idempotent.
CHECKOUT = Path(__file__).resolve().parents[4]
NOT_CONNECTED = "Iris is not connected to this viewer; nothing was run."
TENANT_MISMATCH = "Report tenant does not match session"


class DuplicateTenant(Exception):
    """One tenant id in two roots: which run is meant cannot be decided, so nothing starts."""


def pinned_rank():
    """`iris.account.rank` from the pinned, hash-verified archive, as `/v1/iris/account` uses."""
    source_root()
    from iris.account import rank

    return rank


def pinned_revision() -> str:
    return json.loads((HERE / "source.json").read_text())["revision"]


def no_store(body: Any, status_code: int = 200) -> JSONResponse:
    return JSONResponse(body, status_code=status_code, headers=NO_STORE)


def utc_date(epoch: object) -> str:
    try:
        return datetime.fromtimestamp(float(epoch), timezone.utc).date().isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return "an unknown date"


class Library:
    """The saved runs under several roots. Read from disk on every call, never cached."""

    def __init__(self, roots: Iterable[Path | str]):
        self.roots = [Path(os.path.expanduser(str(r))) for r in roots]

    def tenants(self) -> list[SavedTenant]:
        """Every tenant in every root, sorted by name. Raises DuplicateTenant."""
        found: dict[str, SavedTenant] = {}
        for root in self.roots:
            for tenant in runs.scan(root):
                if tenant.tenant_id in found:
                    raise DuplicateTenant(
                        self._duplicate(tenant.tenant_id, found[tenant.tenant_id].root, root)
                    )
                found[tenant.tenant_id] = tenant
        return sorted(found.values(), key=lambda t: (t.name.casefold(), t.tenant_id))

    def _duplicate(self, tenant_id: str, first: Path, second: Path) -> str:
        return f"Tenant {tenant_id} is in two run roots, {first} and {second}; keep it in one."

    def tenant_root(self, tenant_id: str) -> tuple[Path, dict] | None:
        """The root holding `tenant_id` and its tenant.json, checked as `runs.scan` checks it."""
        if not runs.valid_tenant(tenant_id):
            return None
        hits = []
        for root in self.roots:
            tenant_dir = root / tenant_id
            if tenant_dir.is_symlink() or not tenant_dir.is_dir():
                continue
            try:
                meta = json.loads((tenant_dir / runs.TENANT).read_bytes())
            except (OSError, ValueError):
                continue
            if isinstance(meta, dict) and meta.get("tenant_id") == tenant_id:
                hits.append((root, meta))
        if len(hits) > 1:
            raise DuplicateTenant(self._duplicate(tenant_id, hits[0][0], hits[1][0]))
        return hits[0] if hits else None

    def weeks(self, root: Path, tenant_id: str) -> list[SavedRun]:
        """The tenant's runs, newest week first, each verified or marked unreadable."""
        tenant_dir = root / tenant_id
        return [
            runs.scan_run(root, tenant_id, week_dir.name)
            for week_dir in sorted(tenant_dir.iterdir(), reverse=True)
            if runs.valid_week(week_dir.name) and not week_dir.is_symlink() and week_dir.is_dir()
        ]


def create_app(
    roots: Iterable[Path | str],
    *,
    identity: IdentityProvider | None,
    allowed_hosts: Iterable[str] = LOOPBACK,
) -> FastAPI:
    """The viewer over `roots`. Raises DuplicateTenant when a tenant is in two of them."""
    library = Library(roots)
    library.tenants()
    allowed_hosts = frozenset(allowed_hosts)

    app = FastAPI(
        title="Iris viewer",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.middleware("http")
    async def loopback_host_only(request: Request, call_next):
        # A page on another name that resolves to 127.0.0.1 (DNS rebinding)
        # must not reach tenant evidence through the dev sign-in.
        host = urlsplit(f"//{request.headers.get('host', '')}").hostname
        if host not in allowed_hosts:
            return Response("Host not allowed", status_code=400, media_type="text/plain")
        return await call_next(request)

    def who(request: Request) -> Identity:
        found = identity.resolve(request) if identity is not None else None
        if found is None:
            raise HTTPException(401, "Sign in to the Iris viewer")
        return found

    def visible(found: Identity, tenant_id: str) -> bool:
        allowed = identity.tenants(found) if identity is not None else None
        return allowed is None or tenant_id in allowed

    def visible_tenants(found: Identity) -> list[SavedTenant]:
        return [t for t in library.tenants() if visible(found, t.tenant_id)]

    def tenant_or_404(request: Request, tenant: str) -> tuple[Identity, Path, dict]:
        found = who(request)
        located = library.tenant_root(tenant) if runs.valid_tenant(tenant) else None
        if located is None or not visible(found, tenant):
            raise HTTPException(404)
        return found, *located

    def run_or_404(request: Request, tenant: str, week: str) -> tuple[Identity, dict, SavedRun]:
        found, root, meta = tenant_or_404(request, tenant)
        if not runs.valid_week(week):
            raise HTTPException(404)
        run = runs.scan_run(root, tenant, week)
        if not run.readable:
            raise HTTPException(404)
        return found, meta, run

    def read(action):
        try:
            return action()
        except (RunUnreadable, OSError, ValueError):
            # Changed on disk since the check, or never valid: not served (rule V3).
            raise HTTPException(404) from None

    def week_page(tenant: str, week: str) -> str:
        return f"/t/{tenant}/{week}/ui/"

    # --- Pages ------------------------------------------------------------------------

    @app.get("/", include_in_schema=False)
    @app.get("/iris", include_in_schema=False)
    @app.get("/iris/", include_in_schema=False)
    async def landing(request: Request):
        found = who(request)
        for tenant in visible_tenants(found):
            newest = tenant.newest_readable()
            if newest is not None:
                return RedirectResponse(week_page(tenant.tenant_id, newest.week), 302)
        where = ", ".join(str(r) for r in library.roots) or "no roots"
        return HTMLResponse(
            "<!doctype html><meta charset=utf-8><title>Iris viewer</title>"
            f"<p>No saved runs in {html.escape(where)}.</p>"
            "<p>Export one with <code>python -m omnigent.airbrx.iris.export</code>.</p>",
            headers=NO_STORE,
        )

    @app.get("/t/{tenant}/", include_in_schema=False)
    async def tenant_home(request: Request, tenant: str):
        _, root, _ = tenant_or_404(request, tenant)
        newest = next((r for r in library.weeks(root, tenant) if r.readable), None)
        if newest is None:
            raise HTTPException(404)
        return RedirectResponse(week_page(tenant, newest.week), 302)

    @app.get("/t/{tenant}", include_in_schema=False)
    @app.get("/t/{tenant}/{week}", include_in_schema=False)
    @app.get("/t/{tenant}/{week}/", include_in_schema=False)
    @app.get("/t/{tenant}/{week}/ui", include_in_schema=False)
    async def to_week_page(request: Request, tenant: str, week: str | None = None):
        if week is None:
            tenant_or_404(request, tenant)
            return RedirectResponse(f"/t/{tenant}/", 302)
        run_or_404(request, tenant, week)
        return RedirectResponse(week_page(tenant, week), 302)

    # --- The seams (section 2.2) ------------------------------------------------------

    base = "/t/{tenant}/{week}/ui/api"

    @app.get(f"{base}/host")
    async def host(request: Request, tenant: str, week: str):
        found, _, run = run_or_404(request, tenant, week)
        return no_store(
            {
                "schema": 1,
                "host_label": "Iris viewer",
                "identity": {
                    "kind": found.kind,
                    "email": found.email,
                    "sign_in": getattr(identity, "sign_in", None),
                },
                "agent": {"connected": False, "why": "viewer"},
                "data": {
                    "source": "runs",
                    "scope_id": f"{tenant}/{week}",
                    "tenant_id": tenant,
                    "week": week,
                    "weeks": [r.week_entry() for r in library.weeks(run.root, tenant)],
                },
                "links": {
                    "native_chat": None,
                    "items": "api/items",
                    "session": "api/session",
                    "files": "api/files",
                    "catalog": "/api/tenants",
                    "account": "/api/account",
                    "tenant_home": "/t/{tenant_id}/",
                    "week_page": "/t/{tenant_id}/{week}/ui/",
                },
                "open_tenant": "navigate",
            }
        )

    @app.get(f"{base}/state")
    async def state(request: Request, tenant: str, week: str):
        _, _, run = run_or_404(request, tenant, week)
        body = read(run.state)
        if (body.get("overview") or {}).get("tenant_id") != tenant:
            raise HTTPException(403, TENANT_MISMATCH)
        return no_store(runs.served_state(body, time.time()))

    @app.get(f"{base}/readiness")
    async def readiness(request: Request, tenant: str, week: str):
        _, meta, run = run_or_404(request, tenant, week)
        source = run.manifest.get("source") or {}
        if source.get("kind") == "synthetic":
            origin = "synthetic session records"
        else:
            origin = f"Omnigent at {source.get('server') or 'an unrecorded server'}"
        exported = utc_date(run.manifest.get("exported_at"))
        return no_store(
            {
                "tenant_id": meta["tenant_id"],
                "name": str(meta.get("name") or tenant),
                "fixture": meta.get("fixture") is True,
                "session_status": "saved",
                "turn_completed_here": False,
                "last_task_failed": False,
                "verified": [
                    f"this run was exported from {origin} on {exported}, "
                    "and its files match their checksums"
                ],
                "unverified": [
                    "Iris is not connected to this viewer, so nothing can be asked or "
                    "collected here"
                ],
            }
        )

    @app.get(f"{base}/session")
    async def session(request: Request, tenant: str, week: str):
        run_or_404(request, tenant, week)
        return no_store({"status": "idle"})

    @app.get(f"{base}/items")
    async def items(
        request: Request,
        tenant: str,
        week: str,
        limit: int = Query(default=100, ge=1, le=1000),
        order: str = Query(default="asc", pattern="^(asc|desc)$"),
    ):
        _, _, run = run_or_404(request, tenant, week)
        saved = list(read(run.items).get("data") or [])
        ordered = saved if order == "asc" else saved[::-1]
        page = ordered[:limit]
        return no_store(
            {
                "data": page,
                "first_id": page[0].get("id") if page else None,
                "last_id": page[-1].get("id") if page else None,
                "has_more": len(ordered) > limit,
            }
        )

    @app.get(f"{base}/files")
    async def files(
        request: Request,
        tenant: str,
        week: str,
        limit: int = Query(default=100, ge=1, le=1000),
    ):
        _, _, run = run_or_404(request, tenant, week)
        rows = list(read(run.file_index).get("data") or [])
        return no_store({"data": rows[:limit], "has_more": len(rows) > limit})

    @app.get(f"{base}/files/{{file_id}}/content")
    async def file_content(request: Request, tenant: str, week: str, file_id: str):
        _, _, run = run_or_404(request, tenant, week)
        if not runs.valid_file_id(file_id):
            raise HTTPException(404)
        row = next(
            (r for r in read(run.file_index).get("data") or [] if r.get("id") == file_id), None
        )
        if row is None:
            raise HTTPException(404)
        data = read(lambda: run.file(file_id))
        filename = row.get("filename") if row.get("filename") in runs.DOWNLOADS else "download"
        media = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        return Response(
            data,
            media_type=media,
            headers={**NO_STORE, "Content-Disposition": f'attachment; filename="{filename}"'},
        )

    async def refuse(request: Request, tenant: str, week: str):
        run_or_404(request, tenant, week)
        return no_store({"detail": NOT_CONNECTED}, status_code=409)

    for action in ("chat", "refresh", "cancel"):
        app.add_api_route(f"{base}/{action}", refuse, methods=["POST"], name=f"refuse_{action}")

    @app.get("/t/{tenant}/{week}/ui/{asset:path}", include_in_schema=False)
    async def asset(request: Request, tenant: str, week: str, asset: str):
        run_or_404(request, tenant, week)
        response = ui_response(asset)
        if response is None:
            raise HTTPException(404)
        return response

    # --- Accounts (section 2.2) -------------------------------------------------------

    @app.get("/api/tenants")
    async def tenants(request: Request):
        found = who(request)
        return no_store(
            {
                "agent_id": None,
                "revision": pinned_revision(),
                "bindings": [
                    {
                        "tenant_id": t.tenant_id,
                        "name": t.name,
                        "host_id": None,
                        "workspace": None,
                        "fixture": t.fixture,
                        "host_online": None,
                    }
                    for t in visible_tenants(found)
                ],
            }
        )

    @app.get("/api/account")
    async def account(request: Request):
        """Each tenant's newest readable run, ranked by the pinned `iris.account.rank`.

        Of a run's report only `tenant_id` and `metrics` are read (spec
        invariant 1), as `collect_captures` does for the framed host.
        """
        found = who(request)
        mine = visible_tenants(found)
        captures: dict[str, dict | None] = {}
        for tenant in mine:
            newest = tenant.newest_readable()
            if newest is None:
                captures[tenant.tenant_id] = None
                continue
            try:
                saved = newest.state()
                overview = saved.get("overview") or {}
                captures[tenant.tenant_id] = {
                    "tenant_id": overview.get("tenant_id"),
                    "metrics": overview.get("metrics"),
                    "captured_at": saved.get("captured_at"),
                }
            except (RunUnreadable, OSError, ValueError, AttributeError):
                captures[tenant.tenant_id] = {"error": "the saved run could not be read"}
        rows = [{"tenant_id": t.tenant_id, "name": t.name, "note": None} for t in mine]
        now = time.time()
        return no_store(
            {"generated_at": now, "tenants": len(rows), **pinned_rank()(rows, captures, now=now)}
        )

    @app.get("/healthz")
    async def healthz():
        found = library.tenants()
        every = [r for t in found for r in t.runs]
        return no_store(
            {
                "ok": True,
                "app": APP_NAME,
                "checkout": str(CHECKOUT),
                "roots": [str(r) for r in library.roots],
                "tenants": len(found),
                "runs": len(every),
                "unreadable": sum(not r.readable for r in every),
            }
        )

    return app


def app_from_env() -> FastAPI:
    """The app `__main__` configured, rebuilt in a uvicorn `--reload` worker."""
    from omnigent.airbrx.iris.viewer.identity import DevIdentity

    roots = [r for r in os.environ.get("IRIS_VIEWER__ROOTS", "").split(os.pathsep) if r]
    return create_app(roots, identity=DevIdentity(os.environ["IRIS_VIEWER__EMAIL"]))
