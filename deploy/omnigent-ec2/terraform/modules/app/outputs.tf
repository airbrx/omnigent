output "alb_dns_name" {
  value = aws_lb.this.dns_name
}

output "instance_id" {
  value = aws_instance.app.id
}
