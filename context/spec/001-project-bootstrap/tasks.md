# Tasks: Project Bootstrap

> Each slice is end-to-end runnable. Don't move on until the slice's verification passes.

---

- [x] **Slice 1: Repo skeleton + orientation**
  - [x] Create top-level directories: `backend/`, `mobile/`, `infra/`, `corpus/`, `bin/`, `.github/`. **[Agent: general-purpose]**
  - [x] Add root `README.md` with links to product definition, roadmap, architecture, and a "Where things live" map. **[Agent: general-purpose]**
  - [x] Add `.editorconfig`, `.gitignore`, `.gitattributes`. **[Agent: general-purpose]**
  - [x] Create `justfile` with `default` recipe (`just --list`). **[Agent: general-purpose]**
  - [x] **Verify:** `just` at the root prints the recipe list; every README link resolves to an existing file. **[Agent: general-purpose]**

- [x] **Slice 2: Backend hello-world via `just dev`**
  - [x] Scaffold `backend/` with `uv`: `pyproject.toml`, `uv.lock`, `.python-version` (3.12). **[Agent: python-backend]**
  - [x] Add FastAPI `app/main.py` with `GET /health` returning `{"status":"ok"}` (no DB yet). **[Agent: python-backend]**
  - [x] Add `app/config.py` (pydantic-settings) reading `APP_ENV`, `LOG_LEVEL`. **[Agent: python-backend]**
  - [x] Add `docker-compose.yml` with a Postgres service (not yet wired into the backend). **[Agent: postgres-database]**
  - [x] Create root `Procfile` with `db:` and `api:` lines. **[Agent: general-purpose]**
  - [x] Add `justfile` recipes: `dev` (overmind start), `down`, `fmt`, `lint`, `test`. **[Agent: general-purpose]**
  - [x] Add `bin/check-prereqs.sh` that verifies `docker`, `overmind`, `uv`, `node`. **[Agent: general-purpose]**
  - [x] **Verify:** `just dev` brings up db + api; `curl http://localhost:8000/health` returns 200. **[Agent: python-backend]**

- [x] **Slice 3: Backend ↔ database wired**
  - [x] Add `piccolo_conf.py` and `app/db.py` (Piccolo engine from `DATABASE_URL`). **[Agent: postgres-database]**
  - [x] Add `piccolo_migrations/0001_init.py` with a placeholder table. **[Agent: postgres-database]**
  - [x] Auto-run migrations on backend boot when `APP_ENV in {local, dev}`. **[Agent: python-backend]**
  - [x] Extend `/health` to report `database: ok | down`; return 503 on DB failure. **[Agent: python-backend]**
  - [x] Add `tests/test_health.py` (pytest + pytest-asyncio) hitting a real Postgres fixture. **[Agent: python-backend]**
  - [x] **Verify:** `just dev` shows /health returning `database: ok`; stopping Postgres flips it to 503 with `database: down`. **[Agent: python-backend]**

- [x] **Slice 4: Mobile hello-world (System Status, hardcoded OK)**
  - [x] Scaffold `mobile/` with bare React Native + TypeScript strict mode (`@react-native-community/cli init`). **[Agent: react-native-mobile]**
  - [x] Add `@react-navigation/native` + native-stack, `ESLint`, `Prettier`, `Jest` config. **[Agent: react-native-mobile]**
  - [x] Build `SystemStatusScreen` with hardcoded "All systems OK" placeholder. **[Agent: react-native-mobile]**
  - [x] Add `Procfile` `metro:` line. **[Agent: general-purpose]**
  - [x] Add Jest smoke test rendering `App` without crashing. **[Agent: react-native-mobile]**
  - [x] **Verify:** `just dev` runs Metro; app builds and shows the System Status screen on iOS simulator AND Android emulator. **[Agent: react-native-mobile]**

- [x] **Slice 5: Full vertical — mobile calls real `/health`**
  - [x] Add `mobile/src/api/client.ts` with `BACKEND_URL` from `react-native-config`. **[Agent: react-native-mobile]**
  - [x] `.env` defaults: `http://localhost:8000` (iOS sim), `http://10.0.2.2:8000` (Android emu). **[Agent: react-native-mobile]**
  - [x] `SystemStatusScreen` fetches `/health` on mount: shows *Checking…*, *All systems OK*, or *Degraded — <link> down* with a hint. **[Agent: react-native-mobile]**
  - [x] **Verify:** `just dev` brings up the full stack; sim/emulator both display "All systems OK"; stopping the backend flips the screen to "Degraded — backend down". **[Agent: react-native-mobile]**

