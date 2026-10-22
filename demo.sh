#!/usr/bin/env bash
# One-command offline demo (no AWS credentials needed).
# Creates .venv on first run, installs Saathi, seeds data, starts the MCP server
# and the host, and opens the elder and family views.
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
if [ ! -x .venv/bin/python ] && [ ! -x .venv/Scripts/python.exe ]; then
  echo "Creating virtual environment…"
  "$PY" -m venv .venv
fi
if [ -x .venv/bin/python ]; then VENV_PY=.venv/bin/python; else VENV_PY=.venv/Scripts/python.exe; fi

if ! "$VENV_PY" -c "import saathi, mcp, strands" 2>/dev/null; then
  echo "Installing dependencies…"
  "$VENV_PY" -m pip install --quiet --upgrade pip
  "$VENV_PY" -m pip install --quiet -e ".[dev]"
fi

exec "$VENV_PY" scripts/demo.py "$@"
