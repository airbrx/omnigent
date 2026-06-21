# Intern server — EC2 runbook (`interns.airbrx.ai`)

A self-contained Omnigent **coordination server** for the summer internship,
built from the `2026-summer-internship` branch and run on a small AWS EC2
instance, fronted by an **Application Load Balancer (ALB)** that terminates TLS
with the existing ACM wildcard cert `*.airbrx.ai`.

> **Hostname:** this uses **`interns.airbrx.ai`** to match the `*.airbrx.ai`
> ACM cert. (An earlier draft said `interns.airbrx.com`; a `*.airbrx.ai`
> wildcard does not validate a `.com` name, so the host follows the cert.)

## What this is (and isn't)

The EC2 box is **only the coordinator**: it stores accounts and session
history and brokers WebSocket connections. It holds **no model keys** and
**runs no agents**. Each student registers their own laptop as a *host* and
signs in with their **own Claude subscription** — so their sessions execute on
their laptop and model usage bills to their own account. Server-side LLM spend
is therefore **$0**; the only cost is the EC2 instance + ALB.

```
  Student laptop (host)        ALB (TLS @ *.airbrx.ai)     EC2 t3.small (private)
 ┌─────────────────────┐      ┌──────────────────────┐    ┌────────────────────┐
 │ omnigent + claude   │ 443  │ HTTPS listener        │8000│ omnigent server    │
 │ CLIs, own Claude sub │─────▶│ ACM cert, WS upgrade  │───▶│ (this branch)      │
 │ AGENTS RUN HERE      │◀─────│ → HTTP target group   │◀───│ postgres (accounts │
 └─────────────────────┘      └──────────────────────┘    │  + sessions)       │
                                                            └────────────────────┘
```

## TLS architecture: why an ALB

