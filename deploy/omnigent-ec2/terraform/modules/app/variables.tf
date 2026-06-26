variable "vpc_id" { type = string }
variable "public_subnet_ids" { type = list(string) }
variable "instance_subnet_id" { type = string }

variable "domain" { type = string }
variable "route53_zone_id" { type = string }
variable "acm_certificate_arn" { type = string }

variable "instance_type" { type = string }
variable "key_name" { type = string }
variable "ssh_ingress_cidrs" { type = list(string) }
variable "web_ingress_cidrs" { type = list(string) }

variable "repo_url" { type = string }
variable "branch" { type = string }
variable "database_url" {
  type      = string
  sensitive = true
}

# ── OIDC (JumpCloud) ──
variable "oidc_issuer" { type = string }
variable "oidc_client_id" { type = string }
variable "oidc_client_secret" {
  type      = string
  sensitive = true
}
variable "oidc_allowed_domains" { type = string }
variable "oidc_session_ttl_hrs" { type = number }
variable "oidc_cookie_secret" {
  type      = string
  sensitive = true
}
variable "admins" { type = list(string) }

variable "tags" { type = map(string) }
