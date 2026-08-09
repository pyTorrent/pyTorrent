from __future__ import annotations

import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}

def _env_str(name: str, default: str = "", *, strip: bool = True, lower: bool = False) -> str:
    value = os.getenv(name, default)
    if strip:
        value = value.strip()
    return value.lower() if lower else value

def _env_bool(name: str, default: bool = False, *, extra_true: set[str] | None = None, extra_false: set[str] | None = None) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in (_TRUE_VALUES | (extra_true or set())):
        return True
    if normalized in (_FALSE_VALUES | (extra_false or set())):
        return False
    return default

def _env_int(name: str, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value

def _env_float(name: str, default: float, minimum: float | None = None, maximum: float | None = None) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value

def _env_choice(name: str, default: str, choices: set[str], *, aliases: dict[str, str] | None = None) -> str:
    value = _env_str(name, default, lower=True)
    if aliases:
        value = aliases.get(value, value)
    return value if value in choices else default

def _env_csv(name: str, *, strip_trailing_slash: bool = True) -> list[str]:
    items = [item.strip() for item in _env_str(name).split(",") if item.strip()]
    return [item.rstrip("/") for item in items] if strip_trailing_slash else items

def _env_path(name: str, default: str | Path, *, base_dir: Path = BASE_DIR) -> Path:
    path = Path(_env_str(name, str(default)) or str(default))
    return path if path.is_absolute() else base_dir / path

_SECRET_KEY_ENV = os.getenv("PYTORRENT_SECRET_KEY")
SECRET_KEY = _SECRET_KEY_ENV or "dev-change-me"
DB_PATH = _env_path("PYTORRENT_DB_PATH", BASE_DIR / "data" / "pytorrent.sqlite3")
HOST = _env_str("PYTORRENT_HOST", "0.0.0.0") or "0.0.0.0"
PORT = _env_int("PYTORRENT_PORT", 8090, 1, 65535)
DEBUG = _env_bool("PYTORRENT_DEBUG", False)
USE_OFFLINE_LIBS = _env_bool("PYTORRENT_USE_OFFLINE_LIBS", False)
STATIC_CACHE_MAX_AGE = _env_int("STATIC_CACHE_MAX_AGE", 0, 0)
STATIC_CACHE_IMMUTABLE = _env_bool("STATIC_CACHE_IMMUTABLE", False)
AUTH_ENABLE = _env_bool("PYTORRENT_AUTH_ENABLE", False)
AUTH_PROVIDER = _env_choice("PYTORRENT_AUTH_PROVIDER", "local", {"local", "proxy", "tinyauth"})
AUTH_PROXY_USER_HEADER = _env_str("PYTORRENT_AUTH_PROXY_USER_HEADER", "Remote-User") or "Remote-User"
AUTH_PROXY_AUTO_CREATE = _env_bool("PYTORRENT_AUTH_PROXY_AUTO_CREATE", False)
AUTH_PROXY_AUTO_CREATE_ROLE = _env_choice("PYTORRENT_AUTH_PROXY_AUTO_CREATE_ROLE", "user", {"user", "admin"})
AUTH_PROXY_AUTO_CREATE_PERMISSION = _env_choice("PYTORRENT_AUTH_PROXY_AUTO_CREATE_PERMISSION", "ro", {"none", "ro", "full"}, aliases={"rw": "full"})

if AUTH_ENABLE and (not _SECRET_KEY_ENV or SECRET_KEY == "dev-change-me"):
    _secret_file = BASE_DIR / "data" / ".session_secret"
    _secret_file.parent.mkdir(parents=True, exist_ok=True)
    if _secret_file.exists():
        SECRET_KEY = _secret_file.read_text(encoding="utf-8").strip()
    if not SECRET_KEY or SECRET_KEY == "dev-change-me":
        SECRET_KEY = secrets.token_urlsafe(48)
        _secret_file.write_text(SECRET_KEY, encoding="utf-8")

SESSION_COOKIE_SECURE = _env_bool("PYTORRENT_SESSION_COOKIE_SECURE", False)
ALLOW_UNSAFE_WERKZEUG = _env_bool("PYTORRENT_ALLOW_UNSAFE_WERKZEUG", DEBUG)
POLL_INTERVAL = _env_float("PYTORRENT_POLL_INTERVAL", 0.5, 0.0)
MIN_POLL_INTERVAL_SECONDS = _env_float("MIN_POLL_INTERVAL_SECONDS", 0.5, 0.0)
WORKERS = _env_int("PYTORRENT_WORKERS", 16, 1)
GEOIP_DB = _env_path("PYTORRENT_GEOIP_DB", BASE_DIR / "data" / "GeoLite2-City.mmdb")
PYTORRENT_TMP_DIR = _env_path("PYTORRENT_TMP_DIR", "/tmp")
REMOTE_READ_CHUNK_BYTES = _env_int("PYTORRENT_REMOTE_READ_CHUNK_BYTES", 1048576, 65536)
PYTORRENT_SCGI_MAX_INFLIGHT = _env_int("PYTORRENT_SCGI_MAX_INFLIGHT", 2, 1, 8)
PATH_BROWSE_TIMEOUT_SECONDS = _env_int("PYTORRENT_PATH_BROWSE_TIMEOUT_SECONDS", 12, 2)
PROXY_FIX_ENABLE = _env_bool("PYTORRENT_PROXY_FIX_ENABLE", False)
PROXY_FIX_X_FOR = _env_int("PYTORRENT_PROXY_FIX_X_FOR", 1, 0)
PROXY_FIX_X_PROTO = _env_int("PYTORRENT_PROXY_FIX_X_PROTO", 1, 0)
PROXY_FIX_X_HOST = _env_int("PYTORRENT_PROXY_FIX_X_HOST", 1, 0)
PROXY_FIX_X_PORT = _env_int("PYTORRENT_PROXY_FIX_X_PORT", 1, 0)
PROXY_FIX_X_PREFIX = _env_int("PYTORRENT_PROXY_FIX_X_PREFIX", 1, 0)
_SOCKETIO_CORS = _env_csv("PYTORRENT_SOCKETIO_CORS_ALLOWED_ORIGINS")
SOCKETIO_CORS_ALLOWED_ORIGINS = _SOCKETIO_CORS or None
_API_ALLOWED_ORIGINS = _env_csv("PYTORRENT_API_ALLOWED_ORIGINS")
API_ALLOWED_ORIGINS = _API_ALLOWED_ORIGINS or _env_csv("PYTORRENT_SOCKETIO_CORS_ALLOWED_ORIGINS")
AUTH_BYPASS_HOSTS = {item.lower() for item in _env_csv("PYTORRENT_AUTH_BYPASS_HOSTS", strip_trailing_slash=False)}
AUTH_BYPASS_USER = _env_str("PYTORRENT_AUTH_BYPASS_USER", "admin") or "admin"
METRICS_ENABLE = _env_bool("PYTORRENT_METRICS_ENABLE", False, extra_true={"enable", "enabled"})
_METRICS_PATH = _env_str("PYTORRENT_METRICS_PATH", "/metrics") or "/metrics"
METRICS_PATH = _METRICS_PATH if _METRICS_PATH.startswith("/") else f"/{_METRICS_PATH}"
METRICS_INSTANCE = _env_str("PYTORRENT_METRICS_INSTANCE", os.getenv("HOSTNAME", "pytorrent")) or "pytorrent"
METRICS_ALLOWED_IPS = _env_csv("PYTORRENT_METRICS_ALLOWED_IPS", strip_trailing_slash=False)
METRICS_BASIC_USER = _env_str("PYTORRENT_METRICS_BASIC_USER")
METRICS_BASIC_PASSWORD = _env_str("PYTORRENT_METRICS_BASIC_PASSWORD", strip=False)
TRAFFIC_HISTORY_RETENTION_DAYS = _env_int("PYTORRENT_TRAFFIC_HISTORY_RETENTION_DAYS", 90, 1)
JOBS_RETENTION_DAYS = _env_int("PYTORRENT_JOBS_RETENTION_DAYS", 30, 1)
SMART_QUEUE_HISTORY_RETENTION_DAYS = _env_int("PYTORRENT_SMART_QUEUE_HISTORY_RETENTION_DAYS", 30, 1)
LOG_RETENTION_DAYS = _env_int("PYTORRENT_LOG_RETENTION_DAYS", 1, 1)
LOG_RETENTION_HOURS = _env_int("PYTORRENT_LOG_RETENTION_HOURS", 24, 1)
LOG_ENABLE = _env_bool("PYTORRENT_LOG_ENABLE", True)
LOG_DIR = _env_path("PYTORRENT_LOG_DIR", "data/logs")
SMART_QUEUE_LABEL = _env_str("PYTORRENT_SMART_QUEUE_LABEL") or _env_str("PYTORRENT_SMART_QUEUE_L.ABEL") or "Smart Queue Stopped"
SMART_QUEUE_STALLED_LABEL = _env_str("PYTORRENT_SMART_QUEUE_STALLED_LABEL", "Stalled") or "Stalled"