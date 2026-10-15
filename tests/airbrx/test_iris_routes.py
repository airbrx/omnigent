"""The workspace adapter reads recorded session items, not registration names.

Iris tools are registered bare and dispatched bare, but the model reaches them
through the SDK's MCP namespace, so `GET /v1/sessions/{id}/items` records
`mcp__omnigent__iris_overview`. `completed_answer` runs server-side inside the
chat polling loop, so comparing the recorded spelling against the registration
spelling rejected every correct turn with a 409 -- in the hosted UI, not only
in acceptance.

Shapes here are the ones the deployment actually stores, taken from the
function_call items of real sessions.
"""

import json
import unittest

from fastapi import HTTPException

from omnigent.airbrx.iris.routes import (
    bare_tool_name,
    completed_answer,
    report_references,
)
from omnigent.airbrx.iris.runtime import TOOLS


def call(name, call_id="c0"):
    return {"type": "function_call", "name": name, "call_id": call_id}


def answer(text="hit rate 0.0% over 44 requests"):
    return {
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": text}],
    }


class BareToolName(unittest.TestCase):
    def test_this_servers_prefix_is_removed(self):
        self.assertEqual(bare_tool_name("mcp__omnigent__iris_overview"), "iris_overview")

    def test_an_already_bare_name_is_unchanged(self):
        self.assertEqual(bare_tool_name("iris_overview"), "iris_overview")

    def test_another_servers_prefix_is_preserved(self):
        # Stripping every mcp__ prefix would let a tool from any other server
        # collide with an Iris registration name and pass the boundary check.
        for name in ("mcp__airbrx__iris_overview", "mcp__claude_ai_Slack__iris_overview"):
            self.assertEqual(bare_tool_name(name), name)

    def test_a_missing_name_does_not_raise(self):
        self.assertIsNone(bare_tool_name(None))


class CompletedAnswerNamespacing(unittest.TestCase):
    def test_the_recorded_namespaced_call_is_accepted(self):
        result = completed_answer([call("mcp__omnigent__iris_overview"), answer()])
        self.assertIsNotNone(result)
        # Reported bare, so callers compare against the registration names --
        # verify_host.py asserts exactly ["iris_overview"].
        self.assertEqual(result["tools"], ["iris_overview"])

    def test_every_iris_tool_survives_the_round_trip(self):
        for tool in sorted(TOOLS):
            with self.subTest(tool=tool):
                result = completed_answer([call(f"mcp__omnigent__{tool}"), answer()])
                self.assertEqual(result["tools"], [tool])

    def test_a_bare_call_still_works(self):
        result = completed_answer([call("iris_overview"), answer()])
        self.assertEqual(result["tools"], ["iris_overview"])


class CompletedAnswerBoundaryIsUnchanged(unittest.TestCase):
    """Normalising the spelling must not widen what the guard allows."""

    def test_a_tool_from_another_mcp_server_is_still_refused(self):
        for name in ("mcp__airbrx__get_tenant_rules", "mcp__claude_ai_Slack__slack_read_file"):
            with self.subTest(name=name):
                with self.assertRaises(HTTPException) as caught:
                    completed_answer([call(name), answer()])
                self.assertEqual(caught.exception.status_code, 409)

    def test_a_non_iris_omnigent_tool_is_still_refused(self):
        # Sharing the prefix is not membership: sys_os_shell is on the same
        # server and must not become reachable by stripping it.
        with self.assertRaises(HTTPException) as caught:
            completed_answer([call("mcp__omnigent__sys_os_shell"), answer()])
        self.assertEqual(caught.exception.status_code, 409)


class HarnessToolsAreAllowedByName(unittest.TestCase):
    """ToolSearch is admitted deliberately, and only ToolSearch.

    It is recorded as a real function_call but only loads tool *schemas*: it
    reaches no tenant, and any tool it surfaces still has to be called as its
    own function_call, which the guard above sees. Iris dispatch stays gated
    separately by runtime.invoke() against TOOLS, so this does not widen what
    can actually run.
    """

    def test_the_recorded_shape_of_a_real_turn_is_accepted(self):
        # Exactly what the deployment stores for a correct overview turn.
        result = completed_answer(
            [call("ToolSearch", "c1"), call("mcp__omnigent__iris_overview", "c2"), answer()]
        )
        self.assertEqual(result["tools"], ["ToolSearch", "iris_overview"])

    def test_the_harness_tool_is_reported_not_hidden(self):
        # Filtering it out of the record would make the turn read as though it
        # never happened; consumers that care select the Iris calls themselves.
        result = completed_answer([call("ToolSearch"), answer()])
        self.assertEqual(result["tools"], ["ToolSearch"])

    def test_allowing_it_is_not_a_licence_for_harness_tools_generally(self):
        # The decision was ToolSearch, not "harness tools". Bash and Read are
        # recorded the same way and must stay outside the boundary.
        for name in ("Bash", "Read", "Write", "Edit", "WebFetch", "Skill", "Agent"):
            with self.subTest(name=name):
                with self.assertRaises(HTTPException) as caught:
                    completed_answer([call(name), answer()])
                self.assertEqual(caught.exception.status_code, 409)

    def test_it_is_not_added_to_the_dispatch_allowlist(self):
        # TOOLS gates real dispatch in runtime.invoke(). Widening the
        # record-side guard must never widen that.
        self.assertNotIn("ToolSearch", TOOLS)

    def test_a_turn_with_no_iris_call_still_records_no_iris_dispatch(self):
        # The guard permits it; asserting that an overview actually ran is the
        # caller's job (verify_host.py selects the TOOLS members).
        result = completed_answer([call("ToolSearch"), answer()])
        self.assertEqual([t for t in result["tools"] if t in TOOLS], [])


class ReportReferencesNamespacing(unittest.TestCase):
    """The same mismatch silently produced 'No native session report reference'."""

    def items(self, name):
        output = json.dumps({"downloads": [{"filename": "report.json", "file_id": "f1"}]})
        return [
            call(name, "c1"),
            {"type": "function_call_output", "call_id": "c1", "output": output, "created_at": 7},
        ]

    def test_a_namespaced_call_yields_its_report_reference(self):
        refs = report_references(self.items("mcp__omnigent__iris_overview"))
        self.assertEqual(refs, [("iris_overview", "f1", 7)])

    def test_a_foreign_tools_download_is_still_ignored(self):
        self.assertEqual(report_references(self.items("mcp__airbrx__get_tenant_rules")), [])

    def test_an_errored_result_yields_no_reference(self):
        # Unrelated to namespacing, pinned because a budget-expired run sets
        # error_code and the resulting empty list reads as a missing report.
        items = self.items("mcp__omnigent__iris_overview")
        items[1]["output"] = json.dumps(
            {"error_code": "budget", "downloads": [{"filename": "report.json", "file_id": "f1"}]}
        )
        self.assertEqual(report_references(items), [])


if __name__ == "__main__":
    unittest.main()


