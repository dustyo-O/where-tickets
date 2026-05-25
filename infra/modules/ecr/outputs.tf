output "repository_names" {
  description = "Set of repository names this module is configured to manage."
  value       = local.repository_names
}

output "repository_urls" {
  description = "Placeholder map of repository name -> repository URL. Empty until real repositories are created."
  value       = {}
}
