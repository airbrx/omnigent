"""What a recorded Iris session says about itself: tool names and report references.

Split out of `routes.py` so the account collector can read session records
without importing the router (which imports the collector).
"""

from __future__ import annotations

import json

from omnigent.airbrx.iris.runtime import TOOLS

#: Iris tools are registered bare (see :data:`omnigent.airbrx.iris.runtime.TOOLS`)
#: and dispatched bare, but the model reaches them through the SDK's MCP
#: namespace, so the session record stores ``mcp__omnigent__iris_overview``.
#: Only this server's own prefix is removed. A tool served by any other MCP
#: server -- ``mcp__airbrx__*``, ``mcp__claude_ai_Slack__*`` -- is outside the
#: Iris boundary and must still be refused, so the guard keeps its meaning.
OMNIGENT_TOOL_PREFIX = "mcp__omnigent__"


def bare_tool_name(name):
    """Return the registration name for a recorded tool call."""
    if not isinstance(name, str):
        return name
    return name.removeprefix(OMNIGENT_TOOL_PREFIX)


def report_references(items):
    """(tool, file_id, created_at) for every report.json an Iris tool produced.

    `items` in chronological order. `created_at` is epoch seconds on the
    function_call_output item that carried the report.
    """
    calls = {
        i.get("call_id"): bare_tool_name(i.get("name"))
        for i in items
        if i.get("type") == "function_call"
    }
    references = []
    for item in items:
        if (
            item.get("type") != "function_call_output"
            or calls.get(item.get("call_id")) not in TOOLS
        ):
            continue
        try:
            result = json.loads(item.get("output", ""))
        except (ValueError, TypeError):
            continue
        if isinstance(result, dict) and not result.get("error") and not result.get("error_code"):
            for download in result.get("downloads", []):
                if download.get("filename") == "report.json":
                    references.append(
                        (calls[item["call_id"]], download["file_id"], item.get("created_at", 0))
                    )
    return references
