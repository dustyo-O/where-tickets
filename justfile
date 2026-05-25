# Root command catalog — lists all available recipes.
# Run `just` with no args to see what's available.

default:
    @just --list

# Start the full local stack (db + api + metro) via overmind.
dev:
    ./bin/check-prereqs.sh
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

# Validate the corpus: schema-check every fragment + expected-route, then
# regenerate into a tempdir and confirm zero drift vs the committed scenarios.
test-corpus:
    uv run --python 3.12 --with jsonschema python corpus/validate.py

# Regenerate the committed corpus from the deterministic generator.
regen-corpus:
    uv run --python 3.12 python -m corpus.generator

# Initialize and plan the dev Terraform environment (no apply wired).
plan-infra:
    terraform -chdir=infra/envs/dev init
    terraform -chdir=infra/envs/dev plan