class TenantDisplayNames(unittest.TestCase):
    """A UUID is not a tenant name, and an operator should not have to read one."""

    def binding(self, **kw):
        row = {
            "tenant_id": "00000000-0000-4000-8000-000000000001",
            "host_id": "h1",
            "workspace": "/w",
            "users": ["a@b.com"],
            "pat_ref": "keychain:x",
        }
        row.update(kw)
        return row

    def parse(self, rows):
        import json
        import os
        import tempfile

        from omnigent.airbrx.iris.config import bindings

        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w") as f:
            json.dump(rows, f)
        prev = os.environ.get("OMNIGENT_IRIS_CONFIG")
        os.environ["OMNIGENT_IRIS_CONFIG"] = path
        try:
            return bindings()
        finally:
            if prev is None:
                os.environ.pop("OMNIGENT_IRIS_CONFIG", None)
            else:
                os.environ["OMNIGENT_IRIS_CONFIG"] = prev
            os.unlink(path)

    def test_a_name_is_carried_through(self):
        (b,) = self.parse([self.binding(name="Example Warehouse Tenant")])
        self.assertEqual(b.name, "Example Warehouse Tenant")

    def test_a_binding_without_a_name_still_parses(self):
        # The field is optional on purpose: an operator who has not named a
        # tenant gets the id, which is ugly and never wrong.
        (b,) = self.parse([self.binding()])
        self.assertEqual(b.name, "")

    def test_whitespace_is_not_a_name(self):
        (b,) = self.parse([self.binding(name="   ")])
        self.assertEqual(b.name, "")

    def test_an_unknown_setting_is_still_refused(self):
        # Widening the allowed keys must not widen them to anything.
        with self.assertRaises(ValueError):
            self.parse([self.binding(nickname="db-prod")])


# --- Workspace v2 state (docs/iris/WORKSPACE_V2.md, section 2) -------------------------
#
# The harness mounts stand-ins for the native session routes on the test app;
# see tests/airbrx/test_iris_workspace_fixtures.py.

import pytest  # noqa: E402

from omnigent.airbrx.iris import routes as iris_routes  # noqa: E402
from tests.airbrx.test_iris_workspace_fixtures import (  # noqa: E402
    API,
    AUDIT_ID,
    FILES,
    FRESH_OVERVIEW_ID,
    INVESTIGATE_ID,
    NOW,
    OVERVIEW_ID,
    PROPOSE_ID,
    READ_AT,
    SESSION,
    IrisSession,
    captured,
    investigate_report,
    make_client,
    propose_report,
    read_nothing,
    respond,
    tool_run,
    worked,
)
from tests.airbrx.test_iris_workspace_fixtures import (  # noqa: E402
    answer as answer_item,
)
from tests.airbrx.test_iris_workspace_fixtures import (  # noqa: E402
    user as user_item,
)


def _state(monkeypatch, tmp_path, session):
    response = make_client(monkeypatch, tmp_path, session).get(f"{API}/state")
    assert response.status_code == 200, response.text
    return response.json()


def _refresh(monkeypatch, tmp_path, session):
    return make_client(monkeypatch, tmp_path, session).post(f"{API}/refresh", json={})


def test_captured_at_is_the_overview_items_clock(monkeypatch, tmp_path):
    body = _state(monkeypatch, tmp_path, IrisSession(captured(), FILES))
    assert body["captured_at"] == READ_AT
    # The same clock cache_age_seconds is measured on.
    assert body["cache_age_seconds"] == NOW - body["captured_at"]


def test_investigation_and_proposal_are_returned_whole(monkeypatch, tmp_path):
    body = _state(monkeypatch, tmp_path, IrisSession(worked(), FILES))
    assert body["investigation"] == FILES[INVESTIGATE_ID]
    assert set(body["investigation"]) >= {"current", "previous", "findings", "evidence"}
    assert body["proposal"] == FILES[PROPOSE_ID]
    assert body["proposal"]["proposal_status"] == "validated"
    assert body["proposal"]["proposal_view"]["rule_id"] == "report-cache"


def test_the_newest_investigation_and_proposal_win(monkeypatch, tmp_path):
    newer_investigation = {**investigate_report(), "message": "the newer comparison"}
    newer_proposal = {**propose_report(), "proposal_status": "invalid"}
    files = {**FILES, "inv-2": newer_investigation, "prop-2": newer_proposal}
    items = [
        *worked(),
        *tool_run("iris_investigate", "inv-2", READ_AT + 20, "inv2"),
        *tool_run("iris_propose", "prop-2", READ_AT + 21, "prop2"),
    ]
    body = _state(monkeypatch, tmp_path, IrisSession(items, files))
    assert body["investigation"]["message"] == "the newer comparison"
    assert body["proposal"]["proposal_status"] == "invalid"
    assert body["report_times"]["iris_investigate"] == READ_AT + 20
    assert body["report_times"]["iris_propose"] == READ_AT + 21


def test_absent_investigation_and_proposal_are_null(monkeypatch, tmp_path):
    body = _state(monkeypatch, tmp_path, IrisSession(captured(), FILES))
    assert body["investigation"] is None
    assert body["proposal"] is None


def test_report_times_names_every_tool_and_null_when_absent(monkeypatch, tmp_path):
    body = _state(monkeypatch, tmp_path, IrisSession(captured(), FILES))
    assert body["report_times"] == {
        "iris_audit": READ_AT + 2,
        "iris_investigate": None,
        "iris_overview": READ_AT,
        "iris_propose": None,
    }


def test_a_refresh_keeps_the_investigation_and_the_proposal(monkeypatch, tmp_path):
    """A refresh never calls investigate or propose, so bounding them would blank both tabs."""
    response = _refresh(monkeypatch, tmp_path, IrisSession(worked(), FILES, respond))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["investigation"] == FILES[INVESTIGATE_ID]
    assert body["proposal"] == FILES[PROPOSE_ID]
    # Each says how old it is; the overview is this turn's.
    assert body["report_times"]["iris_investigate"] == READ_AT + 8
    assert body["report_times"]["iris_propose"] == READ_AT + 10
    assert body["captured_at"] == NOW - 1
    assert body["report_times"]["iris_overview"] == NOW - 1


def test_a_refresh_still_bounds_the_overview_and_audit(monkeypatch, tmp_path):
    """Only this turn's overview and audit count; an older audit is not passed off as fresh."""

    def overview_only(text, at):
        return [
            *tool_run("iris_overview", FRESH_OVERVIEW_ID, at, "fresh-o"),
            answer_item("Overview only.", at, "a-o"),
        ]

    response = _refresh(monkeypatch, tmp_path, IrisSession(captured(), FILES, overview_only))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["audit"] == {"findings": []}
    assert body["report_times"]["iris_audit"] is None
    assert body["captured_at"] == NOW - 1


def test_a_refresh_that_read_nothing_is_still_a_409(monkeypatch, tmp_path):
    """An investigation or proposal in the session does not stand in for an overview."""

    response = _refresh(monkeypatch, tmp_path, IrisSession(worked(), FILES, read_nothing))
    assert response.status_code == 409


@pytest.mark.parametrize("tool", ["iris_investigate", "iris_propose"])
@pytest.mark.parametrize("path", ["state", "refresh"])
def test_a_cross_tenant_investigation_or_proposal_is_a_403(monkeypatch, tmp_path, tool, path):
    foreign = {**FILES[INVESTIGATE_ID], "tenant_id": "someone-else"}
    items = [*worked(), *tool_run(tool, "foreign", READ_AT + 20, "foreign")]
    session = IrisSession(items, {**FILES, "foreign": foreign}, respond)
    client = make_client(monkeypatch, tmp_path, session)
    response = (
        client.get(f"{API}/state") if path == "state" else client.post(f"{API}/refresh", json={})
    )
    assert response.status_code == 403
    assert "someone-else" not in response.text


