#!/usr/bin/env bash
set -euo pipefail

APP_HOST="${APP_HOST:-0.0.0.0}"
APP_PORT="${APP_PORT:-8000}"

exec uvicorn main:app --host "${APP_HOST}" --port "${APP_PORT}"
