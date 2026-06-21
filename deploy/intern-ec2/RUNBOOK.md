# Intern server — EC2 runbook (`interns.airbrx.ai`)

A self-contained Omnigent **coordination server** for the summer internship,
built from the `2026-summer-internship` branch and run on a small AWS EC2
instance, fronted by an **Application Load Balancer (ALB)** that terminates TLS
with the existing ACM wildcard cert `*.airbrx.ai`, with state in an external
**RDS Postgres** so the instance can be torn down and rebuilt without losing
data.

The whole stack is codified in Terraform (`terraform/`) — that's the
recommended path. The manual steps below explain what Terraform builds and
serve as a fallback.

> **Hostname:** this uses **`interns.airbrx.ai`** to match the `*.airbrx.ai`
> ACM cert. (An earlier draft said `interns.airbrx.com`; a `*.airbrx.ai`
> wildcard does not validate a `.com` name, so the host follows the cert.)

## What this is (and isn't)

The EC2 box is **only the coordinator**: it stores accounts and session
history and brokers WebSocket connections. It holds **no model keys** and
**runs no agents**. Each student registers their own laptop as a *host* and
signs in with their **own Claude subscription** — so their sessions execute on
their laptop and model usage bills to their own account. Server-side LLM spend
is therefore **$0**; the only cost is the EC2 instance + ALB + RDS.

```
  Student laptop (host)        ALB (TLS @ *.airbrx.ai)     EC2 t3.small (private)
 ┌─────────────────────┐      ┌──────────────────────┐    ┌────────────────────┐
 │ omnigent + claude   │ 443  │ HTTPS listener        │8000│ omnigent server    │
 │ CLIs, own Claude sub │─────▶│ ACM cert, WS upgrade  │───▶│ (this branch)      │
 │ AGENTS RUN HERE      │◀─────│ → HTTP target group   │◀───│        │           │
 └─────────────────────┘      └──────────────────────┘    └────────┼───────────┘
                                                            5432 (VPC, SG-locked)
                                                          ┌─────────▼───────────┐
                                                          │ RDS Postgres        │
                                                          │ accounts + sessions │
                                                          │ (durable, outlives  │
                                                          │  the instance)      │
                                                          └─────────────────────┘
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

## Provisioning with Terraform (recommended)

`terraform/` builds everything below — RDS, EC2 (with first-boot bootstrap that
clones the branch and runs the compose), ALB + listeners + target group, Route
53 alias, and all security groups. RDS lives in a durable root stack; the
compute lives in a `module.app` gated by `var.deploy_app`, so you can tear the
instance + ALB down between sessions and keep the data.

```bash
cd deploy/intern-ec2/terraform
cp terraform.tfvars.example terraform.tfvars   # fill in vpc/subnets/repo_url
terraform init && terraform plan               # review first — not machine-validated
terraform apply
terraform output url                           # https://interns.airbrx.ai
```

Tear the instance down (keep RDS + data): `terraform apply -var deploy_app=false`.
Bring it back: `terraform apply -var deploy_app=true`. Full details and the
end-of-internship teardown are in [`terraform/README.md`](terraform/README.md).

The rest of this document is the **manual equivalent** — read it to understand
what Terraform does, or to operate the box by hand.

---

## One-time setup (manual)

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

### 2. RDS Postgres (external, durable)

Create an RDS Postgres instance (`db.t4g.micro`, 20 GB, encrypted, **not**
publicly accessible) in a DB subnet group spanning ≥2 AZs. Its security group
allows `5432` only from the EC2 instance's subnet/SG. Note the endpoint and the
master password — they form the `DATABASE_URL` below. Because the database is
external, destroying and rebuilding the EC2 box never touches the data.

### 3. Build & launch (standalone compose — RDS, no postgres, no https overlay)

```bash
git clone <your-repo-url> omnigent
cd omnigent
git checkout 2026-summer-internship
cd deploy/intern-ec2
```

Write `deploy/intern-ec2/.env`. The cookie secret **must be stable** across
instance rebuilds (or sessions invalidate on every redeploy); the explicit
`https://` base URL is what makes login cookies use the secure `__Host-` prefix,
since the ALB terminates TLS and uvicorn doesn't trust `X-Forwarded-Proto`:

```bash
DATABASE_URL=postgresql://omnigent:<password>@<rds-endpoint>:5432/omnigent?sslmode=require
OMNIGENT_ACCOUNTS_COOKIE_SECRET=$(openssl rand -hex 32)   # generate ONCE, reuse forever
OMNIGENT_ACCOUNTS_BASE_URL=https://interns.airbrx.ai
```

Bring it up (the `--build` builds from this branch instead of pulling the
official image):

```bash
docker compose up -d --build         # uses deploy/intern-ec2/docker-compose.yaml
docker compose logs -f omnigent      # watch for a clean boot; Ctrl-C when steady
curl -s localhost:8000/health        # → {"status":"ok"}
```

The container publishes `8000` on the host; the ALB target group points here.

### 4. ACM + ALB

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

### 5. Security groups

| SG | Inbound rule | Source |
|---|---|---|
| `intern-alb-sg` | 443, 80 | `0.0.0.0/0` (or restrict to office/student CIDRs) |
| EC2 instance SG | **8000** | **`intern-alb-sg`** (the SG, not a CIDR) — ALB-only |
| EC2 instance SG | 22 (SSH) | your office/VPN IP only — or skip SSH entirely and use SSM Session Manager |
| RDS SG | **5432** | the EC2 instance subnet/SG only |

The instance must **not** expose 8000, 80, or 443 to the internet — only the
ALB reaches 8000, over the VPC. RDS is never publicly accessible.

### 6. DNS (Route 53, `airbrx.ai` hosted zone)

Create an **Alias** record (not a plain A → IP) pointing at the ALB:

| Name | Type | Alias target |
|---|---|---|
| `interns.airbrx.ai` | `A` (Alias) | the `intern-alb` DNS name |

(Add a matching `AAAA` alias if you want IPv6.) Alias records track the ALB's
rotating IPs automatically — no Elastic IP needed for inbound. An EIP is still
handy for stable SSH/egress, but it is no longer the front door.

### 7. First admin login

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
```

Then roll the box. Under Terraform, bump the instance:

```bash
cd deploy/intern-ec2/terraform && terraform apply   # user_data rebuilds from the branch
```

Or by hand on the box:

```bash
cd /opt/omnigent && git pull
cd deploy/intern-ec2 && docker compose up -d --build
```

Accounts and session history live in **RDS** and are untouched by a rebuild —
the box is stateless except for its local artifact volume. The ALB health check
flips the target healthy again once `/health` responds.

## Rotating / offboarding students

No server-side credentials to clean up (students bring their own laptops +
subscriptions). Offboard: **Members** page → deactivate the account. Onboard
the next student: mint a fresh invite.

## Teardown

**Between sessions (keep the data).** Tear the instance + ALB down, keep RDS:

```bash
cd deploy/intern-ec2/terraform && terraform apply -var deploy_app=false
```

**End of internship (destroy everything).**

```bash
terraform apply  -var deploy_app=false               # compute first
terraform destroy -var db_deletion_protection=false  # then RDS (takes a final snapshot)
```

Leave the `*.airbrx.ai` ACM cert (it's shared/wildcard). Doing it by hand
instead: delete the ALB, target group, and `interns.airbrx.ai` alias record,
terminate the instance, then delete the RDS instance.

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
