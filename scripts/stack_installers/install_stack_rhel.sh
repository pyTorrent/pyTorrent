#!/usr/bin/env bash
set -euo pipefail

# One-command installer for rTorrent + pyTorrent on RHEL-compatible systems.
# Notes:
# - rTorrent is built as a minimal v0.16.11 install with tinyxml2 XML-RPC by default.
# - pyTorrent is configured through its HTTP API after the service starts.

if [[ "${EUID}" -ne 0 ]]; then
    echo "Run as root: sudo $0" >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"

log() { printf '[pyTorrent stack] %s\n' "$*"; }
run_quiet() {
    local tmp rc
    tmp="$(mktemp)"
    if "$@" >"${tmp}" 2>&1; then rm -f "${tmp}"; return 0; fi
    rc=$?; cat "${tmp}" >&2; rm -f "${tmp}"; return "${rc}"
}

RTORRENT_USER="${RTORRENT_USER:-rtorrent}"
RTORRENT_HOME="${RTORRENT_HOME:-/home/${RTORRENT_USER}}"
RTORRENT_BASE_DIR="${RTORRENT_BASE_DIR:-/opt/rtorrent_build}"
RTORRENT_SCGI_PORT="${RTORRENT_SCGI_PORT:-5000}"
RTORRENT_TORRENT_PORT="${RTORRENT_TORRENT_PORT:-51300}"
RTORRENT_REF="${RTORRENT_REF:-v0.16.11}"
RTORRENT_VERSION="${RTORRENT_VERSION:-}"
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
USE_RTPROXY="${PYTORRENT_USE_RTPROXY:-1}"
PYTORRENT_RTORRENT_SCGI_URL="${PYTORRENT_RTORRENT_SCGI_URL:-scgi://127.0.0.1:${RTORRENT_SCGI_PORT}}"

RTORRENT_EXTRA_ARGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --version)
            RTORRENT_VERSION="$2"
            shift 2
            ;;
        --with-xmlrpc-c)
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
        --without-rtproxy)
            USE_RTPROXY=0
            shift
            ;;
        *)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
    esac
done
# Note: A version preset is forwarded to the source installer, which selects the compatible libtorrent/RPC backend tuple.
if [[ -n "${RTORRENT_VERSION}" ]]; then
    RTORRENT_EXTRA_ARGS+=(--version "${RTORRENT_VERSION}")
fi
if [[ "${RTORRENT_WITH_XMLRPC_C:-0}" == "1" ]]; then
    RTORRENT_EXTRA_ARGS+=(--with-xmlrpc-c)
fi
if [[ "${RTORRENT_SCGI_BACKEND}" != "tcp" && "${RTORRENT_SCGI_BACKEND}" != "unix" ]]; then
    echo "Invalid RTORRENT_SCGI_BACKEND: ${RTORRENT_SCGI_BACKEND}" >&2
    exit 1
fi

if [[ "${USE_RTPROXY}" == "1" ]]; then
    if [[ -z "${RTORRENT_SCGI_PROXY_TOKEN}" ]]; then
        RTORRENT_SCGI_PROXY_TOKEN="$(${PYTHON_BIN:-python3} - <<'PYTOKEN'
import secrets
print(secrets.token_urlsafe(32))
PYTOKEN
)"
    fi
    PYTORRENT_RTORRENT_SCGI_URL="scgi://${RTORRENT_SCGI_PROXY_LISTEN}/proxy/${RTORRENT_SCGI_PROXY_TOKEN}"
else
    if [[ "${RTORRENT_SCGI_BACKEND}" == "unix" ]]; then
        echo "--without-rtproxy cannot be used with a Unix rTorrent socket; pyTorrent requires rtorrent-scgi-proxy for Unix SCGI." >&2
        exit 1
    fi
    PYTORRENT_RTORRENT_SCGI_URL="scgi://127.0.0.1:${RTORRENT_SCGI_PORT}"
fi

export PYTORRENT_APP_DIR PYTORRENT_PORT PYTORRENT_SERVICE_NAME PYTORRENT_API_TOKEN