def test_the_refresh_prompt_keeps_its_first_sentence(monkeypatch, tmp_path):
    """W3's history reload recognises a refresh turn by this sentence, byte for byte."""
    session = IrisSession(captured(), FILES, respond)
    assert _refresh(monkeypatch, tmp_path, session).status_code == 200
    (posted,) = [e for e in session.posted if e["type"] == "message"]
    text = posted["data"]["content"][0]["text"]
    assert text.startswith("Call iris_overview and iris_audit for the selected tenant. ")


def test_existing_state_keys_are_unchanged(monkeypatch, tmp_path):
    body = _state(monkeypatch, tmp_path, IrisSession(captured(), FILES))
    assert body["overview"] == FILES[OVERVIEW_ID]
    assert body["audit"] == FILES[AUDIT_ID]
    assert body["rules"] == [
        {"ruleId": "report-cache", "cacheHits": 80, "cacheMisses": 20, "totalExecutions": 100}
    ]
    assert body["rule_effectiveness_meta"] == {
        "year": None,
        "generatedAt": None,
        "totalQueries": None,
    }
    assert body["stale"] is False
    assert body["monitoring"] is None


# --- Assets (WORKSPACE_V2.md, D1, section 2 "Assets" and section 7 step 5) --------------
#
# v2 is the only workspace since the cutover (W5). `OMNIGENT_IRIS_UI` is no
# longer read: nothing it is set to brings back the pinned UI or `host.js`.

UI = f"/v1/iris/sessions/{SESSION}/ui"


@pytest.fixture
def v2_ui(tmp_path, monkeypatch):
    """A stand-in iris/ui/ tree (W3 writes the real one) and a stand-in kernel (W2)."""
    import sys
    import types

    root = tmp_path / "iris-ui"
    (root / "views").mkdir(parents=True)
    (root / "index.html").write_text(
        '<!doctype html><script src="kernel/dom.js"></script><script src="app.js"></script>'
    )
    (root / "app.js").write_text("// v2 app\n")
    (root / "style.css").write_text("/* v2 */\n")
    (root / "views" / "overview.js").write_text("// overview view\n")
    (root / "secret.txt").write_text("not an app file\n")
    # Files a partial match on the app names would serve.
    (root / "app.jsx").write_text("// not the app\n")
    (root / "views" / "overview.js.bak").write_text("// a backup\n")
    (root / "old").mkdir()
    (root / "old" / "app.js").write_text("// an old copy\n")
    # The names that must 404 are on disk, so a looser allowlist would serve them.
    (root / "host.js").write_text("// the retired pinned adapter\n")
    (root / "theme.js").write_text("// the pinned app's theme\n")
    (root / "iris-state.json").write_text('{"captured": "tenant evidence"}\n')
    (root / "demo-state.json").write_text('{"demo": "synthetic report"}\n')
    (root / "assets").mkdir()
    (root / "assets" / "PROVENANCE.md").write_text("not served\n")
    # The rules live in `ui_assets`, shared with the standalone viewer, so the
    # stand-in root is patched there: both hosts then serve it.
    from omnigent.airbrx.iris import ui_assets

    monkeypatch.setattr(ui_assets, "UI_ROOT", root)

    kernel = tmp_path / "kernel"
    kernel.mkdir()
    (kernel / "dom.js").write_text("// kernel dom\n")
    (kernel / "unlisted.js").write_text("// not in the allowlist\n")
    assets = types.ModuleType("omnigent.airbrx.workspace.assets")
    assets.KERNEL_ASSETS = frozenset({"dom.js", "missing.js"})
    assets.kernel_asset = lambda name: kernel / name
    package = types.ModuleType("omnigent.airbrx.workspace")
    package.assets = assets
    monkeypatch.setitem(sys.modules, "omnigent.airbrx.workspace", package)
    monkeypatch.setitem(sys.modules, "omnigent.airbrx.workspace.assets", assets)
    # v2 is the default: nothing opts in to it.
    monkeypatch.delenv("OMNIGENT_IRIS_UI", raising=False)
    return root


def _ui_client(monkeypatch, tmp_path):
    return make_client(monkeypatch, tmp_path, IrisSession(captured(), FILES))


def test_v2_serves_the_new_index_without_host_js(v2_ui, monkeypatch, tmp_path):
    for path in (f"{UI}/", f"{UI}/index.html"):
        response = _ui_client(monkeypatch, tmp_path).get(path)
        assert response.status_code == 200
        assert response.text == (v2_ui / "index.html").read_text()
        assert "host.js" not in response.text
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-frame-options"] == "SAMEORIGIN"
        assert response.headers["content-type"].startswith("text/html")


@pytest.mark.parametrize("name", ["app.js", "style.css", "views/overview.js"])
def test_v2_serves_the_app_files_no_store(v2_ui, monkeypatch, tmp_path, name):
    response = _ui_client(monkeypatch, tmp_path).get(f"{UI}/{name}")
    assert response.status_code == 200
    assert response.text == (v2_ui / name).read_text()
    assert response.headers["cache-control"] == "no-store"


def test_v2_serves_allowlisted_kernel_files_no_store(v2_ui, monkeypatch, tmp_path):
    response = _ui_client(monkeypatch, tmp_path).get(f"{UI}/kernel/dom.js")
    assert response.status_code == 200
    assert response.text == "// kernel dom\n"
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("name", ["iris-portrait.png", "airbrx-logo.png"])
def test_v2_serves_the_pinned_images(v2_ui, monkeypatch, tmp_path, name):
    response = _ui_client(monkeypatch, tmp_path).get(f"{UI}/assets/{name}")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, max-age=3600"
    assert response.content[:4] == b"\x89PNG"


@pytest.mark.parametrize(
    "name",
    [
        # Honesty rule 1: no synthetic or captured data, under v2 either.
        "iris-state.json",
        "demo-state.json",
        # v2 does not serve or inject the pinned adapter, or the pinned app.
        "host.js",
        "theme.js",
        "assets/PROVENANCE.md",
        # Only the patterns are served, not whatever sits in the directory.
        "secret.txt",
        # The app names are matched whole: a prefix or substring match serves these.
        "app.jsx",
        "views/overview.js.bak",
        "old/app.js",
        # Encoded, so the client does not normalise the dot segment away.
        "views/..%2Fsecret.txt",
        "views/..%2F..%2Fsecret.txt",
        "views/nested/overview.js",
        "views/overview.css",
        # The kernel's own allowlist, and a listed file that is not on disk.
        "kernel/unlisted.js",
        "kernel/missing.js",
        "kernel/..%2Fapp.js",
        "kernel/..%2F..%2Fsecret.txt",
        "kernel/",
    ],
)
def test_v2_serves_nothing_else(v2_ui, monkeypatch, tmp_path, name):
    assert _ui_client(monkeypatch, tmp_path).get(f"{UI}/{name}").status_code == 404


def test_v2_without_the_kernel_package_404s_kernel_files(v2_ui, monkeypatch, tmp_path):
    import sys

    monkeypatch.setitem(sys.modules, "omnigent.airbrx.workspace.assets", None)
    assert _ui_client(monkeypatch, tmp_path).get(f"{UI}/kernel/dom.js").status_code == 404


