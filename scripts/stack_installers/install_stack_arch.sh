#!/usr/bin/env bash
set -euo pipefail

# One-command installer for rTorrent + pyTorrent on Arch Linux.
# Arch uses current repository packages by default. Source build is opt-in.

if [[ "${EUID}" -ne 0 ]]; then
    echo "Run as root: sudo $0" >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"

RTORRENT_USER="${RTORRENT_USER:-rtorrent}"
RTORRENT_HOME="${RTORRENT_HOME:-/home/${RTORRENT_USER}}"
RTORRENT_BASE_DIR="${RTORRENT_BASE_DIR:-/opt/rtorrent_build}"
RTORRENT_SCGI_PORT="${RTORRENT_SCGI_PORT:-5000}"
RTORRENT_TORRENT_PORT="${RTORRENT_TORRENT_PORT:-51300}"
RTORRENT_REF="${RTORRENT_REF:-v0.16.11}"
LIBTORRENT_REF="${LIBTORRENT_REF:-v0.16.11}"
PYTORRENT_APP_DIR="${PYTORRENT_APP_DIR:-/opt/pytorrent}"
PYTORRENT_PORT="${PYTORRENT_PORT:-8090}"
PYTORRENT_BASE_URL="${PYTORRENT_BASE_URL:-http://127.0.0.1:${PYTORRENT_PORT}}"
PYTORRENT_PROFILE_NAME="${PYTORRENT_PROFILE_NAME:-Local rTorrent}"
PYTORRENT_API_TOKEN="${PYTORRENT_API_TOKEN:-}"
PYTORRENT_SERVICE_NAME="${PYTORRENT_SERVICE_NAME:-pytorrent}"
RTORRENT_SCGI_BACKEND="${RTORRENT_SCGI_BACKEND:-tcp}"
RTORRENT_SCGI_SOCKET="${RTORRENT_SCGI_SOCKET:-/run/rtorrent/rtorrent.sock}"
RTORRENT_SCGI_PROXY_LISTEN="${RTORRENT_SCGI_PROXY_LISTEN:-127.0.0.1:5050}"
RTORRENT_SCGI_PROXY_TOKEN="${RTORRENT_SCGI_PROXY_TOKEN:-}"
PYTORRENT_RTORRENT_SCGI_URL="${PYTORRENT_RTORRENT_SCGI_URL:-scgi://127.0.0.1:${RTORRENT_SCGI_PORT}}"
RTORRENT_BUILD_FROM_SOURCE="${RTORRENT_BUILD_FROM_SOURCE:-0}"
RTORRENT_FORCE_CONFIG="${RTORRENT_FORCE_CONFIG:-1}"

RTORRENT_EXTRA_ARGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --build-rtorrent|--build-from-source|--compile-rtorrent)
            RTORRENT_BUILD_FROM_SOURCE=1
            shift
            ;;
        --with-xmlrpc-c)
            RTORRENT_BUILD_FROM_SOURCE=1
            RTORRENT_EXTRA_ARGS+=(--with-xmlrpc-c)
            shift
            ;;
        --scgi-unix-socket)
            RTORRENT_SCGI_BACKEND=unix
            shift
            ;;
        --rtorrent-socket)
            RTORRENT_SCGI_BACKEND=unix
            RTORRENT_SCGI_SOCKET="$2"
            shift 2
            ;;
        *)
            echo "Unknown option: $1" >&2
            echo "Supported options: --build-rtorrent, --with-xmlrpc-c, --scgi-unix-socket, --rtorrent-socket PATH" >&2
            exit 1
            ;;
    esac
done
if [[ "${RTORRENT_WITH_XMLRPC_C:-0}" == "1" ]]; then
    RTORRENT_BUILD_FROM_SOURCE=1
    RTORRENT_EXTRA_ARGS+=(--with-xmlrpc-c)
fi

if [[ "${RTORRENT_SCGI_BACKEND}" == "unix" ]]; then
    if [[ -z "${RTORRENT_SCGI_PROXY_TOKEN}" ]]; then
        RTORRENT_SCGI_PROXY_TOKEN="$(python - <<'PYTOKEN'
