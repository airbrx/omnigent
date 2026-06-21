output "url" {
  description = "Public URL of the intern server (once DNS + cert propagate)."
  value       = "https://${var.domain}"
}

output "alb_dns_name" {
  description = "ALB DNS name the Route 53 alias points at (empty when deploy_app = false)."
  value       = try(module.app[0].alb_dns_name, null)
}

output "instance_id" {
  description = "EC2 instance id (empty when deploy_app = false). Use with `aws ssm start-session` to shell in."
  value       = try(module.app[0].instance_id, null)
}

output "rds_endpoint" {
  description = "RDS endpoint (host:port)."
  value       = aws_db_instance.this.endpoint
}

output "database_url" {
  description = "Full DATABASE_URL injected into the server. Sensitive."
  value       = local.database_url
  sensitive   = true
}
