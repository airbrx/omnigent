variable "region" {
  description = "AWS region. Must be the region the *.airbrx.ai ACM cert lives in (ALB certs are regional)."
  type        = string
  default     = "us-east-1"
}

variable "deploy_app" {
  description = <<-EOT
    Master on/off switch for the ephemeral compute (EC2 + ALB + listeners +
    Route 53 record). true = the server is up; false = tear it all down while
    keeping the Aurora cluster and the data. Flip to false + `terraform apply`
    to stop paying for the instance/ALB without losing accounts or sessions.
  EOT
  type        = bool
  default     = true
}

# ── Network ────────────────────────────────────────────────────────────
variable "vpc_id" {
  description = "VPC to deploy into. The live box runs in the default VPC vpc-05b69c7707282a5fe."
  type        = string
}

variable "public_subnet_ids" {
  description = "≥2 public subnets in different AZs for the internet-facing ALB."
  type        = list(string)
}

variable "instance_subnet_id" {
  description = "Subnet for the EC2 instance. Inbound :8000 is locked to the ALB SG either way."
  type        = string
}

variable "db_subnet_ids" {
  description = "≥2 subnets in different AZs for the Aurora DB subnet group."
  type        = list(string)
}

variable "db_ingress_cidrs" {
  description = "CIDRs allowed to reach Postgres :5432. Typically the VPC CIDR; the cluster is not publicly accessible regardless."
  type        = list(string)
}

# ── DNS / TLS ──────────────────────────────────────────────────────────
variable "route53_zone_name" {
  description = "Public hosted zone name, e.g. \"airbrx.ai\" (no trailing dot)."
  type        = string
  default     = "airbrx.ai"
}

variable "domain" {
  description = "Public hostname. Must be covered by the wildcard cert AND match the JumpCloud OIDC redirect URI."
  type        = string
  default     = "omnigent.airbrx.ai"
}

variable "cert_domain" {
  description = "Domain on the ACM cert to look up for the ALB HTTPS listener."
  type        = string
  default     = "*.airbrx.ai"
}

# ── Compute ────────────────────────────────────────────────────────────
variable "instance_type" {
  description = "EC2 type. t3.small (2 GB) minimum — t3.micro OOMs on the image build."
  type        = string
  default     = "t3.small"
}

variable "key_name" {
  description = "Optional EC2 key pair for SSH. Leave empty to rely on SSM Session Manager only."
  type        = string
  default     = ""
}

variable "ssh_ingress_cidrs" {
  description = "CIDRs allowed to SSH (:22). Ignored when key_name is empty. Keep tight."
  type        = list(string)
  default     = []
}

variable "web_ingress_cidrs" {
  description = "CIDRs allowed to reach the ALB on 80/443. Default open; restrict to office ranges if you can."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

# ── App source ─────────────────────────────────────────────────────────
variable "repo_url" {
  description = "Public Git URL the instance clones and builds from over HTTPS."
  type        = string
  default     = "https://github.com/airbrx/omnigent.git"
}

variable "branch" {
  description = "Branch the instance checks out and builds."
  type        = string
  default     = "omnigent-airbrx-server"
}

# ── Database (Aurora Serverless v2) ────────────────────────────────────
variable "db_engine_version" {
  description = "Aurora PostgreSQL version. Must support Serverless v2 scale-to-zero (16.3+/15.7+/14.12+/13.15+)."
  type        = string
  default     = "16.9"
}

variable "db_min_acu" {
  description = "Serverless v2 minimum ACU. 0 enables scale-to-zero (auto-pause); 0.5 keeps a warm floor (~$43/mo, no cold start)."
  type        = number
  default     = 0
}

variable "db_max_acu" {
  description = "Serverless v2 maximum ACU."
  type        = number
  default     = 2
}

variable "db_auto_pause_seconds" {
  description = "Idle seconds before pausing to 0 ACU (only relevant when db_min_acu = 0). Min 300."
  type        = number
  default     = 300
}

variable "db_deletion_protection" {
  description = "Block accidental cluster deletion. Set false only when you intend to destroy the database."
  type        = bool
  default     = true
}

# ── Auth: JumpCloud OIDC ───────────────────────────────────────────────
variable "oidc_issuer" {
  description = "OIDC issuer URL."
  type        = string
  default     = "https://oauth.id.jumpcloud.com/"
}

variable "oidc_client_id" {
  description = "JumpCloud application client ID."
  type        = string
}

variable "oidc_client_secret" {
  description = "JumpCloud application client secret. Sensitive — lands in state; keep the S3 backend encrypted/private."
  type        = string
  sensitive   = true
}

variable "oidc_allowed_domains" {
  description = "Comma-separated email domains allowed to auto-provision, e.g. \"airbrx.com,airbrx.ai\"."
  type        = string
  default     = "airbrx.com,airbrx.ai"
}

variable "oidc_session_ttl_hrs" {
  description = "Session cookie TTL in hours."
  type        = number
  default     = 720
}

variable "admins" {
  description = "Admin emails (lowercased) written to the OMNIGENT_ADMIN_LIST_PATH roster. Listed identities are promoted to admin on login."
  type        = list(string)
  default     = ["ben@airbrx.com"]
}

variable "tags" {
  description = "Tags applied to all resources."
  type        = map(string)
  default = {
    Project = "omnigent-server"
    Env     = "omnigent"
  }
}