@pytest.mark.parametrize("value", [None, "", "v2", "V2", "v1", "pinned", "1", " v2"])
def test_v2_is_served_whatever_the_retired_switch_says(v2_ui, monkeypatch, tmp_path, value):
    """The cutover: `/iris` is v2 with no env var, and the old switch cannot undo it.

    `OMNIGENT_IRIS_UI=v1` (or anything else) used to serve the pinned UI with
    `host.js` injected. `host.js` is deleted, and the pinned app without it
    falls back to synthetic data, so there is no v1 left to serve. Rolling back
    is reverting the cutover commit, not setting a variable (docs/iris/RUNBOOK.md).
    """
    if value is None:
        monkeypatch.delenv("OMNIGENT_IRIS_UI", raising=False)
    else:
        monkeypatch.setenv("OMNIGENT_IRIS_UI", value)
    client = _ui_client(monkeypatch, tmp_path)
    for path in (f"{UI}/", f"{UI}/index.html"):
        index = client.get(path)
        assert index.status_code == 200
        assert index.text == (v2_ui / "index.html").read_text()
        assert "host.js" not in index.text
    assert client.get(f"{UI}/app.js").text == "// v2 app\n"
    assert client.get(f"{UI}/kernel/dom.js").status_code == 200
    for name in ("host.js", "theme.js", "iris-state.json", "demo-state.json"):
        assert client.get(f"{UI}/{name}").status_code == 404, name


def test_the_pinned_adapter_is_gone_from_the_package():
    assert not (iris_routes.HERE / "host.js").exists()
    assert not hasattr(iris_routes, "ui_version")


def test_assets_still_require_an_iris_session(v2_ui, monkeypatch, tmp_path):
    client = _ui_client(monkeypatch, tmp_path)
    monkeypatch.setattr(iris_routes, "require_user", lambda request, provider: None)
    assert client.get(f"{UI}/index.html").status_code == 401


@pytest.mark.parametrize(
    ("package", "relpath"),
    [
        ("omnigent.airbrx.iris", "ui/index.html"),
        ("omnigent.airbrx.iris", "ui/app.js"),
        ("omnigent.airbrx.iris", "ui/views/overview.js"),
        ("omnigent.airbrx.workspace", "ui/dom.js"),
        ("omnigent.airbrx.workspace", "ui/brand.css"),
    ],
)
def test_the_v2_ui_and_kernel_are_declared_package_data(package, relpath):
    """Declared before the files exist, so W2 and W3 land into a wheel that already ships them.

    test_airbrx_package_data.py checks the files on disk, which cannot see
    `ui/` before it is written; this checks the declaration itself.
    """
    from tests.airbrx.test_airbrx_package_data import _declared_globs, _matches

    assert _matches(_declared_globs().get(package, []), relpath)


class _Unreadable(dict):
    """Report files where any id not listed makes the native file route answer 500."""

    def __missing__(self, file_id):
        raise HTTPException(500, "file store unavailable")


@pytest.mark.parametrize("tool", ["iris_investigate", "iris_propose"])
@pytest.mark.parametrize("path", ["state", "refresh"])
def test_an_unreadable_investigation_or_proposal_leaves_the_rest_of_state(
    monkeypatch, tmp_path, tool, path
):
    """One failed fetch of an optional report nulls that report, not the whole read."""
    items = [*worked(), *tool_run(tool, "gone", READ_AT + 20, "gone")]
    session = IrisSession(items, FILES, respond)
    session.files = _Unreadable(FILES)
    client = make_client(monkeypatch, tmp_path, session)
    response = (
        client.get(f"{API}/state") if path == "state" else client.post(f"{API}/refresh", json={})
    )
    assert response.status_code == 200, response.text
    body = response.json()
    key = "investigation" if tool == "iris_investigate" else "proposal"
    other = "proposal" if key == "investigation" else "investigation"
    assert body[key] is None
    assert body["report_times"][tool] is None
    assert body[other] is not None
    assert body["overview"] == FILES[FRESH_OVERVIEW_ID if path == "refresh" else OVERVIEW_ID]
    assert body["audit"]["findings"]


def test_an_unreadable_overview_still_fails_the_read(monkeypatch, tmp_path):
    """Only the optional reports are isolated; the capture itself is not papered over."""
    session = IrisSession(captured(), FILES)
    session.files = _Unreadable({k: v for k, v in FILES.items() if k != OVERVIEW_ID})
    client = make_client(monkeypatch, tmp_path, session)
    assert client.get(f"{API}/state").status_code >= 400


def test_readiness_names_the_automatic_first_collect_not_an_import(monkeypatch, tmp_path):
    """The workspace shows this line verbatim; Iris collects, she does not import (iris #29)."""
    response = make_client(monkeypatch, tmp_path, IrisSession()).get(f"{API}/readiness")
    assert response.status_code == 200
    body = response.json()
    assert body["turn_completed_here"] is False
    assert body["unverified"] == [
        "no model turn has completed in this session yet, so whether the execution host "
        "can reach the model is unknown. The first time this workspace opens, it collects "
        "a fresh overview from the host automatically, and that runs a model turn. Asking "
        "Iris a question runs one too. This line goes away once a turn has completed."
    ]
    assert "import" not in " ".join(body["unverified"]).lower()


def test_readiness_has_no_unverified_line_once_a_turn_completed(monkeypatch, tmp_path):
    body = make_client(monkeypatch, tmp_path, IrisSession(captured(), FILES)).get(
        f"{API}/readiness"
    )
    assert body.json()["unverified"] == []


# --- A page reload is not a Stop (WORKSPACE_V2.md, section 7 gate: "a reload mid-chat") --
#
# A reload aborts the page's in-flight `api/chat` fetch, so the server sees the
# chat request's client disconnect. The turn itself runs in the native session,
# and the reloaded page reads its answer back from history. Only the explicit
# Stop (`api/cancel`) may interrupt it.

import asyncio  # noqa: E402


def _drive_chat_then_disconnect(app, session, *, answer_after: float):
    """Send one chat request over raw ASGI whose client goes away as soon as it is sent.

    Every `receive()` after the body answers `http.disconnect`, which is what the
    server is handed when a browser reload aborts the fetch. `answer_after`
    seconds later the native session records Iris's answer, as a turn that
    carried on would.
    """
    body = json.dumps(
        {"history": [{"role": "user", "content": "Why did the hit rate drop?"}], "deadline": 30}
    ).encode()
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": f"{API}/chat",
        "raw_path": f"{API}/chat".encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"testserver"), (b"content-type", b"application/json")],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }
    sent_body = False
    messages = []

    async def receive():
        nonlocal sent_body
        if not sent_body:
            sent_body = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)

    async def answer_later():
        await asyncio.sleep(answer_after)
        session.items.append(answer_item("The hit rate fell on Tuesday.", NOW, "a-late"))

    async def main():
        later = asyncio.create_task(answer_later())
        try:
            await asyncio.wait_for(app(scope, receive, send), timeout=20)
        except asyncio.CancelledError:
            # What the old adapter raised on a disconnect; the assertions say why it fails.
            pass
        finally:
            later.cancel()

    asyncio.run(main())
    return messages


def test_a_reload_mid_turn_does_not_interrupt_the_turn(monkeypatch, tmp_path):
    session = IrisSession(captured(), FILES, lambda text, at: [])
    client = make_client(monkeypatch, tmp_path, session)
    _drive_chat_then_disconnect(client.app, session, answer_after=1.2)
    kinds = [e["type"] for e in session.posted]
    assert kinds == ["message"], f"a reload interrupted Iris's turn: native events {kinds}"
    # The turn ran to its answer, which the reloaded page reads back from history.
    assert any(i.get("id") == "a-late" for i in session.items)