install_rhel_stack_prerequisites() {
    local manager=""
    if command -v dnf >/dev/null 2>&1; then
        manager="dnf"
    elif command -v yum >/dev/null 2>&1; then
        manager="yum"
    else
        echo "dnf or yum is required on RHEL-compatible systems." >&2
        exit 1
    fi

    log "Installing RHEL build packages..."
    run_quiet "${manager}" install -y ca-certificates tar curl gzip sudo python3 dnf-plugins-core epel-release || \
        run_quiet "${manager}" install -y ca-certificates tar curl gzip sudo python3

    if command -v crb >/dev/null 2>&1; then
        run_quiet crb enable || true
    fi
    run_quiet "${manager}" config-manager --set-enabled crb || true
    run_quiet "${manager}" config-manager --set-enabled powertools || true
    run_quiet "${manager}" makecache || true

    run_quiet "${manager}" groupinstall -y "Development Tools" || true
    run_quiet "${manager}" install -y \
        git \
        gcc \
        gcc-c++ \
        make \
        autoconf \
        automake \
        libtool \
        pkgconf-pkg-config \
        ncurses-devel \
        openssl-devel \
        expat-devel \
        tinyxml2-devel \
        zlib-devel \
        libcurl-devel \
        redhat-rpm-config \
        patch \
        diffutils \
        findutils \
        file \
        which \
        libstdc++-devel
}

install_rhel_stack_prerequisites

RTORRENT_INSTALL_ARGS=(
    --yes
    --minimal
    "${RTORRENT_EXTRA_ARGS[@]}"
    --force-config
)
if [[ "${PYTORRENT_DEBUG_INSTALL:-0}" == "1" ]]; then
    RTORRENT_INSTALL_ARGS+=(--debug)
fi
if [[ "${RTORRENT_SCGI_BACKEND}" == "unix" ]]; then
    RTORRENT_INSTALL_ARGS+=(--scgi-unix-socket "${RTORRENT_SCGI_SOCKET}")
fi

python3 "${SCRIPT_DIR}/install_rtorrent_rhel.py" \
    "${RTORRENT_INSTALL_ARGS[@]}" \
    --base-dir "${RTORRENT_BASE_DIR}" \
    --user "${RTORRENT_USER}" \
    --group "${RTORRENT_USER}" \
    --home "${RTORRENT_HOME}" \
    --scgi-port "${RTORRENT_SCGI_PORT}" \
    --torrent-port "${RTORRENT_TORRENT_PORT}" \
    --rtorrent-ref "${RTORRENT_REF}" \
    --libtorrent-ref "${LIBTORRENT_REF}"

cd "${PROJECT_DIR}"
PYTORRENT_ONLY_ARGS=(
    --yes
    --app-dir "${PYTORRENT_APP_DIR}"
    --port "${PYTORRENT_PORT}"
    --service-name "${PYTORRENT_SERVICE_NAME}"
    --profile-name "${PYTORRENT_PROFILE_NAME}"
    --scgi-url "${PYTORRENT_RTORRENT_SCGI_URL}"
)
if [[ "${USE_RTPROXY}" == "1" ]]; then
    PYTORRENT_ONLY_ARGS+=(
        --install-scgi-proxy yes
        --rtorrent-user "${RTORRENT_USER}"
        --proxy-listen "${RTORRENT_SCGI_PROXY_LISTEN}"
        --proxy-token "${RTORRENT_SCGI_PROXY_TOKEN}"
    )
    if [[ "${RTORRENT_SCGI_BACKEND}" == "unix" ]]; then
        PYTORRENT_ONLY_ARGS+=(
            --rtorrent-socket "${RTORRENT_SCGI_SOCKET}"
            --proxy-target-network unix
            --proxy-target-address "${RTORRENT_SCGI_SOCKET}"
        )
    else
        PYTORRENT_ONLY_ARGS+=(
            --proxy-target-network tcp
            --proxy-target-address "127.0.0.1:${RTORRENT_SCGI_PORT}"
        )
    fi
else
    PYTORRENT_ONLY_ARGS+=(--without-rtproxy)
fi
bash "${PROJECT_DIR}/scripts/install_pytorrent_only.sh" "${PYTORRENT_ONLY_ARGS[@]}"

PYTORRENT_DISPLAY_IP=""
if command -v ip >/dev/null 2>&1; then
    PYTORRENT_DISPLAY_IP="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i=1;i<=NF;i++) if ($i=="src") {print $(i+1); exit}}' || true)"
fi
if [[ -z "${PYTORRENT_DISPLAY_IP}" ]]; then
    PYTORRENT_DISPLAY_IP="$(hostname -I 2>/dev/null | tr ' ' '\n' | awk '/^[0-9]+\./ && $1 !~ /^127\./ {print; exit}' || true)"
fi
[[ -n "${PYTORRENT_DISPLAY_IP}" ]] || PYTORRENT_DISPLAY_IP="127.0.0.1"
echo "Done. pyTorrent: http://${PYTORRENT_DISPLAY_IP}:${PYTORRENT_PORT} | rTorrent SCGI: ${PYTORRENT_RTORRENT_SCGI_URL}"
