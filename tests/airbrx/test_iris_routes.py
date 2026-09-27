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
            "tenant_id": "f65d9135-0ba3-4c58-8768-c48a1334041d",
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
        (b,) = self.parse([self.binding(name="Airbrx Databricks Production")])
        self.assertEqual(b.name, "Airbrx Databricks Production")

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


# --- Assets under OMNIGENT_IRIS_UI (WORKSPACE_V2.md, D1 and section 2 "Assets") --------

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
    monkeypatch.setattr(iris_routes, "UI_ROOT", root, raising=False)

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
    monkeypatch.setenv("OMNIGENT_IRIS_UI", "v2")
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


def test_v2_is_read_per_request(v2_ui, monkeypatch, tmp_path):
    client = _ui_client(monkeypatch, tmp_path)
    assert "host.js" not in client.get(f"{UI}/index.html").text
    monkeypatch.delenv("OMNIGENT_IRIS_UI")
    assert "host.js" in client.get(f"{UI}/index.html").text


@pytest.mark.parametrize("value", [None, "", "V2", "v1", "pinned", "1", " v2"])
def test_anything_but_v2_serves_the_pinned_ui_as_before(v2_ui, monkeypatch, tmp_path, value):
    if value is None:
        monkeypatch.delenv("OMNIGENT_IRIS_UI")
    else:
        monkeypatch.setenv("OMNIGENT_IRIS_UI", value)
    client = _ui_client(monkeypatch, tmp_path)
    index = client.get(f"{UI}/index.html")
    assert index.status_code == 200
    assert '<script src="host.js"></script><script src="theme.js">' in index.text
    assert index.text != (v2_ui / "index.html").read_text()
    assert client.get(f"{UI}/host.js").status_code == 200
    assert client.get(f"{UI}/theme.js").status_code == 200
    for name in ("iris-state.json", "demo-state.json", "kernel/dom.js", "views/overview.js"):
        assert client.get(f"{UI}/{name}").status_code == 404, name


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
