# Placeholder ECR module.
#
# Repositories (one per service: backend, parser, etc.) will be created in a
# later slice. For now we surface the intended names through outputs so that
# downstream wiring can be drafted without provisioning real AWS resources.

locals {
  repository_names = toset(var.repository_names)

  resource_tags = var.tags
}