import secrets
print(secrets.token_urlsafe(32))
PYTOKEN
)"
    fi
    PYTORRENT_RTORRENT_SCGI_URL="scgi://${RTORRENT_SCGI_PROXY_LISTEN}/proxy/${RTORRENT_SCGI_PROXY_TOKEN}"
elif [[ "${RTORRENT_SCGI_BACKEND}" != "tcp" ]]; then
    echo "Invalid RTORRENT_SCGI_BACKEND: ${RTORRENT_SCGI_BACKEND}" >&2
    exit 1
fi

command -v pacman >/dev/null 2>&1 || { echo "pacman is required on Arch Linux." >&2; exit 1; }
pacman -Sy --noconfirm --needed ca-certificates curl tar gzip sudo python python-pip git rsync rtorrent

ensure_rtorrent_user() {
    if ! getent group "${RTORRENT_USER}" >/dev/null; then
        groupadd --system "${RTORRENT_USER}"
    fi
    if ! id "${RTORRENT_USER}" >/dev/null 2>&1; then
        local shell="/usr/bin/nologin"
        [[ -x "${shell}" ]] || shell="/sbin/nologin"
        useradd --system --gid "${RTORRENT_USER}" --home-dir "${RTORRENT_HOME}" --create-home --shell "${shell}" "${RTORRENT_USER}"
    fi
    mkdir -p "${RTORRENT_HOME}/downloads" "${RTORRENT_HOME}/.session" "${RTORRENT_HOME}/watch"
    chown -R "${RTORRENT_USER}:${RTORRENT_USER}" "${RTORRENT_HOME}"
}

