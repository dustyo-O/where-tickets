# Root command catalog — lists all available recipes.
# Run `just` with no args to see what's available.

default:
    @just --list

# Start the full local stack (db + api + metro) via overmind.
dev:
    ./bin/check-prereqs.sh
    ./bin/dev-banner.sh
    overmind start -f Procfile

# Stop overmind and tear down the local containers.
down:
    -overmind quit
    docker compose down

# Apply formatters across all sub-projects.
fmt:
    cd backend && uv run ruff format .

# Run lint + type checks across all sub-projects.
lint:
    cd backend && uv run ruff check . && uv run pyright

# Run tests across all sub-projects.
test: test-corpus
    cd backend && uv run pytest

# Validate the corpus: schema-check every fragment + expected-route, regenerate
# into a tempdir and confirm zero drift vs the committed scenarios, then run the
# PDF-corpus validator (schema + city-integrity + per-document-type min counts).
test-corpus:
    uv run --python 3.12 --with jsonschema python corpus/validate.py
    uv run --python 3.12 --with jsonschema --with pymupdf python corpus/pdf/validate.py

# Run the PDF-corpus runner against Layer 1 + Layer 2 scenarios via the
# `where_tickets.extraction.extract_pdf` entry point. While spec 006 is in
# flight the extractor is a stub that raises ExtractionFailedError("not
# implemented yet"), so every scenario FAILs by design; real accuracy lands
# in Slice 9 of 006-ai-document-understanding-pdf-extraction.
# PYTHONPATH=. (= backend/) puts the where_tickets package on sys.path; the
# script itself is referenced by filesystem path (../corpus/pdf/runner.py).
# --isolated runs in an ephemeral venv so the extraction-group anthropic
# install doesn't mutate the backend venv (which would surface latent
# pyright errors in spikes/route_engine_llm/bedrock_client.py and break
# `just lint`).
test-pdf-corpus:
    cd backend && PYTHONPATH=. uv run --isolated --group extraction --group corpus python ../corpus/pdf/runner.py

# Run the production extractor against a single PDF and pretty-print the
# extracted fields on stdout. Diagnostics (extraction_path, model_path) go to
# stderr so stdout can be piped into `jq`. Live Bedrock — costs a few cents
# per call; useful for ad-hoc debugging of one scenario without re-running the
# full corpus. PYTHONPATH=. + --isolated mirror `test-pdf-corpus` (keeps
# anthropic out of the persistent backend venv so `just lint` stays clean).
# Example:
#   just extract-pdf corpus/pdf/layer1/scenarios/001-air-1leg-1pax-paris-lisbon/document.pdf
extract-pdf path:
    cd backend && PYTHONPATH=. uv run --isolated --group extraction python -m where_tickets.extraction {{path}}

# Run the document-to-route integration runner against trips under
# corpus/integration/. Live Bedrock — costs a few cents per PDF. Use
# `--trip <slug>` for single-trip debugging; `--no-route-check` for
# adapter-only sanity; `--json-report PATH` for a machine-readable summary.
# PYTHONPATH=. + --isolated --group extraction mirror `extract-pdf` to keep
# `anthropic` out of the persistent backend venv (see memory
# `project_extraction_isolated_venv`).
# Example:
#   just integration --trip 01-air-return-1pax-paris-lisbon --json-report /tmp/report.json
integration *args:
    cd backend && PYTHONPATH=. uv run --isolated --group extraction python -m spikes.integration.runner {{args}}

# Regenerate Layer 1 PDF scenarios from the deterministic generator (data is
# stable across runs; noise varies). Refreshes corpus/pdf/layer1/scenarios/.
# Uses the backend's `corpus` dep group for WeasyPrint + Jinja2; PYTHONPATH
# points at the repo root so `python -m corpus.pdf.generator` resolves.
regen-pdf-corpus:
    cd backend && PYTHONPATH=.. uv run --group corpus python -m corpus.pdf.generator --output-dir ../corpus/pdf/layer1/scenarios

# Regenerate the committed corpus from the deterministic generator.
regen-corpus:
    uv run --python 3.12 python -m corpus.generator

# Run the LLM route-engine spike against a Bedrock model (live; needs AWS creds
# and the optional `spike` dep group). Extra flags pass through, e.g.:
#   just spike-engine model=haiku --limit 3
#   just spike-engine model=sonnet --shape circle
spike-engine model="haiku" *args:
    cd backend && uv run --group spike python -m spikes.route_engine_llm.run --model {{model}} {{args}}

# Run the algorithmic (rules-based) route-engine spike (offline; no AWS, no
# `spike` group, no token spend). Extra flags pass through, e.g.:
#   just spike-engine-algo --limit 1
#   just spike-engine-algo --scenario 000-straight-1p-forward
spike-engine-algo *args:
    cd backend && uv run python -m spikes.route_engine_algorithmic.run {{args}}

# Render the cross-model comparison (compare.md) from 2+ per-run results.json
# files (offline; no AWS, no `spike` group). Example:
#   just spike-compare runs/<ts>-opus/results.json runs/<ts>-haiku/results.json
spike-compare *results:
    cd backend && uv run python -m spikes.route_engine_llm.compare {{results}}

# Initialize and plan the dev Terraform environment (no apply wired).
plan-infra:
    terraform -chdir=infra/envs/dev init
    terraform -chdir=infra/envs/dev plan

# CI: backend lint + type-check + tests (mirrors .github/workflows/backend.yml).
ci-backend:
    cd backend && uv sync && uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest

# CI: mobile lint + type-check + tests (mirrors .github/workflows/mobile.yml).
ci-mobile:
    cd mobile && npm ci && npm run lint && npx tsc --noEmit && npm test -- --ci

# CI: infra fmt + validate for the dev env (mirrors .github/workflows/infra.yml).
ci-infra:
    terraform -chdir=infra/envs/dev fmt -check -recursive ../..
    terraform -chdir=infra/envs/dev init -backend=false
    terraform -chdir=infra/envs/dev validate
