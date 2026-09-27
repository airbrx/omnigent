"""The run exporter against a fake Omnigent store on loopback (STANDALONE_VIEWER.md, 3.5).

The fake store is a real HTTP server on 127.0.0.1 serving the few native
routes the exporter reads, over the synthetic sessions that build the
committed fixture. Nothing here touches the local stack on 6768 or
``~/iris-local``.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import threading
import urllib.parse
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from omnigent.airbrx.iris import export, runs

REPO = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = REPO / "omnigent/airbrx/iris/fixtures/runs"
AGENT = "agent-iris"
HOST = "0123456789abcdef0123456789abcdef"
REVISION = "4f05f9b214432e8b3fdfa0bf0216c2e627fedacd"


def load_generator() -> Any:
    spec = importlib.util.spec_from_file_location(
        "make_fixture_runs", REPO / "scripts/iris/make_fixture_runs.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GENERATOR = load_generator()


class Store:
    """What the fake server answers: bindings, sessions, items and files."""

    def __init__(self) -> None:
        self.bindings: list[dict[str, Any]] = []
        self.sessions: list[dict[str, Any]] = []
        self.items: dict[str, list[dict[str, Any]]] = {}
        self.files: dict[str, dict[str, bytes]] = {}
        self.broken_items: set[str] = set()
        self.broken_files: set[str] = set()
        self.requests: list[tuple[str, dict[str, str]]] = []

    def bind(self, tenant: str, name: str, *, fixture: bool = True) -> None:
        self.bindings.append(
            {
                "tenant_id": tenant,
                "name": name,
                "host_id": HOST,
                "workspace": f"/tmp/iris/{tenant}",
                "fixture": fixture,
                "host_online": None,
            }
        )

    def add(self, tenant: str, session: Any, *, workspace: str | None = None) -> None:
        self.sessions.append(
            {
                "id": session.id,
                "agent_id": AGENT,
                "host_id": HOST,
                "workspace": workspace or f"/tmp/iris/{tenant}",
            }
        )
        self.items[session.id] = session.items
        self.files[session.id] = session.files

    @classmethod
    def synthetic(cls) -> Store:
        store = cls()
        for tenant, name in GENERATOR.TENANTS.items():
            store.bind(tenant, name)
        for tenant, recorded in GENERATOR.sessions().items():
            for session in recorded:
                store.add(tenant, session)
        return store


def page(rows: list[dict], query: dict[str, str], *, desc: bool) -> dict[str, Any]:
    rows = list(reversed(rows)) if desc else list(rows)
    after = query.get("after")
    if after is not None:
        ids = [r["id"] for r in rows]
        rows = rows[ids.index(after) + 1 :]
    limit = int(query.get("limit", 20))
    data = rows[:limit]
    return {
        "object": "list",
        "data": data,
        "first_id": data[0]["id"] if data else None,
        "last_id": data[-1]["id"] if data else None,
        "has_more": len(rows) > limit,
    }


def handler_for(store: Store) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            pass

        def answer(self, status: int, body: bytes, kind: str = "application/json") -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            url = urllib.parse.urlsplit(self.path)
            query = dict(urllib.parse.parse_qsl(url.query))
            store.requests.append((url.path, dict(self.headers)))
            parts = url.path.strip("/").split("/")
            if url.path == "/api/version":
                return self.answer(200, b'{"version": "0.14.0", "commit": "a2cbdeef2a9bcdc6"}')
            if url.path == "/v1/iris":
                body = {"agent_id": AGENT, "revision": REVISION, "bindings": store.bindings}
                return self.answer(200, json.dumps(body).encode())
            if url.path == "/v1/sessions":
                assert query["agent_id"] == AGENT
                assert query["kind"] == "any" and query["include_archived"] == "true"
                body = page(store.sessions, query, desc=True)
                return self.answer(200, json.dumps(body).encode())
            if parts[:2] == ["v1", "sessions"] and parts[3:] == ["items"]:
                if parts[2] in store.broken_items:
                    return self.answer(500, b'{"detail": "boom"}')
                body = page(store.items[parts[2]], query, desc=query.get("order") == "desc")
                return self.answer(200, json.dumps(body).encode())
            if parts[:2] == ["v1", "sessions"] and parts[3:5] == ["resources", "files"]:
                if parts[5] in store.broken_files:
                    return self.answer(404, b'{"detail": "gone"}')
                return self.answer(
                    200, store.files[parts[2]][parts[5]], "application/octet-stream"
                )
            return self.answer(404, b'{"detail": "Not Found"}')

    return Handler


@pytest.fixture
def store() -> Store:
    return Store.synthetic()


@pytest.fixture
def server(store: Store) -> Iterator[str]:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(store))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture(autouse=True)
def small_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pages of three, so every listing and every session is read across page boundaries."""
    monkeypatch.setattr(export, "_PAGE", 3)


