"""What a recorded Iris session says about itself: tool names and report references.

Split out of `routes.py` so the account collector can read session records
without importing the router (which imports the collector).
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone

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
    return [(output, name) for output, name, _ in _paired_calls(items)]


def _paired_calls(items):
    """`paired`, keeping the function_call item each output was matched to."""
    calls, outputs = {}, {}
    for i in items:
        if i.get("type") == "function_call":
            calls.setdefault(i.get("call_id"), []).append(i)
        elif i.get("type") == "function_call_output":
            outputs.setdefault(i.get("call_id"), []).append(i)
    pairs = []
    for call_id, recorded in calls.items():
        # strict=False: a cancelled or still-running turn leaves a call with no
        # output yet, and the pairing should stop at the shorter side.
        for call, output in zip(recorded, outputs.get(call_id, []), strict=False):
            pairs.append((output, bare_tool_name(call.get("name")), call))
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
        if isinstance(name, str) and name in TOOLS:
            digest = hashlib.sha256((item.get("output") or "").encode()).hexdigest()
            runs.setdefault(digest, name)
    return sorted(runs.values())


def report_references(items):
    """(tool, file_id, created_at) for every report.json an Iris tool produced.

    `items` in chronological order. `created_at` is epoch seconds on the
    function_call_output item that carried the report.
    """
    return [(tool, file_id, created_at) for tool, file_id, created_at, *_ in report_calls(items)]


#: No arguments recorded, spelled the ways a host records "none".
_NO_ARGUMENTS = (None, "", "{}")


def report_calls(items):
    """`report_references`, each with what the tool was asked and when.

    (tool, file_id, created_at, arguments, called_at). `arguments` is the
    call's recorded arguments as a dict (`{}` when none were recorded), or None
    when they were recorded but cannot be read as a JSON object, so a caller
    can tell "called with no arguments" from "called with arguments nobody can
    read". `called_at` is epoch seconds on the function_call item itself: when
    the call was made, not when its answer was recorded (`created_at`). Every
    stored item carries a store-assigned `created_at`; a record without one
    falls back to the output's.
    """
    references = []
    for item, tool, call in _paired_calls(items):
        if tool not in TOOLS:
            continue
        try:
            result = json.loads(item.get("output", ""))
        except (ValueError, TypeError):
            continue
        if isinstance(result, dict) and not result.get("error") and not result.get("error_code"):
            arguments = _arguments(call.get("arguments"))
            created_at = item.get("created_at", 0)
            called_at = call.get("created_at", created_at)
            for download in result.get("downloads", []):
                if download.get("filename") == "report.json":
                    references.append(
                        (tool, download["file_id"], created_at, arguments, called_at)
                    )
    return references


def _arguments(recorded):
    if recorded in _NO_ARGUMENTS:
        return {}
    if isinstance(recorded, dict):
        return recorded
    try:
        parsed = json.loads(recorded)
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


#: iris_overview's default window: this many completed UTC days, ending (end
#: exclusive) on the day it runs. `iris.config.period` in the pinned archive.
_CURRENT_PERIOD_DAYS = 7


def reads_current_period(arguments: dict | None, called_at: float) -> bool:
    """Whether an iris_overview call read the tenant's current period.

    Called without dates, iris_overview reads the current period by definition:
    the seven completed UTC days before the day it runs. Called with dates, it
    read the current period only if the dates name exactly that window for the
    UTC day the call was made (`called_at`, epoch seconds, from the
    function_call item; see `report_calls`). The day the answer was recorded
    does not decide it, so a call made at 23:59:58 UTC and answered at 00:00:04
    is judged by the day it was made. Anything else, typically an older window
    read to compare against, is not the overview.

    Decided from what the tool was asked, never from what it found: a capture
    that covered 3 of 7 days (`period_complete: false`) is still the current
    period, and says so in its own metrics. Arguments that were recorded but
    cannot be read are not assumed to be the default.

    Shared by the workspace state (`routes.read_state`) and the account
    ranking (`account.collect_captures`), so both mean the same capture.
    """
    if arguments is None:
        return False
    start, end = arguments.get("start_date"), arguments.get("end_date")
    if start is None and end is None:
        return True
    try:
        window = (date.fromisoformat(start), date.fromisoformat(end))
    except (TypeError, ValueError):
        return False
    day = datetime.fromtimestamp(called_at, timezone.utc).date()
    return window == (day - timedelta(days=_CURRENT_PERIOD_DAYS), day)
