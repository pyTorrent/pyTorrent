#!/usr/bin/env bash
set -euo pipefail
python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
cp -n .env.example .env || true
grep -q '^PYTORRENT_USE_OFFLINE_LIBS=' .env || echo 'PYTORRENT_USE_OFFLINE_LIBS=true' >> .env
./scripts/download_frontend_libs.py
mkdir -p data
chmod 755 data
./scripts/download_geoip.sh data/GeoLite2-City.mmdb
python -c "from pytorrent.db import init_db; init_db(); print(\"SQLite initialized\")"
echo "Run: . .venv/bin/activate && python app.py"
