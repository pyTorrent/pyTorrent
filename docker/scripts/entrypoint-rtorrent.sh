#!/usr/bin/env bash
set -euo pipefail

run_as_rtorrent_user() {
    if command -v su-exec >/dev/null 2>&1; then
        exec su-exec rtorrent:rtorrent "$@"
    fi
    if command -v gosu >/dev/null 2>&1; then
        exec gosu rtorrent:rtorrent "$@"
    fi
    exec "$@"
}

mkdir -p "${RTORRENT_SESSION_DIR}" "${RTORRENT_DOWNLOAD_DIR}" "${RTORRENT_WATCH_DIR}" "${RTORRENT_LOG_DIR}" /config
chown -R rtorrent:rtorrent "${RTORRENT_SESSION_DIR}" "${RTORRENT_DOWNLOAD_DIR}" "${RTORRENT_WATCH_DIR}" "${RTORRENT_LOG_DIR}" /config

if [[ ! -f /config/rtorrent.rc || "${RTORRENT_FORCE_CONFIG:-false}" =~ ^(1|true|yes|on)$ ]]; then
    sed \
        -e "s#__SESSION_DIR__#${RTORRENT_SESSION_DIR}#g" \
        -e "s#__DOWNLOAD_DIR__#${RTORRENT_DOWNLOAD_DIR}#g" \
        -e "s#__WATCH_DIR__#${RTORRENT_WATCH_DIR}#g" \
        -e "s#__LOG_DIR__#${RTORRENT_LOG_DIR}#g" \
        -e "s#__SCGI_HOST__#${RTORRENT_SCGI_HOST}#g" \
        -e "s#__SCGI_PORT__#${RTORRENT_SCGI_PORT}#g" \
        -e "s#__TORRENT_PORT__#${RTORRENT_TORRENT_PORT}#g" \
        -e "s#__DHT_PORT__#${RTORRENT_DHT_PORT}#g" \
        -e "s#__MIN_PEERS__#${RTORRENT_MIN_PEERS}#g" \
        -e "s#__MAX_PEERS__#${RTORRENT_MAX_PEERS}#g" \
        -e "s#__MAX_UPLOADS__#${RTORRENT_MAX_UPLOADS}#g" \
        -e "s#__DOWNLOAD_RATE__#${RTORRENT_DOWNLOAD_RATE}#g" \
        -e "s#__UPLOAD_RATE__#${RTORRENT_UPLOAD_RATE}#g" \
        -e "s#__DHT__#${RTORRENT_DHT}#g" \
        -e "s#__PEER_EXCHANGE__#${RTORRENT_PEER_EXCHANGE}#g" \
        /usr/local/share/rtorrent/rtorrent.rc.template > /config/rtorrent.rc
    if [[ -n "${RTORRENT_EXTRA_CONFIG:-}" ]]; then
        printf '\n# Extra config from RTORRENT_EXTRA_CONFIG\n%s\n' "${RTORRENT_EXTRA_CONFIG}" >> /config/rtorrent.rc
    fi
fi

if [[ -f /usr/local/share/rtorrent-package-warning.txt ]]; then
    cat /usr/local/share/rtorrent-package-warning.txt >&2
fi

if [ -f "${RTORRENT_SESSION_DIR}/rtorrent.lock" ]; then
    rm -f "${RTORRENT_SESSION_DIR}/rtorrent.lock"
fi

# new rtorrent need
RTORRENT_VERSION="$(rtorrent -h 2>&1 | head -n1 || true)"

if echo "$RTORRENT_VERSION" | grep -Eq '0\.1[0-9]\.|0\.16\.'; then
    if ! grep -q '^network.bind_address.set' /config/rtorrent.rc; then
        printf '\n# Force IPv4 listen for newer rTorrent.\nnetwork.bind_address.set = 0.0.0.0\n' >> /config/rtorrent.rc
    fi
fi

run_as_rtorrent_user "$@"
