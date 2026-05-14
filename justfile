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
test:
    cd backend && uv run pytest
