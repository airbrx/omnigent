"""What a recorded Iris session says about itself: tool names and report references.

Split out of `routes.py` so the account collector can read session records
without importing the router (which imports the collector).
"""

from __future__ import annotations

import hashlib
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


def paired(items):
    """Yield every (function_call_output, tool name) in `items`.

    A `call_id` names neither a tool nor a single run. The hosted item log
    writes each Iris result twice and gives the duplicate an ADJACENT call_id,
    sometimes the preceding ToolSearch's and sometimes the previous Iris
    tool's. Measured on a four-tool turn, 2026-09-22, session
    df952db85cf54a209351604050f3735e:

        ToolSearch       toolu_01WNsg...  -> tool_reference
        iris_overview    toolu_01WNsg...  -> overview payload    (borrowed)
        iris_overview    toolu_01D8bJ...  -> the same payload, byte for byte
        ToolSearch       toolu_01Dcqi...  -> tool_reference
        iris_investigate toolu_01Dcqi...  -> investigate payload (borrowed)
        iris_audit       toolu_01E6jo...  -> audit payload
        iris_audit       toolu_01FzPy...  -> the same payload, byte for byte
        iris_propose     toolu_01FzPy...  -> propose payload     (borrowed)
        iris_propose     toolu_011wXQ...  -> the same payload, byte for byte

    Nine records, four executions. Building {call_id: name} and letting the
    last writer win names toolu_01FzPy `iris_propose`, so the AUDIT output
    under that id is attributed to propose and the audit's own report is never
    found. On the account route that reads as a stale capture or a tenant that
    has never collected — a wrong answer, arrived at quietly.

    What survives the defect is ordering: within one call_id the k-th call
    still lines up with the k-th output. That is enough to name every result
    without trusting the id to be unique.

    Duplicates are deliberately NOT removed here. Both records name the same
    tool and carry the same download, and callers already take the newest
    reference per tool. A caller that needs execution COUNTS (an acceptance
    check, say) dedupes on the output payload, because a genuine second run
    mints a fresh evidence id and advances the tool budget while a duplicated
    record does not.

    :param items: Session items in chronological order.
    :returns: Pairs of (function_call_output item, bare tool name).
    """
    calls, outputs = {}, {}
    for i in items:
        if i.get("type") == "function_call":
            calls.setdefault(i.get("call_id"), []).append(bare_tool_name(i.get("name")))
        elif i.get("type") == "function_call_output":
            outputs.setdefault(i.get("call_id"), []).append(i)
    pairs = []
    for call_id, names in calls.items():
        # strict=False: a cancelled or still-running turn leaves a call with no
        # output yet, and the pairing should stop at the shorter side.
        for name, output in zip(names, outputs.get(call_id, []), strict=False):
            pairs.append((output, name))
    # Chronological, because callers pick the newest reference per tool.
    pairs.sort(key=lambda pair: pair[0].get("created_at", 0))
    return pairs


def executions(items):
    """Names of the Iris tools that actually RAN, one entry per run.

    `paired` says which tool each recorded result belongs to; this says how
    many times a tool ran, which is a different question and the one an
    acceptance check asks. The two are separate because the hosted item log
    writes each result twice, so counting records overcounts every run.

    Two byte-identical payloads are one execution. A genuine second run mints a
    fresh evidence id and advances the tool budget, so it cannot be byte-equal
    to the first; a duplicated record is a copy and always is. Measured on the
    four-tool turn behind :func:`paired`, the duplicated pairs held their
    budgets unchanged at {calls: 10}, {calls: 30} and {calls: 35} while the
    four real runs stepped 10 -> 28 -> 30 -> 35.

    This lived inline in scripts/iris/verify_host.py, which has no test file,
    so the rule deciding whether a duplicate was a second dispatch was the one
    rule with nothing checking it — on a gate whose whole job is to notice a
    second dispatch.

    :param items: Session items in chronological order.
    :returns: Sorted tool names, one per distinct execution.
    """
    runs = {}
    for item, name in paired(items):
        if name in TOOLS:
            digest = hashlib.sha256((item.get("output") or "").encode()).hexdigest()
            runs.setdefault(digest, name)
    return sorted(runs.values())


def report_references(items):
    """(tool, file_id, created_at) for every report.json an Iris tool produced.

    `items` in chronological order. `created_at` is epoch seconds on the
    function_call_output item that carried the report.
    """
    references = []
    for item, tool in paired(items):
        if tool not in TOOLS:
            continue
        try:
            result = json.loads(item.get("output", ""))
        except (ValueError, TypeError):
            continue
        if isinstance(result, dict) and not result.get("error") and not result.get("error_code"):
            for download in result.get("downloads", []):
                if download.get("filename") == "report.json":
                    references.append((tool, download["file_id"], item.get("created_at", 0)))
    return references
