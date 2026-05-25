variable "secret_names" {
  description = "List of Secrets Manager secret names this module would create. Placeholder — no secrets are created yet."
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
