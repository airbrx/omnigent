# omnigent dev box — runbook (`omnigent-devbox`)

A **remote Linux dev box** — a "pseudo-laptop" that registers itself as an
ordinary omnigent *host* against `omnigent.airbrx.ai`, so work happens on Linux
instead of Windows. It is **not** a server and **not** a managed sandbox.

> Read this first: the coordination server (`../omnigent-ec2/`) deliberately
> "runs no agents" — each person registers their own laptop as a host. This box
> IS such a laptop; it just lives in EC2. That is why it uses the plain
> `omnigent host` flow and needs no `sandbox:` config on the server.

```
  You (anywhere)          omnigent.airbrx.ai          omnigent-devbox (EC2)
 ┌────────────────┐      ┌────────────────────┐      ┌──────────────────────┐
 │ browser / CLI  │─────▶│ coordinator (ALB)   │◀────▶│ omnigent host daemon │
 │ GitHub Actions │      │ JumpCloud OIDC      │  WS  │ AGENTS RUN HERE      │
 │  = wake button │      └────────────────────┘      │ claude · gh · aws    │
 └───────┬────────┘                                  └──────────┬───────────┘
         │ ec2:StartInstances (OIDC, no keys)                   │ IMDS role
         └──────────────────────────────────────────────────────┘
```

---

## Facts

| | |
|---|---|
| Instance | `i-099d66548b496d876`, t3.medium, 50 GB gp3 (encrypted), us-east-1a |
| AMI | Ubuntu 24.04 (`ami-052355af2a014bd2c` at build time) |
| VPC / subnet | default `vpc-05b69c7707282a5fe` / `subnet-00f5a0bbdd9282b2c` |
| Security group | `sg-06e59ccfa6fef8952` — **zero inbound rules** |
| Access | SSM only: `aws ssm start-session --target i-099d66548b496d876` |
| Instance profile | `omnigent-devbox-ssm` (SSM core + `AdministratorAccess` + secrets read) |
| Shell user | `michael` (uid **1001**), passwordless sudo, `enable-linger` on |
| Shutdown behavior | **`stop`** — an in-box `shutdown -h` sleeps the box, never destroys it |

Cost: ~$34/mo if left running 24/7; ~$11/mo at ~8h weekdays. The 50 GB volume
is **~$4/mo regardless**, including while stopped — that is the price of
waking in a minute with everything intact.

---

## Power control

**GitHub Actions** (`.github/workflows/devbox-power.yml`) is the wake button —
usable from the Actions tab or the GitHub mobile app, with live per-phase
progress in the job summary. Nightly `stop` at 07:00 UTC is a hard backstop
behind the in-box idle timer.

**Local CLI** (`client/devbox.ps1`, install to a directory on `PATH`):

```
devbox start | stop | status | connect | idle | cost
```

Waking is lossless and needs no login: the EBS volume persists,
`omnigent-creds.service` re-pulls tokens from Secrets Manager, and the
lingering `omnigent-host` user unit re-registers. ~40–60s.

---

## Waking from the omnigent host picker

With this on the SERVER's config, an offline `omnigent-devbox` row in the host
dropdown becomes clickable and starts the box instead of dead-ending:

```yaml
host_wake:
  - host_name: omnigent-devbox
    provider: ec2
    instance_id: i-099d66548b496d876
    region: us-east-1
```

The server needs `ec2:StartInstances` + `ec2:DescribeInstances` on that
instance ARN — grant it on the SERVER's instance role
(`omnigent-server-ssm`), not via stored keys. Install with the `ec2` extra so
boto3 is present.

**Opt-in by construction.** With no `host_wake:` section every host reports
`wakeable: false` and the endpoint refuses with 409, so a laptop-only install
behaves exactly as before — a quiet laptop is never affected.

Matching is by host NAME, not id: ids are minted at first registration and
change when the box is rebuilt, while the name is stable and is what an
operator actually knows.

---

## Idle auto-stop

A 5-minute systemd timer stops the box after `IDLE_MINUTES` (default **60**) of
genuine idleness. Tune in `/etc/omnigent/devbox-idle.conf`. Inspect with
`devbox idle` — every decision is logged with its reason.

Idle means **none** of:

