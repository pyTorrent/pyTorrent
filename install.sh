#!/usr/bin/env bash
set -euo pipefail

log() { printf '[pyTorrent] %s\n' "$*"; }
run_quiet() {
    local tmp rc
    tmp="$(mktemp)"
    if "$@" >"${tmp}" 2>&1; then rm -f "${tmp}"; return 0; fi
    rc=$?; cat "${tmp}" >&2; rm -f "${tmp}"; return "${rc}"
}

log "Preparing Python environment..."
python3 -m venv .venv
. .venv/bin/activate
run_quiet pip install -q --upgrade pip
run_quiet pip install -q -r requirements.txt
cp -n .env.example .env || true
grep -q '^PYTORRENT_USE_OFFLINE_LIBS=' .env || echo 'PYTORRENT_USE_OFFLINE_LIBS=true' >> .env
log "Preparing frontend static files..."
PYTORRENT_INSTALL_VERBOSE=0 ./scripts/download_frontend_libs.py
log "Preparing GeoIP database..."
./scripts/download_geoip.sh data/GeoLite2-City.mmdb >/dev/null 2>&1 || true
mkdir -p data
chmod 755 data
log "Initializing database..."
python -c "from pytorrent.db import init_db; init_db()"
log "Ready. Run: . .venv/bin/activate && python app.py"
