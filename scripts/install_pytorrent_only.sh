#!/usr/bin/env bash
set -euo pipefail

# Install pyTorrent only, for hosts where rTorrent is already configured.

APP_USER="${PYTORRENT_USER:-pytorrent}"
APP_DIR="${PYTORRENT_APP_DIR:-/opt/pytorrent}"
SERVICE_NAME="${PYTORRENT_SERVICE_NAME:-pytorrent}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
APP_HOST="${PYTORRENT_HOST:-0.0.0.0}"
APP_PORT="${PYTORRENT_PORT:-8090}"
PROFILE_NAME="${PYTORRENT_PROFILE_NAME:-Local rTorrent}"
SCGI_URL="${PYTORRENT_RTORRENT_SCGI_URL:-scgi://127.0.0.1:5000}"
LOG_ENABLE="${PYTORRENT_LOG_ENABLE:-true}"
LOG_DIR="${PYTORRENT_LOG_DIR:-data/logs}"
LOG_RETENTION_HOURS="${PYTORRENT_LOG_RETENTION_HOURS:-24}"
LIBS_MODE="${PYTORRENT_LIBS_MODE:-offline}"
AUTH_MODE="${PYTORRENT_AUTH_MODE:-ask}"
AUTH_PROVIDER="${PYTORRENT_AUTH_PROVIDER:-local}"
AUTH_USER="${PYTORRENT_AUTH_USER:-pytorrent}"
AUTH_PASSWORD="${PYTORRENT_AUTH_PASSWORD:-pytorrent}"
ADMIN_PASSWORD="${PYTORRENT_ADMIN_PASSWORD:-}"
REVERSE_PROXY="${PYTORRENT_REVERSE_PROXY:-ask}"
PROXY_DOMAINS="${PYTORRENT_PROXY_DOMAINS:-}"
CORS_ORIGINS="${PYTORRENT_SOCKETIO_CORS_ALLOWED_ORIGINS:-}"
LOCAL_ORIGINS="${PYTORRENT_LOCAL_ORIGINS:-}"
RTORRENT_SOCKET="${RTORRENT_SOCKET:-}"
RTORRENT_USER="${RTORRENT_USER:-rtorrent}"
INSTALL_SCGI_PROXY="${PYTORRENT_INSTALL_SCGI_PROXY:-ask}"
RT_PROXY_USER="${RTORRENT_SCGI_PROXY_USER:-rtproxy}"
RT_PROXY_LISTEN="${RTORRENT_SCGI_PROXY_LISTEN:-127.0.0.1:5050}"
RT_PROXY_TOKEN="${RTORRENT_SCGI_PROXY_TOKEN:-}"
RT_PROXY_ALLOW_NET="${RTORRENT_SCGI_PROXY_ALLOW_NET:-127.0.0.1}"
RT_PROXY_TARGET_NETWORK_EXPLICIT="${RTORRENT_SCGI_PROXY_TARGET_NETWORK+x}"
RT_PROXY_TARGET_NETWORK="${RTORRENT_SCGI_PROXY_TARGET_NETWORK:-tcp}"
RT_PROXY_TARGET_ADDRESS="${RTORRENT_SCGI_PROXY_TARGET_ADDRESS:-127.0.0.1:5000}"
RT_PROXY_BINARY_URL="${RTORRENT_SCGI_PROXY_BINARY_URL:-https://github.com/pyTorrent/rtorrent-scgi-proxy/raw/refs/heads/master/dist/rtorrent-scgi-proxy-linux-amd64}"
RT_PROXY_TARGET_URI="${RTORRENT_SCGI_PROXY_TARGET_URI:-/RPC2}"
ASSUME_YES=0
INTERACTIVE=1
SKIP_PROFILE=0

log() { printf '[pyTorrent only] %s\n' "$*"; }
fail() { printf '[pyTorrent only] ERROR: %s\n' "$*" >&2; exit 1; }

