#!/usr/bin/env bash
# Idempotently configure branch protection for the `main` branch.
#
# Requires GitHub CLI (`gh`) authenticated with admin rights on the repo.
# Usage:
#   ./.github/scripts/setup-branch-protection.sh                 # uses $GITHUB_REPOSITORY
#   ./.github/scripts/setup-branch-protection.sh owner/repo      # explicit
set -euo pipefail

if ! command -v gh >/dev/null 2>&1; then
  echo "error: 'gh' CLI not found. Install from https://cli.github.com/" >&2
  exit 1
fi

if ! gh auth status >/dev/null 2>&1; then
  echo "error: gh is not authenticated. Run: gh auth login" >&2
  exit 1
fi

REPO="${1:-${GITHUB_REPOSITORY:-}}"
if [ -z "$REPO" ]; then
  echo "error: pass owner/repo as the first arg, or set GITHUB_REPOSITORY" >&2
  exit 1
fi

echo "Configuring branch protection on ${REPO}:main ..."

gh api \
  --method PUT \
  -H "Accept: application/vnd.github+json" \
  "/repos/${REPO}/branches/main/protection" \
  --input - <<'JSON'
{
  "required_status_checks": {
    "strict": true,
    "contexts": ["backend", "mobile", "infra", "meta"]
  },
  "enforce_admins": false,
  "required_pull_request_reviews": {
    "required_approving_review_count": 1,
    "dismiss_stale_reviews": false,
    "require_code_owner_reviews": false
  },
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_conversation_resolution": true
}
JSON

echo "Done."
