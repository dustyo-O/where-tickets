terraform {
  # TODO(infra): enable the S3 backend once the bootstrap S3 bucket and
  # DynamoDB lock table exist in the management account. Until then we use
  # the default local backend so `terraform plan` works without AWS
  # credentials.
  #
  # backend "s3" {
  #   bucket         = "where-tickets-tfstate"
  #   key            = "envs/dev/terraform.tfstate"
  #   region         = "us-east-1"
  #   dynamodb_table = "where-tickets-tfstate-lock"
  #   encrypt        = true
  # }
}
