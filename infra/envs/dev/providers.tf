provider "aws" {
  region = local.region

  # Skip credential / region / partition lookups so `terraform plan` works
  # without configured AWS credentials. The dev environment is currently a
  # placeholder — no real AWS API calls are made.
  skip_credentials_validation = true
  skip_region_validation      = true
  skip_requesting_account_id  = true

  default_tags {
    tags = local.required_tags
  }
}