usage() {
    cat <<'USAGE'
Usage: sudo bash scripts/install_pytorrent_only.sh [options]

Options:
  --yes                         Accept defaults and skip prompts.
  --non-interactive             Do not prompt; use flags/env/defaults.
  --app-dir PATH                Installation directory. Default: /opt/pytorrent.
  --user NAME                   System user. Default: pytorrent.
  --service-name NAME           systemd service name. Default: pytorrent.
  --host HOST                   Bind host. Default: 0.0.0.0.
  --port PORT                   Application port. Default: 8090.
  --profile-name NAME           pyTorrent profile name. Default: Local rTorrent.
  --scgi-url URL                rTorrent SCGI URL. Default: scgi://127.0.0.1:5000.
  --rtorrent-socket PATH        rTorrent Unix socket; can enable SCGI proxy setup.
  --rtorrent-user USER          rTorrent system user/group for Unix socket access. Default: rtorrent.
  --auth enable|disable         Enable pyTorrent authentication.
  --auth-provider local|proxy|tinyauth
  --auth-user USER              Local auth user to create/update. Default: pytorrent.
  --auth-password PASSWORD      Local auth user password. Default: pytorrent.
  --admin-password PASSWORD     Optional admin password reset.
  --logs enable|disable         File logging. Default: enable.
  --log-dir PATH                Log directory. Default: data/logs.
  --libs offline|online         Frontend library mode. Default: offline.
  --reverse-proxy yes|no        Configure reverse-proxy-safe env values.
  --proxy-domains CSV           Domains for reverse proxy, e.g. torrent.example.com,https://p.example.com.
  --cors-origins CSV            Extra allowed origins.
  --local-origins CSV           Extra local origins added to CORS.
  --install-scgi-proxy yes|no   Install rtorrent-scgi-proxy.
  --proxy-listen HOST:PORT      SCGI proxy listen address. Default: 127.0.0.1:5050.
  --proxy-token TOKEN           SCGI proxy path token.
  --proxy-allow-net VALUE       SCGI proxy ALLOW_NET. Default: 127.0.0.1.
  --proxy-target-network tcp|unix
  --proxy-target-address VALUE
  --skip-profile                Do not create/update pyTorrent rTorrent profile.
  -h, --help                    Show this help.

Environment variables with the same PYTORRENT_* names are also supported.
USAGE
}

require_root() {
    # Note: The installer writes systemd units, creates users and installs files under /opt.
    [[ "${EUID}" -eq 0 ]] || fail "Run as root, for example: sudo bash scripts/install_pytorrent_only.sh"
}

bool_value() {
    case "$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')" in
        1|true|yes|y|on|enable|enabled) echo "true" ;;
        0|false|no|n|off|disable|disabled) echo "false" ;;
        *) echo "$1" ;;
    esac
}

prompt() {
    local var_name="$1" question="$2" default_value="$3" current_value input
    current_value="${!var_name:-$default_value}"
    if [[ "${INTERACTIVE}" != "1" || "${ASSUME_YES}" == "1" ]]; then
        printf -v "${var_name}" '%s' "${current_value}"
        return
    fi
    read -r -p "${question} [${current_value}]: " input
    printf -v "${var_name}" '%s' "${input:-$current_value}"
}

prompt_secret() {
    local var_name="$1" question="$2" default_value="$3" current_value input
    current_value="${!var_name:-$default_value}"
    if [[ "${INTERACTIVE}" != "1" || "${ASSUME_YES}" == "1" ]]; then
        printf -v "${var_name}" '%s' "${current_value}"
        return
    fi
    read -r -s -p "${question} [default is set]: " input
    printf '\n'
    printf -v "${var_name}" '%s' "${input:-$current_value}"
}

