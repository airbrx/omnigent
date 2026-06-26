terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source = "hashicorp/aws"
      # Aurora Serverless v2 scale-to-zero (min_capacity = 0 +
      # seconds_until_auto_pause) needs a recent provider. Bump if plan
      # rejects min_capacity = 0.
      version = "~> 5.80"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # State lives in S3 (encrypted, versioned, public-access-blocked). It holds
  # the generated DB password + OIDC cookie secret AND the JumpCloud client
  # secret, so it must never live in git or solely on a laptop. No DynamoDB
  # lock table (single operator) — add `dynamodb_table = "..."` if multiple
  # people will run apply concurrently.
  #
  # Create the bucket once (mirrors the airbrx-deploy-state-* convention):
  #   aws s3api create-bucket --bucket airbrx-deploy-state-omnigent-724412576111 --region us-east-1
  #   aws s3api put-bucket-versioning --bucket airbrx-deploy-state-omnigent-724412576111 \
  #       --versioning-configuration Status=Enabled
  #   aws s3api put-bucket-encryption --bucket airbrx-deploy-state-omnigent-724412576111 \
  #       --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
  backend "s3" {
    bucket  = "airbrx-deploy-state-omnigent-724412576111"
    key     = "omnigent-server/terraform.tfstate"
    region  = "us-east-1"
    encrypt = true
  }
}

provider "aws" {
  region = var.region
}
