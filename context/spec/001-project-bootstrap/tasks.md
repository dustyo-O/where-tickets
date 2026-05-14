# Tasks: Project Bootstrap

> Each slice is end-to-end runnable. Don't move on until the slice's verification passes.

---

- [x] **Slice 1: Repo skeleton + orientation**
  - [x] Create top-level directories: `backend/`, `mobile/`, `infra/`, `corpus/`, `bin/`, `.github/`. **[Agent: general-purpose]**
  - [x] Add root `README.md` with links to product definition, roadmap, architecture, and a "Where things live" map. **[Agent: general-purpose]**
  - [x] Add `.editorconfig`, `.gitignore`, `.gitattributes`. **[Agent: general-purpose]**
  - [x] Create `justfile` with `default` recipe (`just --list`). **[Agent: general-purpose]**
  - [x] **Verify:** `just` at the root prints the recipe list; every README link resolves to an existing file. **[Agent: general-purpose]**

- [ ] **Slice 2: Backend hello-world via `just dev`**
  - [ ] Scaffold `backend/` with `uv`: `pyproject.toml`, `uv.lock`, `.python-version` (3.12). **[Agent: python-backend]**
  - [ ] Add FastAPI `app/main.py` with `GET /health` returning `{"status":"ok"}` (no DB yet). **[Agent: python-backend]**
  - [ ] Add `app/config.py` (pydantic-settings) reading `APP_ENV`, `LOG_LEVEL`. **[Agent: python-backend]**
  - [ ] Add `docker-compose.yml` with a Postgres service (not yet wired into the backend). **[Agent: postgres-database]**
  - [ ] Create root `Procfile` with `db:` and `api:` lines. **[Agent: general-purpose]**
  - [ ] Add `justfile` recipes: `dev` (overmind start), `down`, `fmt`, `lint`, `test`. **[Agent: general-purpose]**
  - [ ] Add `bin/check-prereqs.sh` that verifies `docker`, `overmind`, `uv`, `node`. **[Agent: general-purpose]**
  - [ ] **Verify:** `just dev` brings up db + api; `curl http://localhost:8000/health` returns 200. **[Agent: python-backend]**

- [ ] **Slice 3: Backend ↔ database wired**
  - [ ] Add `piccolo_conf.py` and `app/db.py` (Piccolo engine from `DATABASE_URL`). **[Agent: postgres-database]**
  - [ ] Add `piccolo_migrations/0001_init.py` with a placeholder table. **[Agent: postgres-database]**
  - [ ] Auto-run migrations on backend boot when `APP_ENV in {local, dev}`. **[Agent: python-backend]**
  - [ ] Extend `/health` to report `database: ok | down`; return 503 on DB failure. **[Agent: python-backend]**
  - [ ] Add `tests/test_health.py` (pytest + pytest-asyncio) hitting a real Postgres fixture. **[Agent: python-backend]**
  - [ ] **Verify:** `just dev` shows /health returning `database: ok`; stopping Postgres flips it to 503 with `database: down`. **[Agent: python-backend]**

- [ ] **Slice 4: Mobile hello-world (System Status, hardcoded OK)**
  - [ ] Scaffold `mobile/` with bare React Native + TypeScript strict mode (`@react-native-community/cli init`). **[Agent: react-native-mobile]**
  - [ ] Add `@react-navigation/native` + native-stack, `ESLint`, `Prettier`, `Jest` config. **[Agent: react-native-mobile]**
  - [ ] Build `SystemStatusScreen` with hardcoded "All systems OK" placeholder. **[Agent: react-native-mobile]**
  - [ ] Add `Procfile` `metro:` line. **[Agent: general-purpose]**
  - [ ] Add Jest smoke test rendering `App` without crashing. **[Agent: react-native-mobile]**
  - [ ] **Verify:** `just dev` runs Metro; app builds and shows the System Status screen on iOS simulator AND Android emulator. **[Agent: react-native-mobile]**

- [ ] **Slice 5: Full vertical — mobile calls real `/health`**
  - [ ] Add `mobile/src/api/client.ts` with `BACKEND_URL` from `react-native-config`. **[Agent: react-native-mobile]**
  - [ ] `.env` defaults: `http://localhost:8000` (iOS sim), `http://10.0.2.2:8000` (Android emu). **[Agent: react-native-mobile]**
  - [ ] `SystemStatusScreen` fetches `/health` on mount: shows *Checking…*, *All systems OK*, or *Degraded — <link> down* with a hint. **[Agent: react-native-mobile]**
  - [ ] **Verify:** `just dev` brings up the full stack; sim/emulator both display "All systems OK"; stopping the backend flips the screen to "Degraded — backend down". **[Agent: react-native-mobile]**

- [ ] **Slice 6: Infrastructure baseline (`terraform plan` in dev)**
  - [ ] Create `infra/` with `.terraform-version`, `envs/dev/`, `envs/staging/` skeleton, `envs/prod/` skeleton, `modules/{network,ecr,secrets}/`. **[Agent: terraform-aws]**
  - [ ] Compose modules into `envs/dev/main.tf` at placeholder level (network + ECR + Secrets Manager). **[Agent: terraform-aws]**
  - [ ] Pin providers exactly per `terraform-conventions`. **[Agent: terraform-aws]**
  - [ ] Add `just plan-infra` recipe (`terraform -chdir=infra/envs/dev init && plan`). **[Agent: general-purpose]**
  - [ ] Write `infra/README.md` covering workspace switching and remote-state TODO. **[Agent: terraform-aws]**
  - [ ] **Verify:** `just plan-infra` reports a successful plan with no AWS resources created (no `apply` wired). **[Agent: terraform-aws]**

- [ ] **Slice 7: Corpus folder + expected-route schema**
  - [ ] Create `corpus/README.md` documenting purpose and `*.expected.json` schema. **[Agent: general-purpose]**
  - [ ] Add `corpus/schema/expected-route.schema.json` (JSON Schema for the expected-route shape). **[Agent: general-purpose]**
  - [ ] Add `corpus/examples/001-placeholder-air-ticket.pdf` (synthetic, no PII) and its `.expected.json`. **[Agent: general-purpose]**
  - [ ] **Verify:** the placeholder `.expected.json` validates against the schema (`ajv` or `python -m jsonschema` invoked from `just test`). **[Agent: general-purpose]**

- [ ] **Slice 8: CI workflows + branch protection**
  - [ ] Create `.github/workflows/backend.yml`, `mobile.yml`, `infra.yml`, `meta.yml` with `paths:` filters. **[Agent: general-purpose]**
  - [ ] Each workflow calls the corresponding `just ci-*` recipe so commands match local. **[Agent: general-purpose]**
  - [ ] Write `.github/scripts/setup-branch-protection.sh` (idempotent `gh api PUT` to `branches/main/protection`). **[Agent: general-purpose]**
  - [ ] Document one-line invocation in root README. **[Agent: general-purpose]**
  - [ ] **Verify:** push a throwaway PR touching only `backend/`, only `mobile/`, only `infra/`, and root — each triggers exactly the expected workflow(s). Run the branch-protection script once; confirm a PR with a failing check cannot be merged. **[Agent: general-purpose]**
