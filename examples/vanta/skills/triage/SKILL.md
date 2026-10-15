---
name: triage
description: Build an audit burndown dashboard from the live Vanta program — every failing automated test and outstanding evidence request, ranked by what is code-fixable from this repo and by the audit deadline. Use when the user asks "what's left for the audit", "what's failing in Vanta", "what should I fix first", or wants a compliance status snapshot.
---

# triage — audit burndown dashboard

Produce a scannable snapshot of everything still failing, ranked for action.

## 1. Read the live state (Vanta MCP)

- `listAudits` — find the ACTIVE audit; note its observation window / deadline.
- `listFrameworks` (includeProgress: true) — headline progress (controls, tests,
  documents passing vs total).
- `listTests` (statusFilter: `[NEEDS_REMEDIATION, OVERDUE]`, pageSize 100) — the
  full failing set. `type: TEST` are automated; `type: DOCUMENT` are evidence.

Do not dump raw API responses.

## 2. Classify each failing item into tiers

**Ready to fix (code, this repo)** — automated TESTs whose integration matches
code here. Detect deployment code: `provider "aws"` / `aws_*` in `.tf`,
`provider "google"` / `google_*`, `provider "azurerm"` / `azurerm_*`,
`AWSTemplateFormatVersion` (CloudFormation), `cdk.json` (CDK), or GitHub/CI
config for repo-level tests. Use both provider and resource-prefix signals.

**Fixable with guidance** — code-remediable TESTs that don't match this repo
(different cloud/integration). Remediation code is available but applies elsewhere.

**Evidence to collect** — DOCUMENT requests. A human must produce the artifact
(network diagram, background checks, board minutes, DR tabletop, signed policy).
Route these to the `gather-evidence` skill, not the remediator.

## 3. Rank for action

1. Ready-to-fix tests first (fastest wins; the remediator can batch them).
2. Then by failing-entity count (biggest blast radius).
3. Then by longest-overdue.
4. Evidence docs grouped by likely owner so one person clears a cluster.

Highlight **co-failure clusters** — multiple tests sharing a root cause
("5 IAM tests fail on one password policy — fix once, clear all five").

If the user gave you an audit deadline, state items-remaining and days-remaining.

## 4. Present

A table per tier: item name · id · type · failing entities · integration ·
how long failing · next action. For ready-to-fix tests the next action is
"dispatch remediator (`fix-test`)"; for evidence it's "collect (`gather-evidence`)".

End with a recommended first move and offer to start it.

## Edge cases

- **Nothing failing:** "Everything is passing — you're audit-ready." No empty table.
- **Filter request ("just AWS", "just SOC 2 gaps"):** filter by integration or
  framework; if the filter is empty, say so and show the full list.
- **Huge list:** group by integration, show the top 5–10 highest-impact, and
  offer to drill into one integration.
