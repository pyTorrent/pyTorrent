from __future__ import annotations
import os
import secrets
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


_SECRET_KEY_ENV = os.getenv("PYTORRENT_SECRET_KEY")
SECRET_KEY = _SECRET_KEY_ENV or "dev-change-me"
DB_PATH = Path(os.getenv("PYTORRENT_DB_PATH", str(BASE_DIR / "data" / "pytorrent.sqlite3")))
if not DB_PATH.is_absolute():
    DB_PATH = BASE_DIR / DB_PATH

HOST = os.getenv("PYTORRENT_HOST", "0.0.0.0")
PORT = int(os.getenv("PYTORRENT_PORT", "8090"))
DEBUG = _env_bool("PYTORRENT_DEBUG", False)
# Note: Offline mode forces local JS/CSS and disables the CDN dependency.
USE_OFFLINE_LIBS = _env_bool("PYTORRENT_USE_OFFLINE_LIBS", False)
# cache control on static files
STATIC_CACHE_MAX_AGE = int(os.getenv("STATIC_CACHE_MAX_AGE", "0"))
STATIC_CACHE_IMMUTABLE = os.getenv("STATIC_CACHE_IMMUTABLE", "false").lower() in {"1", "true", "yes"}
# Note: Optional authentication remains disabled unless explicitly enabled in .env.
AUTH_ENABLE = _env_bool("PYTORRENT_AUTH_ENABLE", False)
AUTH_PROVIDER = os.getenv("PYTORRENT_AUTH_PROVIDER", "local").strip().lower() or "local"
if AUTH_PROVIDER not in {"local", "proxy", "tinyauth"}:
    AUTH_PROVIDER = "local"

# Note: External auth reads only one identity value from the trusted reverse proxy.
AUTH_PROXY_USER_HEADER = os.getenv("PYTORRENT_AUTH_PROXY_USER_HEADER", "Remote-User").strip() or "Remote-User"
AUTH_PROXY_AUTO_CREATE = _env_bool("PYTORRENT_AUTH_PROXY_AUTO_CREATE", False)
AUTH_PROXY_AUTO_CREATE_ROLE = os.getenv("PYTORRENT_AUTH_PROXY_AUTO_CREATE_ROLE", "user").strip().lower()
AUTH_PROXY_AUTO_CREATE_PERMISSION = os.getenv("PYTORRENT_AUTH_PROXY_AUTO_CREATE_PERMISSION", "ro").strip().lower()
if AUTH_PROXY_AUTO_CREATE_ROLE not in {"user", "admin"}:
    AUTH_PROXY_AUTO_CREATE_ROLE = "user"
# Note: Keep rw as an operator-friendly alias while storing full internally.
if AUTH_PROXY_AUTO_CREATE_PERMISSION == "rw":
    AUTH_PROXY_AUTO_CREATE_PERMISSION = "full"
if AUTH_PROXY_AUTO_CREATE_PERMISSION not in {"none", "ro", "full"}:
    AUTH_PROXY_AUTO_CREATE_PERMISSION = "ro"
if AUTH_ENABLE and (not _SECRET_KEY_ENV or SECRET_KEY == "dev-change-me"):
    # Note: Auth mode cannot use Flask's development secret; persist a local random session key instead.
    _secret_file = BASE_DIR / "data" / ".session_secret"
    _secret_file.parent.mkdir(parents=True, exist_ok=True)
    if _secret_file.exists():
        SECRET_KEY = _secret_file.read_text(encoding="utf-8").strip()
    if not SECRET_KEY or SECRET_KEY == "dev-change-me":
        SECRET_KEY = secrets.token_urlsafe(48)
        _secret_file.write_text(SECRET_KEY, encoding="utf-8")
SESSION_COOKIE_SECURE = _env_bool("PYTORRENT_SESSION_COOKIE_SECURE", False)
# Note: Keep Werkzeug opt-in only for explicit local/dev use, never by default in services.
ALLOW_UNSAFE_WERKZEUG = _env_bool("PYTORRENT_ALLOW_UNSAFE_WERKZEUG", DEBUG)
POLL_INTERVAL = float(os.getenv("PYTORRENT_POLL_INTERVAL", "0.5"))
MIN_POLL_INTERVAL_SECONDS = float(os.getenv("MIN_POLL_INTERVAL_SECONDS", "0.5"))
WORKERS = int(os.getenv("PYTORRENT_WORKERS", "16"))
GEOIP_DB = Path(os.getenv("PYTORRENT_GEOIP_DB", str(BASE_DIR / "data" / "GeoLite2-City.mmdb")))
if not GEOIP_DB.is_absolute():
    GEOIP_DB = BASE_DIR / GEOIP_DB



