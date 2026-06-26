# Terraform — omnigent server (EC2 + ALB + Aurora Serverless v2)

Provisions the `omnigent.airbrx.ai` server on AWS, fronted by an ALB with the
`*.airbrx.ai` ACM cert, authenticated by JumpCloud OIDC, backed by an Aurora
PostgreSQL Serverless v2 cluster. See `../RUNBOOK.md` for the full picture.

> **Reality check.** The live box currently runs the **native installer**
> (systemd + Caddy, direct A-record) and the Aurora cluster (`omnigent-pg`) was
> created by hand with the AWS CLI. This stack codifies the **Docker + ALB**
> target. Applying it as-is creates a *new* parallel stack; to adopt the live
> Aurora cluster, **import** it first (below). Moving the box itself to
> Docker+ALB is a deliberate cutover documented in the RUNBOOK.

## Layout

```
terraform/
├── versions.tf        provider + S3 backend (create the bucket first — see versions.tf)
├── variables.tf       inputs — see terraform.tfvars.example
├── main.tf            DURABLE layer: Aurora Serverless v2, secrets, lookups, app module
├── outputs.tf         url, alb dns, instance id, cluster endpoint
└── modules/app/       EPHEMERAL layer (toggled by var.deploy_app):
    ├── main.tf        EC2 + ALB + listeners + target group + Route 53 alias + SGs
    ├── user_data.sh.tftpl   first-boot: build from the branch, write .env + admins, compose up
    └── …
```

**Durable vs ephemeral:** the Aurora cluster + the generated DB password and
OIDC cookie secret live in the root stack and persist. EC2/ALB/DNS live in
`module.app`, gated by `var.deploy_app`. Flip it off to stop paying for compute
without losing data.

## Prerequisites

- Terraform ≥ 1.5, AWS provider ≥ 5.80 (Serverless v2 scale-to-zero), creds for
  account 724412576111.
- The `*.airbrx.ai` cert **Issued** in ACM in `var.region`.
- The `airbrx.ai` Route 53 hosted zone.
- A JumpCloud OIDC app whose callback is **`https://omnigent.airbrx.ai/auth/callback`**;
  you supply `oidc_client_id` + `oidc_client_secret`.
- The state bucket created (see `versions.tf` header).

## Use

```bash
cd deploy/omnigent-ec2/terraform
cp terraform.tfvars.example terraform.tfvars   # fill in vpc/subnets + oidc_client_secret
terraform init
terraform plan      # REVIEW — not machine-validated
terraform apply
terraform output url    # https://omnigent.airbrx.ai
```

## Adopt the existing Aurora cluster (import, don't recreate)

The live cluster was made by CLI with the same names this stack uses, so import
is 1:1. Run before the first apply that should manage the DB:

```bash
terraform import aws_rds_cluster.this omnigent-pg
terraform import aws_rds_cluster_instance.this omnigent-pg-1
terraform import aws_db_subnet_group.this omnigent-pg-subnets
terraform import aws_security_group.rds sg-09785e493a94bd66b
# random_password.db / random_id.cookie cannot be imported — Terraform will
# generate NEW values. Either accept a password rotation (update the running
# server's DATABASE_URL to match) or refactor these to a data source / SSM
# lookup of the existing secret before applying.
```

`terraform plan` after import should show no changes to the cluster (adjust
`db_engine_version` etc. to match if it does).

## Tear the instance down (keep the data)

```bash
terraform apply -var deploy_app=false    # destroys EC2 + ALB + DNS; Aurora stays
```

## Full teardown

```bash
terraform apply  -var deploy_app=false
terraform destroy -var db_deletion_protection=false   # Aurora (takes a final snapshot)
```

## Notes / caveats

- **State holds secrets** (DB password, OIDC cookie secret, JumpCloud client
  secret). Keep the S3 backend encrypted/private; never commit state.
- **Not machine-validated.** No `terraform`/AWS where this was authored — run
  `terraform validate` + `plan` and expect to adjust (engine version
  availability, the scale-to-zero arguments per provider version, subnet/AZ
  specifics, AMI filter).
- **Scale-to-zero may rarely trigger** — the server holds a persistent pool and
  an always-connected host tunnel, so Aurora often stays awake. Set
  `db_min_acu = 0.5` for a predictable warm floor if you prefer no cold starts.
- **Full-text search is disabled on Postgres** (SQLite-FTS5-only upstream) —
  accepted tradeoff.
- **Artifacts** sit on the instance's local volume and are lost on
  `deploy_app=false`; accounts/sessions are in Aurora and survive.
