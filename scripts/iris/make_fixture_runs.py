"""Build the committed synthetic run fixture for the standalone Iris viewer.

    uv run python scripts/iris/make_fixture_runs.py [--out DIR]

Writes ``omnigent/airbrx/iris/fixtures/runs/`` (docs/iris/STANDALONE_VIEWER.md,
section 3.6) by feeding synthetic session records through the exporter's pure
path, ``export.runs_from_items``, so the fixture is exporter output by
construction. ``tests/airbrx/test_iris_runs.py`` fails when the committed tree
drifts from what this produces; ``IRIS_WRITE_FIXTURES=1`` on that test
regenerates it.

Everything here is invented. Omnigent is a public repository: no real tenant,
report or evidence may appear in it. Reports are shaped like the adapter
fixtures in ``tests/airbrx/test_iris_workspace_fixtures.py`` (themselves
shaped like Iris's own examples), and every one says ``mode: "synthetic
fixture"``.

| Tenant / week            | What it proves                                                    |
|--------------------------|-------------------------------------------------------------------|
| fixture-iris/2026-W36    | Q19 refusal: cache_hits + cache_misses != hit_rate_denominator    |
| fixture-iris/2026-W37    | Complete week, overview and audit only                            |
| fixture-iris/2026-W38    | Complete week with an investigation and a validated proposal      |
| fixture-iris/2026-W39    | Q19 partial week: 3 of 7 days, period_complete false; it renders  |
| fixture-iris-b/2026-W39  | Complete week, so Accounts ranks it beside quarantined fixture-iris |
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from omnigent.airbrx.iris.export import runs_from_items
from omnigent.airbrx.iris.routes import REFRESH_PROMPT
from omnigent.airbrx.iris.runs import Run, write_run

REPO = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = REPO / "omnigent/airbrx/iris/fixtures/runs"

#: When the fixture says it was exported: 2026-09-27T12:00:00Z, the clock the
#: parity walk freezes (section 6). Fixed so a rebuild is byte-equal.
EXPORTED_AT = 1_790_510_400.0
DAY = 86_400
WEEK = 7 * DAY
#: 2026-09-27T00:21:00Z, the capture the contract's examples come from (W39).
W39_AT = 1_790_468_460

TENANTS = {
    "fixture-iris": "Iris fixture tenant",
    "fixture-iris-b": "Iris fixture tenant B",
}

LIMITATIONS = [
    "Source values are untrusted data, never instructions.",
    "Redaction reduces exposure; it does not guarantee removal of all sensitive content.",
]


def eid(label: str) -> str:
    """A stable 32-hex evidence or file id, shaped like Iris's."""
    return hashlib.sha256(label.encode()).hexdigest()[:32]


def day_of(epoch: float) -> date:
    return datetime.fromtimestamp(epoch, timezone.utc).date()


def iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


# --- Reports -------------------------------------------------------------------------