1. an SSM session is attached,
2. 5-minute load average > 0.4,
3. a file under `~/.omnigent/logs/` was written in the last 10 minutes.

### Why not `who` or "is claude running?"

Both are the obvious choice and both are **wrong here**, which is worth
remembering before anyone "simplifies" this:

- omnigent's native harnesses run `claude` inside **tmux**, and tmux panes
  leave permanent `utmp` entries. After the first session, `who` reports a
  logged-in user forever.
- A `claude` process parked in tmux awaiting your next turn is
  indistinguishable, by existence alone, from one doing work. There are
  routinely ~8–12 of them on an idle box.

Either signal pins the box awake permanently and silently defeats the feature.
Activity must be measured as **work** (CPU, fresh logs), not **presence**.

---

## Credentials

Nothing is copied by hand; everything is re-derivable. Secrets live in Secrets
Manager under `omnigent/devbox/*` and are materialized at boot by
`omnigent-creds.service` → `/usr/local/bin/omnigent-fetch-creds.sh` →
`/home/michael/.config/omnigent/host.env` (mode 600), which the
`omnigent-host` unit reads via `EnvironmentFile=`.

| Secret | Env var | Used for |
|---|---|---|
| `omnigent/devbox/claude-oauth-token` | `CLAUDE_CODE_OAUTH_TOKEN` | Claude subscription auth |
| `omnigent/devbox/github-token` | `GIT_TOKEN` | git push, incl. **from agents** |

**AWS** needs no secret: the box assumes `omnigent-devbox-ssm` via IMDS.

### The non-obvious bit

`_build_host_daemon_env` **strips** `CLAUDE_CODE_OAUTH_TOKEN` for *remote*
hosts — it survives only in local-daemon mode. A token merely exported in your
shell will silently never reach the agent. It must be injected into the systemd
unit, which is exactly what the `EnvironmentFile=` drop-in does.

`GIT_TOKEN` is in omnigent's `HARNESS_CREDENTIAL_ENV_VARS`, so it is
deliberately forwarded into agent runners — that is what lets Claude push.

---

## Rebuilding from scratch

1. Launch Ubuntu 24.04, t3.medium, 50 GB gp3, profile `omnigent-devbox-ssm`,
   SG `sg-06e59ccfa6fef8952`, `--instance-initiated-shutdown-behavior stop`,
   user-data = `bootstrap/user-data.sh`.
2. Install the box-side units from `bootstrap/` (`fetch-creds.sh`,
   `omnigent-creds.service`, `omnigent-host.service`, `idle-check.sh`,
   `omnigent-devbox-idle.{service,timer}`, `devbox-report`).
3. **Interactive, cannot be automated** — both need a browser:
   - `omnigent login https://omnigent.airbrx.ai` (JumpCloud; prints a URL,
     polls 5 min — open it anywhere)
   - `claude setup-token` → store as the `claude-oauth-token` secret
   - `gh auth login --web` → device code → store as the `github-token` secret
4. `systemctl --user enable --now omnigent-host.service` as `michael`
   (needs `XDG_RUNTIME_DIR=/run/user/1001`).

### Gotchas already paid for

1. **`omnigent host install` does not exist in PyPI 0.8.1** (repo `main` has
   it). Hence the hand-written unit in `bootstrap/`.
2. **uid is 1001, not 1000** — `systemctl --user` over `runuser` needs
   `XDG_RUNTIME_DIR=/run/user/1001` or it cannot reach the bus.
3. **`~/.bashrc` returns early for non-interactive shells** on Ubuntu, so PATH
   exports appended there never apply to `runuser`/systemd. Ubuntu's stock
   `~/.profile` already adds `~/.local/bin`; `~/.npm-global/bin` must be added
   to `.profile`, and the unit bakes `PATH` explicitly.
4. **The AWS CLI on Windows dies with a `charmap` codec error** on any
   non-ASCII output (systemd's `●`/`→`). Export `PYTHONIOENCODING=utf-8`.
   `client/runbox.py` handles this; use it instead of raw `aws ssm send-command`.
5. **PowerShell 5.1 `Set-Content -Encoding utf8` writes a BOM**, which the AWS
   CLI's JSON parser rejects. Write with `UTF8Encoding($false)`.