def test_stop_still_interrupts_the_turn(monkeypatch, tmp_path):
    session = IrisSession(captured(), FILES)
    response = make_client(monkeypatch, tmp_path, session).post(f"{API}/cancel", json={})
    assert response.status_code == 200, response.text
    assert [e["type"] for e in session.posted] == ["interrupt"]


def test_a_turn_past_its_deadline_is_still_interrupted(monkeypatch, tmp_path):
    """The adapter's own deadline is unchanged: it cancels the turn and says so."""
    session = IrisSession(captured(), FILES, lambda text, at: [])
    response = make_client(monkeypatch, tmp_path, session).post(
        f"{API}/chat",
        json={"history": [{"role": "user", "content": "Still there?"}], "deadline": 1},
    )
    assert response.status_code == 504
    assert [e["type"] for e in session.posted] == ["message", "interrupt"]


# --- `overview` is the tenant's current period, not the newest window read ----------------
#
# iris_overview takes optional start_date/end_date. Called without them it reads
# the tenant's current period: the seven completed UTC days before the day it
# runs. A mid-session question ("how did the week before compare?") makes Iris
# call it again with an older window, and that report is newer than the capture.
# Picking the newest report whatever its window put the comparison window in the
# Overview tab after a reload.

from tests.airbrx.test_iris_workspace_fixtures import metrics, summary_rows  # noqa: E402

COMPARISON_ID = "file-overview-comparison"
PARTIAL_ID = "file-overview-partial"


def _with_arguments(run, arguments):
    for item in run:
        if item["type"] == "function_call":
            item["arguments"] = json.dumps(arguments)
    return run


def _comparison_overview():
    rows = summary_rows(12, "comparison")
    report = FILES[OVERVIEW_ID]
    return {
        **report,
        "message": "the comparison window",
        "evidence": rows,
        "metrics": metrics("2026-09-12", "2026-09-19", rows),
    }


def _partial_overview():
    """3 of 7 days covered: the capture is incomplete, and it is still the current period."""
    rows = summary_rows(24, "partial")[:3]
    return {
        **FILES[OVERVIEW_ID],
        "message": "the partial current capture",
        "incomplete": True,
        "evidence": rows,
        "metrics": {
            **metrics("2026-09-24", "2026-09-27", rows),
            "covered_days": 3,
            "requested_days": 7,
            "period_complete": False,
        },
    }


def _compared(at=READ_AT + 20):
    """After the capture, Iris read an older window to compare against."""
    return [
        user_item("How did the week before compare?", at - 2, "u-compare"),
        *_with_arguments(
            tool_run("iris_overview", COMPARISON_ID, at, "compare"),
            {"start_date": "2026-09-12", "end_date": "2026-09-19"},
        ),
        answer_item("The week before was similar.", at + 2, "a-compare"),
    ]


def _files(**extra):
    return {
        **FILES,
        COMPARISON_ID: _comparison_overview(),
        PARTIAL_ID: _partial_overview(),
        **extra,
    }


def test_a_newer_comparison_window_does_not_become_the_overview(monkeypatch, tmp_path):
    body = _state(monkeypatch, tmp_path, IrisSession([*captured(), *_compared()], _files()))
    assert body["overview"] == FILES[OVERVIEW_ID]
    # Everything measured on the overview follows the one chosen.
    assert body["captured_at"] == READ_AT
    assert body["report_times"]["iris_overview"] == READ_AT
    assert body["cache_age_seconds"] == NOW - READ_AT


def test_a_partial_current_capture_is_still_the_current_period(monkeypatch, tmp_path):
    items = [
        *captured(),
        *tool_run("iris_overview", PARTIAL_ID, READ_AT + 10, "partial"),
        *_compared(READ_AT + 20),
    ]
    body = _state(monkeypatch, tmp_path, IrisSession(items, _files()))
    assert body["overview"]["message"] == "the partial current capture"
    assert body["overview"]["metrics"]["period_complete"] is False
    assert body["report_times"]["iris_overview"] == READ_AT + 10


def test_explicit_dates_naming_the_current_period_are_the_current_period(monkeypatch, tmp_path):
    # READ_AT is 2026-09-27T00:21Z: the current period is 09-20 up to 09-27, end exclusive.
    items = [
        *captured(),
        *_compared(READ_AT + 20),
        *_with_arguments(
            tool_run("iris_overview", PARTIAL_ID, READ_AT + 30, "explicit"),
            {"start_date": "2026-09-20", "end_date": "2026-09-27"},
        ),
    ]
    body = _state(monkeypatch, tmp_path, IrisSession(items, _files()))
    assert body["overview"]["message"] == "the partial current capture"
    assert body["captured_at"] == READ_AT + 30


def test_only_a_comparison_window_is_no_current_overview(monkeypatch, tmp_path):
    """The app auto-collects on a 409, which reads the current period."""
    session = IrisSession(_compared(), _files())
    response = make_client(monkeypatch, tmp_path, session).get(f"{API}/state")
    assert response.status_code == 409
    assert "the comparison window" not in response.text


def test_a_refresh_answers_with_its_current_period_not_its_comparison(monkeypatch, tmp_path):
    def refresh_then_compare(text, at):
        return [
            *tool_run("iris_overview", FRESH_OVERVIEW_ID, at, "fresh-o"),
            *tool_run("iris_audit", AUDIT_ID, at, "fresh-a"),
            *_with_arguments(
                tool_run("iris_overview", COMPARISON_ID, at + 0.5, "fresh-c"),
                {"start_date": "2026-09-12", "end_date": "2026-09-19"},
            ),
            answer_item("Read both.", at + 1, "a-both"),
        ]

    session = IrisSession(captured(), _files(), refresh_then_compare)
    response = make_client(monkeypatch, tmp_path, session).post(f"{API}/refresh", json={})
    assert response.status_code == 200, response.text
    assert response.json()["overview"] == FILES[FRESH_OVERVIEW_ID]
    assert response.json()["captured_at"] == NOW - 1


def test_report_calls_carries_each_calls_arguments():
    from omnigent.airbrx.iris.records import report_calls

    items = [
        *tool_run("iris_overview", "default", 10, "d"),
        *_with_arguments(
            tool_run("iris_overview", "dated", 11, "w"),
            {"start_date": "2026-09-12", "end_date": "2026-09-19"},
        ),
        *tool_run("iris_overview", "garbled", 12, "g"),
    ]
    items[-2]["arguments"] = "{not json"
    assert [(file_id, arguments) for _, file_id, _, arguments, _ in report_calls(items)] == [
        ("default", {}),
        ("dated", {"start_date": "2026-09-12", "end_date": "2026-09-19"}),
        ("garbled", None),
    ]


# --- Follow-ups to #115 (review nits 1a, 2a, 2b, 2c; W3 busy 409) -------------------------

#: 2026-09-27T00:00:00Z. READ_AT is 00:21 the same day.
MIDNIGHT = READ_AT - 21 * 60


def _straddling(window, call_at, output_at, file_id=OVERVIEW_ID, call_id="straddle"):
    """An iris_overview call recorded at `call_at` whose output is recorded at `output_at`."""
    run = _with_arguments(tool_run("iris_overview", file_id, output_at, call_id), window)
    run[0]["created_at"] = call_at
    return run


