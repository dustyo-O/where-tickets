output "environment" {
  description = "Environment name for this composition."
  value       = local.environment
}

output "region" {
  description = "AWS region targeted by this environment."
  value       = local.region
}

output "vpc_id" {
  description = "VPC ID emitted by the network module (placeholder)."
  value       = module.network.vpc_id
}

output "ecr_repository_names" {
  description = "ECR repository names this environment intends to manage."
  value       = module.ecr.repository_names
}

output "secret_names" {
  description = "Secrets Manager secret names this environment intends to manage."
  value       = module.secrets.secret_names
}
