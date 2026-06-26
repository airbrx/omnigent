# omnigent server — EC2 runbook (`omnigent.airbrx.ai`)

The airbrx Omnigent **coordination server** — the original/experimental one,
separate from the interns deployment (`interns.airbrx.ai`, see
`../intern-ec2/`). Authenticated by **JumpCloud SSO (OIDC)** with
auto-provisioning, backed by an external **Aurora PostgreSQL Serverless v2**
cluster so the box is disposable.

> **Two states, read this first.** The box you can reach today runs the
> **native upstream installer** (systemd + Caddy, direct A-record). This
> directory codifies the **Docker + ALB** target (mirroring `../intern-ec2/`).
> The "Current live setup" section describes what's actually running; the
> "Target / Terraform" sections describe what this IaC builds and how to cut
> over. They are intentionally both here.

## What this is (and isn't)

The EC2 box is **only the coordinator**: it stores accounts + session history
and brokers WebSocket connections. It holds **no model keys** and **runs no
agents**. Each person registers their own laptop as a *host* and signs in with
their own model subscription. Server-side LLM spend is **$0**.

```
  Laptop (host)             ALB (TLS @ *.airbrx.ai)     EC2 t3.small
 ┌──────────────────┐  443 ┌────────────────────┐ 8000 ┌──────────────────┐
 │ omnigent CLIs    │─────▶│ HTTPS, WS upgrade   │─────▶│ omnigent (Docker) │
 │ AGENTS RUN HERE  │◀─────│ → HTTP target group │◀─────│        │          │
 └──────────────────┘      └────────────────────┘      └────────┼─────────┘
        JumpCloud OIDC login ▲                          5432 (VPC, SG-locked)
                                                       ┌─────────▼──────────┐
                                                       │ Aurora Serverless  │
                                                       │ v2 (min 0 / max 2) │
                                                       │ accounts+sessions  │
                                                       └────────────────────┘
```

---

## Current live setup (native — what's running today)

- **EC2** `i-02eb2f52439574844` ("omnigent-server"), t3.small, default VPC
  `vpc-05b69c7707282a5fe`, SG `sg-0c2c41c2f30b76305`. Shell in:
  `aws ssm start-session --target i-02eb2f52439574844` (no SSH key; SSM only).
- **App:** systemd `omnigent-server.service` runs `omnigent server --host
  127.0.0.1 --port 6767` from a uv-tool venv (`~/.local/.../omnigent`), env from
  `/etc/omnigent/server.env`. A systemd drop-in
  (`omnigent-server.service.d/override.conf`) appends `--database-uri
  ${DATABASE_URL}` (the CLI has no env binding for the DB).
- **TLS:** native **Caddy** (`/etc/caddy/Caddyfile`) reverse-proxies
  `omnigent.airbrx.ai → 127.0.0.1:6767`. DNS is a **direct A-record** to the
  instance public IP (no ALB).
- **DB:** **Aurora PostgreSQL Serverless v2**, cluster `omnigent-pg` / instance
  `omnigent-pg-1`, endpoint
  `omnigent-pg.cluster-cqlqk4asq42t.us-east-1.rds.amazonaws.com:5432`, db
  `omnigent`, SG `sg-09785e493a94bd66b`, subnet group `omnigent-pg-subnets`,
  scaling min 0 / max 2 ACU, auto-pause 300s. `DATABASE_URL` is in Secrets
  Manager `omnigent/airbrx/database_url` and in `/etc/omnigent/server.env`.
  Migrated from local SQLite on 2026-06-25 (the old `/var/lib/omnigent/chat.db`
  is retained on-box as a fallback).

### Native gotchas (already handled on the live box; here so they're not relearned)

1. **DB driver:** the uv-tool venv lacks Postgres support out of the box —
   `uv pip install --python ~/.local/share/uv/tools/omnigent/bin/python 'psycopg[binary]'`.
2. **DB URL dialect:** the native server's `_create_engine` does NOT normalize
   the URL, so `DATABASE_URL` MUST be the explicit `postgresql+psycopg://…` form
   (plain `postgresql://` selects the absent psycopg2 dialect and crash-loops).
   *(The Docker entrypoint normalizes automatically — this is native-only.)*
3. **DB flag wiring:** `omnigent server` has no env var for the DB; the systemd
   drop-in passes `--database-uri ${DATABASE_URL}` from `server.env`.
