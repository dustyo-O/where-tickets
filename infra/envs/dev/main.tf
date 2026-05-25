# Dev environment composition.
#
# Bootstrap-phase placeholder: the three modules below do not yet create any
# AWS resources, so `terraform plan` reports "No changes." The composition
# shape (network -> ECR -> secrets) is wired now so later slices can fill in
# real resources without restructuring the root module.

module "network" {
  source = "../../modules/network"

  name           = "${local.name_prefix}-network"
  vpc_cidr_block = local.vpc_cidr_block
  tags           = local.required_tags
}

module "ecr" {
  source = "../../modules/ecr"

  repository_names = local.ecr_repositories
  tags             = local.required_tags
}

module "secrets" {
  source = "../../modules/secrets"

  secret_names = local.secret_names
  tags         = local.required_tags
}
