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

  # Recommended: an encrypted S3 backend with DynamoDB locking. Terraform
  # state holds the generated DB password and cookie secret in plaintext, so
  # do NOT keep it in git or on a laptop long-term. Uncomment and fill in:
  #
  # backend "s3" {
  #   bucket         = "airbrx-tfstate"
  #   key            = "intern-omnigent/terraform.tfstate"
  #   region         = "us-east-1"
  #   dynamodb_table = "terraform-locks"
  #   encrypt        = true
  # }
}

provider "aws" {
  region = var.region
}
