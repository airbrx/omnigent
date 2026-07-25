---
name: gather-evidence
description: Turn a Vanta evidence / document request into a concrete collection checklist — what the artifact must contain, who owns it, where it likely lives, and the acceptance criteria Vanta checks. Use for DOCUMENT-type requests (network diagram, background checks, board minutes, DR tabletop, signed policies, access-review proof). Never fabricates the artifact.
---

# gather-evidence — make an evidence request actionable

Evidence requests are real artifacts a human must produce. This skill does NOT
create the artifact; it removes every excuse for it not getting collected.

## 1. Read the request

`getEvidenceRequestDetailsTool` (or `listEvidenceRequests` to find it) for the
document's title, description, mapped controls/frameworks, and status. For a
policy document, `listPolicies` / `downloadPolicy` shows what already exists.

## 2. Extract the acceptance criteria

State plainly what Vanta (and the auditor) will accept: the required contents,
the time window it must cover (audit observation period), the recency it needs,
and how many samples (e.g. "background checks for all employees hired in-period",
"board minutes from at least one meeting in the observation window").

## 3. Identify owner and likely source

Name the function that owns it and where it probably already lives — don't make
the user hunt:
- **People/HR** (background checks, offer letters, confidentiality/NDAs,
  performance reviews, security-training completion) → HR / HRIS.
- **Governance** (board minutes, org policies, risk assessments) → leadership.
- **Engineering/Infra** (network/architecture diagram, network segregation,
  CI/CD proof, change log, status page) → infra/eng — a diagram or a link.
- **Security** (pen-test remediation, vuln scans, incident reports, DR tabletop,
  IR-plan test) → security owner.

## 4. Produce the checklist

Write a short, owned checklist (optionally into a local `vanta-evidence/<id>.md`
draft) with: the request, acceptance criteria, owner, likely source, exact ask,
and — for engineering-owned artifacts that CAN be generated (network diagram
from IaC, a change log from git history, a status page) — offer to draft it from
this repo.

## 5. Hand off

Give the user a one-line ask they can forward to the owner, and offer to draft
what's drafterable. Never upload or mark a request satisfied with a fabricated
document.
