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
    uv run --python 3.12 --with jsonschema python corpus/pdf/validate.py

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
