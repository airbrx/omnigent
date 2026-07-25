# Generated from AWS discovery (account 724412576111, us-east-1).
# Default VPC — isolated from the airbrx SaaS stage/prod/dev VPCs.
# This file is gitignored.

region = "us-east-1"

# Default VPC (172.31.0.0/16) and its public subnets.
vpc_id             = "vpc-05b69c7707282a5fe"
public_subnet_ids  = ["subnet-00f5a0bbdd9282b2c", "subnet-0e0840595e6f9a69b"] # 1a, 1b — ALB
instance_subnet_id = "subnet-00f5a0bbdd9282b2c"                                # 1a — EC2
db_subnet_ids      = ["subnet-00f5a0bbdd9282b2c", "subnet-0e0840595e6f9a69b"] # 1a, 1b — RDS
db_ingress_cidrs   = ["172.31.0.0/16"]                                         # VPC CIDR; RDS not publicly accessible

# DNS / TLS (verified present in this account).
route53_zone_name = "airbrx.ai"
domain            = "interns.airbrx.ai"
cert_domain       = "*.airbrx.ai" # → certificate/9e8de970-de04-4767-9bbd-75cdd39d5419

# App source — public fork, anonymous HTTPS clone.
repo_url = "https://github.com/airbrx/omnigent.git"
branch   = "2026-summer-internship"

# Shell access via SSM Session Manager (no SSH key / inbound 22).
# key_name / ssh_ingress_cidrs intentionally omitted.
