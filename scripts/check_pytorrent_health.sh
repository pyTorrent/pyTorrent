#!/bin/sh
# Note: Simple Nagios-compatible pyTorrent API check; set PYTORRENT_URL if the app is not local.
URL="${PYTORRENT_URL:-http://127.0.0.1:8000/api/health/nagios}"
OUT=$(curl -fsS --max-time "${PYTORRENT_HEALTH_TIMEOUT:-5}" "$URL" 2>&1)
RC=$?
if [ "$RC" -eq 0 ]; then
  printf '%s\n' "$OUT"
  exit 0
fi
printf 'CRITICAL - pyTorrent health check failed: %s\n' "$OUT"
exit 2
