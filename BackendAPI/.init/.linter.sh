#!/usr/bin/env bash
set -euo pipefail

# Optional venv activation if exists
if [ -d "venv" ]; then
  # shellcheck disable=SC1091
  source venv/bin/activate || true
elif [ -d ".venv" ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate || true
fi

# Try to discover flake8, fall back to python -m flake8
if command -v flake8 >/dev/null 2>&1; then
  flake8
else
  python -m flake8
fi
