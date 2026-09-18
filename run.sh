#!/bin/bash

set -euo pipefail

# Ensure we're in the repo root
cd "$(dirname "$0")"

export PYTHONPATH="${PWD}:${PYTHONPATH:-}"
export PYTHONUNBUFFERED=1

# The upstream engine reports usage by default. This service never does.
export NEEDLE_TELEMETRY=0
export DO_NOT_TRACK=1

if [[ ! -x .venv/bin/python || ! -x engine/needle || ! -f engine/needle3.cact ]]; then
  echo "[needle.server] SETUP_MISSING: run ./setup.sh first" >&2
  exit 2
fi

# Defaults (allow override via environment)
export HOST=${HOST:-0.0.0.0}
export PORT=${PORT:-4007}

echo "Starting Kortexa Needle server on $HOST:$PORT..."

exec .venv/bin/python -m needle_server