normalize_yes_no() {
    local value
    value="$(bool_value "$1")"
    case "${value}" in
        true) echo "yes" ;;
        false) echo "no" ;;
        ask) echo "ask" ;;
        *) fail "Invalid yes/no value: $1" ;;
    esac
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --yes) ASSUME_YES=1; INTERACTIVE=0; shift ;;
            --non-interactive) INTERACTIVE=0; shift ;;
            --app-dir) APP_DIR="$2"; shift 2 ;;
            --user) APP_USER="$2"; shift 2 ;;
            --service-name) SERVICE_NAME="$2"; shift 2 ;;
            --host) APP_HOST="$2"; shift 2 ;;
            --port) APP_PORT="$2"; shift 2 ;;
            --profile-name) PROFILE_NAME="$2"; shift 2 ;;
            --scgi-url) SCGI_URL="$2"; shift 2 ;;
            --rtorrent-socket) RTORRENT_SOCKET="$2"; shift 2 ;;
            --rtorrent-user) RTORRENT_USER="$2"; shift 2 ;;
            --auth) AUTH_MODE="$(bool_value "$2")"; shift 2 ;;
            --auth-provider) AUTH_PROVIDER="$2"; shift 2 ;;
            --auth-user) AUTH_USER="$2"; shift 2 ;;
            --auth-password) AUTH_PASSWORD="$2"; shift 2 ;;
            --admin-password) ADMIN_PASSWORD="$2"; shift 2 ;;
            --logs) LOG_ENABLE="$(bool_value "$2")"; shift 2 ;;
            --log-dir) LOG_DIR="$2"; shift 2 ;;
            --libs) LIBS_MODE="$2"; shift 2 ;;
            --reverse-proxy) REVERSE_PROXY="$(normalize_yes_no "$2")"; shift 2 ;;
            --proxy-domains) PROXY_DOMAINS="$2"; shift 2 ;;
            --cors-origins) CORS_ORIGINS="$2"; shift 2 ;;
            --local-origins) LOCAL_ORIGINS="$2"; shift 2 ;;
            --install-scgi-proxy) INSTALL_SCGI_PROXY="$(normalize_yes_no "$2")"; shift 2 ;;
            --proxy-listen) RT_PROXY_LISTEN="$2"; shift 2 ;;
            --proxy-token) RT_PROXY_TOKEN="$2"; shift 2 ;;
            --proxy-allow-net) RT_PROXY_ALLOW_NET="$2"; shift 2 ;;
            --proxy-target-network) RT_PROXY_TARGET_NETWORK="$2"; RT_PROXY_TARGET_NETWORK_EXPLICIT=1; shift 2 ;;
            --proxy-target-address) RT_PROXY_TARGET_ADDRESS="$2"; shift 2 ;;
            --skip-profile) SKIP_PROFILE=1; shift ;;
            -h|--help) usage; exit 0 ;;
            *) fail "Unknown option: $1" ;;
        esac
    done
}

detect_os_family() {
    # Note: Use the same Debian/RHEL split as the full stack installer.
    [[ -f /etc/os-release ]] || fail "Cannot detect OS: /etc/os-release is missing."
    # shellcheck disable=SC1091
    . /etc/os-release
    case "${ID:-} ${ID_LIKE:-}" in
        *debian*|*ubuntu*) echo "debian" ;;
        *rhel*|*fedora*|*centos*|*rocky*|*almalinux*) echo "rhel" ;;
        *arch*) echo "arch" ;;
        *) fail "Unsupported OS: ID=${ID:-unknown}, ID_LIKE=${ID_LIKE:-unknown}." ;;
    esac
}

install_prerequisites() {
    # Note: Only pyTorrent runtime dependencies are installed; rTorrent is left untouched.
    local family="$1"
    case "${family}" in
        debian)
            export DEBIAN_FRONTEND=noninteractive
            apt-get update
            apt-get install -y --no-install-recommends ca-certificates curl git rsync sudo python3 python3-venv python3-dev python3-pip gcc pkg-config
            ;;
        rhel)
            local manager
            manager="$(command -v dnf || command -v yum || true)"
            [[ -n "${manager}" ]] || fail "dnf or yum is required."
            "${manager}" install -y ca-certificates curl git rsync sudo python3 python3-devel python3-pip gcc pkgconf-pkg-config
            ;;
        arch)
            command -v pacman >/dev/null 2>&1 || fail "pacman is required on Arch Linux."
            pacman -Sy --noconfirm --needed ca-certificates curl git rsync sudo python python-pip gcc pkgconf
            ;;
    esac
}