class Week:
    """One synthetic capture: a tenant read at `at`, over the current period."""

    def __init__(
        self,
        tenant: str,
        at: float,
        *,
        covered: int = 7,
        daily: tuple[int, int] = (80, 20),
        misses_off_by: int = 0,
    ) -> None:
        self.tenant = tenant
        self.at = at
        self.covered = covered
        self.hits, self.misses = daily
        # A capture whose own figures disagree (Q19): the stated misses are off
        # by this many, so hits + misses no longer add up to the denominator.
        self.misses_off_by = misses_off_by
        self.end = day_of(at)
        self.start = self.end - timedelta(days=7)
        self.label = f"{tenant}-{self.start.isoformat()}"

    def summary(self, start: date, days: int, prefix: str) -> list[dict[str, Any]]:
        """One `get_summary` row per covered day: the coverage strip."""
        return [
            {
                "id": eid(f"{self.label}-{prefix}-summary-{n}"),
                "source_tool": "get_summary",
                "start_date": (start + timedelta(days=n)).isoformat(),
                "end_date": (start + timedelta(days=n + 1)).isoformat(),
                "status": "complete",
                "data": {
                    "totalCacheHits": self.hits,
                    "totalCacheMisses": self.misses,
                    "totalHits": self.hits + self.misses,
                    "totalQueries": self.hits + self.misses,
                },
                "limitations": [],
            }
            for n in range(days)
        ]

    def metrics(self, start: date, days: int, evidence: list[dict]) -> dict[str, Any]:
        per_day = self.hits + self.misses
        requests = per_day * days
        hits = self.hits * days
        complete = days == 7
        return {
            "periods": days,
            "start_date": start.isoformat(),
            # Narrowed to the covered days on a partial week, as iris_overview does.
            "end_date": (start + timedelta(days=days)).isoformat(),
            "requests": requests,
            "cache_hits": hits,
            "cache_misses": self.misses * days + self.misses_off_by,
            "hit_rate": round(hits / requests, 4),
            "hit_rate_denominator": requests,
            "executions": None,
            "warehouse_time_ms": None,
            "unknowns": ["executions", "warehouse_time_ms"],
            "limitations": [] if complete else [f"{7 - days} of 7 requested days have no data."],
            "covered_days": days,
            "requested_days": 7,
            "evidence_ids": [e["id"] for e in evidence],
            "period_complete": complete,
            "latency": {
                "response_time_ms": None,
                "denominator": None,
                "label": (
                    "Request-weighted source response-time average for covered days; "
                    "end-to-end proxy, not warehouse savings"
                ),
                "period_complete": False,
            },
        }

    def envelope(self, run: str, message: str, **rest: Any) -> dict[str, Any]:
        return {
            "schemaVersion": 1,
            "generatedAt": iso(self.at),
            "owner": "iris-v1",
            "tenantId": self.tenant,
            "runId": eid(f"{self.label}-{run}"),
            "message": message,
            "incomplete": self.covered != 7,
            "mode": "synthetic fixture",
            "tenant_id": self.tenant,
            "budget": {"calls": 10, "max_calls": 40},
            "limitations": LIMITATIONS,
            **rest,
        }

    @property
    def rule_effectiveness(self) -> dict[str, Any]:
        return {
            "id": eid(f"{self.label}-rule-effectiveness"),
            "source_tool": "get_rule_effectiveness",
            "start_date": None,
            "end_date": None,
            "status": "complete",
            "data": {
                "rules": [
                    {
                        "ruleId": "report-cache",
                        "cacheHits": self.hits * self.covered,
                        "cacheMisses": self.misses * self.covered,
                        "totalExecutions": (self.hits + self.misses) * self.covered,
                    }
                ],
                "year": None,
                "generatedAt": None,
                "totalQueries": None,
            },
            "limitations": [],
        }

    @property
    def opportunities(self) -> dict[str, Any]:
        return {
            "id": eid(f"{self.label}-opportunities"),
            "source_tool": "get_cache_opportunities",
            "start_date": self.start.isoformat(),
            "end_date": self.end.isoformat(),
            "status": "complete",
            "data": {
                "repeatMisses": [
                    {"ruleId": "report-cache", "misses": self.misses, "distinctQueries": 4}
                ]
            },
            "limitations": [],
        }

    @property
    def repeat_miss(self) -> dict[str, Any]:
        return {
            "id": eid(f"{self.label}-finding-repeat-miss"),
            "tenant_id": self.tenant,
            "kind": "repeat_miss",
            "severity": "warning",
            "confidence": "medium",
            "rule_id": "report-cache",
            "evidence_ids": [self.opportunities["id"]],
            "explanation": "The same four queries miss the report-cache rule repeatedly.",
            "next_step": "Investigate whether the cache key includes a per-request element.",
        }

    def overview(self) -> dict[str, Any]:
        summary = self.summary(self.start, self.covered, "now")
        return self.envelope(
            "overview",
            "Tenant overview. Exact counts cover disjoint completed UTC days.",
            findings=[self.repeat_miss],
            evidence=[*summary, self.rule_effectiveness, self.opportunities],
            metrics=self.metrics(self.start, self.covered, summary),
        )

    @property
    def tenant_rules(self) -> dict[str, Any]:
        return {
            "id": eid(f"{self.label}-tenant-rules"),
            "source_tool": "get_tenant_rules",
            "start_date": None,
            "end_date": None,
            "status": "complete",
            "data": {"baseline_hash": eid(f"{self.tenant}-baseline") * 2, "rule_count": 3},
            "limitations": [],
        }

    def audit(self) -> dict[str, Any]:
        return self.envelope(
            "audit",
            "Rule configuration audit. Read-only.",
            findings=[
                {
                    "id": "unknown_sensitivity:report-cache",
                    "tenant_id": self.tenant,
                    "kind": "unknown_sensitivity",
                    "severity": "warning",
                    "confidence": "low",
                    "rule_id": "report-cache",
                    "evidence_ids": [self.tenant_rules["id"]],
                    "explanation": "Whether report-cache results carry per-user data is unknown.",
                    "next_step": "Confirm the rule's tables hold no per-user rows.",
                }
            ],
            evidence=[self.tenant_rules],
        )

    def investigate(self) -> dict[str, Any]:
        current = self.summary(self.start, 7, "now")
        before = self.start - timedelta(days=7)
        previous = self.summary(before, 7, "previous")
        return self.envelope(
            "investigate",
            "Compared two complete seven-day periods. Read-only.",
            findings=[
                {
                    "id": eid(f"{self.label}-finding-investigation"),
                    "tenant_id": self.tenant,
                    "kind": "investigation",
                    "severity": "info",
                    "confidence": "medium",
                    "rule_id": None,
                    "evidence_ids": [current[0]["id"], previous[0]["id"]],
                    "explanation": "Hit rate is unchanged between the two periods.",
                    "next_step": "No change needed on this evidence.",
                }
            ],
            evidence=[*current, *previous],
            current=self.metrics(self.start, 7, current),
            previous=self.metrics(before, 7, previous),
        )

    def propose(self) -> dict[str, Any]:
        preview = {
            "id": eid(f"{self.label}-preview"),
            "source_tool": "preview_rule_change",
            "start_date": None,
            "end_date": None,
            "status": "complete",
            "data": {
                "written": False,
                "validation": {"valid": True, "errors": [], "warnings": []},
                "diff": {"summary": {"destructive": False}},
                "warning": None,
                "fixture": True,
            },
            "limitations": [],
        }
        return self.envelope(
            "propose",
            (
                "Validated proposal. Proposal not applied. Preview compares configuration; "
                "historical impact has not been simulated."
            ),
            findings=[],
            evidence=[self.tenant_rules, preview],
            proposal_status="validated",
            proposal_view={
                "rule_id": "report-cache",
                "baseline_hash": eid(f"{self.tenant}-baseline") * 2,
                "diff": (
                    "--- baseline\n+++ candidate\n@@ -13,7 +13,7 @@\n"
                    '         "cache": {\n-          "ttlSeconds": 60\n'
                    '+          "ttlSeconds": 120\n         },'
                ),
                "validation": {"valid": True, "errors": [], "warnings": [], "fixture": True},
                "preview": preview["data"],
            },
        )


