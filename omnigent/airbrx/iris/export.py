"""Export saved Iris runs from the local Omnigent stack (docs/iris/STANDALONE_VIEWER.md, 3.5).

::

    python -m omnigent.airbrx.iris.export \\
        [--server http://127.0.0.1:6768] [--out ~/.iris-viewer/runs] \\
        [--tenant ID ...] [--since 2026-08-01] [--session ID ...] [--dry-run] [--force]

The source is the Omnigent session store, read through its native API: the
caller's Iris bindings from `GET /v1/iris`, the Iris agent's sessions matched
on `(host_id, workspace)` as `account.collect_captures` matches them, and each
session's items in chronological order. For every current-period
`iris_overview` capture, the run is `workspace.build_state` over the items up
to the end of the turn that recorded it, so a saved run and a framed session
cannot disagree about what a capture is. The newest capture per (tenant, ISO
week) across all sessions wins.

Decision D-2 (a), Abram, 27 Sep: real runs come from the local stack only. The
server must be a loopback address, and no credential is ever read or sent.
The output is refused inside any git worktree, because omnigent is a public
repository and real tenant evidence must never land in one.

`runs_from_items` is the pure path: no HTTP and no clock. The committed
synthetic fixture (`scripts/iris/make_fixture_runs.py`) is built through it.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from omnigent.airbrx.iris.records import bare_tool_name, reads_current_period, report_calls
from omnigent.airbrx.iris.runs import (
    DOWNLOADS,
    RefusedOutput,
    Run,
    SavedFile,
    git_worktree,
    served_state,
    valid_file_id,
    valid_tenant,
    write_run,
)
from omnigent.airbrx.iris.runtime import TOOLS
from omnigent.airbrx.iris.workspace import (
    ReportUnavailable,
    StateUnavailable,
    TenantMismatch,
    build_state,
    report_ids,
)

DEFAULT_SERVER = "http://127.0.0.1:6768"
DEFAULT_OUT = "~/.iris-viewer/runs"
_PAGE = 1000
_TEXT_PARTS = {"user": "input_text", "assistant": "output_text"}


class ExportError(Exception):
    """One tenant cannot be exported; nothing is written for it."""


# --- The pure path -------------------------------------------------------------------


def _downloads(item: dict) -> list[dict]:
    try:
        result = json.loads(item.get("output") or "")
    except (TypeError, ValueError):
        return []
    if not isinstance(result, dict) or result.get("error") or result.get("error_code"):
        return []
    return [d for d in result.get("downloads") or [] if isinstance(d, dict)]


def _iris_outputs(items: list[dict]) -> Iterable[tuple[int, dict]]:
    """(position, output item) for every function_call_output of an Iris tool."""
    names = {
        i.get("call_id"): bare_tool_name(i.get("name"))
        for i in items
        if i.get("type") == "function_call"
    }
    for position, item in enumerate(items):
        if item.get("type") == "function_call_output" and names.get(item.get("call_id")) in TOOLS:
            yield position, item


#: The state key an optional report lands in; null when it could not be read.
_OPTIONAL_KEYS = {"iris_investigate": "investigation", "iris_propose": "proposal"}


def _standing_reports(window: list[dict], state: dict) -> set[str]:
    """The report.json ids the state stands behind: the newest per tool, as `build_state` reads.

    A run's Downloads are those reports' files (report.json, report.md,
    proposal.json), not every file the session ever wrote: an older capture's
    report is not this run's.
    """
    used = set(report_ids(window))
    return {
        file_id
        for tool, file_id, *_ in report_calls(window)
        if file_id in used and not (tool in _OPTIONAL_KEYS and state[_OPTIONAL_KEYS[tool]] is None)
    }


def saved_messages(items: list[dict]) -> list[dict]:
    """The chat as the viewer shows it: user and assistant text only (rule 15).

    Tool calls, tool outputs and reasoning are dropped; evidence lives in
    state.json and nowhere else. Text parts are kept byte-for-byte, so the
    refresh sentence still reads as a refresh in the history.
    """
    kept = []
    for item in items:
        role = item.get("role")
        if item.get("type") != "message" or role not in _TEXT_PARTS:
            continue
        parts = [
            {"type": part["type"], "text": part.get("text", "")}
            for part in item.get("content") or []
            if isinstance(part, dict) and part.get("type") == _TEXT_PARTS[role]
        ]
        if not parts:
            continue
        kept.append(
            {
                "id": item.get("id"),
                "type": "message",
                "role": role,
                "status": item.get("status", "completed"),
                "content": parts,
                "created_at": item.get("created_at"),
            }
        )
    return kept


def capture_windows(items: list[dict]) -> list[list[dict]]:
    """One window per current-period `iris_overview` capture, in chronological order.

    A window is the items up to, but not including, the first user message
    after the capture's item: the end of the turn that recorded it, so the
    audit from the same turn is included.
    """
    positions: dict[str, int] = {}
    for position, item in _iris_outputs(items):
        for download in _downloads(item):
            file_id = download.get("file_id")
            if isinstance(file_id, str):
                positions.setdefault(file_id, position)
    ends = set()
    for tool, file_id, _created_at, arguments, called_at in report_calls(items):
        if tool != "iris_overview" or not reads_current_period(arguments, called_at):
            continue
        start = positions.get(file_id)
        if start is None:
            continue
        end = next(
            (
                i
                for i in range(start + 1, len(items))
                if items[i].get("type") == "message" and items[i].get("role") == "user"
            ),
            len(items),
        )
        ends.add(end)
    return [items[:end] for end in sorted(ends)]


def runs_from_items(
    items: list[dict],
    content: Callable[[str], bytes],
    *,
    tenant_id: str,
    source: dict,
) -> list[Run]:
    """The runs one session's chronological items hold: the newest capture per ISO week.

    `content(file_id)` returns a stored file's bytes, or raises
    `ReportUnavailable`. `source` is the manifest's `source` without
    `through_item_id`, which is filled per run. Raises `TenantMismatch` when a
    report names another tenant, and `ReportUnavailable` when a report or a
    download the run needs cannot be read: a partial run is never produced.
    """
    cache: dict[str, bytes] = {}

    def read(file_id: str) -> bytes:
        if file_id not in cache:
            cache[file_id] = content(file_id)
        return cache[file_id]

    def report(file_id: str) -> Any:
        try:
            return json.loads(read(file_id))
        except ValueError as exc:
            raise ReportUnavailable("report is not JSON") from exc

    newest: dict[str, Run] = {}
    for window in capture_windows(items):
        try:
            state = build_state(window, report, tenant_id=tenant_id, now=0.0)
        except StateUnavailable:
            # Not reachable for a window that holds a current-period capture,
            # but a window the builder will not stand behind is not a run.
            continue
        # Saved as of the capture: 0 seconds old, not stale. The viewer
        # recomputes both at request time (section 3.3), so the file stays a
        # function of the records alone and a re-export is byte-equal.
        state = served_state(state, state["captured_at"])
        files: dict[str, SavedFile] = {}
        standing = _standing_reports(window, state)
        for _position, item in _iris_outputs(window):
            downloads = _downloads(item)
            if not any(d.get("file_id") in standing for d in downloads):
                continue
            for download in downloads:
                file_id, filename = download.get("file_id"), download.get("filename")
                if filename not in DOWNLOADS or file_id in files:
                    continue
                if not isinstance(file_id, str) or not valid_file_id(file_id):
                    raise ReportUnavailable(f"a download has an unusable file id: {file_id!r}")
                files[file_id] = SavedFile(file_id, filename, read(file_id))
        run = Run(
            tenant_id=tenant_id,
            state=state,
            items=saved_messages(window),
            # Newest first, as the native files list answers.
            files=list(reversed(files.values())),
            source={**source, "through_item_id": window[-1].get("id") if window else None},
        )
        held = newest.get(run.week)
        if held is None or run.captured_at >= held.captured_at:
            newest[run.week] = run
    return [newest[week] for week in sorted(newest)]


# --- The native API ---------------------------------------------------------------


def loopback_server(server: str) -> str:
    """The server URL, refused unless it is http(s) on a loopback address (D-2 a)."""
    parsed = urllib.parse.urlsplit(server)
    host = parsed.hostname or ""
    if parsed.scheme not in ("http", "https") or not host:
        raise SystemExit(f"export: --server must be an http URL, got {server!r}")
    try:
        loopback = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = False
    if not loopback or parsed.username or parsed.password:
        raise SystemExit(
            f"export: refusing {server!r}. Runs are exported from the local Omnigent stack "
            "only (127.0.0.1, ::1 or localhost); no remote server and no credential."
        )
    return server.rstrip("/")


class Omnigent:
    """The few native routes the exporter reads. No credential is ever sent."""

    def __init__(self, server: str, timeout: float = 30.0) -> None:
        self.server = loopback_server(server)
        self.timeout = timeout

    def get(self, path: str, params: dict | None = None) -> bytes:
        url = self.server + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            raise ExportError(f"{path} answered HTTP {exc.code}") from None
        except (urllib.error.URLError, OSError) as exc:
            raise ExportError(f"{path} could not be reached ({exc.__class__.__name__})") from None

    def json(self, path: str, params: dict | None = None) -> Any:
        try:
            return json.loads(self.get(path, params))
        except ValueError:
            raise ExportError(f"{path} did not answer JSON") from None

    def pages(self, path: str, params: dict) -> Iterable[dict]:
        after = None
        while True:
            page = self.json(path, {**params, **({"after": after} if after else {})})
            yield from page.get("data", [])
            after = page.get("last_id")
            if not page.get("has_more") or not after:
                return

    def version(self) -> str | None:
        try:
            commit = self.json("/api/version").get("commit")
        except (ExportError, AttributeError):
            return None
        return commit[:8] if isinstance(commit, str) else None


def export_runs(
    api: Omnigent,
    *,
    tenants: set[str] | None = None,
    sessions: set[str] | None = None,
    since: float = 0.0,
) -> tuple[dict[str, dict], dict[str, list[Run]], dict[str, str]]:
    """Read every run the caller's Iris sessions hold.

    Returns (bindings by tenant, runs by tenant newest capture per week,
    failures by tenant). A tenant with any unreadable session is failed closed
    and has no runs, as `collect_captures` does: the unreadable one might be
    newer.
    """
    catalog = api.json("/v1/iris")
    agent_id = catalog.get("agent_id")
    if not agent_id:
        raise ExportError("the server has no Iris agent registered")
    bindings = {
        b["tenant_id"]: b
        for b in catalog.get("bindings", [])
        if tenants is None or b.get("tenant_id") in tenants
    }
    for tenant_id in bindings:
        if not valid_tenant(tenant_id):
            raise ExportError(f"tenant id {tenant_id!r} does not fit the run layout")
    by_workspace = {(b["host_id"], b["workspace"]): t for t, b in bindings.items()}
    source = {
        "kind": "omnigent-session",
        "server": api.server,
        "omnigent_version": api.version(),
        "iris_revision": catalog.get("revision"),
    }
    newest: dict[str, dict[str, Run]] = {t: {} for t in bindings}
    failed: dict[str, str] = {}
    listing = api.pages(
        "/v1/sessions",
        {"agent_id": agent_id, "limit": _PAGE, "kind": "any", "include_archived": "true"},
    )
    for session in listing:
        tenant_id = by_workspace.get((session.get("host_id"), session.get("workspace")))
        if tenant_id is None or tenant_id in failed:
            continue
        if sessions is not None and session.get("id") not in sessions:
            continue
        session_id = session["id"]
        try:
            items = list(
                api.pages(f"/v1/sessions/{session_id}/items", {"limit": _PAGE, "order": "asc"})
            )

            def content(file_id: str, session_id: str = session_id) -> bytes:
                try:
                    return api.get(f"/v1/sessions/{session_id}/resources/files/{file_id}/content")
                except ExportError as exc:
                    raise ReportUnavailable(str(exc)) from None

            found = runs_from_items(
                items, content, tenant_id=tenant_id, source={**source, "session_id": session_id}
            )
        except ExportError as exc:
            failed[tenant_id] = f"session {session_id}: {exc}"
            continue
        except ReportUnavailable as exc:
            failed[tenant_id] = f"session {session_id}: a report could not be read ({exc})"
            continue
        except TenantMismatch as exc:
            failed[tenant_id] = f"session {session_id}: {exc.detail}"
            continue
        for run in found:
            if run.captured_at < since:
                continue
            held = newest[tenant_id].get(run.week)
            if held is None or run.captured_at > held.captured_at:
                newest[tenant_id][run.week] = run
    runs = {t: [weeks[w] for w in sorted(weeks)] for t, weeks in newest.items() if t not in failed}
    return bindings, runs, failed


# --- The command -----------------------------------------------------------------------


def _since(value: str) -> float:
    try:
        day = date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError("--since takes a date, YYYY-MM-DD") from None
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp()


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m omnigent.airbrx.iris.export",
        description=(
            "Export saved Iris runs from the local Omnigent stack for the standalone viewer."
        ),
    )
    p.add_argument("--server", default=DEFAULT_SERVER, help="a loopback Omnigent server")
    p.add_argument("--out", default=DEFAULT_OUT, help="runs root; refused inside a git worktree")
    p.add_argument("--tenant", action="append", help="only this tenant (repeatable)")
    p.add_argument("--session", action="append", help="only this session (repeatable)")
    p.add_argument("--since", type=_since, default=0.0, help="only captures on or after this day")
    p.add_argument("--dry-run", action="store_true", help="list what would be written")
    p.add_argument("--force", action="store_true", help="replace runs even when not newer")
    return p


def main(argv: list[str] | None = None, *, now: Callable[[], float] = time.time) -> int:
    args = parser().parse_args(argv)
    out = Path(args.out).expanduser()
    worktree = git_worktree(out)
    if worktree is not None:
        print(
            f"export: refusing --out {out}: it is inside the git worktree {worktree}. "
            "Real tenant runs are never written into a repository.",
            file=sys.stderr,
        )
        return 2
    api = Omnigent(args.server)
    try:
        bindings, runs, failed = export_runs(
            api,
            tenants=set(args.tenant) if args.tenant else None,
            sessions=set(args.session) if args.session else None,
            since=args.since,
        )
    except ExportError as exc:
        print(f"export: {exc}", file=sys.stderr)
        return 1
    exported_at = now()
    for tenant_id in sorted(runs):
        binding = bindings[tenant_id]
        for run in runs[tenant_id]:
            row = (
                f"{tenant_id} {run.week} {run.captured_at:.3f} "
                f"{run.source.get('session_id')} "
                f"{str(run.period.get('period_complete')).lower()}"
            )
            if args.dry_run:
                print(row)
                continue
            try:
                action = write_run(
                    out,
                    run,
                    name=str(binding.get("name") or tenant_id),
                    fixture=binding.get("fixture") is True,
                    exported_at=exported_at,
                    force=args.force,
                )
            except RefusedOutput as exc:
                print(f"export: {exc}", file=sys.stderr)
                return 2
            print(f"{action} {row}")
    for tenant_id in sorted(failed):
        print(f"export: {tenant_id} not exported: {failed[tenant_id]}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