ask_configuration() {
    # Note: Interactive mode collects only pyTorrent-specific choices for an existing rTorrent host.
    prompt APP_USER "pyTorrent system user" "pytorrent"
    prompt APP_DIR "pyTorrent install directory" "/opt/pytorrent"
    prompt SERVICE_NAME "systemd service name" "pytorrent"
    prompt APP_HOST "Application bind host" "0.0.0.0"
    prompt APP_PORT "Application port (use a high port like 8090; ports below 1024 may be blocked or require extra privileges)" "8090"
    prompt PROFILE_NAME "pyTorrent profile name" "Local rTorrent"

    if [[ -n "${RTORRENT_SOCKET}" ]]; then
        INSTALL_SCGI_PROXY="${INSTALL_SCGI_PROXY:-ask}"
    fi
    if [[ "${INSTALL_SCGI_PROXY}" == "ask" ]]; then
        prompt INSTALL_SCGI_PROXY "Install rtorrent-scgi-proxy for Unix socket backend? yes/no" "no"
        INSTALL_SCGI_PROXY="$(normalize_yes_no "${INSTALL_SCGI_PROXY}")"
    fi
    if [[ "${INSTALL_SCGI_PROXY}" == "yes" ]]; then
        if [[ -n "${RTORRENT_SOCKET}" ]]; then
            RT_PROXY_TARGET_NETWORK="unix"
            RT_PROXY_TARGET_ADDRESS="${RTORRENT_SOCKET}"
        elif [[ -z "${RT_PROXY_TARGET_NETWORK_EXPLICIT}" ]]; then
            RT_PROXY_TARGET_NETWORK="unix"
        fi
        prompt RT_PROXY_TARGET_NETWORK "rTorrent SCGI backend: tcp or unix" "${RT_PROXY_TARGET_NETWORK}"
        if [[ "${RT_PROXY_TARGET_NETWORK}" == "unix" ]]; then
            prompt RT_PROXY_TARGET_ADDRESS "rTorrent Unix socket path" "${RTORRENT_SOCKET:-/run/rtorrent/rtorrent.sock}"
        elif [[ "${RT_PROXY_TARGET_NETWORK}" == "tcp" ]]; then
            prompt RT_PROXY_TARGET_ADDRESS "rTorrent SCGI TCP address" "${RT_PROXY_TARGET_ADDRESS}"
        else
            fail "Invalid SCGI proxy backend network: ${RT_PROXY_TARGET_NETWORK}"
        fi
        prompt RT_PROXY_ALLOW_NET "SCGI proxy allowed client network/IP/CIDR" "127.0.0.1"
        prompt RT_PROXY_LISTEN "SCGI proxy TCP listen address for pyTorrent" "127.0.0.1:5050"
        if [[ -z "${RT_PROXY_TOKEN}" ]]; then
            RT_PROXY_TOKEN="$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
)"
        fi
        SCGI_URL="scgi://${RT_PROXY_LISTEN}/proxy/${RT_PROXY_TOKEN}"
    else
        prompt SCGI_URL "rTorrent SCGI URL for pyTorrent profile" "${SCGI_URL}"
    fi

    if [[ "${AUTH_MODE}" == "ask" ]]; then
        prompt AUTH_MODE "Enable pyTorrent authentication? yes/no" "no"
        AUTH_MODE="$(bool_value "${AUTH_MODE}")"
    fi
    if [[ "${AUTH_MODE}" == "true" ]]; then
        prompt AUTH_PROVIDER "Authentication provider: local, proxy or tinyauth" "local"
        if [[ "${AUTH_PROVIDER}" == "local" ]]; then
            prompt AUTH_USER "Local auth username to create/update" "pytorrent"
            prompt_secret AUTH_PASSWORD "Password for local auth user" "pytorrent"
            prompt_secret ADMIN_PASSWORD "Optional new admin password; leave default to keep current/default" "${ADMIN_PASSWORD}"
        else
            log "External auth selected. Configure trusted proxy headers according to auth.md."
        fi
    fi

    prompt LOG_ENABLE "Enable file logging? yes/no" "true"
    LOG_ENABLE="$(bool_value "${LOG_ENABLE}")"
    if [[ "${LOG_ENABLE}" == "true" ]]; then
        prompt LOG_DIR "Log directory" "data/logs"
        prompt LOG_RETENTION_HOURS "Log retention in hours" "24"
    fi
    prompt LIBS_MODE "Frontend libraries mode: offline or online" "offline"

    if [[ "${REVERSE_PROXY}" == "ask" ]]; then
        prompt REVERSE_PROXY "Will pyTorrent run behind a reverse proxy? yes/no" "no"
        REVERSE_PROXY="$(normalize_yes_no "${REVERSE_PROXY}")"
    fi
    if [[ "${REVERSE_PROXY}" == "yes" ]]; then
        prompt PROXY_DOMAINS "Reverse proxy domains/origins, comma separated" "${PROXY_DOMAINS}"
        prompt CORS_ORIGINS "Extra CORS origins, comma separated" "${CORS_ORIGINS}"
        prompt LOCAL_ORIGINS "Extra local IP:port origins, comma separated" "${LOCAL_ORIGINS}"
    fi
}