write_rtorrent_config() {
    local config="${RTORRENT_HOME}/.rtorrent.rc"
    if [[ -f "${config}" && "${RTORRENT_FORCE_CONFIG}" != "1" ]]; then
        echo "Keeping existing config: ${config}"
        return
    fi
    cat > "${config}" <<EOF_CONFIG
directory.default.set = ${RTORRENT_HOME}/downloads
session.path.set = ${RTORRENT_HOME}/.session
encoding.add = UTF-8

$(if [[ "${RTORRENT_SCGI_BACKEND}" == "unix" ]]; then printf 'network.scgi.open_local = %s\n' "${RTORRENT_SCGI_SOCKET}"; else printf 'network.scgi.open_port = 127.0.0.1:%s\n' "${RTORRENT_SCGI_PORT}"; fi)
network.port_range.set = ${RTORRENT_TORRENT_PORT}-${RTORRENT_TORRENT_PORT}
network.port_random.set = no
network.bind_address.ipv4.set = 0.0.0.0

system.file.allocate.set = 0
system.umask.set = 0022

dht.mode.set = disable
protocol.pex.set = no
trackers.use_udp.set = no
protocol.encryption.set = allow_incoming,enable_retry,prefer_plaintext

schedule2 = session_save,300,300,((session.save))
schedule2 = watch_directory,60,60,load.normal=${RTORRENT_HOME}/watch/*.torrent

ratio.max.set = -1
network.xmlrpc.size_limit.set = 33554432

network.http.max_open.set = 64
network.max_open_sockets.set = 1024
network.max_open_files.set = 8192
network.http.dns_cache_timeout.set = 25
network.http.ssl_verify_peer.set = 0

network.send_buffer.size.set = 4M
network.receive_buffer.size.set = 4M

throttle.min_peers.normal.set = 30
throttle.max_peers.normal.set = 150
throttle.min_peers.seed.set = -1
throttle.max_peers.seed.set = -1
throttle.max_downloads.global.set = 300
throttle.max_uploads.global.set = 300
throttle.max_downloads.set = 20
throttle.max_uploads.set = 20

trackers.numwant.set = 80
pieces.hash.on_completion.set = 0
EOF_CONFIG
    chown "${RTORRENT_USER}:${RTORRENT_USER}" "${config}"
}

write_rtorrent_service() {
    cat > /etc/systemd/system/rtorrent@.service <<EOF_SERVICE
[Unit]
Description=rTorrent for %I
After=network.target

[Service]
Type=simple
User=%I
Group=%I
KillMode=process
RuntimeDirectory=${RTORRENT_USER}
RuntimeDirectoryMode=0750
WorkingDirectory=${RTORRENT_HOME}
ExecStartPre=-/bin/rm -f ${RTORRENT_HOME}/.session/rtorrent.lock
ExecStart=/usr/bin/rtorrent -o system.daemon.set=true -n -o import=${RTORRENT_HOME}/.rtorrent.rc
KillSignal=SIGTERM
TimeoutStopSec=300
Restart=always
RestartSec=3
LimitNOFILE=1048576

[Install]
WantedBy=multi-user.target
EOF_SERVICE
    systemctl daemon-reload
    systemctl enable --now "rtorrent@${RTORRENT_USER}.service"
}

if [[ "${RTORRENT_BUILD_FROM_SOURCE}" == "1" ]]; then
    pacman -Sy --noconfirm --needed gcc pkgconf base-devel automake autoconf libtool make patch diffutils file openssl ncurses expat curl tinyxml2 readline libxml2
    RTORRENT_INSTALL_ARGS=(
        --yes
        --minimal
        "${RTORRENT_EXTRA_ARGS[@]}"
    )
    if [[ "${PYTORRENT_DEBUG_INSTALL:-0}" == "1" ]]; then
        RTORRENT_INSTALL_ARGS+=(--debug)
    fi

    if [[ "${RTORRENT_SCGI_BACKEND}" == "unix" ]]; then
        RTORRENT_INSTALL_ARGS+=(--scgi-unix-socket "${RTORRENT_SCGI_SOCKET}")
    fi
    python "${SCRIPT_DIR}/install_rtorrent.py" \
        "${RTORRENT_INSTALL_ARGS[@]}" \
        --force-config \
        --base-dir "${RTORRENT_BASE_DIR}" \
        --user "${RTORRENT_USER}" \
        --group "${RTORRENT_USER}" \
        --home "${RTORRENT_HOME}" \
        --scgi-port "${RTORRENT_SCGI_PORT}" \
        --torrent-port "${RTORRENT_TORRENT_PORT}" \
        --rtorrent-ref "${RTORRENT_REF}" \
        --libtorrent-ref "${LIBTORRENT_REF}"
else
    ensure_rtorrent_user
    write_rtorrent_config
    write_rtorrent_service
fi

cd "${PROJECT_DIR}"
PYTORRENT_ONLY_ARGS=(
    --yes
    --app-dir "${PYTORRENT_APP_DIR}"
    --port "${PYTORRENT_PORT}"
    --service-name "${PYTORRENT_SERVICE_NAME}"
    --profile-name "${PYTORRENT_PROFILE_NAME}"
    --scgi-url "${PYTORRENT_RTORRENT_SCGI_URL}"
)
if [[ "${RTORRENT_SCGI_BACKEND}" == "unix" ]]; then
    PYTORRENT_ONLY_ARGS+=(
        --install-scgi-proxy yes
        --rtorrent-user "${RTORRENT_USER}"
        --rtorrent-socket "${RTORRENT_SCGI_SOCKET}"
        --proxy-target-network unix
        --proxy-target-address "${RTORRENT_SCGI_SOCKET}"
        --proxy-listen "${RTORRENT_SCGI_PROXY_LISTEN}"
        --proxy-token "${RTORRENT_SCGI_PROXY_TOKEN}"
    )
fi
bash "${PROJECT_DIR}/scripts/install_pytorrent_only.sh" "${PYTORRENT_ONLY_ARGS[@]}"

if [[ -n "${PYTORRENT_API_TOKEN}" ]]; then
    "${PYTORRENT_APP_DIR}/.venv/bin/python" "${PYTORRENT_APP_DIR}/scripts/stack_installers/configure_pytorrent_api.py" \
        --base-url "${PYTORRENT_BASE_URL}" \
        --profile-name "${PYTORRENT_PROFILE_NAME}" \
        --scgi-url "${PYTORRENT_RTORRENT_SCGI_URL}" \
        --api-token "${PYTORRENT_API_TOKEN}"
fi

if [[ "${RTORRENT_BUILD_FROM_SOURCE}" == "1" ]]; then
    RTORRENT_MODE="source build"
else
    RTORRENT_MODE="Arch pacman package"
fi

echo "Done. pyTorrent: ${PYTORRENT_BASE_URL} | rTorrent SCGI: ${PYTORRENT_RTORRENT_SCGI_URL} | rTorrent: ${RTORRENT_MODE}"
