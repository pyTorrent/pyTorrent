#!/usr/bin/env bash
set -euo pipefail

APP_USER="${PYTORRENT_USER:-pytorrent}"
APP_DIR="${PYTORRENT_APP_DIR:-/opt/pytorrent}"
SERVICE_NAME="${PYTORRENT_SERVICE_NAME:-pytorrent}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
PYTORRENT_HOST_VALUE="${PYTORRENT_HOST:-0.0.0.0}"
PYTORRENT_PORT_VALUE="${PYTORRENT_PORT:-8090}"
PYTORRENT_LOG_DIR_VALUE="${PYTORRENT_LOG_DIR:-/data/logs}"
PYTORRENT_LOG_RETENTION_HOURS_VALUE="${PYTORRENT_LOG_RETENTION_HOURS:-24}"

if [[ "${EUID}" -ne 0 ]]; then
    echo "Run as root: sudo $0" >&2
    exit 1
fi

export DEBIAN_FRONTEND=noninteractive

apt-get update

apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    tar \
    gzip \
    sudo \
    git \
    rsync \
    pkg-config \
    python3 \
    python3-venv \
    python3-dev \
    python3-pip

if ! id -u "${APP_USER}" >/dev/null 2>&1; then
    useradd \
        --system \
        --create-home \
        --home-dir "/var/lib/${APP_USER}" \
        --shell /usr/sbin/nologin \
        "${APP_USER}"
fi

mkdir -p "${APP_DIR}"

rsync -a --delete \
    --exclude '.git' \
    --exclude '.venv' \
    --exclude 'venv' \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    ./ "${APP_DIR}/"

cd "${APP_DIR}"

"${PYTHON_BIN}" -m venv .venv

.venv/bin/pip install --upgrade pip wheel
.venv/bin/pip install -r requirements.txt

mkdir -p data instance logs

chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}" "/var/lib/${APP_USER}"


upsert_env_value() {
    local key="$1"
    local value="$2"
    local file="${3:-.env}"
    touch "${file}"
    if grep -qE "^${key}=" "${file}"; then
        sed -i "s|^${key}=.*|${key}=${value}|" "${file}"
    else
        printf '%s=%s\n' "${key}" "${value}" >> "${file}"
    fi
}

if [[ ! -f .env && -f .env.example ]]; then
    cp .env.example .env
    chown "${APP_USER}:${APP_USER}" .env
fi

# Keep systemd service config aligned with installer overrides.
upsert_env_value "PYTORRENT_HOST" "${PYTORRENT_HOST_VALUE}" .env
upsert_env_value "PYTORRENT_PORT" "${PYTORRENT_PORT_VALUE}" .env
upsert_env_value "PYTORRENT_LOG_DIR" "${PYTORRENT_LOG_DIR_VALUE}" .env
upsert_env_value "PYTORRENT_LOG_RETENTION_HOURS" "${PYTORRENT_LOG_RETENTION_HOURS_VALUE}" .env
mkdir -p "${PYTORRENT_LOG_DIR_VALUE}"
chown -R "${APP_USER}:${APP_USER}" "${PYTORRENT_LOG_DIR_VALUE}" || true
chown "${APP_USER}:${APP_USER}" .env

if [[ -f scripts/download_frontend_libs.py ]]; then
    sudo -u "${APP_USER}" \
        "${APP_DIR}/.venv/bin/python" \
        scripts/download_frontend_libs.py || true
fi

if [[ -f scripts/download_geoip.sh ]]; then
    sudo -u "${APP_USER}" \
        bash scripts/download_geoip.sh || true
fi

cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<SERVICE
[Unit]
Description=pyTorrent Web UI
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=${APP_DIR}
Environment="PYTHONUNBUFFERED=1"
EnvironmentFile=${APP_DIR}/.env
ExecStart=${APP_DIR}/.venv/bin/gunicorn -c ${APP_DIR}/gunicorn.conf.py --worker-class gthread --workers 1 --threads 32 --bind \${PYTORRENT_HOST}:\${PYTORRENT_PORT} --access-logfile - --error-logfile - wsgi:app
Restart=always
RestartSec=3
KillSignal=SIGINT
TimeoutStopSec=20
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
SERVICE

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"
systemctl restart "${SERVICE_NAME}"
systemctl status "${SERVICE_NAME}" --no-pager --lines=20 || true

echo "pyTorrent installed in ${APP_DIR}. Service: ${SERVICE_NAME}."