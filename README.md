# Where Tickets

Where Tickets is a travel app that gives travelers a single, offline-available home for every ticket, booking, and reservation in a trip — and uses AI to assemble the trip's route automatically from the documents they upload.

## Product Documents

- [Product Definition](context/product/product-definition.md)
- [Roadmap](context/product/roadmap.md)
- [Architecture](context/product/architecture.md)

## Where things live

- [`backend/`](backend/) — Python FastAPI service (Piccolo + Postgres)
- [`mobile/`](mobile/) — React Native app (TypeScript)
- [`infra/`](infra/) — Terraform (AWS, dev/staging/prod)
- [`corpus/`](corpus/) — Mock travel documents + expected-route schema
- [`context/`](context/) — Product/spec documents (this is where AWOS lives)
- [`.github/`](.github/) — CI workflows + branch-protection script

## CI & branch protection

Each sub-project has its own path-filtered GitHub Actions workflow under [`.github/workflows/`](.github/workflows/): `backend.yml`, `mobile.yml`, `infra.yml`, plus a `meta.yml` that always runs and sanity-checks the repo root. Each workflow invokes the matching `just ci-*` recipe (`just ci-backend`, `just ci-mobile`, `just ci-infra`) so CI and local commands stay in lockstep.

After the first push to GitHub, configure branch protection on `main` (requires admin + an authenticated `gh`):

```sh
./.github/scripts/setup-branch-protection.sh owner/repo
```

The script is idempotent — rerun it any time the required checks change.
