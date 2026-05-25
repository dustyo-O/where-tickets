# envs/staging

Placeholder for the staging environment. Not wired up during bootstrap.

When staging is brought online, mirror the layout of `envs/dev/`:

- `versions.tf` — pin Terraform and AWS provider to the same exact versions used in dev
- `backend.tf` — enable the S3 backend with `key = "envs/staging/terraform.tfstate"`
- `providers.tf` — AWS provider with `default_tags` populated from `locals.required_tags`
- `locals.tf` — set `environment = "staging"` and staging-specific inputs
- `main.tf` — compose the same modules as dev with staging values
- `outputs.tf` — re-export the values consumers need

See `infra/README.md` for the workflow.
