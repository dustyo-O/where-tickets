# envs/prod

Placeholder for the production environment. Not wired up during bootstrap.

When prod is brought online, mirror the layout of `envs/dev/`:

- `versions.tf` — pin Terraform and AWS provider to the same exact versions used in dev
- `backend.tf` — enable the S3 backend with `key = "envs/prod/terraform.tfstate"`
- `providers.tf` — AWS provider with `default_tags` populated from `locals.required_tags`
- `locals.tf` — set `environment = "prod"` and prod-specific inputs
- `main.tf` — compose the same modules as dev with prod values
- `outputs.tf` — re-export the values consumers need

Production changes must be applied via the saved-plan workflow:

```bash
terraform -chdir=infra/envs/prod plan -out=plan.tfplan
terraform -chdir=infra/envs/prod show plan.tfplan
# After explicit approval:
terraform -chdir=infra/envs/prod apply plan.tfplan
```

See `infra/README.md` for the workflow.