def test_a_call_made_before_midnight_and_answered_after_is_current(monkeypatch, tmp_path):
    """Called 09-26T23:59:58Z for 09-19..09-26 (that day's current period), answered 00:00:04."""
    items = [
        user_item("Read the tenant.", MIDNIGHT - 10, "u-straddle"),
        *_straddling(
            {"start_date": "2026-09-19", "end_date": "2026-09-26"}, MIDNIGHT - 2, MIDNIGHT + 4
        ),
    ]
    body = _state(monkeypatch, tmp_path, IrisSession(items, FILES))
    assert body["overview"] == FILES[OVERVIEW_ID]
    assert body["captured_at"] == MIDNIGHT + 4


def test_the_window_is_decided_by_when_the_call_was_made_not_answered(monkeypatch, tmp_path):
    """Called on 09-26 for 09-20..09-27, the NEXT day's window, though answered on 09-27."""
    items = [
        *captured(MIDNIGHT - 600),
        *_straddling(
            {"start_date": "2026-09-20", "end_date": "2026-09-27"},
            MIDNIGHT - 2,
            MIDNIGHT + 4,
            file_id=PARTIAL_ID,
        ),
    ]
    body = _state(monkeypatch, tmp_path, IrisSession(items, _files()))
    assert body["overview"] == FILES[OVERVIEW_ID]
    assert body["captured_at"] == MIDNIGHT - 600


def test_unreadable_recorded_arguments_are_not_the_current_period(monkeypatch, tmp_path):
    """A newer overview whose arguments cannot be read is not assumed to be the default."""
    garbled = tool_run("iris_overview", PARTIAL_ID, READ_AT + 10, "garbled")
    garbled[0]["arguments"] = "{not json"
    body = _state(monkeypatch, tmp_path, IrisSession([*captured(), *garbled], _files()))
    assert body["overview"] == FILES[OVERVIEW_ID]
    assert body["captured_at"] == READ_AT


def test_only_unreadable_arguments_is_no_current_overview(monkeypatch, tmp_path):
    garbled = tool_run("iris_overview", PARTIAL_ID, READ_AT, "garbled")
    garbled[0]["arguments"] = "[1, 2]"
    session = IrisSession(garbled, _files())
    response = make_client(monkeypatch, tmp_path, session).get(f"{API}/state")
    assert response.status_code == 409
    assert "the partial current capture" not in response.text


def test_a_refresh_that_produced_nothing_says_so_despite_an_older_comparison(
    monkeypatch, tmp_path
):
    """The comparison was read before this turn; this turn read nothing at all."""
    session = IrisSession(_compared(), _files(), read_nothing)
    response = make_client(monkeypatch, tmp_path, session).post(f"{API}/refresh", json={})
    assert response.status_code == 409
    assert "produced no overview" in response.json()["detail"]


def test_a_refresh_that_read_only_a_comparison_window_says_that(monkeypatch, tmp_path):
    def compare_only(text, at):
        return [
            *_with_arguments(
                tool_run("iris_overview", COMPARISON_ID, at, "fresh-c"),
                {"start_date": "2026-09-12", "end_date": "2026-09-19"},
            ),
            answer_item("Read the week before.", at + 1, "a-cmp"),
        ]

    session = IrisSession(captured(), _files(), compare_only)
    response = make_client(monkeypatch, tmp_path, session).post(f"{API}/refresh", json={})
    assert response.status_code == 409
    assert "read no current-period overview" in response.json()["detail"]


def test_a_refresh_while_a_turn_runs_is_a_distinguishable_busy_409(monkeypatch, tmp_path):
    """A reload during the first auto-collect: the orphan turn still runs.

    The kernel's api() surfaces only `status` and the `detail` string, so the
    busy answer is told apart by its detail, which is pinned here and differs
    from both "no overview" answers.
    """
    session = IrisSession(captured(), FILES, respond, status="running")
    response = make_client(monkeypatch, tmp_path, session).post(f"{API}/refresh", json={})
    assert response.status_code == 409
    assert response.json() == {"detail": iris_routes.BUSY_DETAIL}
    assert "no overview" not in iris_routes.BUSY_DETAIL
    assert "current-period" not in iris_routes.BUSY_DETAIL
    assert session.posted == []


def test_a_cancelled_handler_does_not_interrupt_the_turn(monkeypatch, tmp_path):
    """Cancellation is teardown (a disconnect on a stack that cancels, or shutdown), not a Stop.

    The reload test above never cancels the handler, so on its own it leaves the
    `CancelledError` half of the old interrupt untested (review nit 1a).
    """
    session = IrisSession(captured(), FILES, lambda text, at: [])
    client = make_client(monkeypatch, tmp_path, session)
    body = json.dumps(
        {"history": [{"role": "user", "content": "Why did the hit rate drop?"}], "deadline": 30}
    ).encode()
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": f"{API}/chat",
        "raw_path": f"{API}/chat".encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"testserver"), (b"content-type", b"application/json")],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }
    sent_body = False

    async def receive():
        nonlocal sent_body
        if not sent_body:
            sent_body = True
            return {"type": "http.request", "body": body, "more_body": False}
        await asyncio.Event().wait()  # the client never goes away on its own
        return {"type": "http.disconnect"}

    async def send(message):
        pass

    async def main():
        handler = asyncio.create_task(client.app(scope, receive, send))
        # Until the turn is posted and the watch has polled at least once.
        for _ in range(100):
            if session.posted:
                break
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.7)
        handler.cancel()
        with pytest.raises(asyncio.CancelledError):
            await handler
        await asyncio.sleep(0.1)
        return [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]

    leftover = asyncio.run(main())
    kinds = [e["type"] for e in session.posted]
    assert kinds == ["message"], f"a cancelled handler interrupted Iris's turn: {kinds}"
    assert leftover == []


def test_report_calls_carries_when_each_call_was_made():
    """`called_at` is the function_call item's clock; `created_at` stays the output's."""
    from omnigent.airbrx.iris.records import report_calls

    straddle = _straddling({}, MIDNIGHT - 2, MIDNIGHT + 4)
    unstamped = tool_run("iris_overview", "unstamped", 50, "u")
    del unstamped[0]["created_at"]
    assert [
        (file_id, created_at, called_at)
        for _, file_id, created_at, _, called_at in report_calls([*unstamped, *straddle])
    ] == [("unstamped", 50, 50), (OVERVIEW_ID, MIDNIGHT + 4, MIDNIGHT - 2)]


# --- N1: the current period does not depend on the host's time zone --------------------
#
# QA re-walk, 2026-09-26 (N1). From 17:00 Pacific to midnight the host's local
# date is a day behind the UTC date. Asked for "the last 7 days", Iris worked the
# dates out from the local date the harness gives her (09-19..09-26), while the
# tool and the adapter both count UTC days (09-20..09-27). The first collect was
# refused as "not the current period", and every page open collected again until
# the session's tool budget was gone.
#
# The fix is on the prompt side: the refresh turn tells Iris to call
# iris_overview with no dates, so the tool resolves the current period on its own
# UTC clock and the adapter recognises the call without comparing clocks at all.

import time as _time  # noqa: E402
from datetime import date, datetime, timedelta  # noqa: E402