def tree(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def export_to(server: str, out: Path, *args: str) -> int:
    return export.main(["--server", server, "--out", str(out), *args], now=lambda: 1.0e9)


# --- End to end ------------------------------------------------------------------------


def test_the_exporter_over_http_writes_what_the_pure_path_wrote_for_the_fixture(
    server: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The same records, read over the native API, give the committed runs byte-for-byte.

    Only the manifests differ, in provenance (source, exported_at) and nothing else.
    """
    out = tmp_path / "runs"
    assert export_to(server, out) == 0
    exported, committed = tree(out), tree(FIXTURE_ROOT)
    assert sorted(exported) == sorted(committed)
    for path in exported:
        if path.endswith("manifest.json"):
            mine, theirs = json.loads(exported[path]), json.loads(committed[path])
            assert mine["source"] == {
                **theirs["source"],
                "kind": "omnigent-session",
                "server": server,
                "omnigent_version": "a2cbdeef",
                "iris_revision": REVISION,
            }
            assert mine["exported_at"] == 1.0e9
            drop = ("source", "exported_at")
            assert {k: v for k, v in mine.items() if k not in drop} == {
                k: v for k, v in theirs.items() if k not in drop
            }
        else:
            assert exported[path] == committed[path], path
    printed = capsys.readouterr().out.splitlines()
    assert "wrote fixture-iris 2026-W39 1790468460.000 fixture-session-iris-w39 false" in printed
    assert len(printed) == 5


def test_a_second_export_keeps_what_it_has_and_force_replaces(
    server: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "runs"
    assert export_to(server, out) == 0
    before = tree(out)
    capsys.readouterr()
    assert export_to(server, out) == 0
    assert all(line.startswith("kept ") for line in capsys.readouterr().out.splitlines())
    assert tree(out) == before
    assert export_to(server, out, "--force") == 0
    assert all(line.startswith("replaced ") for line in capsys.readouterr().out.splitlines())


def test_dry_run_lists_rows_and_writes_nothing(
    server: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "runs"
    assert export_to(server, out, "--dry-run") == 0
    assert not out.exists()
    assert capsys.readouterr().out.splitlines() == [
        "fixture-iris 2026-W36 1788654060.000 fixture-session-iris-sep true",
        "fixture-iris 2026-W37 1789258860.000 fixture-session-iris-sep true",
        "fixture-iris 2026-W38 1789863660.000 fixture-session-iris-sep true",
        "fixture-iris 2026-W39 1790468460.000 fixture-session-iris-w39 false",
        "fixture-iris-b 2026-W39 1790469060.000 fixture-session-iris-b-w39 true",
    ]


def test_tenant_session_and_since_narrow_the_export(
    server: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert export_to(server, tmp_path / "a", "--dry-run", "--tenant", "fixture-iris-b") == 0
    assert [r.split()[:2] for r in capsys.readouterr().out.splitlines()] == [
        ["fixture-iris-b", "2026-W39"]
    ]
    assert export_to(server, tmp_path / "b", "--dry-run", "--since", "2026-09-14") == 0
    assert [r.split()[:2] for r in capsys.readouterr().out.splitlines()] == [
        ["fixture-iris", "2026-W38"],
        ["fixture-iris", "2026-W39"],
        ["fixture-iris-b", "2026-W39"],
    ]
    assert (
        export_to(server, tmp_path / "c", "--dry-run", "--session", "fixture-session-iris-w39")
        == 0
    )
    assert [r.split()[:2] for r in capsys.readouterr().out.splitlines()] == [
        ["fixture-iris", "2026-W39"]
    ]


def test_the_newest_capture_in_a_week_wins_across_sessions(
    store: Store, server: str, tmp_path: Path
) -> None:
    later = GENERATOR.Session("fixture-session-iris-w39-later")
    later.capture(GENERATOR.Week("fixture-iris", GENERATOR.W39_AT + 3600))
    store.add("fixture-iris", later)
    out = tmp_path / "runs"
    assert export_to(server, out) == 0
    saved = runs.load_run(out, "fixture-iris", "2026-W39")
    assert saved.manifest["source"]["session_id"] == "fixture-session-iris-w39-later"
    assert saved.manifest["captured_at"] == GENERATOR.W39_AT + 3600
    assert saved.manifest["period"]["period_complete"] is True


# --- Fail closed -------------------------------------------------------------------------


def test_an_unreadable_session_fails_its_tenant_closed(
    store: Store, server: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """One unreadable session hides every run of its tenant: it might hold the newer capture."""
    store.broken_items.add("fixture-session-iris-w39")
    out = tmp_path / "runs"
    assert export_to(server, out) == 1
    assert [t.tenant_id for t in runs.scan(out)] == ["fixture-iris-b"]
    err = capsys.readouterr().err
    assert "fixture-iris not exported" in err and "HTTP 500" in err


def test_an_unreadable_required_report_fails_its_tenant_closed(
    store: Store, server: str, tmp_path: Path
) -> None:
    session = GENERATOR.sessions()["fixture-iris-b"][0]
    audit = "mcp__omnigent__iris_audit"
    call = next(i["call_id"] for i in session.items if i.get("name") == audit)
    output = next(i for i in session.items if i.get("call_id") == call and "output" in i)
    store.broken_files.add(json.loads(output["output"])["downloads"][0]["file_id"])
    out = tmp_path / "runs"
    assert export_to(server, out) == 1
    assert "fixture-iris-b" not in {t.tenant_id for t in runs.scan(out)}


def test_a_report_naming_another_tenant_is_refused(
    store: Store, server: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The adapter's 403: a session bound to one tenant holding another tenant's report."""
    store.bind("fixture-other", "Another fixture")
    impostor = GENERATOR.Session("fixture-session-impostor")
    impostor.capture(GENERATOR.Week("fixture-iris", GENERATOR.W39_AT))
    store.add("fixture-other", impostor)
    out = tmp_path / "runs"
    assert export_to(server, out) == 1
    assert "fixture-other" not in {t.tenant_id for t in runs.scan(out)}
    assert "Report tenant does not match session" in capsys.readouterr().err


def test_a_session_of_an_unbound_workspace_is_ignored(
    store: Store, server: str, tmp_path: Path
) -> None:
    stray = GENERATOR.Session("fixture-session-stray")
    stray.capture(GENERATOR.Week("fixture-iris", GENERATOR.W39_AT + 7200))
    store.add("fixture-iris", stray, workspace="/tmp/iris/somewhere-else")
    out = tmp_path / "runs"
    assert export_to(server, out) == 0
    saved = runs.load_run(out, "fixture-iris", "2026-W39")
    assert saved.manifest["source"]["session_id"] == "fixture-session-iris-w39"


# --- Refusals: D-2 (a) and rule V4 --------------------------------------------------------


@pytest.mark.parametrize(
    "remote",
    [
        "http://10.0.0.5:6768",
        "https://omnigent.airbrx.ai",
        "http://127.0.0.1.nip.io:6768",
        "http://user:secret@127.0.0.1:6768",
        "http://0.0.0.0:6768",
        "file:///etc/hosts",
        "127.0.0.1:6768",
    ],
)
def test_a_non_loopback_server_is_refused_before_any_request(
    remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_network(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the exporter reached for the network")

    monkeypatch.setattr(export.urllib.request, "urlopen", no_network)
    with pytest.raises(SystemExit) as refused:
        export.main(["--server", remote, "--out", str(tmp_path / "runs")])
    assert "local Omnigent stack only" in str(refused.value) or "http URL" in str(refused.value)
    assert not (tmp_path / "runs").exists()


@pytest.mark.parametrize(
    "local", ["http://127.0.0.1:6768", "http://localhost:6768", "http://[::1]:6768"]
)
def test_loopback_servers_are_accepted(local: str) -> None:
    assert export.loopback_server(local + "/") == local


def test_no_credential_is_sent_even_when_one_is_in_the_environment(
    store: Store, server: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OMNIGENT_API_TOKEN", "not-a-real-token")
    assert export_to(server, tmp_path / "runs") == 0
    assert store.requests
    for _path, headers in store.requests:
        assert not {h.lower() for h in headers} & {"authorization", "cookie"}


def test_an_output_inside_a_git_worktree_is_refused(
    server: str, tmp_path: Path, store: Store, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    assert export_to(server, repo / "runs") == 2
    assert not (repo / "runs").exists()
    assert store.requests == [], "refused before reading anything"
    assert "inside the git worktree" in capsys.readouterr().err


def test_the_default_output_is_the_home_runs_directory() -> None:
    assert export.parser().parse_args([]).out == "~/.iris-viewer/runs"
    assert export.parser().parse_args([]).server == "http://127.0.0.1:6768"


# --- The pure path ------------------------------------------------------------------------


def test_a_capture_window_ends_before_the_next_user_message() -> None:
    session = GENERATOR.Session("s")
    week = GENERATOR.Week("fixture-iris", GENERATOR.W39_AT)
    session.capture(week)
    session.message("user", "And what about last month?", week.at + 60, "next")
    session.message("assistant", "Not measured.", week.at + 61, "next-answer")
    [window] = export.capture_windows(session.items)
    assert window == session.items[:-2]


def test_an_overview_over_another_window_is_not_a_capture() -> None:
    session = GENERATOR.Session("s")
    session.capture(GENERATOR.Week("fixture-iris", GENERATOR.W39_AT))
    comparison = GENERATOR.Session("s")
    comparison.capture(GENERATOR.Week("fixture-iris", GENERATOR.W39_AT + 3600))
    for item in comparison.items:
        if item["type"] == "function_call" and item["name"].endswith("iris_overview"):
            item["arguments"] = json.dumps({"start_date": "2026-08-01", "end_date": "2026-08-08"})
    session.items += comparison.items
    session.files.update(comparison.files)
    [window] = export.capture_windows(session.items)
    source = {"kind": "synthetic", "session_id": "s"}
    [run] = export.runs_from_items(
        session.items, session.files.__getitem__, tenant_id="fixture-iris", source=source
    )
    assert run.captured_at == GENERATOR.W39_AT
    assert run.source["through_item_id"] == window[-1]["id"]


def test_saved_messages_drop_tool_items_and_non_text_parts() -> None:
    items = [
        {"id": "r", "type": "reasoning", "created_at": 1},
        {"id": "c", "type": "function_call", "name": "iris_overview", "call_id": "x"},
        {"id": "o", "type": "function_call_output", "call_id": "x", "output": "{}"},
        {"id": "sys", "type": "message", "role": "system", "content": [], "created_at": 1},
        {
            "id": "u",
            "type": "message",
            "role": "user",
            "status": "completed",
            "content": [
                {"type": "input_text", "text": "hi"},
                {"type": "input_image", "image_url": "data:"},
            ],
            "created_at": 2,
            "metadata": {"internal": True},
        },
        {
            "id": "a",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "hello", "annotations": []}],
            "created_at": 3,
        },
    ]
    assert export.saved_messages(items) == [
        {
            "id": "u",
            "type": "message",
            "role": "user",
            "status": "completed",
            "content": [{"type": "input_text", "text": "hi"}],
            "created_at": 2,
        },
        {
            "id": "a",
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": "hello"}],
            "created_at": 3,
        },
    ]
