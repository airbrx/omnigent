variable "region" {
  description = "AWS region. Must be the region the *.airbrx.ai ACM cert lives in (ALB certs are regional)."
  type        = string
  default     = "us-east-1"
}

variable "deploy_app" {
  description = <<-EOT
    Master on/off switch for the ephemeral compute (EC2 + ALB + listeners +
    Route 53 record). true = the intern server is up; false = tear it all down
    while keeping RDS and the data. Flip to false + `terraform apply` between
    internship sessions to stop paying for the instance/ALB without losing
    accounts or session history.
  EOT
  type        = bool
  default     = true
}

# ── Network ────────────────────────────────────────────────────────────
variable "vpc_id" {
  description = "VPC to deploy into."
  type        = string
}

variable "public_subnet_ids" {
  description = "≥2 public subnets in different AZs for the internet-facing ALB."
  type        = list(string)
}

variable "instance_subnet_id" {
  description = "Subnet for the EC2 instance (private subnet with NAT preferred; a public subnet works too — inbound :8000 is locked to the ALB SG either way)."
  type        = string
}

variable "db_subnet_ids" {
  description = "≥2 subnets in different AZs for the RDS subnet group (private)."
  type        = list(string)
}

variable "db_ingress_cidrs" {
  description = "CIDRs allowed to reach RDS :5432. Typically the VPC or the instance subnet CIDR — RDS is not publicly accessible regardless."
  type        = list(string)
}

# ── DNS / TLS ──────────────────────────────────────────────────────────
variable "route53_zone_name" {
  description = "Public hosted zone name, e.g. \"airbrx.ai\" (no trailing dot)."
  type        = string
  default     = "airbrx.ai"
}

variable "domain" {
  description = "Public hostname for the intern server. Must be covered by the wildcard cert."
  type        = string
  default     = "interns.airbrx.ai"
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
  description = "Optional EC2 key pair for SSH. Leave empty to rely on SSM Session Manager only (no inbound 22 needed)."
  type        = string
  default     = ""
}

variable "ssh_ingress_cidrs" {
  description = "CIDRs allowed to SSH (:22) to the instance. Ignored when key_name is empty. Keep this tight (office/VPN)."
  type        = list(string)
  default     = []
}

variable "web_ingress_cidrs" {
  description = "CIDRs allowed to reach the ALB on 80/443. Default open; restrict to office/student ranges if you can."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

# ── App source ─────────────────────────────────────────────────────────
variable "repo_url" {
  description = "Public Git URL the instance clones and builds from over HTTPS (the fork is public, so no credential is needed)."
  type        = string
}

variable "branch" {
  description = "Branch the instance checks out and builds."
  type        = string
  default     = "2026-summer-internship"
}

# ── Database ───────────────────────────────────────────────────────────
variable "db_instance_class" {
  description = "RDS instance class."
  type        = string
  default     = "db.t4g.micro"
}

variable "db_allocated_storage" {
  description = "RDS storage (GiB)."
  type        = number
  default     = 20
}

variable "db_engine_version" {
  description = "Postgres major (or major.minor) version for RDS."
  type        = string
  default     = "16"
}

variable "db_deletion_protection" {
  description = "Block accidental RDS deletion. Keep true during the internship; set false only when you intend to destroy the database."
  type        = bool
  default     = true
}

variable "tags" {
  description = "Tags applied to all resources."
  type        = map(string)
  default = {
    Project = "intern-omnigent"
    Env     = "interns"
  }
}
