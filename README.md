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
