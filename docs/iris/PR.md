## Related issue

User-requested Iris integration; no separate issue.

## Summary

Iris could run in a developer bridge but was absent from the Omnigent drawer, and its runner could not inherit tenant credentials or deliver session reports. Add a tenant-bound native chat/workspace entry, preserve the accepted UI, and route the existing four Iris tools through native execution and authenticated session-file delivery.

In plain terms: choosing Iris starts a normal Omnigent conversation; her workspace reads the reports that conversation actually produced. The server owns history and authorization, and credentials stay on the execution host.

```text
Agents → Iris → tenant selection → native session
                                   ├─ Claude SDK → four Iris tools
                                   └─ accepted workspace ← session reports
```

The unchanged Iris package/UI is pinned to `fb0c4daa12254a6c7069d347ac4232f21c6d318e`. Its 2.3 MB source/portrait archive has an exact large-file-hook exception and a SHA256 integrity check. The Iris extra pins the package's reviewed dependencies, including Claude Agent SDK 0.2.118. The extension API currently grants only session reads, so the authenticated workspace adapter is a small fork-owned route rather than an expanded extension capability.

## Test Plan

- 26 Python integration/runner tests: real Iris subprocess dispatch and session-file uploads/downloads, cross-session/user/workspace denial, tenant persistence, budget exhaustion, cancellation, credential isolation, native history forwarding and honest refresh failures.
- 177 native Claude SDK regression tests.
- 16 frontend tests covering separate chat/workspace actions, explicit tenant selection, session creation, mounted route and focus trapping.
- Frontend lint, type check and production build pass. Full pre-commit result is recorded in `docs/iris/acceptance.json`.
- The existing production site was inspected through authenticated CLI and browser: 0.13.0 at `6359361d…`; Iris was absent. This is baseline evidence, not acceptance of the new integration.

## Demo

- [ ] Visual demo attached below
- [x] Non-visual evidence provided below or in Test Plan
- [ ] Not applicable — no behavioral change

**Draft rollout gate:** actual hosted light/dark/mobile/keyboard screenshots and native SDK fixture/live turns remain pending deployment and execution-host restart approval. No standalone preview is offered as deployment evidence. `docs/iris/RUNBOOK.md` gives the exact mount, walkthrough, credential-reference setup, prepared launchd change and hosted verifier.

## Type of change

- [x] Bug fix
- [x] Feature
- [x] UI / frontend change
- [ ] Refactor / chore
- [x] Docs
- [x] Test / CI
- [ ] Breaking change

## Test coverage

- [x] Unit tests added / updated
- [x] Integration tests added / updated
- [ ] E2E tests added / updated
- [ ] Manual verification completed
- [x] Existing tests cover this change
- [ ] Not applicable

## Coverage notes

Mocked native-event contract tests are separate from real subprocess and real file-route tests. Neither proves a deployed model turn. The PAT reference and non-secret launchd replacement are prepared locally; the running service and production deployment remain unchanged. Monitoring remains unavailable. Token-only embedded clients still need an authenticated document transport; the accepted workspace uses the deployed website's same-origin cookie authentication.

## Changelog

Open Iris from the agent drawer for a tenant-bound native conversation or her existing evidence workspace, with authenticated report downloads.