ensure_app_user() {
    # Note: The service runs as a dedicated unprivileged user by default.
    if ! id -u "${APP_USER}" >/dev/null 2>&1; then
        local shell_path="/usr/sbin/nologin"
        [[ -x "${shell_path}" ]] || shell_path="/sbin/nologin"
        [[ -x "${shell_path}" ]] || shell_path="/usr/bin/nologin"
        useradd --system --create-home --home-dir "/var/lib/${APP_USER}" --shell "${shell_path}" "${APP_USER}"
    fi
}

copy_application() {
    # Note: Copy the current repository without development artifacts or a previous virtualenv.
    local project_dir
    project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
    mkdir -p "${APP_DIR}"
    rsync -a --delete --exclude '.git' --exclude 'venv' --exclude '.venv'  --exclude '__pycache__' --exclude '*.pyc' "${project_dir}/" "${APP_DIR}/"
    chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}" "/var/lib/${APP_USER}" || true
}

install_python_app() {
    # Note: A private virtualenv keeps pyTorrent dependencies isolated from system Python packages.
    cd "${APP_DIR}"
    "${PYTHON_BIN}" -m venv .venv
    .venv/bin/pip install --upgrade pip wheel
    .venv/bin/pip install -r requirements.txt
    mkdir -p data instance logs
    chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}"
}

upsert_env_value() {
    local key="$1" value="$2" file="${3:-${APP_DIR}/.env}"
    touch "${file}"
    if grep -qE "^${key}=" "${file}"; then
        sed -i "s|^${key}=.*|${key}=${value}|" "${file}"
    else
        printf '%s=%s\n' "${key}" "${value}" >> "${file}"
    fi
}

make_secret() {
    python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(48))
PY
}

