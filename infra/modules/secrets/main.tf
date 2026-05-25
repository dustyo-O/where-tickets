# Placeholder Secrets Manager module.
#
# Real aws_secretsmanager_secret resources (DATABASE_URL, JWT signing key,
# third-party API keys) will be added in a later slice. Keeping the module
# resource-free for bootstrap means `terraform plan` needs no AWS credentials.

locals {
  secret_names = toset(var.secret_names)

  resource_tags = var.tags
}
