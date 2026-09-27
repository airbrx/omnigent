"""The workspace state body built from recorded Iris session items: one builder for every host.

`build_state` is the WORKSPACE_V2 section 2 state body. The Omnigent adapter
(`routes.read_state`) calls it over a session's items, and the run exporter
calls it over a capture's window, so a framed session and a saved run cannot
disagree about what a capture is (docs/iris/STANDALONE_VIEWER.md, section 3.4).

It is pure: no HTTP, no clock, no session. The caller supplies the items, a
way to read a report file, and `now`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

from omnigent.airbrx.iris.records import reads_current_period, report_calls
from omnigent.airbrx.iris.runtime import TOOLS

#: Tools whose reports a refresh bounds by its own turn marker. A refresh calls
#: exactly these, so an older report of either is the previous capture and must
#: not be passed off as the fresh one. `iris_investigate` and `iris_propose` are
#: absent on purpose: a refresh never calls them, so bounding them would blank
#: the investigation and the proposal on every refresh. Each carries its own
#: `report_times` entry instead, which says how old it is.
REFRESH_BOUND = frozenset({"iris_overview", "iris_audit"})

#: Reports a state read carries when it can, and leaves null when it cannot.
#: Their read failing must not take the overview and audit down with it: the
#: workspace can still show the capture, and a null already means "absent".
#: A tenant mismatch is not a read failure and still refuses the whole state.
OPTIONAL_REPORTS = frozenset({"iris_investigate", "iris_propose"})

#: How old a capture may be before the workspace calls it stale.
STALE_AFTER_SECONDS = 300

TENANT_MISMATCH_DETAIL = "Report tenant does not match session"


class StateUnavailable(Exception):
    """No state can be shown; the adapter answers 409 with `detail`."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class TenantMismatch(Exception):
    """A report names another tenant; the adapter answers 403."""

    detail = TENANT_MISMATCH_DETAIL

    def __init__(self) -> None:
        super().__init__(TENANT_MISMATCH_DETAIL)


class ReportUnavailable(Exception):
    """`report(file_id)` could not read that report, or it is not a JSON object.

    For an optional report the state carries null in its place; for the
    overview or the audit it propagates to the caller.
    """


def _candidates(
    items: list[dict], collected_after: float
) -> Iterator[tuple[str, str, float] | None]:
    """Each report ref `build_state` would read, newest first; None marks an other-window overview.

    A caller that reads reports asynchronously uses `report_ids` (built on this)
    to fetch exactly the files `build_state` will ask for.
    """
    for tool, file_id, created_at, arguments, called_at in reversed(report_calls(items)):
        # On a fresh collection only this turn's overview and audit count: a
        # refresh that returns the previous capture is worse than one that
        # admits it collected nothing. Checked before the window below, so an
        # older comparison does not turn "produced no overview" into "read no
        # current-period overview".
        if tool in REFRESH_BOUND and created_at < collected_after:
            continue
        # `overview` is the tenant's current period. An overview Iris read over
        # another window mid-session is newer than the capture but is not it;
        # the window is judged on the day the call was made.
        if tool == "iris_overview" and not reads_current_period(arguments, called_at):
            yield None
            continue
        yield tool, file_id, created_at


def report_ids(items: list[dict], *, collected_after: float = 0.0) -> list[str]:
    """The report file ids `build_state` reads for these items: the newest usable one per tool."""
    seen: set[str] = set()
    ids = []
    for candidate in _candidates(items, collected_after):
        if candidate is None or candidate[0] in seen:
            continue
        seen.add(candidate[0])
        ids.append(candidate[1])
    return ids


def build_state(
    items: list[dict],
    report: Callable[[str], Any],
    *,
    tenant_id: str,
    collected_after: float = 0.0,
    fresh: bool = False,
    now: float,
) -> dict:
    """The V2 section 2 state body from chronological session items.

    `report(file_id)` returns a parsed report.json, or raises
    `ReportUnavailable`. `collected_after` bounds the overview and audit to a
    refresh turn's own reports; `fresh` only chooses the 409 wording. Raises
    `StateUnavailable(detail)` for every 409 the adapter answers from its
    items, and `TenantMismatch` for the 403.
    """
    reports: dict[str, dict] = {}
    report_times: dict[str, float | None] = dict.fromkeys(sorted(TOOLS))
    cache_age = 0.0
    captured_at = None
    unreadable: set[str] = set()
    other_windows = False
    for candidate in _candidates(items, collected_after):
        if candidate is None:
            other_windows = True
            continue
        tool, file_id, created_at = candidate
        if tool in reports or tool in unreadable:
            continue
        try:
            body = report(file_id)
            if not isinstance(body, dict):
                raise ReportUnavailable("report is not an object")
        except ReportUnavailable:
            if tool not in OPTIONAL_REPORTS:
                raise
            # Null, as if absent, and no older report in its place: the newest
            # is the one the session stands behind.
            unreadable.add(tool)
            continue
        if body.get("tenant_id") != tenant_id:
            raise TenantMismatch()
        reports[tool] = body
        report_times[tool] = float(created_at)
        if tool == "iris_overview":
            captured_at = float(created_at)
            cache_age = max(0, now - created_at)
    if "iris_overview" not in reports:
        # Two different situations, and on the fresh path neither may point at
        # the button that has just run.
        if other_windows:
            raise StateUnavailable(
                "The collection read no current-period overview; open native chat to "
                "see what Iris did."
                if fresh
                else "No current-period overview yet; use Refresh from host"
            )
        raise StateUnavailable(
            "The collection produced no overview; open native chat to see what Iris did."
            if fresh
            else "No session overview yet; use Refresh from host"
        )
    rules, meta = [], {}
    for evidence in reports["iris_overview"].get("evidence", []):
        if evidence.get("source_tool") == "get_rule_effectiveness":
            data = evidence.get("data") or {}
            rules = data.get("rules") or []
            meta = {k: data.get(k) for k in ("year", "generatedAt", "totalQueries")}
    return {
        "overview": reports["iris_overview"],
        "audit": reports.get("iris_audit", {"findings": []}),
        "rules": rules,
        "rule_effectiveness_meta": meta,
        "stale": cache_age > STALE_AFTER_SECONDS,
        "cache_age_seconds": round(cache_age),
        "monitoring": None,
        # The clock `cache_age_seconds` is measured on: when the item carrying
        # the overview report was recorded.
        "captured_at": captured_at,
        "investigation": reports.get("iris_investigate"),
        "proposal": reports.get("iris_propose"),
        "report_times": report_times,
    }
