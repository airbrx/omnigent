# Terraform — intern omnigent (EC2 + ALB + RDS)

Provisions the intern coordination server on AWS, fronted by `interns.airbrx.ai`
with the `*.airbrx.ai` ACM cert. See `../RUNBOOK.md` for the full picture and
the manual fallback.

## Layout

```
terraform/
├── versions.tf        provider + (commented) S3 backend
├── variables.tf       inputs — see terraform.tfvars.example
├── main.tf            DURABLE layer: RDS, secrets, lookups, app module call
├── outputs.tf         url, alb dns, instance id, rds endpoint
└── modules/app/       EPHEMERAL layer (toggled by var.deploy_app):
    ├── main.tf        EC2 + ALB + listeners + target group + Route 53 alias + SGs
    ├── user_data.sh.tftpl   first-boot: build from the branch, run docker compose
    └── …
```

**Durable vs ephemeral** is the whole point: RDS + the generated DB password and
cookie secret live in the root stack and persist. EC2/ALB/DNS live in
`module.app`, gated by `var.deploy_app`. Flip it off to stop paying for compute
between internship sessions without losing accounts or session history.

## Prerequisites

- Terraform ≥ 1.5 and AWS credentials for the target account.
- The `*.airbrx.ai` cert **Issued** in ACM **in `var.region`**.
- An existing VPC with ≥2 public subnets (ALB) and ≥2 private subnets (RDS).
- The `airbrx.ai` public hosted zone in Route 53.
- A repo URL the instance can clone (deploy token for a private repo).

## Use

```bash
cd deploy/intern-ec2/terraform
cp terraform.tfvars.example terraform.tfvars   # fill in vpc/subnets/repo_url
terraform init
terraform plan      # REVIEW — this module has not been machine-validated
terraform apply
terraform output url    # https://interns.airbrx.ai
```

First boot builds the image on the instance (a few minutes on t3.small); the
ALB target is unhealthy until `/health` answers. Then grab the admin password:

```bash
aws ssm start-session --target "$(terraform output -raw instance_id)"
sudo docker --version && cd /opt/omnigent/deploy/intern-ec2
sudo docker compose logs omnigent | grep -A4 "Created initial admin"
```

## Tear the instance down (keep the data)

```bash
terraform apply -var deploy_app=false    # destroys EC2 + ALB + DNS; RDS stays
```

Bring it back later with `terraform apply -var deploy_app=true` — the new
instance re-bootstraps and reconnects to the same RDS. (Put `deploy_app = false`
in `terraform.tfvars` to make it sticky.)

## Full teardown (end of internship)

```bash
terraform apply -var deploy_app=false              # compute first
terraform destroy -var db_deletion_protection=false # then RDS (takes a final snapshot)
```

## Notes / caveats

- **State holds secrets** (DB password, cookie secret) in plaintext. Use the
  commented S3 backend with encryption + locking; never commit state.
- **Not machine-validated.** No `terraform`/AWS available where this was
  authored — run `terraform validate` and `plan` and expect to adjust
  (engine version availability, subnet/AZ specifics, AMI filter).
- **Artifacts** (uploaded files) sit on the instance's local volume and are
  lost on `deploy_app=false`. Accounts/sessions are in RDS and survive. For
  durable artifacts set `OMNIGENT_ARTIFACT_URI=s3://…` (see the compose file).
- **GitHub Actions** can drive this later: an OIDC-assumed role + a
  `terraform apply` job on push, and/or a redeploy job that bumps the instance
  on branch merges. Not included here — add once the infra is settled.
