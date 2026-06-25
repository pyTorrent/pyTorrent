#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-stack}"
COMPOSE_BIN="${COMPOSE_BIN:-docker compose}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOCKER_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

ensure_env() {
    local example_file="$1"
    cd "${DOCKER_DIR}"
    if [ ! -f .env ]; then
        cp "${example_file}" .env
        echo "Created docker/.env from ${example_file}."
    fi
}

print_podman_dns_note() {
    case "${COMPOSE_BIN}" in
        *podman*)
            if ! command -v aardvark-dns >/dev/null 2>&1; then
                echo "Note: aardvark-dns was not found. The stack uses fixed IP + extra_hosts fallback for rTorrent name resolution." >&2
            fi
            ;;
    esac
}

case "${MODE}" in
    pytorrent|only)
        ensure_env ".env.pytorrent.example"
        ${COMPOSE_BIN} --env-file .env -f docker-compose.pytorrent.yml up -d --build
        ;;
    stack|full)
        ensure_env ".env.stack.example"
        print_podman_dns_note
        ${COMPOSE_BIN} --env-file .env -f docker-compose.stack.yml up -d --build
        ;;
    *)
        echo "Usage: docker/scripts/docker-up.sh [pytorrent|stack]" >&2
        exit 1
        ;;
esac
