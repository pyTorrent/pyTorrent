#!/usr/bin/env bash
set -euo pipefail

# Bootstrap installer for pyTorrent + rTorrent.
# Installs from a Git clone so /opt/pytorrent remains updateable with `pytorrent update`.

if [[ "${EUID}" -ne 0 ]]; then
    echo "Run as root, for example: curl -fsSL <url> | sudo bash" >&2
    exit 1
fi

REPO_URL="${PYTORRENT_REPO_URL:-https://github.com/pyTorrent/pyTorrent.git}"
REPO_BRANCH="${PYTORRENT_REPO_BRANCH:-master}"
WORK_DIR="${PYTORRENT_BOOTSTRAP_DIR:-/tmp/pytorrent-stack-installer}"
KEEP_WORK_DIR="${PYTORRENT_KEEP_BOOTSTRAP_DIR:-0}"
CLONE_RETRIES="${PYTORRENT_DOWNLOAD_RETRIES:-4}"
CLONE_RETRY_DELAY="${PYTORRENT_DOWNLOAD_RETRY_DELAY:-10}"
PROJECT_DIR="${WORK_DIR}/src"

log() { printf '[pyTorrent stack] %s\n' "$*"; }
fail() { printf '[pyTorrent stack] ERROR: %s\n' "$*" >&2; exit 1; }

run_quiet() {
    local tmp rc
    tmp="$(mktemp)"
    if "$@" >"${tmp}" 2>&1; then
        rm -f "${tmp}"
        return 0
    fi
    rc=$?
    cat "${tmp}" >&2
    rm -f "${tmp}"
    return "${rc}"
}

prepare_bootstrap() {
    log "Installing bootstrap packages..."
    if command -v apt-get >/dev/null 2>&1; then
        export DEBIAN_FRONTEND=noninteractive
        run_quiet apt-get update
        run_quiet apt-get install -y --no-install-recommends ca-certificates git sudo python3
    elif command -v dnf >/dev/null 2>&1; then
        run_quiet dnf install -y ca-certificates git sudo python3
    elif command -v yum >/dev/null 2>&1; then
        run_quiet yum install -y ca-certificates git sudo python3
    elif command -v pacman >/dev/null 2>&1; then
        run_quiet pacman -Sy --noconfirm --needed ca-certificates git sudo python
    else
        fail "No supported package manager found."
    fi
    command -v git >/dev/null 2>&1 || fail "git is required."
}

clone_repository() {
    local attempt
    rm -rf "${PROJECT_DIR}"
    mkdir -p "${WORK_DIR}"
    for ((attempt=1; attempt<=CLONE_RETRIES; attempt++)); do
        log "Cloning pyTorrent repository (${REPO_BRANCH})..."
        if run_quiet git clone --quiet --depth 1 --branch "${REPO_BRANCH}" --single-branch "${REPO_URL}" "${PROJECT_DIR}"; then
            return 0
        fi
        if [[ "${attempt}" -lt "${CLONE_RETRIES}" ]]; then
            sleep "${CLONE_RETRY_DELAY}"
        fi
    done
    fail "Cannot clone ${REPO_URL} branch ${REPO_BRANCH}."
}

detect_os_family() {
    [[ -f /etc/os-release ]] || fail "Cannot detect OS: /etc/os-release is missing."
    # shellcheck disable=SC1091
    . /etc/os-release
    case "${ID:-} ${ID_LIKE:-}" in
        *debian*|*ubuntu*) echo debian ;;
        *rhel*|*fedora*|*centos*|*rocky*|*almalinux*) echo rhel ;;
        *arch*) echo arch ;;
        *) fail "Unsupported OS: ID=${ID:-unknown}, ID_LIKE=${ID_LIKE:-unknown}." ;;
    esac
}

cleanup() {
    if [[ "${KEEP_WORK_DIR}" != "1" ]]; then
        rm -rf "${WORK_DIR}"
    else
        log "Keeping bootstrap directory: ${WORK_DIR}"
    fi
}
trap cleanup EXIT

prepare_bootstrap
clone_repository

OS_FAMILY="$(detect_os_family)"
case "${OS_FAMILY}" in
    debian) INSTALLER="${PROJECT_DIR}/scripts/stack_installers/install_stack_debian_ubuntu.sh" ;;
    rhel) INSTALLER="${PROJECT_DIR}/scripts/stack_installers/install_stack_rhel.sh" ;;
    arch) INSTALLER="${PROJECT_DIR}/scripts/stack_installers/install_stack_arch.sh" ;;
    *) fail "Unsupported OS family: ${OS_FAMILY}." ;;
esac

export PYTORRENT_REPO_URL="${REPO_URL}"
export PYTORRENT_REPO_BRANCH="${REPO_BRANCH}"
chmod +x "${PROJECT_DIR}/scripts/stack_installers/"*.sh || true
log "Starting ${OS_FAMILY} stack installer..."
bash "${INSTALLER}" "$@"
