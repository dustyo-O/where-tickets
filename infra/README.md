# infra/

Terraform configuration for the Where Tickets AWS environments.

This is a **bootstrap-phase** skeleton: the modules are placeholders and the
dev environment intentionally creates no AWS resources. `terraform plan`
should report `No changes.` and requires no AWS credentials.

## Layout

```
infra/
├── README.md
├── .terraform-version          # Pinned Terraform version (1.9.8)
├── envs/
│   ├── dev/                    # Active bootstrap environment
│   │   ├── versions.tf         # Pinned Terraform + provider versions
│   │   ├── backend.tf          # S3 backend pre-declared, commented out
│   │   ├── providers.tf        # AWS provider with default_tags
│   │   ├── locals.tf           # Environment config (per Provectus convention)
│   │   ├── variables.tf        # Empty placeholder; root configures via locals
│   │   ├── main.tf             # Module composition (network + ecr + secrets)
│   │   └── outputs.tf
│   ├── staging/                # Skeleton; see envs/staging/README.md
│   └── prod/                   # Skeleton; see envs/prod/README.md
└── modules/
    ├── network/                # Placeholder VPC module
    ├── ecr/                    # Placeholder ECR module
    └── secrets/                # Placeholder Secrets Manager module
```

## Conventions

- **Terraform version** pinned via `.terraform-version` (consumed by `tfenv` / `asdf`).
- **All provider and module versions** pinned to exact versions — no `~>` ranges.
- **Root-module configuration** lives in `locals.tf`, not `terraform.tfvars`.
- **Required tags** on every taggable resource: `Environment`, `Project`,
  `Owner`, `ManagedBy`. The dev environment applies these via the AWS provider
  `default_tags` block, so individual resources do not need to merge them in.

## Switching environments

Run Terraform with `-chdir` pointed at the environment you want:

```bash
terraform -chdir=infra/envs/dev init
terraform -chdir=infra/envs/dev plan

# Once staging exists:
terraform -chdir=infra/envs/staging init
terraform -chdir=infra/envs/staging plan

# Production uses the saved-plan workflow:
terraform -chdir=infra/envs/prod plan -out=plan.tfplan
terraform -chdir=infra/envs/prod show plan.tfplan
# After explicit approval:
terraform -chdir=infra/envs/prod apply plan.tfplan
```

The root `justfile` exposes the dev plan as `just plan-infra`.

## Apply workflow

`apply` is **not** wired into `justfile` during bootstrap. When the time comes,
follow the saved-plan pattern from the `terraform-conventions` skill:

```bash
terraform -chdir=infra/envs/<env> plan -out=plan.tfplan
terraform -chdir=infra/envs/<env> show plan.tfplan
# Wait for explicit human approval, then:
terraform -chdir=infra/envs/<env> apply plan.tfplan
```

## TODO: remote state

State is currently local (default backend) so the bootstrap plan works without
AWS credentials. Before the first real `apply`:

1. Create an S3 bucket `where-tickets-tfstate` (versioned, encrypted, public
   access blocked) and a DynamoDB table `where-tickets-tfstate-lock` (PK
   `LockID`, on-demand billing) in the management account.
2. Uncomment the `backend "s3"` block in each environment's `backend.tf`.
3. Run `terraform -chdir=infra/envs/<env> init -migrate-state` once per
   environment to migrate state from local to S3.

Each environment uses a distinct state key (`envs/<env>/terraform.tfstate`) in
the same bucket.

## Prerequisites

- Terraform `1.9.8` (install via `tfenv install` — it reads `.terraform-version`)
- AWS credentials are **not** required for the current bootstrap-phase plan.

