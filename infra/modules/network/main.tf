# Placeholder network module.
#
# Slice 6 of the project bootstrap intentionally avoids creating any real AWS
# resources so that `terraform plan` is internally consistent and requires no
# credentials. Real VPC / subnets / NAT gateway / route tables will land in a
# later slice. The locals below exist so that downstream code can reference
# realistic output shapes without provisioning anything.

locals {
  # Computed but not consumed by any aws_* resource yet.
  vpc_cidr_block = var.vpc_cidr_block

  resource_tags = merge(var.tags, {
    Name = "${var.name}-vpc"
  })
}
