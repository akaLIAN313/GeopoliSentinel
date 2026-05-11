#!/usr/bin/env bash
# bootstrap.sh — Cold-start: generate initial data/state.md via Tavily + Claude.
#
# Usage:
#   ./bootstrap.sh          # skips if state.md already exists
#   ./bootstrap.sh --force  # overwrite existing state.md

set -euo pipefail

cd "$(dirname "$0")"

if [ ! -f .env ]; then
  echo "ERROR: .env not found. Copy .env.example and fill in your keys." >&2
  exit 1
fi

if [ ! -d .venv ]; then
  echo "Setting up virtual environment..."
  uv venv
  uv sync
fi

source .venv/bin/activate

echo "=== Resonance Bootstrap ==="
uv run python src/bootstrap.py "$@"
echo "=== Done. Run ./update.sh to generate today's report. ==="
