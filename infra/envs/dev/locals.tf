locals {
  environment = "dev"
  project     = "where-tickets"
  owner       = "platform-team"
  region      = "us-east-1"

  name_prefix = "${local.project}-${local.environment}"

  # Required tags — applied to every taggable resource per Provectus convention.
  required_tags = {
    Environment = local.environment
    Project     = local.project
    Owner       = local.owner
    ManagedBy   = "terraform"
  }

  # Placeholder inputs for downstream modules. Nothing in these lists is
  # provisioned yet; they exist so the composition shape is reviewable.
  vpc_cidr_block   = "10.0.0.0/16"
  ecr_repositories = ["backend", "parser"]
  secret_names     = ["database-url", "jwt-signing-key"]
}
