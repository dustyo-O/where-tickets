variable "name" {
  description = "Logical name prefix for network resources created by this module."
  type        = string

  nullable = false
}

variable "vpc_cidr_block" {
  description = "Primary CIDR block for the VPC (placeholder — no resource is created yet)."
  type        = string
  default     = "10.0.0.0/16"

  nullable = false
}

variable "tags" {
  description = "Tags applied to all resources created by this module."
  type        = map(string)
  default     = {}

  nullable = false
}
