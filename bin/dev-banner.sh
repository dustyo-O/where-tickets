#!/usr/bin/env bash
# Print a startup summary so a fresh dev knows where to reach things.
# Invoked from `just dev` right before overmind starts.
set -euo pipefail

bold=$(tput bold 2>/dev/null || echo "")
dim=$(tput dim 2>/dev/null || echo "")
green=$(tput setaf 2 2>/dev/null || echo "")
reset=$(tput sgr0 2>/dev/null || echo "")

cat <<EOF

${bold}${green}Where Tickets — local stack starting${reset}

  ${bold}Backend:${reset}    http://localhost:8000
  ${bold}Health:${reset}     http://localhost:8000/health

  ${bold}Mobile app:${reset}
    iOS:      cd mobile && npm run ios
    Android:  cd mobile && npm run android

  ${dim}Stop everything with Ctrl+C, then \`just down\`.${reset}

EOF
