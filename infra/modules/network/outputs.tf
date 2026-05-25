output "vpc_id" {
  description = "Placeholder VPC ID. Empty string until a real VPC resource is added."
  value       = ""
}

output "vpc_cidr_block" {
  description = "CIDR block intended for the VPC."
  value       = local.vpc_cidr_block
}

output "private_subnet_ids" {
  description = "Placeholder list of private subnet IDs."
  value       = []
}

output "public_subnet_ids" {
  description = "Placeholder list of public subnet IDs."
  value       = []
}
