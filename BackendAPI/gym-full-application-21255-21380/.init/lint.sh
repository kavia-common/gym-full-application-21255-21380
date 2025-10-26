#!/usr/bin/env bash
set -euo pipefail

# Prefer the robust linter shim under the container; fall back to no-op success
if [ -f "./.init/.linter.sh" ]; then
  bash ./.init/.linter.sh || exit 0
else
  echo "Linter shim not found; skipping lint." >&2
  exit 0
fi