- [x] **Slice 6: Infrastructure baseline (`terraform plan` in dev)**
  - [x] Create `infra/` with `.terraform-version`, `envs/dev/`, `envs/staging/` skeleton, `envs/prod/` skeleton, `modules/{network,ecr,secrets}/`. **[Agent: terraform-aws]**
  - [x] Compose modules into `envs/dev/main.tf` at placeholder level (network + ECR + Secrets Manager). **[Agent: terraform-aws]**
  - [x] Pin providers exactly per `terraform-conventions`. **[Agent: terraform-aws]**
  - [x] Add `just plan-infra` recipe (`terraform -chdir=infra/envs/dev init && plan`). **[Agent: general-purpose]**
  - [x] Write `infra/README.md` covering workspace switching and remote-state TODO. **[Agent: terraform-aws]**
  - [x] **Verify:** `just plan-infra` reports a successful plan with no AWS resources created (no `apply` wired). **[Agent: terraform-aws]**

- [x] **Slice 7: Corpus generator for route-assembly scenarios**
  - [x] Remove the placeholder corpus from the earlier draft (`corpus/examples/`, `corpus/README.md`, `corpus/schema/expected-route.schema.json`, `corpus/validate.py`) so the new layout starts clean. **[Agent: general-purpose]**
  - [x] Add `corpus/schema/extracted-fragment.schema.json` (discriminated union on `documentType`: air/bus/train ticket, hotel booking). **[Agent: general-purpose]**
  - [x] Add `corpus/schema/expected-route.schema.json` (travelers, ordered stops with accommodations, ordered transits with `sourceFragmentId`). **[Agent: general-purpose]**
  - [x] Implement `corpus/generator/` (matrix enumeration, shape generators for straight/circle/star, return-trip composition, hotel insertion at stopovers, fragmenter that produces one fragment per simulated document, deterministic orderings: forward/reverse/bisect/seeded-shuffle). Fully deterministic — fixed seeds, fixed epoch date. **[Agent: python-backend]**
  - [x] Run the generator and commit ~100+ scenarios under `corpus/scenarios/NNN-<shape>-<pax>p-<order>/` with `fragments/*.json`, `expected-route.json`, and one-line `README.md`. **[Agent: python-backend]**
  - [x] Implement `corpus/validate.py` to (a) schema-validate every fragment and expected-route, (b) re-run the generator into a temp dir and diff against committed scenarios; exit non-zero on any failure. **[Agent: python-backend]**
  - [x] Add `just test-corpus` recipe (`uv run --with jsonschema python corpus/validate.py`) and wire into root `just test`. **[Agent: general-purpose]**
  - [x] Write `corpus/README.md` covering: purpose (route assembly, NOT extraction), schema files, coverage matrix, how to regenerate, how to add new axes. **[Agent: general-purpose]**
  - [x] **Verify:** `just test-corpus` passes against the committed scenarios; manually mutate a committed scenario and confirm validation fails; manually tweak the generator and confirm the drift check fails. **[Agent: python-backend]**

- [ ] **Slice 8: CI workflows + branch protection**
  - [x] Create `.github/workflows/backend.yml`, `mobile.yml`, `infra.yml`, `meta.yml` with `paths:` filters. **[Agent: general-purpose]**
  - [x] Each workflow calls the corresponding `just ci-*` recipe so commands match local. **[Agent: general-purpose]**
  - [x] Write `.github/scripts/setup-branch-protection.sh` (idempotent `gh api PUT` to `branches/main/protection`). **[Agent: general-purpose]**
  - [x] Document one-line invocation in root README. **[Agent: general-purpose]**
  - [ ] **Verify:** push a throwaway PR touching only `backend/`, only `mobile/`, only `infra/`, and root — each triggers exactly the expected workflow(s). Run the branch-protection script once; confirm a PR with a failing check cannot be merged. **[Agent: general-purpose]**
