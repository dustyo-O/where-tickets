# Technical Specification: Project Bootstrap

- **Functional Specification:** [001-project-bootstrap/functional-spec.md](./functional-spec.md)
- **Status:** Completed
- **Author(s):** Dusty

---

## 1. High-Level Technical Approach

Three independent sub-projects (`backend/`, `mobile/`, `infra/`) plus a top-level `corpus/` folder, all coordinated by a root `justfile`. A process orchestrator (`overmind` driven by a root `Procfile`) launches the Postgres container, the backend API, and the mobile Metro bundler together from `just dev`.

The backend is a Python 3.12 service using `uv` for dependency management, FastAPI for HTTP, Piccolo for the database layer, ruff for lint/format, pyright for type-checking, and pytest for tests. The mobile client is a bare React Native app with TypeScript strict mode, ESLint + Prettier, and Jest. The infrastructure project is Terraform with workspaces (`dev`/`staging`/`prod`), modules in `infra/modules/`, and environment-specific configs in `infra/envs/`; bootstrap proves `terraform plan` works against `dev` without applying anything.

CI is one GitHub Actions workflow per sub-project, scoped by path filters. Branch protection on `main` is configured via a committed `gh api` script so the rule is reproducible from the repo. The end-to-end "system status" screen in the mobile app calls a single backend endpoint that touches the database via Piccolo, proving the whole vertical works on a fresh checkout.

---

## 2. Proposed Solution & Implementation Plan (The "How")

### 2.1 Repository Layout

```
where-tickets/
├── justfile                     # root commands (dev, test, lint, fmt, ci)
├── Procfile                     # overmind process map (db, api, metro)
├── docker-compose.yml           # postgres only (and pgadmin, optional)
├── .github/
│   ├── workflows/
│   │   ├── backend.yml
│   │   ├── mobile.yml
│   │   └── infra.yml
│   └── scripts/
│       └── setup-branch-protection.sh   # `gh api` calls
├── backend/                     # Python service (see §2.3)
├── mobile/                      # bare RN app (see §2.4)
├── infra/                       # Terraform (see §2.5)
├── corpus/                      # mock-document folder (see §2.8)
├── context/                     # already in repo (product, spec)
├── .editorconfig
├── .gitignore
├── .gitattributes
└── README.md
```

### 2.2 Root Orchestration