from tests.airbrx import test_iris_workspace_fixtures as fixtures  # noqa: E402
from tests.airbrx.test_iris_workspace_fixtures import FRESH_AUDIT_ID  # noqa: E402

#: 2026-09-27T00:30:00Z, which is 17:30 on 2026-09-26 in America/Los_Angeles (PDT).
PACIFIC_1730 = READ_AT + 9 * 60
#: How the refresh prompt tells Iris to leave the window to the tool.
LEAVE_DATES_OUT = "leave out start_date and end_date"


@pytest.fixture
def pacific_1730(monkeypatch):
    """The execution host runs in America/Los_Angeles, and it is 17:30 there."""
    monkeypatch.setenv("TZ", "America/Los_Angeles")
    _time.tzset()
    # The fixture harness reads NOW at call time: the handlers' clock and the
    # time a posted turn is recorded at both move with it.
    monkeypatch.setattr(fixtures, "NOW", PACIFIC_1730)
    # The host-local date, on purpose: that is the clock this test is about.
    assert datetime.fromtimestamp(PACIFIC_1730).date() == date(2026, 9, 26)  # noqa: DTZ006
    yield
    monkeypatch.undo()
    _time.tzset()


def _local_week(at):
    """'The last 7 days' by the host's local date, as Iris worked it out in QA."""
    today = datetime.fromtimestamp(at).date()  # noqa: DTZ006 (the host-local date)
    return {"start_date": str(today - timedelta(days=7)), "end_date": str(today)}


def iris_as_observed(text, at):
    """A refresh turn as Iris ran it in the QA re-walk.

    Told to leave the dates out, she calls iris_overview without them. Otherwise
    she passes the last seven days by the host's local date (session 0fc1cd66:
    `{"start_date":"2026-09-19","end_date":"2026-09-26"}` at 20:47 PDT).
    """
    arguments = {} if LEAVE_DATES_OUT in text else _local_week(at)
    return [
        *_with_arguments(tool_run("iris_overview", FRESH_OVERVIEW_ID, at, "fresh-o"), arguments),
        *tool_run("iris_audit", FRESH_AUDIT_ID, at, "fresh-a"),
        answer_item("Hit rate 80.0% over 700 requests.", at, "a-fresh"),
    ]


def test_the_refresh_prompt_leaves_the_window_to_the_tool(monkeypatch, tmp_path):
    session = IrisSession(captured(), FILES, respond)
    assert _refresh(monkeypatch, tmp_path, session).status_code == 200
    (posted,) = [e for e in session.posted if e["type"] == "message"]
    text = posted["data"]["content"][0]["text"]
    assert text.startswith("Call iris_overview and iris_audit for the selected tenant. ")
    assert LEAVE_DATES_OUT in text
    assert text == iris_routes.REFRESH_PROMPT


def test_the_first_collect_at_1730_pacific_is_the_current_period(
    pacific_1730, monkeypatch, tmp_path
):
    """The N1 repro: a new session's first open, on a host at 17:30 PDT."""
    session = IrisSession([], _files(), iris_as_observed)
    client = make_client(monkeypatch, tmp_path, session)
    assert client.get(f"{API}/state").status_code == 409  # nothing collected yet
    response = client.post(f"{API}/refresh", json={})
    assert response.status_code == 200, response.text
    assert response.json()["overview"] == FILES[FRESH_OVERVIEW_ID]
    assert response.json()["captured_at"] == PACIFIC_1730 - 1
    # The next page open reads it back instead of collecting again.
    assert client.get(f"{API}/state").status_code == 200
    assert len([e for e in session.posted if e["type"] == "message"]) == 1


def _narrowed_partial():
    """3 of 7 days, as the tool returns it: metric dates narrowed to the days covered."""
    rows = summary_rows(20, "narrowed")[:3]
    return {
        **FILES[OVERVIEW_ID],
        "message": "the narrowed partial capture",
        "incomplete": True,
        "evidence": rows,
        "metrics": {
            **metrics("2026-09-20", "2026-09-22", rows),
            "covered_days": 3,
            "requested_days": 7,
            "period_complete": False,
        },
    }


def test_a_narrowed_partial_capture_at_1730_pacific_is_current(
    pacific_1730, monkeypatch, tmp_path
):
    """Called without dates, the tool covered 09-20..09-22 only; a newer comparison follows."""
    items = [
        user_item(iris_routes.REFRESH_PROMPT, PACIFIC_1730 - 60, "u-partial"),
        *tool_run("iris_overview", PARTIAL_ID, PACIFIC_1730 - 50, "partial"),
        answer_item("3 of 7 days covered.", PACIFIC_1730 - 48, "a-partial"),
        *_compared(PACIFIC_1730 - 20),
    ]
    files = _files(**{PARTIAL_ID: _narrowed_partial()})
    body = _state(monkeypatch, tmp_path, IrisSession(items, files))
    assert body["overview"]["message"] == "the narrowed partial capture"
    assert body["overview"]["metrics"]["end_date"] == "2026-09-22"
    assert body["captured_at"] == PACIFIC_1730 - 50


def test_an_older_window_at_1730_pacific_is_still_not_the_overview(
    pacific_1730, monkeypatch, tmp_path
):
    """The week before, read with explicit dates, stays a comparison on any host clock."""
    session = IrisSession(_compared(PACIFIC_1730 - 20), _files())
    response = make_client(monkeypatch, tmp_path, session).get(f"{API}/state")
    assert response.status_code == 409
    assert "the comparison window" not in response.text


# --- N1, server side: a refresh right after one that missed does not run again -----------


def _missed_collect(at):
    """A refresh turn at `at` whose overview read a window that is not the current period."""
    return [
        user_item(iris_routes.REFRESH_PROMPT, at, "u-missed"),
        *_with_arguments(
            tool_run("iris_overview", COMPARISON_ID, at + 5, "missed"),
            {"start_date": "2026-09-12", "end_date": "2026-09-19"},
        ),
        answer_item("Read 09-12..09-19.", at + 8, "a-missed"),
    ]


def _turns(session):
    return [e for e in session.posted if e["type"] == "message"]


def test_page_opens_after_a_missed_collect_do_not_collect_again(monkeypatch, tmp_path):
    """Four page opens in QA made four collects and spent the session's 60-call budget."""

    def always_misses(text, at):
        return _missed_collect(at)[1:]

    session = IrisSession([], _files(), always_misses)
    client = make_client(monkeypatch, tmp_path, session)
    answers = []
    for _ in range(4):  # each open: state 409, then the first-open auto-collect
        assert client.get(f"{API}/state").status_code == 409
        answers.append(client.post(f"{API}/refresh", json={}))
    assert [a.status_code for a in answers] == [409, 409, 409, 409]
    assert len(_turns(session)) == 1
    # The clock is frozen, so each refusal comes the moment after the miss.
    assert answers[1].json() == {"detail": iris_routes.repeat_collect_detail(600)}
    assert "about 10 minutes" in answers[1].json()["detail"]
    assert "no overview" not in iris_routes.REPEAT_COLLECT_DETAIL
    assert iris_routes.REPEAT_COLLECT_DETAIL != iris_routes.BUSY_DETAIL


