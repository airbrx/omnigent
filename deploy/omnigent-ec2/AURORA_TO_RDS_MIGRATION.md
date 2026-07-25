# omnigent.airbrx.ai — Aurora Serverless v2 → provisioned RDS migration

**Status:** planned, not executed. **Motivation:** cut DB cost roughly in half for
a workload that never benefits from serverless.

## Why do this

Aurora Serverless v2 is priced to reward scaling to zero. **This cluster never
scales to zero.** The native server holds a persistent connection pool and the
always-on host tunnel keeps a live session, so Aurora floats at its active floor
(~0.5 ACU) 24/7 — verified via CloudWatch `DatabaseConnections`, which sat at a
flat **7 connections in every 5-minute bucket** with no drop to zero. We are
paying serverless prices for an always-on, tiny workload (server RSS ~225 MB,
load ~0.05, 6 users / ~50 conversations / ~4300 items).

A provisioned burstable-ARM instance (t4g) is a near-perfect fit: low steady
baseline with occasional spikes, at roughly half the cost.

**Confirmed spend (Cost Explorer, 2026-07-01…16, this account):**

- `Aurora:ServerlessV2Usage` **$21.78 / 16 days ≈ $41/mo** compute alone —
  matches the ~0.5 ACU × $0.12/ACU-h estimate exactly.
- `Aurora:StorageIOUsage` $0.89 + `RDS:GP3-Storage` (shared line) — a few $/mo.
- So Aurora is running **~$44–46/mo** all-in.

**Real in-account price for the alternative:** the interns deployment ran a
provisioned **`db.t4g.micro` (20 GB gp3, single-AZ) Postgres** in this same
account/region — Cost Explorer shows `InstanceUsage:db.t4g.micro` at **$9.17 /
16 days ≈ $18/mo**. That is a live datapoint for exactly the target class this
migration proposes: **provisioned Postgres for this workload costs ~$18/mo vs
Aurora's ~$44/mo — a ~60% cut.** (The interns box is being decommissioned, so
this line will disappear from the bill.)

## Cost comparison (us-east-1 list price, ~730 h/mo)

| Option | Compute | + storage (gp3/Aurora) | **Total/mo** |
|---|---|---|---|
| **Aurora SLv2 (current)** | ~0.5 ACU × $0.12 ≈ **$44** | Aurora storage $0.10/GB + I/O | **~$45–55** |
| **RDS db.t4g.micro** (1 GB) | $0.016/h ≈ **$11.7** | gp3 20 GB ~$1.6 | **~$14** |
| **RDS db.t4g.small** (2 GB) ← recommended | $0.032/h ≈ **$23** | gp3 20 GB ~$1.6 | **~$25** |
| RDS db.t4g.medium (4 GB) | $0.065/h ≈ **$47** | gp3 20 GB ~$1.6 | ~$49 |

**Recommendation: `db.t4g.small`, gp3 20 GB, single-AZ, PostgreSQL 16.**
~$25/mo, roughly half the current spend, 2 GB RAM gives headroom over micro.
`t4g.micro` (~$14/mo) would also handle this load — the server barely touches
the DB — if we want to squeeze further.

> Confirm the exact current Aurora spend before/after with Cost Explorer:
> ```
> aws ce get-cost-and-usage --time-period Start=$(date -u -d '30 days ago' +%F),End=$(date -u +%F) \
>   --granularity MONTHLY --metrics UnblendedCost \
>   --filter '{"Dimensions":{"Key":"SERVICE","Values":["Amazon Relational Database Service"]}}' \
>   --group-by Type=DIMENSION,Key=USAGE_TYPE
> ```

## What we give up (and why it's fine here)

Aurora's edge is a 6-way storage replica across 3 AZs, sub-second failover, and
independent storage/compute scaling. For an **experimental single-box server**
none of that is load-bearing — it's durability/HA we aren't using. Full-text
search is already a no-op on Postgres (SQLite-FTS5-only), so no change there.

## This is a migration, not a toggle

You cannot convert Aurora Serverless v2 → provisioned RDS in place — they are
different engines (`aurora-postgresql` vs `postgres`). It's a `pg_dump` from
Aurora → fresh RDS instance → `pg_restore` → swap `DATABASE_URL` → restart.
Small DB → ~10–15 min maintenance window. Logical replication would give
near-zero downtime but is overkill at this size.

## Facts to confirm before starting (fill in)

- Aurora cluster: `omnigent-pg`, instance `omnigent-pg-1`, engine **16.9**,
  endpoint `omnigent-pg.cluster-cqlqk4asq42t.us-east-1.rds.amazonaws.com:5432`,
  db `omnigent`.
