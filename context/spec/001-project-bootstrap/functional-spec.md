# Functional Specification: Project Bootstrap

- **Roadmap Item:** Project Bootstrap (Linear DUS-5) — the foundational developer-environment setup that unblocks every other umbrella in v1.
- **Status:** Completed
- **Author:** Dusty

---

## 1. Overview and Rationale (The "Why")

Where Tickets is a fresh project: there is no codebase, no environment, no automation. Before anyone can build the route engine, the document-ingest pipeline, or the mobile app, a new developer needs to be able to clone the repository and reach a state where the whole product can be run, exercised, and tested locally — and where every change is automatically checked before it lands in `main`.

This specification defines the developer-facing experience of that bootstrap milestone: what a developer can do on a fresh checkout, what they see when they push a change, and what they find inside the repository as starting material for later work.

**Success looks like:**

- A developer joining the team is productive within the first hour: clone, run one command, see the app talking to the backend.
- Any change to the repository is automatically checked for correctness before it can land.
- Every later umbrella (route engine, document ingest, account essentials, etc.) starts from a working baseline instead of building infrastructure from scratch.

---

## 2. Functional Requirements (The "What")

### 2.1 Running the project locally

- **As a developer, I want to** start the entire local environment with a single command at the repository root, **so that** I can begin working without having to learn three separate setup procedures.
  - **Acceptance Criteria:**
    - [x] On a fresh checkout, running the documented root command starts the local database, the backend API, and the mobile development server together.
    - [x] The command prints a clear summary at the end showing how to reach the backend (URL) and how to launch the mobile app on a simulator/emulator.
    - [x] If a prerequisite is missing on the developer's machine (e.g., the local container runtime is not installed), the command stops with a human-readable error explaining what to install.

### 2.2 End-to-end "hello world" proof

- **As a developer, I want to** see a single screen in the mobile app that confirms the app, the backend, and the database are all talking to each other, **so that** I have confidence the full vertical works from day one.
  - **Acceptance Criteria:**
    - [x] On launch (after sign-in is skipped or stubbed for bootstrap), the mobile app shows a "system status" screen.
    - [x] The screen reaches out to the backend, the backend touches the database, and the screen shows an "All systems OK" message when the chain succeeds.
    - [x] If any link in the chain is broken, the screen shows which link failed and a short, actionable hint.

### 2.3 Backend baseline

- **As a developer, I want to** be able to run the backend on its own and run its tests, **so that** I can work on backend code in isolation when I don't need the mobile app.
  - **Acceptance Criteria:**
    - [x] The backend exposes a health endpoint reachable in a browser or `curl`.
    - [x] The backend can be started against an empty local database; database tables are created automatically the first time.
    - [x] Running the backend's tests on a fresh checkout passes with zero failures.

### 2.4 Mobile app baseline

- **As a developer, I want to** be able to build and run the mobile app on both iOS and Android, **so that** I can develop and test on either platform.
  - **Acceptance Criteria:**
    - [x] The mobile app builds and runs on the iOS simulator from a fresh checkout, with documented steps in the mobile sub-project's README.
    - [x] The mobile app builds and runs on an Android emulator from a fresh checkout, with documented steps in the mobile sub-project's README.
    - [x] Running the mobile app's tests passes with zero failures.

### 2.5 Infrastructure baseline

- **As a developer, I want to** validate the cloud-infrastructure configuration without actually creating any cloud resources, **so that** I can review changes safely and only spend money when we're ready.
  - **Acceptance Criteria:**
    - [x] A documented command, run from the infrastructure sub-project, reports a successful plan against a "dev" environment without creating any AWS resources.
    - [x] The plan includes the foundational pieces the architecture calls for (network, container registry, secrets storage) at a placeholder level — enough to validate the configuration parses and is internally consistent.
    - [x] A README explains how to switch between dev / staging / production environments.

### 2.6 Automated checks on every change

