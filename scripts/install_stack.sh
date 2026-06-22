#!/usr/bin/env bash
set -euo pipefail

# Bootstrap installer for pyTorrent + rTorrent.
# Intended usage from a clean server:
#   curl -fsSL https://raw.githubusercontent.com/pyTorrent/pyTorrent/master/scripts/install_stack.sh | sudo bash
#
# The script downloads the current pyTorrent repository, detects the OS family,
# and runs the matching installer from scripts/stack_installers/.

if [[ "${EUID}" -ne 0 ]]; then
    echo "Run as root, for example: curl -fsSL <url> | sudo bash" >&2
    exit 1
fi

REPO_URL="${PYTORRENT_REPO_URL:-https://github.com/pyTorrent/pyTorrent}"
REPO_BRANCH="${PYTORRENT_REPO_BRANCH:-master}"
WORK_DIR="${PYTORRENT_BOOTSTRAP_DIR:-/tmp/pytorrent-stack-installer}"
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

default_raw_base() {
    case "${REPO_URL%/}" in
        https://github.com/*)
            local path
            path="${REPO_URL#https://github.com/}"
            printf 'https://raw.githubusercontent.com/%s/%s\n' "${path%/}" "${REPO_BRANCH}"
            ;;
        *)
            printf '%s/raw/branch/%s\n' "${REPO_URL%/}" "${REPO_BRANCH}"
            ;;
    esac
}

RAW_BASE="${PYTORRENT_RAW_BASE:-$(default_raw_base)}"
ARCHIVE_URL="${PYTORRENT_ARCHIVE_URL:-$(default_archive_url)}"
PROJECT_DIR="${WORK_DIR}/src"
ARCHIVE_PATH="${WORK_DIR}/pytorrent.tar.gz"

log() {
    printf '[pyTorrent stack] %s\n' "$*"
}

fail() {
    printf '[pyTorrent stack] ERROR: %s\n' "$*" >&2
    exit 1
}

command_exists() {
    command -v "$1" >/dev/null 2>&1
}

prepare_downloader() {
    # Bootstrap needs both a downloader and tar before repository extraction.
    if command_exists apt-get; then
        apt-get update
        apt-get install -y --no-install-recommends ca-certificates tar curl gzip python3 sudo
    elif command_exists dnf; then
        dnf install -y ca-certificates tar curl gzip python3 sudo
    elif command_exists yum; then
        yum install -y ca-certificates tar curl gzip python3 sudo
    fi

    if command_exists curl; then
        DOWNLOADER="curl"
        return
    fi
    if command_exists wget; then
        DOWNLOADER="wget"
        return
    fi

    if command_exists apt-get; then
        apt-get update
        apt-get install -y --no-install-recommends curl ca-certificates tar gzip python3 sudo
        DOWNLOADER="curl"
        return
    fi
    if command_exists dnf; then
        dnf install -y curl ca-certificates tar gzip python3 sudo
        DOWNLOADER="curl"
        return
    fi
    if command_exists yum; then
        yum install -y curl ca-certificates tar gzip python3 sudo
        DOWNLOADER="curl"
        return
    fi
    if command_exists pacman; then
        pacman -Sy --noconfirm --needed curl ca-certificates tar gzip python sudo
        DOWNLOADER="curl"
        return
    fi

    fail "curl or wget is required and no supported package manager was found."
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

detect_os_family() {
    if [[ ! -f /etc/os-release ]]; then
        fail "Cannot detect OS: /etc/os-release is missing."
    fi

    # shellcheck disable=SC1091
    . /etc/os-release
    local os_id="${ID:-}"
    local os_like="${ID_LIKE:-}"

    case "${os_id} ${os_like}" in
        *debian*|*ubuntu*)
            echo "debian"
            ;;
        *rhel*|*fedora*|*centos*|*rocky*|*almalinux*)
            echo "rhel"
            ;;
        *arch*)
            echo "arch"
            ;;
        *)
            fail "Unsupported OS: ID=${ID:-unknown}, ID_LIKE=${ID_LIKE:-unknown}."
            ;;
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

prepare_downloader
rm -rf "${WORK_DIR}"
mkdir -p "${WORK_DIR}"

log "Downloading pyTorrent from ${ARCHIVE_URL}"
if ! download_file "${ARCHIVE_URL}" "${ARCHIVE_PATH}"; then
    log "Archive download failed, trying raw stack installer fallback."
    mkdir -p "${PROJECT_DIR}/scripts/stack_installers"
    for file in \
        install_stack_debian_ubuntu.sh \
        install_stack_rhel.sh \
        install_stack_arch.sh \
        install_pytorrent_rhel.sh \
        install_rtorrent.py \
        install_rtorrent_rhel.py \
        configure_pytorrent_api.py \
        INSTALL.md
    do
        download_file "${RAW_BASE}/scripts/stack_installers/${file}" "${PROJECT_DIR}/scripts/stack_installers/${file}"
    done
    download_file "${RAW_BASE}/scripts/install_debian_ubuntu.sh" "${PROJECT_DIR}/scripts/install_debian_ubuntu.sh"
else
    mkdir -p "${PROJECT_DIR}"
    tar -xzf "${ARCHIVE_PATH}" -C "${PROJECT_DIR}" --strip-components=1
fi

[[ -d "${PROJECT_DIR}/scripts/stack_installers" ]] || fail "Missing scripts/stack_installers in downloaded repository."

OS_FAMILY="$(detect_os_family)"
case "${OS_FAMILY}" in
    debian)
        INSTALLER="${PROJECT_DIR}/scripts/stack_installers/install_stack_debian_ubuntu.sh"
        ;;
    rhel)
        INSTALLER="${PROJECT_DIR}/scripts/stack_installers/install_stack_rhel.sh"
        ;;
    arch)
        INSTALLER="${PROJECT_DIR}/scripts/stack_installers/install_stack_arch.sh"
        ;;
    *)
        fail "Unsupported OS family: ${OS_FAMILY}."
        ;;
esac

chmod +x "${PROJECT_DIR}/scripts/stack_installers/"*.sh || true
log "Running ${INSTALLER}"
bash "${INSTALLER}" "$@"
