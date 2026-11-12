#!/bin/sh
# Start the dashboard at http://localhost:8000 (macOS / Linux). Windows: run.ps1
cd "$(dirname "$0")"
[ -f .env ] || { cp .env.example .env; echo "Created .env from .env.example - edit it (passwords!) and run again."; exit 1; }
[ -d .venv ] || { python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt; }
if ! grep -qE '^DASHBOARD_PASSWORD=.+' .env && [ "${HOST:-0.0.0.0}" != "127.0.0.1" ]; then
  echo "Warning: DASHBOARD_PASSWORD is empty and the dashboard listens on your network. Set it in .env."
fi
exec .venv/bin/uvicorn app.main:app --host "${HOST:-0.0.0.0}" --port "${PORT:-8000}"