An ACM certificate's private key is **not exportable**, so it cannot be
installed on the instance (no Caddy/nginx/Let's Encrypt on the box). ACM certs
attach only to AWS-managed front doors. For a WebSocket coordinator the right
one is an **ALB**: it terminates TLS with `*.airbrx.ai`, natively proxies the
HTTP/1.1 WebSocket upgrade, and forwards plain HTTP to the container inside the
VPC. **The Caddy HTTPS overlay (`docker-compose.https.yaml`) is not used.**

## Branch model

- The intern server tracks the **`2026-summer-internship`** branch.
- Customizations/fixes land on `main`. To ship updates to the students, merge
  `main → 2026-summer-internship`, push, then rebuild on the box (see
  [Updating](#updating-the-server)). Nothing reaches the interns until you
  deliberately merge — the branch is a gate, not a mirror of `main`.

---

## One-time setup

### 1. EC2 instance (private app server)

- **Type: `t3.small` (2 GB RAM) minimum.** A `t3.micro` (1 GB) will OOM during
  the image build (web UI + Python deps). On `t3.micro`, build the image
  elsewhere and pull it instead of `--build`.
- **AMI:** Ubuntu 24.04 LTS (x86_64). **Disk:** 20 GB gp3.
- Place it in a subnet in the same VPC as the ALB. A private subnet (with NAT
  for outbound) is ideal; a public subnet is fine too — inbound HTTP is locked
  to the ALB's security group either way (step 4).

Install the host packages:

```bash
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-v2 git
sudo usermod -aG docker "$USER"   # log out/in so docker works without sudo
```

### 2. Build & launch (base compose — NO https overlay)

```bash
git clone <your-repo-url> omnigent
cd omnigent
git checkout 2026-summer-internship

cd deploy/docker
./bootstrap.sh                    # mints POSTGRES_PASSWORD + cookie secret into .env
```

Add the public-URL setting to `deploy/docker/.env` (bootstrap already wrote the
secrets). The ALB terminates TLS and forwards plain HTTP, and uvicorn does not
trust `X-Forwarded-Proto` — so this explicit `https://` base URL is what makes
login cookies use the secure `__Host-` prefix and redirects resolve correctly:

```bash
OMNIGENT_ACCOUNTS_BASE_URL=https://interns.airbrx.ai
# OMNIGENT_DOMAIN is only consumed by the Caddy overlay — leave it unset here.
```

Bring it up with the **base compose only** (the `--build` is what builds from
this branch instead of pulling the official image):

```bash
docker compose up -d --build
docker compose logs -f omnigent      # watch for a clean boot; Ctrl-C when steady
```

The container publishes `8000` on the host; the ALB target group points here.
Confirm liveness locally before wiring the ALB:

```bash
curl -s localhost:8000/health        # → {"status":"ok"}
```

### 3. ACM + ALB

1. **Cert:** confirm the `*.airbrx.ai` certificate is **Issued** in ACM **in
   the same region** as the ALB (ALB certs must be regional, not us-east-1
   unless that's your region).
2. **Target group** (`intern-tg`): target type **Instance**, protocol **HTTP**,
   port **8000**, your VPC. Health check path **`/health`**, success code
   **200**. Register the EC2 instance.
3. **ALB** (`intern-alb`): **internet-facing**, across **≥2 public subnets in
   different AZs** (an AWS requirement even for one backend). Give it a security
   group `intern-alb-sg`.
4. **Listeners:**
   - **HTTPS :443** → default action **forward to `intern-tg`**, cert =
     `*.airbrx.ai` (ACM), a modern TLS security policy.
   - **HTTP :80** → **redirect to HTTPS :443** (301).
5. **Idle timeout:** raise the ALB attribute **idle timeout to 3600s** (default
   60s). Agent sessions hold long-lived WebSockets; 60s would drop them.

### 4. Security groups

| SG | Inbound rule | Source |
|---|---|---|
| `intern-alb-sg` | 443, 80 | `0.0.0.0/0` (or restrict to office/student CIDRs) |
| EC2 instance SG | **8000** | **`intern-alb-sg`** (the SG, not a CIDR) — ALB-only |
| EC2 instance SG | 22 (SSH) | your office/VPN IP only |

The instance must **not** expose 8000, 80, or 443 to the internet — only the
ALB reaches 8000, over the VPC.

### 5. DNS (Route 53, `airbrx.ai` hosted zone)

Create an **Alias** record (not a plain A → IP) pointing at the ALB:

| Name | Type | Alias target |
|---|---|---|
| `interns.airbrx.ai` | `A` (Alias) | the `intern-alb` DNS name |

(Add a matching `AAAA` alias if you want IPv6.) Alias records track the ALB's
rotating IPs automatically — no Elastic IP needed for inbound. An EIP is still
handy for stable SSH/egress, but it is no longer the front door.

### 6. First admin login

```bash
docker compose logs omnigent | grep -A4 "Created initial admin"
```

Open `https://interns.airbrx.ai`, sign in as that admin, change the password.
(The credential also persists at `/data/admin-credentials` on the
`artifact-data` volume, surviving restarts.)

---

## Inviting students

Web UI → your username (top-right) → **Members → Invite member**. Send the
single-use link; the student picks their own username + password on redeem.
Signup is invite-only.

Optionally pin a stable admin roster: copy `deploy/intern-ec2/config.yaml` to
the box's volume as `/data/config.yaml` (admin **usernames** for accounts mode)
and restart.

## Student onboarding (each student, once, on their own laptop)

Prereqs on the laptop: **Node.js 22+**, **tmux**, and on Linux **bubblewrap**
(`bwrap`); macOS needs nothing extra. Then install Omnigent (repo README).

```bash
claude login                                   # their OWN Claude Pro/Max subscription
omnigent login https://interns.airbrx.ai       # redeem-account login
omnigent host  https://interns.airbrx.ai       # register THIS laptop as a host
```

Then open `https://interns.airbrx.ai` → **New Chat**, pick their laptop as the
host, and run. Their Claude subscription is the credential; the session runs on
their machine.

---

## Updating the server

```bash
# on your workstation
git checkout 2026-summer-internship
git merge main            # bring in vetted customizations/fixes
git push

# on the EC2 box
cd ~/omnigent && git pull
cd deploy/docker
docker compose up -d --build
```

Account/session data live on the `postgres-data` / `artifact-data` Docker
volumes and survive a rebuild. The ALB health check flips the target healthy
again once `/health` responds.

## Rotating / offboarding students

No server-side credentials to clean up (students bring their own laptops +
subscriptions). Offboard: **Members** page → deactivate the account. Onboard
the next student: mint a fresh invite.

## Teardown (end of internship)

```bash
cd ~/omnigent/deploy/docker
docker compose down -v          # drops the DB + artifacts
```

Then in AWS: delete the ALB, the target group, and the `interns.airbrx.ai`
alias record; terminate the instance. Leave the `*.airbrx.ai` ACM cert (it's
shared/wildcard).

---

## Troubleshooting

- **ALB target unhealthy.** Health check must hit **HTTP :8000 `/health`** and
  expect **200**. Verify the instance SG allows 8000 **from `intern-alb-sg`**,
  and `curl localhost:8000/health` returns `{"status":"ok"}` on the box.
- **502 / 504 from the ALB.** Container not up or not publishing 8000
  (`docker compose ps`), or the target group port ≠ 8000.
- **Sessions drop after ~1 minute.** ALB idle timeout still at the 60s default —
  raise it to 3600s. WebSockets are long-lived.
- **Login works but redirect/cookie is wrong (or "insecure cookie" errors).**
  `OMNIGENT_ACCOUNTS_BASE_URL` must be exactly `https://interns.airbrx.ai` —
  the public URL the browser sees, with `https`. The app doesn't infer the
  scheme from the ALB, so this env var is mandatory here.
- **Cert not selectable on the listener.** The ACM cert must be **Issued** and
  **in the ALB's region**.
- **Build OOMs / hangs.** Too small an instance — use `t3.small`+ or build the
  image off-box and pull it.
- **Student can't start a session.** They skipped `omnigent host`, or their
  `claude` CLI isn't logged in — both run on *their* laptop, not the server.
