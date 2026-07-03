#!/usr/bin/env bash

set -euo pipefail

APP_ENTRY="app.py"
MOCK_ENTRY="scripts/mock"

RUN_APP=true
RUN_MOCK=true
PYTHON_BIN=""

APP_PID=""
MOCK_PID=""
STOPPED=false

show_help() {
  cat <<'EOF'
Usage:
  ./dev.sh [options]

Options:
  -h, --help       Show help
  --no-mock        Run only app.py
  --mock-only      Run only scripts/mock

Description:
  Starts the local development environment.
  Uses .venv first, then venv, then python3.
  Runs app.py and scripts/mock in parallel.
  Press Ctrl+C once to stop all started processes.

Environment:
  PYTORRENT_ALLOW_UNSAFE_WERKZEUG=1 is set automatically for local dev.
EOF
}

find_python() {
  if [ -x ".venv/bin/python" ]; then
    PYTHON_BIN=".venv/bin/python"
  elif [ -x "venv/bin/python" ]; then
    PYTHON_BIN="venv/bin/python"
  elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
  else
    echo "Error: Python not found. Create .venv/venv or install python3." >&2
    exit 1
  fi
}

stop_process() {
  local pid="$1"

  if [ -n "$pid" ] && kill -0 "$pid" >/dev/null 2>&1; then
    kill "$pid" >/dev/null 2>&1 || true
  fi
}

stop_all() {
  if [ "$STOPPED" = true ]; then
    return
  fi

  STOPPED=true

  echo
  echo "Stopping development processes..."

  stop_process "$APP_PID"
  stop_process "$MOCK_PID"

  wait >/dev/null 2>&1 || true

  echo "Stopped."
}

run_app() {
  if [ ! -f "$APP_ENTRY" ]; then
    echo "Error: $APP_ENTRY not found." >&2
    exit 1
  fi

  echo "Starting app: $PYTHON_BIN $APP_ENTRY"
  PYTORRENT_ALLOW_UNSAFE_WERKZEUG=1 "$PYTHON_BIN" "$APP_ENTRY" &
  APP_PID="$!"
}

run_mock() {
  if [ ! -e "$MOCK_ENTRY" ]; then
    echo "Error: $MOCK_ENTRY not found." >&2
    exit 1
  fi

  echo "Starting mock: $PYTHON_BIN $MOCK_ENTRY"
  "$PYTHON_BIN" "$MOCK_ENTRY" &
  MOCK_PID="$!"
}

for arg in "$@"; do
  case "$arg" in
    -h|--help)
      show_help
      exit 0
      ;;
    --no-mock)
      RUN_MOCK=false
      ;;
    --mock-only)
      RUN_APP=false
      RUN_MOCK=true
      ;;
    *)
      echo "Unknown option: $arg" >&2
      echo "Use ./dev.sh --help"
      exit 1
      ;;
  esac
done

trap stop_all INT TERM EXIT

find_python

echo "Using Python: $PYTHON_BIN"

if [ "$RUN_APP" = true ]; then
  run_app
fi

if [ "$RUN_MOCK" = true ]; then
  run_mock
fi

wait