- **As a developer, I want** every pull request to be automatically checked, **so that** broken code, failing tests, or invalid infrastructure changes never reach `main`.
  - **Acceptance Criteria:**
    - [x] Opening a pull request that changes the backend triggers backend lint, type checks, and tests.
    - [x] Opening a pull request that changes the mobile app triggers mobile lint, type checks, and tests.
    - [x] Opening a pull request that changes infrastructure files triggers an infrastructure validation check.
    - [x] A pull request that touches only one sub-project does **not** unnecessarily run the other sub-projects' checks.
    - [x] A pull request cannot be merged into `main` until all relevant checks pass. The branch-protection rule is configured as part of this work using the `gh` CLI (committed as a script or documented command), so it can be re-applied or audited from the repo itself.

### 2.7 Starter material for the route engine

- **As a route-engine developer, I want to** find a large, systematically generated set of mock travel scenarios in the repository, **so that** I can build and regression-test the engine that composes a travel route from a pile of extracted document fragments — the *real* hard problem, since the order, completeness, and mix of fragments is what trips the engine up.
  - **Acceptance Criteria:**
    - [x] A top-level `corpus/` folder exists with a README explaining its purpose: testing **route assembly from extracted document fragments**, not PDF extraction.
    - [x] The corpus contains many scenarios (target: ~100+) generated by a committed generator script that enumerates a defined coverage matrix (traveler count, route shape, leg count, return vs one-way, fragment ordering, mode mix, hotels).
    - [x] Each scenario is a self-contained folder with `fragments/*.json` (the inputs — one fragment per simulated document, in a deliberately non-chronological order) and `expected-route.json` (the correctly composed route).
    - [x] Two JSON Schemas — one for fragment shape, one for expected-route shape — are committed; every fragment and every expected-route validates against its schema.
    - [x] The generator is deterministic: re-running it produces byte-identical output, so PRs surface drift between the generator and the committed scenarios.
    - [x] A single command (`just test-corpus`, wired into `just test`) validates all scenarios against the schemas **and** verifies the generator output matches what's committed (CI fails on drift).

### 2.8 Repository navigation

- **As a developer, I want** the root of the repository to clearly point me to the product documents and to each sub-project, **so that** I can orient myself without asking anyone.
  - **Acceptance Criteria:**
    - [x] The root README links to the product definition, the roadmap, and the architecture document.
    - [x] The root README has a short "Where things live" section pointing to the backend, mobile, infrastructure, and corpus directories.

---

## 3. Scope and Boundaries

### In-Scope

- Repository layout, root README, and orientation material.
- One root command that starts the full local stack.
- A backend baseline: runnable API, health endpoint, local database, passing tests.
- A mobile-app baseline: iOS + Android run instructions, passing tests.
- An end-to-end "system status" screen proving the mobile app, backend, and database are wired together.
- An infrastructure baseline that validates a dev-environment plan, without applying any cloud resources.
- Automated checks on every pull request, scoped to the changed sub-project, and required for merging.
- The `corpus/` folder with a deterministic generator producing a coverage-matrix of route-assembly scenarios (~100+), schemas for fragment and expected-route shapes, and CI validation that scenarios stay in sync with the generator.

### Out-of-Scope

- Account creation, login, or any user-visible authentication (handled by **Account Essentials** umbrella).
- Document upload, AI extraction, and the document-processing pipeline (handled by **Document Ingest** umbrella).
- The actual route-engine implementation and the engine spike (handled by **Route Engine (Foundation)** umbrella).
- Real-world or synthetic travel PDFs in the corpus — bootstrap ships *structured fragments* only, not PDF source documents. PDFs are added later under the Document Ingest umbrella.
- Custom-segment editing, completeness checks, on-the-day surfacing, offline sync (handled by **Trip Completeness & On-the-Day Experience** umbrella).
- Travelspace sharing, invites, and app-store publication (handled by **Travelspace Sharing & Launch** umbrella).
- Applying any infrastructure to a real AWS account; the bootstrap only validates the configuration.
- Production monitoring, alerting, and dashboards beyond what comes for free from the automated checks.