- Aurora SG: `sg-09785e493a94bd66b` (5432 from EC2 SG `sg-0c2c41c2f30b76305`),
  subnet group `omnigent-pg-subnets`.
- App box: EC2 `i-02eb2f52439574844` (`t3.small`), native server, driven over
  **SSM send-command** (no interactive shell needed).
- `DATABASE_URL` lives in `/etc/omnigent/server.env` **and** Secrets Manager
  `omnigent/airbrx/database_url`, in the working **`postgresql+psycopg://`** form.
- Check installed extensions on Aurora (expect none beyond defaults):
  `SELECT extname FROM pg_extension;` — recreate any non-default ones on target.

## Migration steps

Run DB steps from the box via SSM as the venv python/psql, or locally if you can
reach both endpoints. The box can already reach Aurora; put the new RDS in the
**same VPC + subnet group + a SG that allows 5432 from the EC2 SG** so the box
can reach it too.

1. **Snapshot Aurora** (safety, keep for the rollback window):
   ```
   aws rds create-db-cluster-snapshot --db-cluster-identifier omnigent-pg \
     --db-cluster-snapshot-identifier omnigent-pg-pre-rds-migration-$(date -u +%Y%m%dt%H%M%Sz)
   ```

2. **Provision the target RDS** (Terraform preferred; CLI shown for clarity):
   ```
   aws rds create-db-instance \
     --db-instance-identifier omnigent-pg-rds \
     --db-instance-class db.t4g.small \
     --engine postgres --engine-version 16.9 \
     --allocated-storage 20 --storage-type gp3 \
     --no-multi-az \
     --db-name omnigent \
     --master-username <admin> --master-user-password <pw> \
     --db-subnet-group-name omnigent-pg-subnets \
     --vpc-security-group-ids <new-or-reused-sg-allowing-5432-from-EC2-SG> \
     --backup-retention-period 7 --no-publicly-accessible
   ```
   Prefer codifying this in `deploy/omnigent-ec2/terraform/` alongside the
   cluster so the box stays reproducible; import or replace the hand-made
   resources as needed.

3. **Dump Aurora** (schema + data, custom format):
   ```
   pg_dump "postgresql://<user>:<pw>@<aurora-endpoint>:5432/omnigent" \
     -Fc -f /tmp/omnigent-$(date -u +%Y%m%dt%H%M%Sz).dump
   ```
   Match the client `pg_dump`/`pg_restore` major version to the server (16.x).

4. **(Maintenance window opens)** Stop the server so no writes race the cutover:
   `systemctl stop omnigent-server`. Take a **final** dump to capture last writes
   (repeat step 3).

5. **Restore into the new RDS**:
   ```
   pg_restore --no-owner --no-privileges \
     -d "postgresql://<admin>:<pw>@<new-rds-endpoint>:5432/omnigent" \
     /tmp/omnigent-<final>.dump
   ```
   (`createdb omnigent` first if the instance came up without the named DB.)

6. **Verify row counts match** the pre-cutover source (users, conversations,
   conversation_items, hosts, session_permissions, labels). Reuse the same
   read-only count script pattern used in prior migrations.

7. **Swap the connection string** — keep the `postgresql+psycopg://` prefix:
   - `/etc/omnigent/server.env` → new RDS endpoint.
   - Secrets Manager `omnigent/airbrx/database_url` → new RDS endpoint.

8. **Restart + migrate**: `systemctl restart omnigent-server`. Boot runs
   `alembic upgrade head` (should be a no-op — schema came over in the dump).

9. **Verify**: `curl -fsS https://omnigent.airbrx.ai` → 200; spot-check the app
   (login, a session loads, hosts list). Confirm the new RDS shows the live
   connections in CloudWatch.

10. **(Maintenance window closes.)**

## Rollback

Within the window: point `DATABASE_URL` back to the Aurora endpoint (both
`server.env` and Secrets Manager), `systemctl restart omnigent-server`. Aurora
was never dropped, so this is instant. Keep Aurora **stopped but not deleted**
for a few days after cutover, then delete the cluster + the
`omnigent-pg-pre-rds-migration-*` snapshot once confident.

## Gotchas (carried from prior box work)

- `_create_engine` does **not** normalize the URL, so `DATABASE_URL` MUST be
  `postgresql+psycopg://…` — bare `postgresql://` routes to the absent psycopg2
  dialect and crash-loops the server.
- Update **both** `server.env` and Secrets Manager, or a future rebuild reads a
  stale endpoint.
- New SG must allow 5432 **from the EC2 SG** (`sg-0c2c41c2f30b76305`), mirroring
  the Aurora SG rule.
- The auto-deploy workflow does NOT snapshot the DB; snapshots here are manual.
- FTS stays disabled on Postgres — no action.
