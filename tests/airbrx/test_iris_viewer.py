"""The standalone Iris viewer: saved runs on disk, dev sign-in, no agent.

docs/iris/STANDALONE.md and the viewer contract (STANDALONE_VIEWER.md,
sections 2.1, 3.3 and 4). Every test serves a temporary copy of the committed
synthetic fixture (``omnigent/airbrx/iris/fixtures/runs``), so a test that
tampers with a run never touches the repository.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from omnigent.airbrx.iris import runs

REPO = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = REPO / "omnigent/airbrx/iris/fixtures/runs"
DEV_SH = REPO / "scripts/iris/dev.sh"
W38 = "/t/fixture-iris/2026-W38/ui"
NOT_CONNECTED = "Iris is not connected to this viewer; nothing was run."


@pytest.fixture
def root(tmp_path: Path) -> Path:
    copy = tmp_path / "runs"
    shutil.copytree(FIXTURE_ROOT, copy)
    return copy


def make_client(*roots: Path, **kwargs) -> TestClient:
    from omnigent.airbrx.iris.viewer.app import create_app
    from omnigent.airbrx.iris.viewer.identity import DevIdentity

    app = create_app(list(roots), identity=DevIdentity("dev@localhost"), **kwargs)
    return TestClient(app, base_url="http://127.0.0.1:6790", follow_redirects=False)


@pytest.fixture
def client(root: Path) -> TestClient:
    return make_client(root)


def rewrite(root: Path, tenant: str, week: str, relpath: str, data: bytes) -> None:
    """Change one run file and re-sign it in the manifest, so the run stays readable."""
    run_dir = root / tenant / week
    (run_dir / relpath).write_bytes(data)
    manifest = json.loads((run_dir / "manifest.json").read_bytes())
    manifest["files"][relpath] = runs.sha256(data)
    (run_dir / "manifest.json").write_bytes(runs.dump_json(manifest))


# --- Import discipline (section 1) ---------------------------------------------------


def test_the_viewer_imports_neither_the_server_nor_the_adapter_routes() -> None:
    probe = (
        "import sys, omnigent.airbrx.iris.viewer, omnigent.airbrx.iris.viewer.app, "
        "omnigent.airbrx.iris.viewer.identity, omnigent.airbrx.iris.viewer.__main__\n"
        "bad = sorted(m for m in sys.modules if m == 'omnigent.server' "
        "or m.startswith('omnigent.server.') or m == 'omnigent.airbrx.iris.routes')\n"
        "print(bad)"
    )
    out = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO,
    )
    assert out.stdout.strip() == "[]", out.stdout


# --- Dev sign-in never leaves loopback (rule V5) -------------------------------------


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_dev_sign_in_is_allowed_on_loopback(host: str) -> None:
    from omnigent.airbrx.iris.viewer.identity import check_dev_bind

    check_dev_bind(host, public_url=None)


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.20", "iris.airbrx.ai", ""])
def test_dev_sign_in_refuses_a_non_loopback_bind(host: str) -> None:
    from omnigent.airbrx.iris.viewer.identity import check_dev_bind

    with pytest.raises(SystemExit, match="loopback"):
        check_dev_bind(host, public_url=None)


def test_dev_sign_in_refuses_an_https_public_url() -> None:
    from omnigent.airbrx.iris.viewer.identity import check_dev_bind

    with pytest.raises(SystemExit, match="https"):
        check_dev_bind("127.0.0.1", public_url="https://iris.airbrx.ai")


def test_the_command_line_refuses_to_start_on_a_non_loopback_host(root: Path) -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("IRIS_VIEWER_")}
    out = subprocess.run(
        [
            *(sys.executable, "-m", "omnigent.airbrx.iris.viewer", "--host", "0.0.0.0"),
            *("--port", "1", "--auth", "dev", "--runs", str(root)),
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert out.returncode != 0
    assert "loopback" in out.stderr


def test_a_request_naming_another_host_is_refused(client: TestClient) -> None:
    # DNS rebinding: a page on another name that resolves to 127.0.0.1 must
    # not be able to read tenant evidence through the dev sign-in.
    response = client.get(f"{W38}/api/state", headers={"host": "evil.example:6790"})
    assert response.status_code == 400


def test_every_request_resolves_to_the_dev_identity(client: TestClient) -> None:
    host = client.get(f"{W38}/api/host").json()
    assert host["identity"] == {"kind": "dev", "email": "dev@localhost", "sign_in": None}


def test_an_unresolved_identity_is_401_for_the_api(root: Path) -> None:
    from omnigent.airbrx.iris.viewer.app import create_app
    from omnigent.airbrx.iris.viewer.identity import Identity

    class Nobody:
        def resolve(self, request) -> Identity | None:
            return None

        def tenants(self, identity: Identity) -> set[str] | None:
            return None

    app = TestClient(create_app([root], identity=Nobody()), base_url="http://127.0.0.1")
    assert app.get(f"{W38}/api/state").status_code == 401
    assert app.get("/api/tenants").status_code == 401
    assert app.get(f"{W38}/").status_code == 401


def test_a_tenant_outside_the_identitys_set_is_404(root: Path) -> None:
    from omnigent.airbrx.iris.viewer.app import create_app
    from omnigent.airbrx.iris.viewer.identity import Identity

    class OnlyB:
        def resolve(self, request) -> Identity | None:
            return Identity("dev", "b@localhost")

        def tenants(self, identity: Identity) -> set[str] | None:
            return {"fixture-iris-b"}

    app = TestClient(create_app([root], identity=OnlyB()), base_url="http://127.0.0.1")
    assert app.get(f"{W38}/api/state").status_code == 404
    assert app.get("/t/fixture-iris-b/2026-W39/ui/api/state").status_code == 200
    ids = [b["tenant_id"] for b in app.get("/api/tenants").json()["bindings"]]
    assert ids == ["fixture-iris-b"]


# --- Landing and redirects (section 4.2) ---------------------------------------------


@pytest.mark.parametrize("path", ["/", "/iris", "/iris/"])
def test_the_landing_page_opens_the_first_tenants_newest_readable_run(
    client: TestClient, path: str
) -> None:
    response = client.get(path)
    assert response.status_code == 302
    # "Iris fixture tenant" sorts before "Iris fixture tenant B".
    assert response.headers["location"] == "/t/fixture-iris/2026-W39/ui/"


def test_no_saved_runs_is_a_plain_page_naming_the_roots(tmp_path: Path) -> None:
    empty = tmp_path / "nothing"
    empty.mkdir()
    response = make_client(empty).get("/")
    assert response.status_code == 200
    assert f"No saved runs in {empty}" in response.text
    assert "\u2014" not in response.text


def test_a_tenant_home_opens_its_newest_readable_run(client: TestClient, root: Path) -> None:
    assert client.get("/t/fixture-iris/").headers["location"] == "/t/fixture-iris/2026-W39/ui/"
    # Unreadable newest week: the next readable one opens instead.
    (root / "fixture-iris/2026-W39/state.json").write_bytes(b"{}")
    assert client.get("/t/fixture-iris/").headers["location"] == "/t/fixture-iris/2026-W38/ui/"


@pytest.mark.parametrize("path", ["/t/fixture-iris/2026-W38", "/t/fixture-iris/2026-W38/ui"])
def test_a_run_without_its_trailing_ui_slash_redirects(client: TestClient, path: str) -> None:
    response = client.get(path)
    assert response.status_code in (301, 302, 307, 308)
    assert response.headers["location"].endswith("/t/fixture-iris/2026-W38/ui/")


# --- The host descriptor (section 2.1) -----------------------------------------------


def test_the_host_descriptor_is_the_contracts_standalone_answer(client: TestClient) -> None:
    host = client.get(f"{W38}/api/host").json()
    assert {k: v for k, v in host.items() if k != "data"} == {
        "schema": 1,
        "host_label": "Iris viewer",
        "identity": {"kind": "dev", "email": "dev@localhost", "sign_in": None},
        "agent": {"connected": False, "why": "viewer"},
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
    data = host["data"]
    assert data["source"] == "runs"
    assert data["scope_id"] == "fixture-iris/2026-W38"
    assert data["tenant_id"] == "fixture-iris"
    assert data["week"] == "2026-W38"
    assert [w["week"] for w in data["weeks"]] == ["2026-W39", "2026-W38", "2026-W37", "2026-W36"]


def test_each_week_carries_its_coverage_for_the_picker(client: TestClient) -> None:
    weeks = {w["week"]: w for w in client.get(f"{W38}/api/host").json()["data"]["weeks"]}
    assert weeks["2026-W39"] == {
        "week": "2026-W39",
        "captured_at": 1790468460.0,
        "start_date": "2026-09-20",
        "end_date": "2026-09-23",
        "period_complete": False,
        "covered_days": 3,
        "requested_days": 7,
        "readable": True,
    }
    assert weeks["2026-W38"]["period_complete"] is True
    assert weeks["2026-W38"]["covered_days"] == weeks["2026-W38"]["requested_days"] == 7


def test_an_unreadable_week_is_listed_as_unreadable(client: TestClient, root: Path) -> None:
    (root / "fixture-iris/2026-W37/items.json").write_bytes(b"{}")
    weeks = {w["week"]: w for w in client.get(f"{W38}/api/host").json()["data"]["weeks"]}
    assert weeks["2026-W37"]["readable"] is False
    assert weeks["2026-W38"]["readable"] is True


# --- State (sections 3.3 and 5.4, V2 and V3) ------------------------------------------


@pytest.mark.parametrize("week", ["2026-W36", "2026-W37", "2026-W38", "2026-W39"])
def test_state_is_the_saved_body_with_only_the_clock_fields_recomputed(
    client: TestClient, root: Path, week: str
) -> None:
    saved = json.loads((root / "fixture-iris" / week / "state.json").read_bytes())
    served = client.get(f"/t/fixture-iris/{week}/ui/api/state")
    assert served.status_code == 200
    assert served.headers["cache-control"] == "no-store"
    body = served.json()
    clock = {"cache_age_seconds", "stale"}
    assert {k: v for k, v in body.items() if k not in clock} == {
        k: v for k, v in saved.items() if k not in clock
    }
    assert body["cache_age_seconds"] > 300 and body["stale"] is True


def test_the_clock_fields_follow_captured_at(client: TestClient, monkeypatch) -> None:
    from omnigent.airbrx.iris.viewer import app as viewer_app

    monkeypatch.setattr(viewer_app.time, "time", lambda: 1789863660.0 + 120.4)
    body = client.get(f"{W38}/api/state").json()
    assert body["cache_age_seconds"] == 120 and body["stale"] is False


def test_the_inconsistent_week_is_served_as_saved_and_refused_by_the_app(
    client: TestClient,
) -> None:
    # Q19: the viewer adds no check of its own and removes none. W36's
    # mismatch reaches the app, which refuses it.
    metrics = client.get("/t/fixture-iris/2026-W36/ui/api/state").json()["overview"]["metrics"]
    assert metrics["cache_hits"] + metrics["cache_misses"] != metrics["hit_rate_denominator"]


def test_a_state_naming_another_tenant_is_403(client: TestClient, root: Path) -> None:
    state = json.loads((root / "fixture-iris/2026-W38/state.json").read_bytes())
    state["overview"]["tenant_id"] = "fixture-iris-b"
    rewrite(root, "fixture-iris", "2026-W38", "state.json", runs.dump_json(state))
    response = client.get(f"{W38}/api/state")
    assert response.status_code == 403
    assert response.json() == {"detail": "Report tenant does not match session"}


def test_a_tampered_run_is_never_served(client: TestClient, root: Path) -> None:
    path = root / "fixture-iris/2026-W38/state.json"
    path.write_bytes(path.read_bytes().replace(b"fixture", b"fixturf", 1))
    for suffix in ("/", "/app.js", "/api/host", "/api/state", "/api/items", "/api/files"):
        assert client.get(W38 + suffix).status_code == 404, suffix
    health = client.get("/healthz").json()
    assert health["unreadable"] == 1


def test_a_file_changed_after_the_scan_is_not_served(client: TestClient, root: Path) -> None:
    assert client.get(f"{W38}/api/host").status_code == 200
    (root / "fixture-iris/2026-W38/items.json").write_bytes(b'{"data": [], "has_more": false}')
    assert client.get(f"{W38}/api/items").status_code == 404


# --- Path safety (section 4.2) --------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/t/../2026-W38/ui/api/state",
        "/t/%2e%2e/2026-W38/ui/api/state",
        "/t/Fixture-Iris/2026-W38/ui/api/state",
        "/t/fixture-iris/2026-38/ui/api/state",
        "/t/fixture-iris/..%2f2026-W38/ui/api/state",
        "/t/fixture-iris/2026-W38%2f..%2f2026-W37/ui/api/state",
        "/t/fixture-iris/2026-W38/ui/api/files/..%2fstate.json/content",
        "/t/fixture-iris/2026-W38/ui/api/files/index.json/content",
        "/t/fixture-iris/2026-W38/ui/api/files/state/content",
        "/t/fixture-iris/2026-W38/ui/api/files/file_00000000000000000000000000000000/content",
        "/t/fixture-iris/2026-W35/ui/api/state",
        "/t/fixture-nobody/2026-W38/ui/api/state",
        "/t/fixture-nobody/",
    ],
)
def test_anything_off_the_layout_is_404(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 404


def test_a_symlinked_tenant_out_of_the_root_is_not_followed(tmp_path: Path, root: Path) -> None:
    outside = tmp_path / "outside"
    shutil.copytree(root / "fixture-iris-b", outside / "fixture-iris-b")
    shutil.rmtree(root / "fixture-iris-b")
    (root / "fixture-iris-b").symlink_to(outside / "fixture-iris-b")
    assert make_client(root).get("/t/fixture-iris-b/2026-W39/ui/api/state").status_code == 404


def test_a_tenant_in_two_roots_stops_the_server_naming_both(tmp_path: Path, root: Path) -> None:
    from omnigent.airbrx.iris.viewer.app import DuplicateTenant

    second = tmp_path / "second"
    shutil.copytree(root / "fixture-iris-b", second / "fixture-iris-b")
    with pytest.raises(DuplicateTenant) as raised:
        make_client(root, second)
    assert str(root) in str(raised.value) and str(second) in str(raised.value)


def test_runs_from_several_roots_are_served_together(tmp_path: Path, root: Path) -> None:
    second = tmp_path / "second"
    second.mkdir()
    shutil.move(root / "fixture-iris-b", second / "fixture-iris-b")
    client = make_client(root, second)
    assert client.get("/t/fixture-iris-b/2026-W39/ui/api/state").status_code == 200
    assert client.get(f"{W38}/api/state").status_code == 200
    assert client.get("/healthz").json()["tenants"] == 2


# --- The workspace files, through the shared rules (SV1 ui_assets) -------------------


def test_the_workspace_is_served_by_the_shared_asset_rules(client: TestClient) -> None:
    index = client.get(f"{W38}/")
    assert index.status_code == 200
    assert index.headers["cache-control"] == "no-store"
    assert index.headers["x-frame-options"] == "SAMEORIGIN"
    assert client.get(f"{W38}/app.js").headers["cache-control"] == "no-store"
    assert client.get(f"{W38}/style.css").status_code == 200
    assert client.get(f"{W38}/assets/iris-portrait.png").status_code == 200


@pytest.mark.parametrize(
    "asset", ["iris-state.json", "demo-state.json", "host.js", "views/a.b.js", "kernel/nope.js"]
)
def test_files_outside_the_shared_rules_are_404(client: TestClient, asset: str) -> None:
    assert client.get(f"{W38}/{asset}").status_code == 404


# --- No agent (section 5, rule V1 belt and braces) ------------------------------------


@pytest.mark.parametrize("action", ["chat", "refresh", "cancel"])
def test_asking_or_collecting_runs_nothing(client: TestClient, action: str) -> None:
    response = client.post(f"{W38}/api/{action}", json={"message": "hi"})
    assert response.status_code == 409
    assert response.json() == {"detail": NOT_CONNECTED}


def test_the_only_non_read_routes_are_the_three_refusals() -> None:
    from omnigent.airbrx.iris.viewer.app import create_app

    app = create_app([], identity=None)
    writes = sorted(
        (route.path, sorted(route.methods - {"GET", "HEAD"}))
        for route in app.routes
        if getattr(route, "methods", None) and route.methods - {"GET", "HEAD"}
    )
    assert writes == [
        ("/t/{tenant}/{week}/ui/api/cancel", ["POST"]),
        ("/t/{tenant}/{week}/ui/api/chat", ["POST"]),
        ("/t/{tenant}/{week}/ui/api/refresh", ["POST"]),
    ]


def test_the_session_is_idle(client: TestClient) -> None:
    assert client.get(f"{W38}/api/session").json() == {"status": "idle"}


def test_readiness_names_the_saved_run_and_the_missing_agent(client: TestClient) -> None:
    body = client.get(f"{W38}/api/readiness").json()
    assert body == {
        "tenant_id": "fixture-iris",
        "name": "Iris fixture tenant",
        "fixture": True,
        "session_status": "saved",
        "turn_completed_here": False,
        "last_task_failed": False,
        "verified": [
            "this run was exported from synthetic session records on 2026-09-27, "
            "and its files match their checksums"
        ],
        "unverified": [
            "Iris is not connected to this viewer, so nothing can be asked or collected here"
        ],
    }
    assert "\u2014" not in json.dumps(body)


# --- Items and files -----------------------------------------------------------------


def test_items_honour_order_and_limit_in_the_native_page_shape(
    client: TestClient, root: Path
) -> None:
    saved = json.loads((root / "fixture-iris/2026-W38/items.json").read_bytes())["data"]
    assert len(saved) >= 2
    everything = client.get(f"{W38}/api/items").json()
    assert everything["data"] == saved and everything["has_more"] is False
    newest = client.get(f"{W38}/api/items", params={"order": "desc", "limit": 1}).json()
    assert newest["data"] == [saved[-1]] and newest["has_more"] is True
    assert newest["first_id"] == newest["last_id"] == saved[-1]["id"]
    oldest = client.get(f"{W38}/api/items", params={"order": "asc", "limit": 1}).json()
    assert oldest["data"] == [saved[0]] and oldest["has_more"] is True
    assert client.get(f"{W38}/api/items", params={"order": "up"}).status_code == 422
    assert client.get(f"{W38}/api/items", params={"limit": 0}).status_code == 422


def test_files_list_and_download_as_attachments(client: TestClient, root: Path) -> None:
    index = json.loads((root / "fixture-iris/2026-W38/files/index.json").read_bytes())
    listed = client.get(f"{W38}/api/files", params={"limit": 100}).json()
    assert listed["data"] == index["data"]
    first = index["data"][0]
    content = client.get(f"{W38}/api/files/{first['id']}/content")
    assert content.status_code == 200
    assert content.content == (root / "fixture-iris/2026-W38/files" / first["id"]).read_bytes()
    assert content.headers["content-disposition"].startswith("attachment")
    assert first["filename"] in content.headers["content-disposition"]


# --- Accounts (section 4.2) ----------------------------------------------------------


def test_tenants_answer_in_the_catalog_shape(client: TestClient) -> None:
    source = json.loads((REPO / "omnigent/airbrx/iris/source.json").read_text())
    assert client.get("/api/tenants").json() == {
        "agent_id": None,
        "revision": source["revision"],
        "bindings": [
            {
                "tenant_id": "fixture-iris",
                "name": "Iris fixture tenant",
                "host_id": None,
                "workspace": None,
                "fixture": True,
                "host_online": None,
            },
            {
                "tenant_id": "fixture-iris-b",
                "name": "Iris fixture tenant B",
                "host_id": None,
                "workspace": None,
                "fixture": True,
                "host_online": None,
            },
        ],
    }


def test_accounts_rank_the_newest_readable_runs_with_the_pinned_rank(
    client: TestClient,
) -> None:
    body = client.get("/api/account").json()
    assert body["tenants"] == 2
    assert [r["tenant_id"] for r in body["ranked"]] == ["fixture-iris-b"]
    assert [(q["tenant_id"], q["reason"]) for q in body["quarantined"]] == [
        ("fixture-iris", "incomplete_period")
    ]


def test_accounts_read_only_tenant_id_and_metrics_of_a_report(
    client: TestClient, monkeypatch
) -> None:
    from omnigent.airbrx.iris.viewer import app as viewer_app

    seen = {}

    def spy(tenants, captures, *, now):
        seen.update(captures)
        return {"ranked": [], "quarantined": []}

    monkeypatch.setattr(viewer_app, "pinned_rank", lambda: spy)
    client.get("/api/account")
    assert set(seen) == {"fixture-iris", "fixture-iris-b"}
    for capture in seen.values():
        assert set(capture) == {"tenant_id", "metrics", "captured_at"}
    assert seen["fixture-iris"]["captured_at"] == 1790468460.0


def test_a_tenant_with_no_readable_run_is_never_collected(client: TestClient, root: Path) -> None:
    (root / "fixture-iris-b/2026-W39/state.json").write_bytes(b"{}")
    body = client.get("/api/account").json()
    reasons = {q["tenant_id"]: q["reason"] for q in body["quarantined"]}
    assert reasons["fixture-iris-b"] == "never_collected"


# --- Health ---------------------------------------------------------------------------


def test_healthz_names_the_app_the_checkout_and_the_counts(client: TestClient, root: Path) -> None:
    assert client.get("/healthz").json() == {
        "ok": True,
        "app": "iris-viewer",
        "checkout": str(REPO),
        "roots": [str(root)],
        "tenants": 2,
        "runs": 5,
        "unreadable": 0,
    }


# --- scripts/iris/dev.sh (section 4.4) -----------------------------------------------


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def serve(body: dict | None):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            payload = json.dumps(body or {}).encode()
            self.send_response(200 if body is not None else 404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", free_port()), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


needs_lsof = pytest.mark.skipif(
    shutil.which("lsof") is None or shutil.which("curl") is None, reason="needs lsof and curl"
)


def run_dev_sh(port: int) -> subprocess.CompletedProcess:
    env = {"PATH": os.environ["PATH"], "HOME": os.environ["HOME"], "IRIS_VIEWER_PORT": str(port)}
    return subprocess.run(
        ["bash", str(DEV_SH)], capture_output=True, text=True, env=env, timeout=30
    )


@needs_lsof
def test_dev_sh_is_idempotent_when_this_checkouts_viewer_is_listening() -> None:
    server = serve({"ok": True, "app": "iris-viewer", "checkout": str(REPO)})
    try:
        out = run_dev_sh(server.server_address[1])
    finally:
        server.shutdown()
    assert out.returncode == 0, out.stderr
    assert f"http://127.0.0.1:{server.server_address[1]}/iris" in out.stdout


@needs_lsof
@pytest.mark.parametrize(
    "body", [None, {"ok": True, "app": "iris-viewer", "checkout": "/elsewhere"}]
)
def test_dev_sh_never_kills_another_listener(body: dict | None) -> None:
    server = serve(body)
    try:
        out = run_dev_sh(server.server_address[1])
        assert out.returncode == 1
        assert str(os.getpid()) in out.stderr
        # Still listening: nothing was killed.
        with socket.create_connection(("127.0.0.1", server.server_address[1]), timeout=5):
            pass
    finally:
        server.shutdown()


def stub_uv(tmp_path: Path) -> tuple[Path, Path]:
    """A `uv` on PATH that records the environment and arguments it was started with."""
    bin_dir, record = tmp_path / "bin", tmp_path / "uv-env.txt"
    bin_dir.mkdir()
    stub = bin_dir / "uv"
    stub.write_text(f'#!/bin/sh\nenv > "{record}"\necho "ARGS $*" >> "{record}"\n')
    stub.chmod(0o755)
    return bin_dir, record


@needs_lsof
def test_dev_sh_starts_clean_and_skips_the_web_ui_build(tmp_path: Path) -> None:
    # A fresh checkout's `uv run` builds the editable package; the viewer never
    # serves Omnigent's web UI, so that build must be skipped.
    bin_dir, record = stub_uv(tmp_path)
    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "IRIS_VIEWER_PORT": str(free_port()),
        "CLAUDE_CODE_FAKE": "leak",
        "SECRET_TOKEN": "leak",
    }
    out = subprocess.run(
        ["bash", str(DEV_SH)], capture_output=True, text=True, env=env, timeout=30
    )
    assert out.returncode == 0, out.stderr
    started = record.read_text()
    assert "OMNIGENT_SKIP_WEB_UI=true" in started.splitlines()
    assert "leak" not in started
    assert "--host 127.0.0.1" in started


@needs_lsof
def test_dev_sh_is_idempotent_through_a_symlinked_checkout(tmp_path: Path) -> None:
    link = tmp_path / "checkout-link"
    link.symlink_to(REPO)
    server = serve({"ok": True, "app": "iris-viewer", "checkout": str(REPO.resolve())})
    try:
        env = {
            "PATH": os.environ["PATH"],
            "HOME": os.environ["HOME"],
            "IRIS_VIEWER_PORT": str(server.server_address[1]),
        }
        out = subprocess.run(
            ["bash", str(link / "scripts/iris/dev.sh")],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
    finally:
        server.shutdown()
    assert out.returncode == 0, out.stderr
    assert "already running from this checkout" in out.stdout


def test_a_download_is_never_read_under_an_id_off_the_layout(
    client: TestClient, root: Path, monkeypatch
) -> None:
    # A corrupted index listing an id the layout refuses is still not read.
    real_index = runs.SavedRun.file_index
    read = []

    def index_with_a_bad_id(self):
        body = real_index(self)
        return {"data": [*body["data"], {"id": "a.b", "filename": "report.json", "bytes": 1}]}

    monkeypatch.setattr(runs.SavedRun, "file_index", index_with_a_bad_id)
    monkeypatch.setattr(runs.SavedRun, "file", lambda self, file_id: read.append(file_id) or b"x")
    assert client.get(f"{W38}/api/files/a.b/content").status_code == 404
    assert read == []


def test_a_download_whose_listed_name_is_not_a_report_is_named_download(
    client: TestClient, root: Path
) -> None:
    index_path = root / "fixture-iris/2026-W38/files/index.json"
    index = json.loads(index_path.read_bytes())
    index["data"][0]["filename"] = 'x"\r\nSet-Cookie: a=b.json'
    rewrite(root, "fixture-iris", "2026-W38", "files/index.json", runs.dump_json(index))
    content = client.get(f"{W38}/api/files/{index['data'][0]['id']}/content")
    assert content.status_code == 200
    assert content.headers["content-disposition"] == 'attachment; filename="download"'
    assert content.headers["content-type"] == "application/octet-stream"
    assert "set-cookie" not in content.headers