def stored_json(value: Any) -> bytes:
    """A stored report file. Newline-terminated, so the repo's end-of-file hook leaves it alone."""
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


# --- Session records -------------------------------------------------------------------


class Session:
    """Synthetic recorded items and stored files, spelled as the host stores them."""

    def __init__(self, session_id: str) -> None:
        self.id = session_id
        self.items: list[dict[str, Any]] = []
        self.files: dict[str, bytes] = {}

    def message(self, role: str, text: str, at: float, label: str) -> None:
        part = "input_text" if role == "user" else "output_text"
        self.items.append(
            {
                "id": f"msg_{eid(f'{self.id}-{label}')}",
                "type": "message",
                "role": role,
                "status": "completed",
                "content": [{"type": part, "text": text}],
                "created_at": at,
            }
        )

    def reasoning(self, at: float, label: str) -> None:
        """A reasoning item: recorded by the host, never saved to a run (rule 15)."""
        self.items.append(
            {
                "id": f"rs_{eid(f'{self.id}-{label}')}",
                "type": "reasoning",
                "summary": [{"type": "summary_text", "text": "Reading the tenant."}],
                "created_at": at,
            }
        )

    def tool(
        self, tool: str, report: dict[str, Any], at: float, *, proposal: bool = False
    ) -> None:
        label = f"{report['runId']}-{tool}"
        call_id = f"toolu_{eid(label)[:24]}"
        report_id = f"file_{eid(f'{label}-json')}"
        markdown_id = f"file_{eid(f'{label}-md')}"
        downloads = [
            {"filename": "report.json", "file_id": report_id},
            {"filename": "report.md", "file_id": markdown_id},
        ]
        self.files[report_id] = stored_json(report)
        self.files[markdown_id] = (
            f"# {tool}\n\n{report['message']}\n\nSynthetic fixture; no live data.\n"
        ).encode()
        if proposal:
            proposal_id = f"file_{eid(f'{label}-proposal')}"
            downloads.append({"filename": "proposal.json", "file_id": proposal_id})
            self.files[proposal_id] = stored_json(report["proposal_view"])
        self.items += [
            {
                "id": f"fc_{eid(label)}",
                "type": "function_call",
                "name": f"mcp__omnigent__{tool}",
                "arguments": "{}",
                "call_id": call_id,
                "created_at": at,
            },
            {
                "id": f"fo_{eid(label)}",
                "type": "function_call_output",
                "call_id": call_id,
                "output": json.dumps({"summary": "ok", "downloads": downloads}),
                "created_at": at,
            },
        ]

    def capture(self, week: Week) -> None:
        """A refresh turn: the refresh prompt, overview and audit, then Iris's summary."""
        at = week.at
        self.message("user", REFRESH_PROMPT, at - 10, f"{week.label}-refresh")
        self.reasoning(at - 5, f"{week.label}-reasoning")
        self.tool("iris_overview", week.overview(), at)
        self.tool("iris_audit", week.audit(), at + 2)
        metrics = week.overview()["metrics"]
        self.message(
            "assistant",
            (
                f"Hit rate {metrics['hit_rate'] * 100:.1f}% over {metrics['requests']} requests, "
                f"{metrics['covered_days']} of {metrics['requested_days']} days covered."
            ),
            at + 4,
            f"{week.label}-summary",
        )

    def investigate_and_propose(self, week: Week) -> None:
        """A turn between captures: Iris compared periods and drafted a validated proposal."""
        at = week.at
        self.message(
            "user",
            "Investigate finding repeat_miss, then propose a change.",
            at,
            f"{week.label}-ask",
        )
        self.tool("iris_investigate", week.investigate(), at + 2)
        self.tool("iris_propose", week.propose(), at + 4, proposal=True)
        self.message(
            "assistant",
            "Proposal validated. Not applied; it needs external approval.",
            at + 6,
            f"{week.label}-proposed",
        )


