# Security Policy

To report a security vulnerability, use
[GitHub private security advisories](https://github.com/airbrx/omnigent/security/advisories/new).

Please do not open a public issue for security problems, and do not include live
credentials, tokens, or customer data in any report.

## Dependency CVE scanning

There is no repository-wide CVE scanner in CI. The Trivy workflow that once
scanned the lockfiles on every PR, on pushes to `main`, and on a daily
schedule was part of the inherited upstream CI and has been removed, so **a
green PR says nothing about dependency CVEs**.

What remains is Dependabot, configured in
[`dependabot.yml`](.github/dependabot.yml) for the `pip`, `npm` (`/web`,
`/web/electron`), `cargo`, `bundler`, and `github-actions` ecosystems. Every
ecosystem sets `open-pull-requests-limit: 0` and a `security-updates` group, so
Dependabot opens no routine version-bump PRs and raises only grouped security
updates. That depends on Dependabot alerts and security updates being enabled
in repository settings; the configuration file alone does not turn them on.

To check the lockfiles by hand, from the repository root:

```bash
trivy fs --scanners vuln --include-dev-deps \
  --severity UNKNOWN,LOW,MEDIUM,HIGH,CRITICAL --ignore-unfixed=false \
  --skip-dirs '.git,**/.venv,**/node_modules' --timeout 10m .
```

Review findings by affected lockfile, installed version, and available fix;
prioritize reachable high/critical vulnerabilities.

## Contributor PR security scan

Untrusted PRs are put through a deterministic scan of the diff, which runs once
per PR in
[**`.github/workflows/security-scan.yml`**](.github/workflows/security-scan.yml)
and reports the `Security Scan` check.

This used to be a true gate: a companion poller ran as the first job of every CI
workflow, and those workflows declared `needs: gate`, so a finding stopped
untrusted code from being checked out, built, or run on our runners. The poller
and the CI workflows it fronted were part of the inherited upstream CI and have
been removed. **The scan now reports; it gates nothing.** Nothing reads the
`Security Scan` check, and no other job waits on it.

By trust tier (GitHub `author_association`):

- **Trusted** (`OWNER` / `MEMBER` / `COLLABORATOR`, or an author listed in
  [`.github/MAINTAINER`](.github/MAINTAINER), which covers maintainers whose org
  membership is private) and all non-PR events (push, schedule, dispatch): not
  scanned.
- **Returning contributor** (`CONTRIBUTOR`): scanned; a finding fails the
  `Security Scan` check.
- **First-time contributor**: GitHub's native *“require approval to run fork
  pull request workflows”* repo setting already holds every workflow until a
  maintainer clicks **Approve and run**; after approval the scan still applies.
  With the CI lanes gone, this setting — not the scan — is what actually keeps
  a first-time contributor's code off our runners.

The scan inspects the PR diff for committed secrets, secret-exfiltration shapes
(a secret-named credential source plus a network sink in one file, an
`os.environ` dump, a decode-then-exec, or a reverse shell), changes to
privileged repo config (CI workflows, `.github/MAINTAINER`, `CODEOWNERS`,
`.github/scripts`), CI-workflow misuse (`pull_request_target` + PR-head
checkout, unpinned actions), and known code-execution / obfuscation patterns
(semgrep, local ruleset). It only *statically* analyses the diff and runs with
**no secrets** on fork PRs,
and the scanner itself always runs from `main`, so a PR cannot weaken its own
scan.

The `Security Scan` check itself is **not** merge-required, and it no longer
blocks merges transitively either: the pytest and e2e checks that used to carry
that block are gone.

`Maintainer Approval` is enforced: approvers are listed in
[`.github/MAINTAINER`](.github/MAINTAINER) and the
`ENFORCE_MAINTAINER_APPROVAL` repository variable is set, so the gate really
evaluates rather than passing everything through. A PR opened by a maintainer
satisfies it on its own; any other PR needs an approving review from a
maintainer on the **current head SHA**, so a later push invalidates an earlier
approval.

It is **not** a required status check, though, and `main` carries no branch
protection — so a red `Maintainer Approval` reports the problem without
preventing the merge. Until it is made a required check on `main`, treat it as
a signal to reviewers, not a barrier.

A finding fails the `Security Scan` check and nothing else. Detectors run
fail-fast, so a clean PR must pass every one, but treat the result as a signal
for a human reviewer rather than a barrier — reviewing an untrusted diff before
merging is the real control.

### Maintainer override

A maintainer can waive the scan on a specific PR with the **`skip-security-scan`**
label. The waiver is **label-only**: applying a label requires GitHub Triage
permission or higher, which a fork author never has, so the label's presence is
itself the maintainer gate and no separate approval is required. The label is
read from the API, and the decision runs from `should-scan.sh` on `main`, so a
PR cannot edit the waiver logic.

Accepted risk, as repo policy rather than something GitHub enforces: Triage can
be granted independently of Write, so a triage-only collaborator could in
principle self-waive. We accept it because Triage here is granted only to
write/admin collaborators, who can already push code — the waiver hands them no
privilege they lack.

To use it: apply `skip-security-scan`; the label event re-runs the scan and the
`Security Scan` check flips to passing. The waiver stays effective across pushes
while the label is present — remove it to re-enable scanning.
