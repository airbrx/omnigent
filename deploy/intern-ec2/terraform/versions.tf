terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.40"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # State lives in S3 (encrypted, versioned, public-access-blocked). It holds
  # the generated DB password + cookie secret, so it must never live in git or
  # solely on a laptop. No DynamoDB lock table (single operator, matching the
  # account's other state buckets) — add `dynamodb_table = "..."` here and the
  # table if multiple people will run apply concurrently.
  backend "s3" {
    bucket  = "airbrx-deploy-state-interns-724412576111"
    key     = "intern-omnigent/terraform.tfstate"
    region  = "us-east-1"
    encrypt = true
  }
}

provider "aws" {
  region = var.region
}