def test_a_refresh_right_after_a_collect_that_read_nothing_does_not_run(monkeypatch, tmp_path):
    items = [
        user_item(iris_routes.REFRESH_PROMPT, NOW - 120, "u-nothing"),
        answer_item("I could not reach the tenant's gateway.", NOW - 110, "a-nothing"),
    ]
    session = IrisSession(items, _files(), respond)
    response = _refresh(monkeypatch, tmp_path, session)
    assert response.status_code == 409
    assert response.json() == {"detail": iris_routes.repeat_collect_detail(480)}
    assert _turns(session) == []


def test_the_repeat_collect_refusal_says_collect_when_and_how_the_hold_lifts(
    monkeypatch, tmp_path
):
    """Review N1/N2 on #119: the page's buttons say "collect", and a wait needs a length."""
    session = IrisSession(_missed_collect(NOW - 120), _files(), respond)
    detail = _refresh(monkeypatch, tmp_path, session).json()["detail"]
    assert "about 8 minutes" in detail  # 600 s hold, missed 120 s ago
    assert "collect again" in detail.lower()
    assert "ask Iris anything in chat" in detail
    assert "refresh" not in detail.lower()
    # The page keeps "produced no overview" for these two prefixes only.
    assert not detail.startswith("The collection produced no overview")
    assert not detail.startswith("The collection read no current-period overview")
    assert _turns(session) == []


def test_the_repeat_collect_refusal_never_says_less_than_a_minute(monkeypatch, tmp_path):
    at = NOW - iris_routes.REPEAT_COLLECT_COOLDOWN_SECONDS + 5
    session = IrisSession(_missed_collect(at), _files(), respond)
    detail = _refresh(monkeypatch, tmp_path, session).json()["detail"]
    assert "about 1 minute," in detail


def test_a_refresh_after_the_cooldown_runs_again(monkeypatch, tmp_path):
    at = NOW - iris_routes.REPEAT_COLLECT_COOLDOWN_SECONDS - 1
    session = IrisSession(_missed_collect(at), _files(), respond)
    response = _refresh(monkeypatch, tmp_path, session)
    assert response.status_code == 200, response.text
    assert len(_turns(session)) == 1


def test_a_chat_turn_after_a_missed_collect_lets_the_next_refresh_run(monkeypatch, tmp_path):
    items = [
        *_missed_collect(NOW - 120),
        user_item("Why was that the wrong week?", NOW - 60, "u-why"),
        answer_item("I passed dates.", NOW - 55, "a-why"),
    ]
    session = IrisSession(items, _files(), respond)
    response = _refresh(monkeypatch, tmp_path, session)
    assert response.status_code == 200, response.text
    assert len(_turns(session)) == 1


def test_a_refresh_after_a_collect_that_found_the_current_period_still_runs(monkeypatch, tmp_path):
    """Refresh after a good capture 28 s ago is a real refresh, not a repeat."""
    session = IrisSession(captured(), FILES, respond)
    assert _refresh(monkeypatch, tmp_path, session).status_code == 200
    assert len(_turns(session)) == 1


def test_a_busy_session_after_a_missed_collect_still_answers_busy(monkeypatch, tmp_path):
    session = IrisSession(_missed_collect(NOW - 120), _files(), respond, status="running")
    response = _refresh(monkeypatch, tmp_path, session)
    assert response.status_code == 409
    assert response.json() == {"detail": iris_routes.BUSY_DETAIL}


# --- The host descriptor (docs/iris/STANDALONE_VIEWER.md, section 2.1) -------------------


def test_the_host_descriptor_names_omnigent_and_the_native_session_links(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path, IrisSession(captured(), FILES))
    response = client.get(f"{API}/host")
    assert response.status_code == 200, response.text
    assert response.json() == {
        "schema": 1,
        "host_label": "Omnigent",
        "identity": {"kind": "omnigent", "email": None, "sign_in": None},
        "agent": {"connected": True, "why": None},
        "data": {
            "source": "session",
            "scope_id": SESSION,
            "tenant_id": "fixture-iris",
            "week": None,
            "weeks": None,
        },
        "links": {
            "native_chat": f"/c/{SESSION}",
            "items": f"/v1/sessions/{SESSION}/items",
            "session": f"/v1/sessions/{SESSION}",
            "files": f"/v1/sessions/{SESSION}/resources/files",
            "catalog": "/v1/iris",
            "account": "/v1/iris/account",
            "tenant_home": None,
            "week_page": None,
        },
        "open_tenant": "postMessage",
    }


def test_the_host_descriptor_requires_an_iris_session(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path, IrisSession(captured(), FILES))
    monkeypatch.setattr(iris_routes, "require_user", lambda request, provider: None)
    assert client.get(f"{API}/host").status_code == 401


def test_the_host_descriptor_is_not_served_as_a_ui_asset(v2_ui, monkeypatch, tmp_path):
    """`ui/api/host` is the descriptor, not a 404 from the asset catch-all."""
    response = _ui_client(monkeypatch, tmp_path).get(f"{UI}/api/host")
    assert response.status_code == 200
    assert response.json()["host_label"] == "Omnigent"


def test_the_host_descriptor_stays_out_of_the_schema_and_carries_no_email(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path, IrisSession(captured(), FILES))
    paths = client.app.openapi()["paths"]
    assert not [p for p in paths if p.endswith("/api/host")], sorted(paths)
    body = client.get(f"{API}/host").json()
    assert body["identity"]["email"] is None
    assert "@" not in json.dumps(body)


# --- The shared asset rules (STANDALONE_VIEWER.md, section 7, SV1) -----------------------


def test_the_asset_rules_live_in_ui_assets_and_routes_serves_through_them():
    """One set of rules, so the viewer and the adapter serve the same files."""
    from omnigent.airbrx.iris import ui_assets

    assert ui_assets.UI_ROOT == iris_routes.HERE / "ui"
    assert {"assets/iris-portrait.png", "assets/airbrx-logo.png"} == ui_assets.PINNED_IMAGES
    for name in ("app.js", "style.css", "views/overview.js"):
        assert ui_assets.APP_FILES.fullmatch(name), name
    for name in ("UI_ROOT", "_V2_APP_FILES", "_PINNED_IMAGES", "kernel_file"):
        assert not hasattr(iris_routes, name), f"routes.py still defines {name}"


@pytest.mark.parametrize(
    ("asset", "served"),
    [
        ("", True),
        ("index.html", True),
        ("app.js", True),
        ("views/overview.js", True),
        ("kernel/dom.js", True),
        ("assets/airbrx-logo.png", True),
        ("host.js", False),
        ("iris-state.json", False),
        ("demo-state.json", False),
        ("secret.txt", False),
        ("kernel/unlisted.js", False),
        ("views/../secret.txt", False),
    ],
)
def test_ui_response_is_the_one_rule_both_hosts_serve_by(v2_ui, asset, served):
    from omnigent.airbrx.iris.ui_assets import ui_response

    assert (ui_response(asset) is not None) is served


def test_the_host_descriptor_is_refused_for_another_users_session(monkeypatch, tmp_path):
    """Session-scoped like every other Iris route: someone outside the binding gets its refusal."""
    client = make_client(monkeypatch, tmp_path, IrisSession(captured(), FILES))
    monkeypatch.setattr(iris_routes, "require_user", lambda request, provider: "someone-else")
    host = client.get(f"{API}/host")
    assert host.status_code == 403
    assert host.status_code == client.get(f"{API}/state").status_code
    assert host.status_code == client.get(f"{UI}/index.html").status_code
    assert "host_label" not in host.text