4. **Admin roster path:** `resolve_admin_list_path()` uses `~/.omnigent/admins`
   and **ignores `OMNIGENT_DATA_DIR`**. Set `OMNIGENT_ADMIN_LIST_PATH=/var/lib/omnigent/admins`
   in `server.env` (requires restart) so the roster is actually consulted.

---

## Auth — JumpCloud OIDC

- `OMNIGENT_AUTH_PROVIDER=oidc`, issuer `https://oauth.id.jumpcloud.com/`,
  client id `ab49aaeb-9398-4624-9d3f-55696b5c6680`, allowed domains
  `airbrx.com,airbrx.ai`, session TTL 720h.
- The JumpCloud app's callback MUST be exactly
  **`https://omnigent.airbrx.ai/auth/callback`** (the server derives it from
  `OMNIGENT_DOMAIN`).
- Any `airbrx.com`/`airbrx.ai` JumpCloud user auto-provisions as a **non-admin
  member** on first login.

### Admin

Admin is the only thing the roster file controls. Listed emails are promoted to
admin on **login** (additive, case-insensitive, never demotes). Edit the roster
(`admins.example` → `/etc/omnigent/admins` native, or `/config/admins` in
Docker), then the user logs out/in. To grant admin immediately without a login:

```bash
# on the box, against Aurora
psql "$DATABASE_URL" -c "update users set is_admin=true where id='someone@airbrx.com';"
```

---

## Target / Terraform (Docker + ALB)

`terraform/` builds the Docker+ALB version of this stack — Aurora (durable root)
+ EC2/ALB/DNS (`module.app`, toggled by `var.deploy_app`). See
[`terraform/README.md`](terraform/README.md), including how to **import** the
existing Aurora cluster rather than create a second one.

```bash
cd deploy/omnigent-ec2/terraform
cp terraform.tfvars.example terraform.tfvars   # vpc/subnets + oidc_client_secret
terraform init && terraform plan               # review — not machine-validated
terraform apply
```

Tear compute down (keep data): `terraform apply -var deploy_app=false`.

### Native → Docker+ALB cutover (when you choose to do it)

1. `terraform apply` (after importing Aurora) to stand up the ALB + a fresh
   Docker instance pointing at the same cluster. Verify the new instance is
   healthy in the target group (`/health` → 200).
2. Flip DNS: the `aws_route53_record.app` alias replaces the direct A-record.
3. Decommission the native box (stop `omnigent-server` + `caddy`, terminate the
   old instance). Keep a DB backup first (below).

---

## Backups

Aurora has 7-day automated backups + a final snapshot on destroy. For a
portable dump:

```bash
pg_dump "$DATABASE_URL" | gzip | \
  aws s3 cp - s3://airbrx-omnigent-backups-724412576111/omnigent.airbrx.ai/manual/$(date -u +%Y%m%dT%H%M%SZ).sql.gz
```

The pre-Postgres-migration SQLite snapshot lives at
`s3://airbrx-omnigent-backups-724412576111/omnigent.airbrx.ai/pre-postgres-migration/`.

---

## Updating the server

```bash
git checkout omnigent-airbrx-server && git merge main && git push   # bring in fixes
```

Then roll the box. Docker/Terraform: `terraform apply` (user_data rebuilds from
the branch). Native (live box): `cd /opt/omnigent && git pull && uv tool upgrade
omnigent` then `sudo systemctl restart omnigent-server`. Accounts + sessions
live in Aurora and survive a rebuild.

---

## Troubleshooting

- **ALB target unhealthy.** Health check must hit HTTP :8000 `/health` → 200;
  instance SG must allow 8000 **from the ALB SG**.
- **Sessions drop after ~1 min.** Raise ALB idle timeout to 3600s (long-lived
  WebSockets). Terraform sets this.
- **OIDC redirect/login fails.** `OMNIGENT_DOMAIN` must be `omnigent.airbrx.ai`
  and the JumpCloud callback must be `https://omnigent.airbrx.ai/auth/callback`.
- **Server crash-loops on `psycopg2` ModuleNotFoundError (native).** Use the
  `postgresql+psycopg://` URL form — gotcha #2 above.
- **Listed admin never promoted (native).** Roster path — gotcha #4 above.
- **DB seems paused / first query slow.** Scale-to-zero cold start (~10-15s);
  set `db_min_acu = 0.5` to avoid it.
