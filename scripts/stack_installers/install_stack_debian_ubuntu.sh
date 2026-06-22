#!/usr/bin/env bash
set -euo pipefail

# One-command installer for rTorrent + pyTorrent on Debian/Ubuntu.
# Notes:
# - rTorrent is built as a minimal v0.16.11 install with tinyxml2 XML-RPC by default.
# - pyTorrent is configured through its HTTP API after the service starts.

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

RTORRENT_EXTRA_ARGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
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
        *)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
    esac
done
if [[ "${RTORRENT_WITH_XMLRPC_C:-0}" == "1" ]]; then
    RTORRENT_EXTRA_ARGS+=(--with-xmlrpc-c)
fi
if [[ "${RTORRENT_SCGI_BACKEND}" == "unix" ]]; then
    if [[ -z "${RTORRENT_SCGI_PROXY_TOKEN}" ]]; then
        RTORRENT_SCGI_PROXY_TOKEN="$(${PYTHON_BIN:-python3} - <<'PYTOKEN'
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

export PYTORRENT_APP_DIR PYTORRENT_PORT PYTORRENT_SERVICE_NAME PYTORRENT_API_TOKEN

install_debian_stack_prerequisites() {
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        tar \
        gzip \
        sudo \
        python3 \
        python3-venv \
        python3-pip \
        build-essential \
        pkg-config \
        libtool \
        autoconf \
        automake \
        git \
        make \
        gcc \
        g++ \
        libssl-dev \
        libncurses-dev \
        libncurses5-dev \
        libncursesw5-dev \
        libexpat1-dev \
        libcurl4-openssl-dev \
        libxml2-dev \
        libtinyxml2-dev \
        libreadline-dev \
        zlib1g-dev \
        bison \
        flex \
        m4 \
        gettext \
        texinfo \
        patch \
        diffutils \
        file \
        procps \
        xz-utils
}

install_debian_stack_prerequisites

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

python3 "${SCRIPT_DIR}/install_rtorrent.py" \
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

echo "Done. pyTorrent: ${PYTORRENT_BASE_URL} | rTorrent SCGI: ${PYTORRENT_RTORRENT_SCGI_URL}"
