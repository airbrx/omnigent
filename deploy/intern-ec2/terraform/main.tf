# Root stack: the DURABLE layer (RDS + secrets) plus the toggleable app module.
#
#   root        → RDS, DB subnet group, DB security group, generated secrets.
#                 Always present; survives `deploy_app = false`.
#   module.app  → EC2, ALB, listeners, target group, Route 53 record, app SGs.
#                 Counted in/out by var.deploy_app.

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
  length  = 30
  special = false
}

# 32 bytes → 64 hex chars, the form the accounts cookie secret expects.
resource "random_id" "cookie" {
  byte_length = 32
}

# ── Database (durable) ─────────────────────────────────────────────────
resource "aws_db_subnet_group" "this" {
  name       = "intern-omnigent"
  subnet_ids = var.db_subnet_ids
  tags       = var.tags
}

resource "aws_security_group" "rds" {
  name        = "intern-omnigent-rds"
  description = "Postgres access for the intern omnigent server"
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

resource "aws_db_instance" "this" {
  identifier     = "intern-omnigent"
  engine         = "postgres"
  engine_version = var.db_engine_version
  instance_class = var.db_instance_class

  db_name  = "omnigent"
  username = "omnigent"
  password = random_password.db.result

  allocated_storage   = var.db_allocated_storage
  storage_type        = "gp3"
  storage_encrypted   = true
  multi_az            = false
  publicly_accessible = false

  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [aws_security_group.rds.id]

  backup_retention_period = 7
  deletion_protection     = var.db_deletion_protection
  # On `terraform destroy` of the DB, take a final snapshot so data is
  # recoverable. Set to true only if you truly want a clean wipe.
  skip_final_snapshot       = false
  final_snapshot_identifier = "intern-omnigent-final"

  tags = var.tags
}

locals {
  database_url = "postgresql://omnigent:${random_password.db.result}@${aws_db_instance.this.endpoint}/omnigent?sslmode=require"
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

  repo_url      = var.repo_url
  branch        = var.branch
  database_url  = local.database_url
  cookie_secret = random_id.cookie.hex
  base_url      = "https://${var.domain}"

  tags = var.tags
}
