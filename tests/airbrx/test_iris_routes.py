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
