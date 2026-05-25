output "secret_names" {
  description = "Set of secret names this module is configured to manage."
  value       = local.secret_names
}

output "secret_arns" {
  description = "Placeholder map of secret name -> ARN. Empty until real secrets are created."
  value       = {}
}
