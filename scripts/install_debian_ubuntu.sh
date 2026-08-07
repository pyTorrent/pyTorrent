#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTORRENT_REPO_URL="${PYTORRENT_REPO_URL:-https://github.com/pyTorrent/pyTorrent.git}"
export PYTORRENT_REPO_BRANCH="${PYTORRENT_REPO_BRANCH:-master}"
exec bash "${SCRIPT_DIR}/install_pytorrent_only.sh" --yes "$@"