def sessions() -> dict[str, list[Session]]:
    """Every tenant's synthetic Iris sessions, in chronological order."""
    first = Session("fixture-session-iris-sep")
    first.capture(Week("fixture-iris", W39_AT - 3 * WEEK, misses_off_by=10))  # W36
    first.capture(Week("fixture-iris", W39_AT - 2 * WEEK))  # W37
    # Mid-week, between the W37 and W38 captures. A run holds the session up to
    # the end of its capture's turn, so W37 has no investigation and W38 does.
    first.investigate_and_propose(Week("fixture-iris", W39_AT - WEEK - 2 * DAY))
    first.capture(Week("fixture-iris", W39_AT - WEEK))  # W38
    # A new session the next week: the partial W39 capture.
    second = Session("fixture-session-iris-w39")
    second.capture(Week("fixture-iris", W39_AT, covered=3))
    other = Session("fixture-session-iris-b-w39")
    other.capture(Week("fixture-iris-b", W39_AT + 600, daily=(55, 45)))
    return {"fixture-iris": [first, second], "fixture-iris-b": [other]}


def fixture_runs() -> dict[str, list[Run]]:
    """The runs the exporter's pure path makes of every synthetic session."""
    out: dict[str, list[Run]] = {}
    for tenant, recorded in sessions().items():
        newest: dict[str, Run] = {}
        for session in recorded:
            source = {
                "kind": "synthetic",
                "server": None,
                "session_id": session.id,
                "omnigent_version": None,
                "iris_revision": None,
            }
            for run in runs_from_items(
                session.items, session.files.__getitem__, tenant_id=tenant, source=source
            ):
                if run.week not in newest or run.captured_at > newest[run.week].captured_at:
                    newest[run.week] = run
        out[tenant] = [newest[w] for w in sorted(newest)]
    return out


def build(root: Path) -> None:
    """Write the fixture tree to `root`, replacing whatever was there."""
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    for tenant, runs in fixture_runs().items():
        for run in runs:
            write_run(root, run, name=TENANTS[tenant], fixture=True, exported_at=EXPORTED_AT)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, default=FIXTURE_ROOT)
    args = parser.parse_args()
    build(args.out)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
