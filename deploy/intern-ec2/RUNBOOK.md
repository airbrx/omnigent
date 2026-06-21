# Intern server — EC2 runbook (`interns.airbrx.com`)

A self-contained Omnigent **coordination server** for the summer internship,
built from the `2026-summer-internship` branch and run on a small AWS EC2
instance behind `interns.airbrx.com`.

## What this is (and isn't)

The EC2 box is **only the coordinator**: it stores accounts and session
history and brokers WebSocket connections. It holds **no model keys** and
**runs no agents**. Each student registers their own laptop as a *host* and
signs in with their **own Claude subscription** — so their sessions execute on
their laptop and model usage bills to their own account. Server-side LLM spend
is therefore **$0**; the only cost is the EC2 instance.

```
  Student laptop (host)                 EC2 t3.small (coordinator)
 ┌─────────────────────┐              ┌────────────────────────────────┐
 │ omnigent + claude   │  register    │ Caddy  → HTTPS (Let's Encrypt) │
 │ CLIs, own Claude sub │ ──host──────▶│ omnigent server (this branch)  │
 │ AGENTS RUN HERE      │◀─coordinate──│ postgres (accounts + sessions) │
 └─────────────────────┘              └────────────────────────────────┘
```

## Branch model

- The intern server tracks the **`2026-summer-internship`** branch.
- Day-to-day customizations and fixes land on `main`. To ship updates to the
  students, merge `main → 2026-summer-internship`, push, then rebuild on the
  box (see [Updating](#updating-the-server)). Nothing reaches the interns until
  you deliberately merge — the branch is a gate, not a mirror of `main`.

---

## One-time setup

### 1. DNS (Route 53)

You own `airbrx.com` in Route 53. After the instance has an Elastic IP
(step 2), create one record:

| Name | Type | Value | TTL |
|---|---|---|---|
| `interns.airbrx.com` | `A` | the instance's Elastic IP | 300 |

Caddy uses the Let's Encrypt **HTTP-01** challenge on port 80, so the A record
must resolve to the box *before* you bring the HTTPS overlay up.

### 2. EC2 instance

- **Type: `t3.small` (2 GB RAM) minimum.** A `t3.micro` (1 GB) will OOM during
  the image build (it compiles the web UI + Python deps). If you must use
  `t3.micro`, build the image elsewhere and pull it instead of `--build`.
- **AMI:** Ubuntu 24.04 LTS (x86_64).
- **Disk:** 20 GB gp3 is plenty.
- **Elastic IP:** allocate one and associate it, so the address survives a
  stop/start. Point the Route 53 record at it.
- **Security group (inbound):**

  | Port | Source | Why |
  |---|---|---|
  | 22 (SSH) | your office/VPN IP only | admin access |
  | 80 (HTTP) | `0.0.0.0/0` | Let's Encrypt HTTP-01 challenge + redirect to 443 |
  | 443 (HTTPS) | `0.0.0.0/0` | the web UI + WebSocket coordinator |

  Do **not** open 8000 — with the HTTPS overlay the omnigent container is not
  published to the host; only Caddy's 80/443 are.

### 3. Host packages

```bash
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-v2 git
sudo usermod -aG docker "$USER"   # log out/in so docker works without sudo
```

### 4. Build & launch

```bash
git clone <your-repo-url> omnigent
cd omnigent
git checkout 2026-summer-internship

cd deploy/docker
./bootstrap.sh                    # mints POSTGRES_PASSWORD + cookie secret into .env
```

Add the public-domain settings to `deploy/docker/.env` (bootstrap already wrote
the secrets — only add these two lines):

```bash
OMNIGENT_DOMAIN=interns.airbrx.com
OMNIGENT_ACCOUNTS_BASE_URL=https://interns.airbrx.com
```

Bring it up with **both** compose files (the `--build` is what makes it build
from this branch instead of pulling the official image):

```bash
docker compose -f docker-compose.yaml -f docker-compose.https.yaml up -d --build
docker compose logs -f omnigent      # watch for a clean boot; Ctrl-C when steady
```

### 5. First admin login

Grab the auto-generated admin password from the logs:

```bash
docker compose logs omnigent | grep -A4 "Created initial admin"
```

Open `https://interns.airbrx.com`, sign in as that admin, then change the
password. (The credential also persists at `/data/admin-credentials` on the
`artifact-data` volume, surviving restarts.)

---

## Inviting students

Web UI → your username (top-right) → **Members → Invite member**. Send the
single-use link to the student; they pick their own username + password when
they redeem it. Signup is invite-only — no link, no account.

Optionally pin a stable admin roster that survives restarts: copy
`deploy/intern-ec2/config.yaml` to the box's volume as `/data/config.yaml`
(it lists admin **usernames** for accounts mode) and restart.

## Student onboarding (each student, once, on their own laptop)

Prereqs on the laptop: **Node.js 22+**, **tmux**, and on Linux **bubblewrap**
(`bwrap`); macOS needs nothing extra. Then install Omnigent (see the repo
README's install section).

```bash
claude login                                   # their OWN Claude Pro/Max subscription
omnigent login https://interns.airbrx.com      # redeem-account login
omnigent host  https://interns.airbrx.com      # register THIS laptop as a host
```

After `omnigent host`, the student opens `https://interns.airbrx.com`, hits
**New Chat**, picks their laptop as the host, and runs. Their Claude
subscription is the credential; the session executes on their machine.

---

## Updating the server

When you want the students to get newer code:

```bash
# on your workstation
git checkout 2026-summer-internship
git merge main            # bring in vetted customizations/fixes
git push

# on the EC2 box
cd ~/omnigent && git pull
cd deploy/docker
docker compose -f docker-compose.yaml -f docker-compose.https.yaml up -d --build
```

Account and session data live on the `postgres-data` / `artifact-data` Docker
volumes and are untouched by a rebuild.

## Rotating / offboarding students

Because students bring their own laptops and subscriptions, there are no
server-side credentials to clean up. To offboard: **Members** page → deactivate
the departing student's account. To onboard the next one: mint a fresh invite.

## Teardown (end of internship)

```bash
cd ~/omnigent/deploy/docker
docker compose -f docker-compose.yaml -f docker-compose.https.yaml down -v   # -v drops the DB + artifacts
```

Then release the Elastic IP and terminate the instance in the AWS console, and
delete the `interns.airbrx.com` A record in Route 53.

---

## Troubleshooting

- **Cert won't issue / 526 / connection refused on 443.** Confirm the A record
  resolves to the Elastic IP (`dig interns.airbrx.com`) and that port 80 is
  open to `0.0.0.0/0` — Let's Encrypt's HTTP-01 challenge needs it. Watch
  `docker compose logs caddy`.
- **Build OOMs / hangs.** You're on too small an instance — use `t3.small`+ or
  build the image off-box and pull it.
- **Student can't start a session.** They likely skipped `omnigent host`, or
  their `claude` CLI isn't logged in. Both run on *their* laptop, not the
  server.
- **Login works but redirect/cookie is wrong.** `OMNIGENT_ACCOUNTS_BASE_URL`
  must exactly match `https://interns.airbrx.com` (the URL the browser sees),
  not the container address.
