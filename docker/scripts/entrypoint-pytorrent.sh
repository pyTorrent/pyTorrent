#!/usr/bin/env bash
set -euo pipefail

run_as_app_user() {
    if command -v su-exec >/dev/null 2>&1; then
        exec su-exec pytorrent:pytorrent "$@"
    fi
    if command -v gosu >/dev/null 2>&1; then
        exec gosu pytorrent:pytorrent "$@"
    fi
    exec "$@"
}

mkdir -p /data/logs
chown -R pytorrent:pytorrent /data

if [[ ! -f /app/.env && -f /app/.env.example ]]; then
    cp /app/.env.example /app/.env
fi

if [[ "${PYTORRENT_CONFIGURE_PROFILE:-false}" =~ ^(1|true|yes|on)$ ]]; then
    (
        /usr/local/bin/configure-pytorrent-profile || true
    ) &
fi

run_as_app_user "$@"
