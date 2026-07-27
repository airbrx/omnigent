# Root stack: the DURABLE layer (Aurora Serverless v2 + secrets) plus the
# toggleable app module.
#
#   root        → Aurora PostgreSQL Serverless v2 cluster, DB subnet group,
#                 DB security group, generated secrets. Always present;
#                 survives `deploy_app = false`.
#   module.app  → EC2, ALB, listeners, target group, Route 53 record, app SGs.
#                 Counted in/out by var.deploy_app.
#
# NOTE: the LIVE omnigent.airbrx.ai cluster (omnigent-pg / omnigent-pg-1) and
# its SG/subnet group were created by hand (aws CLI) before this Terraform
# existed, and the box currently runs the NATIVE installer (systemd + Caddy),
# not this Docker+ALB target. This stack codifies the intended Docker+ALB
# architecture. To adopt the existing Aurora cluster instead of creating a new
# one, `terraform import` it first — see terraform/README.md.

# ── Lookups ────────────────────────────────────────────────────────────
data "aws_route53_zone" "this" {
  name         = var.route53_zone_name
  private_zone = false
}

data "aws_acm_certificate" "wildcard" {
  domain      = var.cert_domain
  statuses    = ["ISSUED"]
  most_recent = true
}

# ── Secrets (state-resident — keep the backend encrypted/private) ──────
# Alphanumeric-only so the value is URL-safe inside DATABASE_URL without
# percent-encoding.
resource "random_password" "db" {
  length  = 32
  special = false
}

# 32 bytes → 64 hex chars, the form OMNIGENT_OIDC_COOKIE_SECRET expects.
resource "random_id" "cookie" {
  byte_length = 32
}

# ── Database: Aurora PostgreSQL Serverless v2 (durable) ────────────────
resource "aws_db_subnet_group" "this" {
  name       = "omnigent-pg-subnets"
  subnet_ids = var.db_subnet_ids
  tags       = var.tags
}

resource "aws_security_group" "rds" {
  name        = "omnigent-pg-sg"
  description = "Postgres 5432 for the omnigent server"
  vpc_id      = var.vpc_id
  tags        = var.tags

  ingress {
    description = "Postgres from the app subnet/VPC"
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = var.db_ingress_cidrs
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_rds_cluster" "this" {
  cluster_identifier = "omnigent-pg"
  engine             = "aurora-postgresql"
  engine_mode        = "provisioned" # Serverless v2 runs under the provisioned engine
  engine_version     = var.db_engine_version

  database_name   = "omnigent"
  master_username = "omnigent"
  master_password = random_password.db.result

  db_subnet_group_name    = aws_db_subnet_group.this.name
  vpc_security_group_ids  = [aws_security_group.rds.id]
  storage_encrypted       = true
  backup_retention_period = 7

  # Scale-to-zero: pauses to 0 ACU after the idle window, scales up on demand.
  # The server's persistent pool + an always-connected host tunnel may keep it
  # awake in practice (see RUNBOOK "scale-to-zero caveat").
  serverlessv2_scaling_configuration {
    min_capacity             = var.db_min_acu
    max_capacity             = var.db_max_acu
    seconds_until_auto_pause = var.db_auto_pause_seconds
  }

  deletion_protection       = var.db_deletion_protection
  skip_final_snapshot       = false
  final_snapshot_identifier = "omnigent-pg-final"

  tags = var.tags
}

resource "aws_rds_cluster_instance" "this" {
  identifier          = "omnigent-pg-1"
  cluster_identifier  = aws_rds_cluster.this.id
  instance_class      = "db.serverless"
  engine              = aws_rds_cluster.this.engine
  engine_version      = aws_rds_cluster.this.engine_version
  publicly_accessible = false
  tags                = var.tags
}

locals {
  # Plain postgresql:// form — the Docker entrypoint normalizes it to the
  # psycopg3 dialect SQLAlchemy needs (deploy/docker/entrypoint.py).
  database_url = "postgresql://omnigent:${random_password.db.result}@${aws_rds_cluster.this.endpoint}:5432/omnigent?sslmode=require"
}

# ── Ephemeral compute (toggle with var.deploy_app) ─────────────────────
module "app" {
  count  = var.deploy_app ? 1 : 0
  source = "./modules/app"

  vpc_id             = var.vpc_id
  public_subnet_ids  = var.public_subnet_ids
  instance_subnet_id = var.instance_subnet_id

  domain              = var.domain
  route53_zone_id     = data.aws_route53_zone.this.zone_id
  acm_certificate_arn = data.aws_acm_certificate.wildcard.arn

  instance_type     = var.instance_type
  key_name          = var.key_name
  ssh_ingress_cidrs = var.ssh_ingress_cidrs
  web_ingress_cidrs = var.web_ingress_cidrs

  repo_url     = var.repo_url
  branch       = var.branch
  database_url = local.database_url

  # OIDC (JumpCloud). client_secret + cookie_secret are sensitive.
  oidc_issuer          = var.oidc_issuer
  oidc_client_id       = var.oidc_client_id
  oidc_client_secret   = var.oidc_client_secret
  oidc_allowed_domains = var.oidc_allowed_domains
  oidc_session_ttl_hrs = var.oidc_session_ttl_hrs
  oidc_cookie_secret   = random_id.cookie.hex
  admins               = var.admins

  tags = var.tags
}
