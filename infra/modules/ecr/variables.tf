variable "repository_names" {
  description = "List of ECR repository names this module would create. Placeholder — no repositories are created yet."
  type        = list(string)
  default     = []

  nullable = false
}

variable "tags" {
  description = "Tags applied to all resources created by this module."
  type        = map(string)
  default     = {}

  nullable = false
}
