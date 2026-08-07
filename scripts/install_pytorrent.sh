#!/usr/bin/env bash
set -euo pipefail

# Bootstrap installer for pyTorrent only.
# Intended usage:
#   curl -fsSL https://raw.githubusercontent.com/pyTorrent/pyTorrent/master/scripts/install_pytorrent.sh | sudo bash

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    cat <<'USAGE'
Usage: curl -fsSL https://raw.githubusercontent.com/pyTorrent/pyTorrent/master/scripts/install_pytorrent.sh | sudo bash -s -- [options]

This bootstrap downloads pyTorrent and forwards all options to scripts/install_pytorrent_only.sh.
Run scripts/install_pytorrent_only.sh --help inside the repository for the full option list.
USAGE
    exit 0
fi

if [[ "${EUID}" -ne 0 ]]; then
    echo "Run as root, for example: curl -fsSL <url> | sudo bash" >&2
    exit 1
fi

REPO_URL="${PYTORRENT_REPO_URL:-https://github.com/pyTorrent/pyTorrent}"
REPO_BRANCH="${PYTORRENT_REPO_BRANCH:-master}"
WORK_DIR="${PYTORRENT_BOOTSTRAP_DIR:-/tmp/pytorrent-only-installer}"
KEEP_WORK_DIR="${PYTORRENT_KEEP_BOOTSTRAP_DIR:-0}"
DOWNLOAD_RETRIES="${PYTORRENT_DOWNLOAD_RETRIES:-4}"
DOWNLOAD_RETRY_DELAY="${PYTORRENT_DOWNLOAD_RETRY_DELAY:-10}"
DOWNLOAD_CONNECT_TIMEOUT="${PYTORRENT_DOWNLOAD_CONNECT_TIMEOUT:-30}"
DOWNLOAD_MAX_TIME="${PYTORRENT_DOWNLOAD_MAX_TIME:-600}"

default_archive_url() {
    case "${REPO_URL%/}" in
        https://github.com/*)
            printf '%s/archive/refs/heads/%s.tar.gz\n' "${REPO_URL%/}" "${REPO_BRANCH}"
            ;;
        *)
            printf '%s/archive/%s.tar.gz\n' "${REPO_URL%/}" "${REPO_BRANCH}"
            ;;
    esac
}

ARCHIVE_URL="${PYTORRENT_ARCHIVE_URL:-$(default_archive_url)}"
PROJECT_DIR="${WORK_DIR}/src"
ARCHIVE_PATH="${WORK_DIR}/pytorrent.tar.gz"

log() { printf '[pyTorrent bootstrap] %s\n' "$*"; }
fail() { printf '[pyTorrent bootstrap] ERROR: %s\n' "$*" >&2; exit 1; }
command_exists() { command -v "$1" >/dev/null 2>&1; }

prepare_downloader() {
    # Note: Bootstrap installs only tools required to fetch and unpack the repository.
    if command_exists apt-get; then
        apt-get update
        apt-get install -y --no-install-recommends ca-certificates curl tar gzip python3 sudo
    elif command_exists dnf; then
        dnf install -y ca-certificates curl tar gzip python3 sudo
    elif command_exists yum; then
        yum install -y ca-certificates curl tar gzip python3 sudo
    elif command_exists pacman; then
        pacman -Sy --noconfirm --needed ca-certificates curl tar gzip python sudo
    fi
    if command_exists curl; then DOWNLOADER="curl"; return; fi
    if command_exists wget; then DOWNLOADER="wget"; return; fi
    fail "curl or wget is required."
}

retry_countdown() {
    local seconds="$1"
    local remaining
    for ((remaining=seconds; remaining>0; remaining--)); do
        printf 'Retrying in %ss...\r' "${remaining}"
        sleep 1
    done
    [[ "${seconds}" -gt 0 ]] && printf '%*s\r' 40 ''
}

archive_url_candidates() {
    local url="$1"
    printf '%s\n' "${url}"
    case "${url}" in
        https://github.com/*/archive/refs/heads/*.tar.gz)
            local rest owner repo branch
            rest="${url#https://github.com/}"
            owner="${rest%%/*}"
            rest="${rest#*/}"
            repo="${rest%%/*}"
            branch="${url##*/}"
            branch="${branch%.tar.gz}"
            printf 'https://codeload.github.com/%s/%s/tar.gz/refs/heads/%s\n' "${owner}" "${repo}" "${branch}"
            ;;
        https://github.com/*/archive/*.tar.gz)
            local rest owner repo ref
            rest="${url#https://github.com/}"
            owner="${rest%%/*}"
            rest="${rest#*/}"
            repo="${rest%%/*}"
            ref="${url##*/}"
            ref="${ref%.tar.gz}"
            printf 'https://codeload.github.com/%s/%s/tar.gz/%s\n' "${owner}" "${repo}" "${ref}"
            ;;
    esac
}

download_file() {
    local url="$1"
    local destination="$2"
    local candidate attempt status
    while IFS= read -r candidate; do
        [[ -n "${candidate}" ]] || continue
        for ((attempt=1; attempt<=DOWNLOAD_RETRIES; attempt++)); do
            if [[ "${DOWNLOADER}" == "curl" ]]; then
                curl -fL --connect-timeout "${DOWNLOAD_CONNECT_TIMEOUT}" --max-time "${DOWNLOAD_MAX_TIME}" "${candidate}" -o "${destination}" && return 0
                status=$?
            else
                wget --timeout="${DOWNLOAD_CONNECT_TIMEOUT}" --read-timeout="${DOWNLOAD_MAX_TIME}" --tries=1 -O "${destination}" "${candidate}" && return 0
                status=$?
            fi
            log "Download failed (${attempt}/${DOWNLOAD_RETRIES}) from ${candidate} (exit ${status})."
            if [[ "${attempt}" -lt "${DOWNLOAD_RETRIES}" ]]; then
                retry_countdown "${DOWNLOAD_RETRY_DELAY}"
            fi
        done
        log "Trying alternative source if available after: ${candidate}"
    done < <(archive_url_candidates "${url}")
    return 1
}

cleanup() {
    if [[ "${KEEP_WORK_DIR}" != "1" ]]; then
        rm -rf "${WORK_DIR}"
    else
        log "Keeping bootstrap directory: ${WORK_DIR}"
    fi
}
trap cleanup EXIT

prepare_downloader
rm -rf "${WORK_DIR}"
mkdir -p "${PROJECT_DIR}"
log "Downloading pyTorrent from ${ARCHIVE_URL}"
download_file "${ARCHIVE_URL}" "${ARCHIVE_PATH}"
tar -xzf "${ARCHIVE_PATH}" -C "${PROJECT_DIR}" --strip-components=1
[[ -f "${PROJECT_DIR}/scripts/install_pytorrent_only.sh" ]] || fail "Missing scripts/install_pytorrent_only.sh in downloaded repository."
chmod +x "${PROJECT_DIR}/scripts/install_pytorrent_only.sh"
log "Running pyTorrent-only installer"
bash "${PROJECT_DIR}/scripts/install_pytorrent_only.sh" "$@"
