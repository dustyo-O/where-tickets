#!/usr/bin/env bash
# Verifies that local development prerequisites are installed.
# Invoked by `just dev` before launching overmind.
set -euo pipefail

missing=()

check() {
    local cmd="$1"
    local install_hint="$2"
    if ! command -v "$cmd" >/dev/null 2>&1; then
        missing+=("$cmd|$install_hint")
    fi
}

check docker   "brew install --cask docker          # or Docker Desktop / OrbStack"
check overmind "brew install overmind"
check uv       "brew install uv"
check node     "brew install node                   # or use nvm / fnm"

if [ ${#missing[@]} -ne 0 ]; then
    echo "Missing prerequisites:" >&2
    for entry in "${missing[@]}"; do
        cmd="${entry%%|*}"
        hint="${entry#*|}"
        printf "  - %-10s install with: %s\n" "$cmd" "$hint" >&2
    done
    echo "" >&2
    echo "Install the tools above, then re-run \`just dev\`." >&2
    exit 1
fi

# Ensure the Docker daemon is actually reachable (not just the CLI installed).
if ! docker info >/dev/null 2>&1; then
    echo "Docker CLI is installed but the daemon isn't running." >&2
    echo "Start Docker Desktop / OrbStack / colima, then re-run \`just dev\`." >&2
    exit 1
fi

echo "All prerequisites OK."