normalize_origin() {
    local item="$1"
    item="${item# }"
    item="${item% }"
    [[ -n "${item}" ]] || return 0
    if [[ "${item}" == http://* || "${item}" == https://* ]]; then
        printf '%s\n' "${item%/}"
    else
        printf 'https://%s\n' "${item%/}"
    fi
}

local_ip_origin() {
    local ip
    ip="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
    [[ -n "${ip}" ]] && printf 'http://%s:%s\n' "${ip}" "${APP_PORT}"
}

build_origins() {
    # Note: Reverse proxy mode must allow public HTTPS origins and direct local IP:port origins for Socket.IO/API checks.
    {
        IFS=',' read -ra domains <<< "${PROXY_DOMAINS}"
        for item in "${domains[@]}"; do normalize_origin "${item}"; done
        IFS=',' read -ra extra <<< "${CORS_ORIGINS}"
        for item in "${extra[@]}"; do normalize_origin "${item}"; done
        printf 'http://localhost:%s\n' "${APP_PORT}"
        printf 'http://127.0.0.1:%s\n' "${APP_PORT}"
        local_ip_origin
        IFS=',' read -ra local_extra <<< "${LOCAL_ORIGINS}"
        for item in "${local_extra[@]}"; do normalize_origin "${item}"; done
    } | awk 'NF && !seen[$0]++' | paste -sd, -
}

write_env() {
    # Note: The installer preserves .env comments but overwrites selected runtime keys.
    cd "${APP_DIR}"
    if [[ ! -f .env && -f .env.example ]]; then
        cp .env.example .env
    fi
    upsert_env_value "PYTORRENT_SECRET_KEY" "$(make_secret)"
    upsert_env_value "PYTORRENT_HOST" "${APP_HOST}"
    upsert_env_value "PYTORRENT_PORT" "${APP_PORT}"
    upsert_env_value "PYTORRENT_LOG_ENABLE" "${LOG_ENABLE}"
    upsert_env_value "PYTORRENT_LOG_DIR" "${LOG_DIR}"
    upsert_env_value "PYTORRENT_LOG_RETENTION_HOURS" "${LOG_RETENTION_HOURS}"
    if [[ "${LOG_ENABLE}" == "true" ]]; then
        upsert_env_value "PYTORRENT_GUNICORN_ACCESS_LOG" "${LOG_DIR%/}/gunicorn-access.log"
        upsert_env_value "PYTORRENT_GUNICORN_ERROR_LOG" "${LOG_DIR%/}/gunicorn-error.log"
    else
        upsert_env_value "PYTORRENT_GUNICORN_ACCESS_LOG" "/dev/null"
        upsert_env_value "PYTORRENT_GUNICORN_ERROR_LOG" "-"
    fi
    if [[ "${LIBS_MODE}" == "offline" ]]; then
        upsert_env_value "PYTORRENT_USE_OFFLINE_LIBS" "true"
    elif [[ "${LIBS_MODE}" == "online" ]]; then
        upsert_env_value "PYTORRENT_USE_OFFLINE_LIBS" "false"
    else
        fail "Invalid --libs value: ${LIBS_MODE}"
    fi
    if [[ "${AUTH_MODE}" == "true" ]]; then
        upsert_env_value "PYTORRENT_AUTH_ENABLE" "true"
        upsert_env_value "PYTORRENT_AUTH_PROVIDER" "${AUTH_PROVIDER}"
        if [[ "${AUTH_PROVIDER}" == "proxy" || "${AUTH_PROVIDER}" == "tinyauth" ]]; then
            upsert_env_value "PYTORRENT_AUTH_PROXY_AUTO_CREATE" "true"
            upsert_env_value "PYTORRENT_AUTH_PROXY_AUTO_CREATE_ROLE" "admin"
            upsert_env_value "PYTORRENT_AUTH_PROXY_AUTO_CREATE_PERMISSION" "rw"
        fi
    else
        upsert_env_value "PYTORRENT_AUTH_ENABLE" "false"
    fi
    if [[ "${REVERSE_PROXY}" == "yes" ]]; then
        local origins
        origins="$(build_origins)"
        upsert_env_value "PYTORRENT_PROXY_FIX_ENABLE" "true"
        upsert_env_value "PYTORRENT_SESSION_COOKIE_SECURE" "true"
        upsert_env_value "PYTORRENT_SOCKETIO_CORS_ALLOWED_ORIGINS" "${origins}"
        upsert_env_value "PYTORRENT_API_ALLOWED_ORIGINS" "${origins}"
    else
        upsert_env_value "PYTORRENT_PROXY_FIX_ENABLE" "false"
        upsert_env_value "PYTORRENT_SESSION_COOKIE_SECURE" "false"
    fi
    if [[ "${LOG_ENABLE}" == "true" ]]; then
        if [[ "${LOG_DIR}" == /* ]]; then
            mkdir -p "${LOG_DIR}"
            chown -R "${APP_USER}:${APP_USER}" "${LOG_DIR}" || true
        else
            mkdir -p "${APP_DIR}/${LOG_DIR}"
            chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}/${LOG_DIR}" || true
        fi
    fi
    chown "${APP_USER}:${APP_USER}" .env || true
}

install_frontend_libs() {
    # Note: Offline mode downloads local JS/CSS assets during installation; online mode uses CDN links.
    if [[ "${LIBS_MODE}" == "offline" && -f "${APP_DIR}/scripts/download_frontend_libs.py" ]]; then
        sudo -u "${APP_USER}" "${APP_DIR}/.venv/bin/python" "${APP_DIR}/scripts/download_frontend_libs.py" || true
    fi
    if [[ -f "${APP_DIR}/scripts/download_geoip.sh" ]]; then
        sudo -u "${APP_USER}" bash "${APP_DIR}/scripts/download_geoip.sh" "${APP_DIR}/data/GeoLite2-City.mmdb" || true
    fi
}

configure_database() {
    # Note: Configure the initial database, local users and rTorrent profile without needing an API token.
    sudo -u "${APP_USER}" env \
        AUTH_MODE="${AUTH_MODE}" \
        AUTH_PROVIDER="${AUTH_PROVIDER}" \
        AUTH_USER="${AUTH_USER}" \
        AUTH_PASSWORD="${AUTH_PASSWORD}" \
        ADMIN_PASSWORD="${ADMIN_PASSWORD}" \
        PROFILE_NAME="${PROFILE_NAME}" \
        SCGI_URL="${SCGI_URL}" \
        SKIP_PROFILE="${SKIP_PROFILE}" \
        "${APP_DIR}/.venv/bin/python" - <<'PY'
import os
from pytorrent.db import connect, init_db, utcnow
from pytorrent.services.auth import password_hash

init_db()
now = utcnow()
auth_enabled = os.environ.get("AUTH_MODE") == "true"
auth_provider = os.environ.get("AUTH_PROVIDER", "local")
with connect() as conn:
    if auth_enabled and auth_provider == "local":
        username = os.environ.get("AUTH_USER", "pytorrent").strip() or "pytorrent"
        password = os.environ.get("AUTH_PASSWORD", "pytorrent")
        row = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
        if row:
            conn.execute(
                "UPDATE users SET password_hash=?, role='admin', is_active=1, updated_at=? WHERE username=?",
                (password_hash(password), now, username),
            )
        else:
            conn.execute(
                "INSERT INTO users(username,password_hash,role,is_active,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (username, password_hash(password), "admin", 1, now, now),
            )
        admin_password = os.environ.get("ADMIN_PASSWORD", "")
        if admin_password:
            conn.execute(
                "UPDATE users SET password_hash=?, role='admin', is_active=1, updated_at=? WHERE username='admin'",
                (password_hash(admin_password), now),
            )
    if os.environ.get("SKIP_PROFILE") != "1":
        profile_name = os.environ.get("PROFILE_NAME", "Local rTorrent")
        scgi_url = os.environ.get("SCGI_URL", "scgi://127.0.0.1:5000")
        existing = conn.execute(
            "SELECT id FROM rtorrent_profiles WHERE name=? OR scgi_url=? ORDER BY id LIMIT 1",
            (profile_name, scgi_url),
        ).fetchone()
        if existing:
            pid = int(existing["id"])
            conn.execute(
                "UPDATE rtorrent_profiles SET name=?, scgi_url=?, is_default=1, updated_at=? WHERE id=?",
                (profile_name, scgi_url, now, pid),
            )
        else:
            cur = conn.execute(
                """INSERT INTO rtorrent_profiles(user_id,name,scgi_url,is_default,timeout_seconds,max_parallel_jobs,light_parallel_jobs,light_job_timeout_seconds,heavy_job_timeout_seconds,pending_job_timeout_seconds,is_remote,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (1, profile_name, scgi_url, 1, 10, 5, 4, 300, 7200, 900, 0, now, now),
            )
            pid = int(cur.lastrowid)
        conn.execute("UPDATE rtorrent_profiles SET is_default=0 WHERE id<>?", (pid,))
        conn.execute(
            "UPDATE user_preferences SET active_rtorrent_id=?, updated_at=? WHERE user_id=1",
            (pid, now),
        )
print("Database initialized")
PY
}

write_systemd_service() {
    # Note: The systemd unit mirrors the repository service but uses installer-selected paths and user.
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
ExecStart=${APP_DIR}/.venv/bin/gunicorn -c ${APP_DIR}/gunicorn.conf.py --worker-class gthread --workers 1 --threads 32 --bind \${PYTORRENT_HOST}:\${PYTORRENT_PORT} wsgi:app
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
}


ensure_scgi_proxy_socket_access() {
    [[ "${RT_PROXY_TARGET_NETWORK}" == "unix" ]] || return 0
    if getent group "${RTORRENT_USER}" >/dev/null 2>&1; then
        usermod -a -G "${RTORRENT_USER}" "${RT_PROXY_USER}" || true
    fi
    if [[ -n "${RT_PROXY_TARGET_ADDRESS}" ]]; then
        local socket_dir
        socket_dir="$(dirname "${RT_PROXY_TARGET_ADDRESS}")"
        if [[ -d "${socket_dir}" && "${socket_dir}" == /run/* ]]; then
            chgrp "${RTORRENT_USER}" "${socket_dir}" 2>/dev/null || true
            chmod g+rx "${socket_dir}" 2>/dev/null || true
        fi
    fi
}

install_scgi_proxy() {
    # Note: The proxy exposes a TCP SCGI endpoint for pyTorrent when rTorrent listens on a Unix socket.
    [[ "${INSTALL_SCGI_PROXY}" == "yes" ]] || return 0
    if ! id -u "${RT_PROXY_USER}" >/dev/null 2>&1; then
        local shell_path="/usr/sbin/nologin"
        [[ -x "${shell_path}" ]] || shell_path="/sbin/nologin"
        [[ -x "${shell_path}" ]] || shell_path="/usr/bin/nologin"
        useradd --system --no-create-home --shell "${shell_path}" "${RT_PROXY_USER}"
    fi
    ensure_scgi_proxy_socket_access
    curl -fL "${RT_PROXY_BINARY_URL}" -o /usr/local/bin/rtorrent-scgi-proxy
    chmod 0755 /usr/local/bin/rtorrent-scgi-proxy
    cat > /etc/rtorrent-scgi-proxy.env <<ENV
LISTEN_ADDR=${RT_PROXY_LISTEN}
TOKEN=${RT_PROXY_TOKEN}
TARGET_NETWORK=${RT_PROXY_TARGET_NETWORK}
TARGET_ADDRESS=${RT_PROXY_TARGET_ADDRESS}
TARGET_URI=${RT_PROXY_TARGET_URI}
ALLOW_NET=${RT_PROXY_ALLOW_NET}
READ_TIMEOUT=15s
WRITE_TIMEOUT=30s
DIAL_TIMEOUT=5s
MAX_HEADER_BYTES=65536
MAX_CONTENT_BYTES=10485760
ENV
    chmod 0600 /etc/rtorrent-scgi-proxy.env
    chown root:root /etc/rtorrent-scgi-proxy.env
    local supplementary_groups=""
    if [[ "${RT_PROXY_TARGET_NETWORK}" == "unix" ]] && getent group "${RTORRENT_USER}" >/dev/null 2>&1; then
        supplementary_groups="SupplementaryGroups=${RTORRENT_USER}"
    fi
    local protect_home="yes"
    if [[ "${RT_PROXY_TARGET_NETWORK}" == "unix" && "${RT_PROXY_TARGET_ADDRESS}" == /home/* ]]; then
        protect_home="read-only"
    fi
    cat > /etc/systemd/system/rtorrent-scgi-proxy.service <<SERVICE
[Unit]
Description=rTorrent SCGI proxy
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${RT_PROXY_USER}
Group=${RT_PROXY_USER}
${supplementary_groups}
EnvironmentFile=/etc/rtorrent-scgi-proxy.env
ExecStart=/usr/local/bin/rtorrent-scgi-proxy
Restart=on-failure
RestartSec=2

NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=${protect_home}
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
MemoryDenyWriteExecute=yes
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
LockPersonality=yes

[Install]
WantedBy=multi-user.target
SERVICE
    systemctl daemon-reload
    systemctl enable --now rtorrent-scgi-proxy
}

print_summary() {
    # Note: Print only actionable installation facts, not release notes.
    local base_url="http://127.0.0.1:${APP_PORT}"
    log "Installed in ${APP_DIR}"
    log "Service: ${SERVICE_NAME}"
    log "Local URL: ${base_url}"
    log "rTorrent profile SCGI URL: ${SCGI_URL}"
    if [[ "${AUTH_MODE}" == "true" && "${AUTH_PROVIDER}" == "local" ]]; then
        log "Local auth user: ${AUTH_USER}"
        if [[ "${AUTH_PASSWORD}" == "pytorrent" ]]; then
            log "Default local password is still set. Change it after first login."
        fi
    elif [[ "${AUTH_MODE}" == "true" ]]; then
        log "External auth provider: ${AUTH_PROVIDER}. Finish proxy setup according to auth.md."
    fi
    if [[ "${REVERSE_PROXY}" == "yes" ]]; then
        log "Reverse proxy CORS/API origins: $(build_origins)"
    fi
}

main() {
    parse_args "$@"
    require_root
    ask_configuration
    local family
    family="$(detect_os_family)"
    install_prerequisites "${family}"
    install_scgi_proxy
    ensure_app_user
    copy_application
    install_python_app
    write_env
    install_frontend_libs
    configure_database
    write_systemd_service
    systemctl status "${SERVICE_NAME}" --no-pager --lines=20 || true
    print_summary
}

main "$@"
