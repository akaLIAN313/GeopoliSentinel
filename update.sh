#!/usr/bin/env bash
# update.sh — Daily update: fetch articles, run AI analysis, send Telegram report.
#
# Usage:
#   ./update.sh             # full pipeline: fetch → analyze → notify
#   ./update.sh --no-notify # skip Telegram (useful for local testing)

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

NOTIFY=true
for arg in "$@"; do
  [ "$arg" = "--no-notify" ] && NOTIFY=false
done

if [ ! -f data/state.md ]; then
  echo "No state.md found — running bootstrap first..."
  uv run python src/bootstrap.py
fi

echo "=== Phase 1 + 2: Fetch & Analyze ==="
uv run python src/analyzer.py

if [ "$NOTIFY" = true ]; then
  echo "=== Phase 3: Sending Telegram report ==="
  uv run python src/notifier.py
else
  echo "=== Phase 3: Skipped (--no-notify) ==="
  echo "Report saved to data/latest_report.json"
fi

echo "=== Done ==="