def _env_int(name: str, default: int, minimum: int = 0) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


PYTORRENT_TMP_DIR = Path(os.getenv("PYTORRENT_TMP_DIR", "/tmp"))
if not PYTORRENT_TMP_DIR.is_absolute():
    PYTORRENT_TMP_DIR = BASE_DIR / PYTORRENT_TMP_DIR
REMOTE_READ_CHUNK_BYTES = _env_int("PYTORRENT_REMOTE_READ_CHUNK_BYTES", 1048576, 65536)


PROXY_FIX_ENABLE = _env_bool("PYTORRENT_PROXY_FIX_ENABLE", False)
PROXY_FIX_X_FOR = _env_int("PYTORRENT_PROXY_FIX_X_FOR", 1, 0)
PROXY_FIX_X_PROTO = _env_int("PYTORRENT_PROXY_FIX_X_PROTO", 1, 0)
PROXY_FIX_X_HOST = _env_int("PYTORRENT_PROXY_FIX_X_HOST", 1, 0)
PROXY_FIX_X_PORT = _env_int("PYTORRENT_PROXY_FIX_X_PORT", 1, 0)
PROXY_FIX_X_PREFIX = _env_int("PYTORRENT_PROXY_FIX_X_PREFIX", 1, 0)

def _env_csv(name: str) -> list[str]:
    return [item.strip().rstrip("/") for item in os.getenv(name, "").split(",") if item.strip()]

_SOCKETIO_CORS = os.getenv("PYTORRENT_SOCKETIO_CORS_ALLOWED_ORIGINS", "").strip()
SOCKETIO_CORS_ALLOWED_ORIGINS = None if not _SOCKETIO_CORS else [item.strip() for item in _SOCKETIO_CORS.split(",") if item.strip()]
# Note: API origin checks are separate from Socket.IO CORS. When unset, reuse the Socket.IO allowlist for operator-friendly reverse proxy setups.
_API_ALLOWED_ORIGINS = _env_csv("PYTORRENT_API_ALLOWED_ORIGINS")
API_ALLOWED_ORIGINS = _API_ALLOWED_ORIGINS or _env_csv("PYTORRENT_SOCKETIO_CORS_ALLOWED_ORIGINS")
# Note: Optional auth bypass for trusted direct-IP/local access. Values can be hosts or host:port pairs.
AUTH_BYPASS_HOSTS = {item.lower() for item in _env_csv("PYTORRENT_AUTH_BYPASS_HOSTS")}
# Note: Trusted auth-bypass requests act as this existing active user.
AUTH_BYPASS_USER = os.getenv("PYTORRENT_AUTH_BYPASS_USER", "admin").strip() or "admin"

TRAFFIC_HISTORY_RETENTION_DAYS = _env_int("PYTORRENT_TRAFFIC_HISTORY_RETENTION_DAYS", 90, 1)
JOBS_RETENTION_DAYS = _env_int("PYTORRENT_JOBS_RETENTION_DAYS", 30, 1)
SMART_QUEUE_HISTORY_RETENTION_DAYS = _env_int("PYTORRENT_SMART_QUEUE_HISTORY_RETENTION_DAYS", 30, 1)
LOG_RETENTION_DAYS = _env_int("PYTORRENT_LOG_RETENTION_DAYS", 1, 1)
LOG_RETENTION_HOURS = _env_int("PYTORRENT_LOG_RETENTION_HOURS", 24, 1)
LOG_ENABLE = _env_bool("PYTORRENT_LOG_ENABLE", True)
LOG_DIR = Path(os.getenv("PYTORRENT_LOG_DIR", "data/logs"))
if not LOG_DIR.is_absolute():
    LOG_DIR = BASE_DIR / LOG_DIR
SMART_QUEUE_LABEL = os.getenv("PYTORRENT_SMART_QUEUE_LABEL", os.getenv("PYTORRENT_SMART_QUEUE_L.ABEL", "Smart Queue Stopped"))
SMART_QUEUE_STALLED_LABEL = os.getenv("PYTORRENT_SMART_QUEUE_STALLED_LABEL", "Stalled")
