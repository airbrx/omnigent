---
name: fix-test
description: Remediate one code-fixable Vanta compliance test end-to-end — fetch its test-specific remediation intelligence, delegate the code change to the remediator sub-agent, and land a PR that Vanta auto-reruns. Use when the user names a test id or Vanta test URL, or says "fix <test>", "open a PR for <test>".
---

# fix-test — remediate one test via a PR

## 1. Resolve the test

If given a URL (`https://app.vanta.com/.../tests/<testId>`), extract the id. If
given a plain string, use it as the id. If it doesn't resolve, DON'T dead-end —
`listTests` and fuzzy-match, then present the closest matches to pick from.

Confirm it is actually failing (`getAutomatedTestDetails`). If it's already
passing, say so and show the failing list instead.

## 2. Get the remediation intelligence — REQUIRED

Call `getAgentRemediationPrompt` with the test id. This returns a system prompt,
a user message, and the failing entity context with test-specific fix guidance.
**Never remediate from general knowledge — always fetch this first.** If it
returns guidance for an external console rather than code, supplement with a web
search for current docs (published instructions are often stale).

## 3. Confirm it's code-fixable from here

Check for matching IaC / config in this repo (see the `triage` detection
signals). If there are no IaC files, or none manage the failing entities, tell
the user plainly and offer options (import + fix, a different repo, or CLI
commands) — do not force a fabricated fix.

## 4. Delegate to the remediator (purpose: implement)

Dispatch the `remediator` via `sys_session_send` with `args.purpose: implement`
and a descriptive `title` (e.g. `implement-vpc-flow-logs`). Hand it the test id
and the full `getAgentRemediationPrompt` output. It writes the minimal fix and
opens a PR with `Fixes: <testUrl>` so Vanta auto-reruns the test.

Collect its result with a SINGLE `sys_read_inbox`. If it's still running, END
YOUR TURN — you'll be woken when it finishes. Do not busy-poll.

## 5. Report

Show the branch, the PR URL, what changed, and any cost implication the fix
carries (paid services — CloudTrail data events, GuardDuty, KMS — must be
called out). Offer the next test.

## Hard rule

Never accept a fix that weakens security (disabling encryption, removing access
controls, opening 0.0.0.0/0). If remediation seems to require it, stop and flag it.