- **`justfile` recipes:**
  - `default` — list all recipes (`just --list`)
  - `dev` — `overmind start -f Procfile`
  - `down` — `overmind quit && docker compose down`
  - `test` — runs backend + mobile tests
  - `lint` — runs backend + mobile + infra checks
  - `fmt` — applies formatters
  - `ci-backend` / `ci-mobile` / `ci-infra` — exact commands CI runs (so they're reproducible locally)
- **`Procfile`:**
  - `db:    docker compose up postgres`
  - `api:   cd backend && uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000`
  - `metro: cd mobile && npx react-native start`
- After all three processes report ready, overmind tail prints a summary banner via a small `bin/dev-banner.sh` (URL of backend, commands to launch iOS/Android sim).
- **Prerequisite check:** `just dev` first invokes `bin/check-prereqs.sh` which verifies `docker`, `overmind`, `uv`, `node`, `pnpm`/`yarn`/`npm`, and platform-specific iOS/Android requirements; missing tools produce a clear "install X with `brew install Y`" message.

### 2.3 Backend (`backend/`)

- **Language/Runtime:** Python 3.12+ pinned via `.python-version`.
- **Tooling:** `uv` for env + dependencies (`pyproject.toml`, `uv.lock`). No virtualenv discipline required from contributors — `uv run` handles it.
- **Frameworks/Libraries:**
  - FastAPI for HTTP
  - Piccolo (PostgreSQL engine) for ORM + migrations
  - pytest + pytest-asyncio for tests
  - ruff for lint and format
  - pyright for type-check
- **Layout:**
  ```
  backend/
  ├── pyproject.toml
  ├── uv.lock
  ├── piccolo_conf.py
  ├── app/
  │   ├── main.py            # FastAPI app factory + routers
  │   ├── config.py          # pydantic-settings (env vars)
  │   ├── db.py              # Piccolo engine wiring
  │   └── routers/
  │       └── health.py      # GET /health
  ├── piccolo_migrations/
  │   └── 0001_init.py       # placeholder table to prove migrations run
  └── tests/
      ├── conftest.py
      └── test_health.py
  ```
- **Health endpoint contract:** `GET /health` → `200 {"status":"ok","database":"ok","version":"<commit-sha>"}`. On DB failure: `503 {"status":"degraded","database":"down","error":"<message>"}`.
- **Environment variables** (loaded by `app/config.py` via pydantic-settings):
  - `DATABASE_URL` — Postgres DSN
  - `APP_ENV` — `local`/`dev`/`staging`/`prod`
  - `LOG_LEVEL` — default `INFO`
  - Cognito and Bedrock variables are **declared but unused** in bootstrap (kept commented in `.env.example`).
- **Migrations:** run automatically on backend boot in `local`/`dev`, gated behind `APP_ENV` so production keeps them as an explicit step.

### 2.4 Mobile (`mobile/`)

- **Flavor:** bare React Native (CLI initialized via `npx @react-native-community/cli init`), TypeScript strict mode.
- **Package manager:** `pnpm` (or `npm` — pinned via `packageManager` in `package.json`).
- **Libraries baseline:**
  - `@react-navigation/native` + native-stack for navigation
  - `axios` (or built-in `fetch`) for backend calls
  - `eslint`, `@react-native/eslint-config`, `prettier`, `jest`
- **Layout:**
  ```
  mobile/
  ├── package.json
  ├── tsconfig.json            # strict: true
  ├── .eslintrc.js
  ├── .prettierrc
  ├── ios/                     # native project
  ├── android/                 # native project
  ├── src/
  │   ├── App.tsx
  │   ├── api/
  │   │   └── client.ts        # baseURL from env
  │   ├── config.ts            # BACKEND_URL via react-native-config
  │   ├── screens/
  │   │   └── SystemStatusScreen.tsx
  │   └── navigation/
  │       └── RootNavigator.tsx
  └── __tests__/
      └── App.test.tsx         # Jest smoke test
  ```
- **System Status screen:** mounts, calls `GET /health`, renders one of three states — *Checking…*, *All systems OK* (green), or *Degraded — <which link> is down* (red, with hint).
- **Backend URL config:** `react-native-config` reads `BACKEND_URL` from `.env`; defaults baked in for local (`http://10.0.2.2:8000` on Android emulator, `http://localhost:8000` on iOS simulator).

### 2.5 Infrastructure (`infra/`)

- **Tool:** Terraform pinned via `.terraform-version` (tfenv-compatible).
- **Layout:**
  ```
  infra/
  ├── README.md
  ├── .terraform-version
  ├── modules/
  │   ├── network/         # VPC, subnets, NAT
  │   ├── ecr/             # backend + lambda repos
  │   └── secrets/         # Secrets Manager placeholders
  └── envs/
      ├── dev/
      │   ├── main.tf      # composes the modules
      │   ├── variables.tf
      │   ├── backend.tf   # S3 backend, dev workspace
      │   └── terraform.tfvars
      ├── staging/  (skeleton, no body in bootstrap)
      └── prod/     (skeleton, no body in bootstrap)
  ```
- **Provider pinning** follows the `terraform-conventions` skill (exact versions, no `~>` for providers).
- **Bootstrap behavior:** `just plan-infra` runs `terraform -chdir=infra/envs/dev init && terraform -chdir=infra/envs/dev plan`. **No `apply` is wired in bootstrap.**
- **Remote state:** S3 backend pre-declared but commented out with a TODO; bootstrap plans locally to keep zero AWS cost. README documents how to flip to remote state once an S3 bucket exists.

### 2.6 CI (GitHub Actions)

Three workflows, each triggered on `pull_request` with `paths:` filter:

| Workflow | Path filter | Jobs |
| --- | --- | --- |
| `backend.yml` | `backend/**`, root tooling | `uv sync` → `just ci-backend` (ruff, pyright, pytest) |
| `mobile.yml` | `mobile/**` | `pnpm install` → `just ci-mobile` (tsc, eslint, jest) |
| `infra.yml` | `infra/**` | `terraform fmt -check`, `terraform validate`, `tflint` (optional) |

- All workflows run on `ubuntu-latest` with cached package managers.
- A fourth, lightweight `meta.yml` runs always to verify root files (`justfile` parses, README links resolve).

### 2.7 Branch Protection Script

- **File:** `.github/scripts/setup-branch-protection.sh`
- **Mechanism:** `gh api` PUT to `/repos/{owner}/{repo}/branches/main/protection` with required status checks for the four workflows above, `required_pull_request_reviews` (1 approval, optional in bootstrap — configurable in the script), `enforce_admins: false`, no force pushes.
- Script reads `GITHUB_REPOSITORY` env var, fails clearly with `gh auth status` instructions if not authenticated. README documents one-line invocation.

### 2.8 Corpus (`corpus/`)

**Purpose:** stress-test the future route-assembly engine. The hard problem is composing a coherent travel route from a pile of extracted document fragments delivered in arbitrary order, across varied traveler counts, route shapes, and transport modes. PDF extraction is out of scope here — fragments are pre-structured JSON.

- **Layout:**
  ```
  corpus/
  ├── README.md                              # purpose, schemas, regen instructions
  ├── schema/
  │   ├── extracted-fragment.schema.json     # one fragment = one simulated document
  │   └── expected-route.schema.json         # the composed answer
  ├── generator/
  │   ├── __init__.py
  │   ├── __main__.py                        # `python -m corpus.generator` → writes scenarios/
  │   ├── matrix.py                          # enumerates the coverage matrix
  │   ├── shapes.py                          # straight / circle / star route generators
  │   ├── fragmenter.py                      # splits a route into per-document fragments
  │   └── orderings.py                       # forward / reverse / bisect / seeded-shuffle
  ├── validate.py                            # validates scenarios + checks drift vs generator
  └── scenarios/                             # **committed, generated** — never hand-edited
      └── NNN-<shape>-<pax>p-<order>/
          ├── fragments/
          │   ├── 01-<doc-type>.json
          │   └── ...
          ├── expected-route.json
          └── README.md                       # one line: human-readable scenario summary
  ```

- **Coverage matrix** (cartesian product, generator enumerates):
  - **Travelers:** `[1, 2, 3, 4]`
  - **Route shape:** `straight` (a→b→c…), `circle` (a→b→c→d→b→e), `star` (hub-and-spoke: a→b→c→b→d→b→e→…)
  - **Leg count:** parameterized per shape (typically 2–6)
  - **Return:** `yes` / `no` — orthogonal to shape; "return" simply means the last hop lands back at the origin. Composes with any shape (e.g., star with return: `a→b→c→b→d→b→e→a`, optionally without `e`).
  - **Hotels:** generator inserts plausible hotel-booking fragments at stopovers
  - **Mode mix:** air, bus, train (varied per scenario; `mode` is part of each transit segment)
  - **Fragment ordering:** `forward` (chronological), `reverse`, `bisect`, `seeded-shuffle` (deterministic with the scenario index as seed)
  - Target output: ~100+ scenarios from the full cartesian product.

- **Fragment grain:** one fragment = one simulated document (one ticket PDF, one hotel booking confirmation, etc.). A return ticket = one fragment containing two legs. This mirrors what extraction will actually produce and forces the engine to handle multi-leg single documents.

- **Schemas (shape only — full schema lives in the files):**
  - `extracted-fragment.schema.json`: discriminated union on `documentType`:
    - `air-ticket | bus-ticket | train-ticket`: `{ documentType, pnr, travelers[], legs[]: { from, to, departureAt, arrivalAt, carrier?, vehicleNumber? } }`
    - `hotel-booking`: `{ documentType, confirmationCode, travelers[], city, checkInAt, checkOutAt, hotelName? }`
    - Common: `sourceDocumentId` (stable per fragment), all timestamps ISO 8601 `date-time`.
  - `expected-route.schema.json`:
    - `travelers[]`: distinct traveler identifiers seen across fragments
    - `stops[]`: ordered `{ city, arrivalAt?, departureAt?, travelers[], accommodations[]?: { checkInAt, checkOutAt, hotelName? } }`
    - `transits[]`: ordered `{ from, to, mode, departureAt, arrivalAt, travelers[], sourceFragmentId }`
    - `notes?`: free-form

- **Determinism:** generator uses fixed seeds derived from `(scenario_index, axis_values)`; no `random()` without a seed, no `datetime.now()`. Dates are anchored to a fixed epoch (`2027-03-01`) so regeneration is byte-identical.

- **Validation (`just test-corpus`):**
  1. Walk every `corpus/scenarios/*/fragments/*.json` → validate against `extracted-fragment.schema.json`.
  2. Walk every `corpus/scenarios/*/expected-route.json` → validate against `expected-route.schema.json`.
  3. Re-run the generator into a temp dir and diff against `corpus/scenarios/`. Drift → exit non-zero.
  - Runs via `uv run --with jsonschema python corpus/validate.py` so no project dependency is added.
  - Wired into the root `test` recipe.

- **PDFs:** not in scope for bootstrap. `source-pdfs/` subfolders may be added per-scenario later under the Document Ingest umbrella.

---

## 3. Impact and Risk Analysis

### System Dependencies

- This work has **no upstream dependencies** — it is the foundational layer.
- All later umbrellas (DUS-6, DUS-7, DUS-8, DUS-9, DUS-10) inherit the choices made here.

### Potential Risks & Mitigations

| Risk | Mitigation |
| --- | --- |
| **`overmind` is mac/linux only; not all contributors have it.** | `just dev` checks for overmind and prints a one-line `brew install` instruction; provide a fallback `just dev-simple` recipe that uses `concurrently` (via npx) as a degraded mode. |
| **Bare RN setup is more brittle than Expo, especially on Windows/Apple Silicon.** | Document pinned Xcode + Android Studio versions in `mobile/README.md`; commit Ruby version for CocoaPods (`.ruby-version`); CI runs install on `ubuntu-latest` to catch dependency regressions even though it can't build iOS. |
| **Piccolo's auto-migration on boot in `local`/`dev` could mask migration bugs.** | `just test` runs migrations against an empty DB in CI; a follow-up issue ensures production deploys run migrations as an explicit step (out of scope for bootstrap). |
| **Terraform plan without remote state can drift between contributors.** | Bootstrap explicitly does not commit a state file; README marks remote-state setup as the next step before any `apply`. |
| **Branch-protection script can lock the repo owner out.** | Script keeps `enforce_admins: false` so the owner can bypass in emergencies; documents how to disable protection via `gh` if the script misconfigures. |
| **`gh` CLI must be authenticated to run the branch-protection script.** | Script checks `gh auth status` first and exits with a clear hint pointing at `gh auth login`. |
| **CI path filters can miss cross-project changes (e.g., a root `justfile` change that breaks all sub-projects).** | The `meta.yml` workflow runs unconditionally on every PR and validates root files. |

---

## 4. Testing Strategy

- **Backend unit tests (pytest):** at minimum `test_health.py` — asserts `/health` returns 200 with `database: "ok"` against a real Postgres started via a session-scoped fixture using `pytest-docker` or by reusing the `docker compose` Postgres.
- **Mobile smoke test (Jest):** `App.test.tsx` renders the root component without crashing; one render test for `SystemStatusScreen` with `fetch` mocked to return OK.
- **Infra validation (CI only):** `terraform fmt -check` + `terraform validate` per environment; no resources created, no `plan` artefacts asserted.
- **Manual acceptance (one-time, recorded in the bootstrap PR description):**
  - `just dev` on a fresh clone brings up db + api + metro; iOS sim launches and System Status shows "All systems OK".
  - Same flow on an Android emulator.
  - Opening a PR with a trivial change in each sub-project triggers only that sub-project's workflow and goes green.
  - Branch-protection script run once on the repo blocks merging a red PR.
- **No end-to-end automated test in bootstrap** — the "system status" screen + manual acceptance is the proof. Automated E2E (Detox / Maestro) is deferred.
