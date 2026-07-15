from __future__ import annotations
from functools import wraps
from typing import Any
import secrets
from urllib.parse import urlparse
from flask import abort, g, has_request_context, jsonify, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from ..config import (
    AUTH_ENABLE,
    AUTH_PROVIDER,
    AUTH_PROXY_AUTO_CREATE,
    AUTH_PROXY_AUTO_CREATE_PERMISSION,
    AUTH_PROXY_AUTO_CREATE_ROLE,
    AUTH_PROXY_USER_HEADER,
    API_ALLOWED_ORIGINS,
    AUTH_BYPASS_HOSTS,
    AUTH_BYPASS_USER,
)
from ..db import connect, default_user_id, utcnow

PUBLIC_ENDPOINTS = {"main.login", "main.logout", "api.auth_login", "api.auth_me", "static"}
RTORRENT_WRITE_PREFIXES = (
    "/api/torrents/",
    "/api/speed/limits",
    "/api/labels",
    "/api/ratio-groups",
    "/api/rss",
    "/api/smart-queue",
    "/api/automations",
    "/api/download-planner",
    "/api/poller/settings",
    "/api/operation-logs",
    "/api/jobs",
    "/api/cleanup",
)
RTORRENT_CONFIG_PREFIXES = ("/api/rtorrent-config",)
ADMIN_PREFIXES = ("/api/auth/users", "/api/profiles")
PROFILE_READ_PREFIXES = (
    "/api/torrents",
    "/api/torrent-stats",
    "/api/system/status",
    "/api/app/status",
    "/api/port-check",
    "/api/path",
    "/api/labels",
    "/api/ratio-groups",
    "/api/rss",
    "/api/rtorrent-config",
    "/api/smart-queue",
    "/api/traffic/history",
    "/api/automations",
    "/api/download-planner",
    "/api/poller/settings",
    "/api/operation-logs",
)


def enabled() -> bool:
    return bool(AUTH_ENABLE)


def provider() -> str:
    return AUTH_PROVIDER if AUTH_PROVIDER in {"local", "proxy", "tinyauth"} else "local"


def uses_external_provider() -> bool:
    return enabled() and provider() in {"proxy", "tinyauth"}


def external_auth_summary() -> dict[str, Any]:
    # Note: Exposes safe auth-mode facts for the Users panel without leaking secrets.
    return {
        "enabled": enabled(),
        "provider": provider(),
        "external": uses_external_provider(),
        "auto_create": bool(AUTH_PROXY_AUTO_CREATE) if uses_external_provider() else False,
        "auto_create_role": AUTH_PROXY_AUTO_CREATE_ROLE,
        "auto_create_permission": AUTH_PROXY_AUTO_CREATE_PERMISSION,
        "bypass_enabled": bool(AUTH_BYPASS_HOSTS),
        "bypass_hosts": sorted(AUTH_BYPASS_HOSTS),
        "bypass_user": AUTH_BYPASS_USER,
        "password_editable": not uses_external_provider(),
    }


def password_hash(password: str) -> str:
    return generate_password_hash(password or "")


def _host_matches_bypass(host: str) -> bool:
    clean = str(host or "").strip().lower()
    if not clean:
        return False
    return clean in AUTH_BYPASS_HOSTS or clean.split(":", 1)[0] in AUTH_BYPASS_HOSTS


def auth_bypassed_request() -> bool:
    if not enabled() or not AUTH_BYPASS_HOSTS or not has_request_context():
        return False
    return _host_matches_bypass(request.host)



def bypass_user_id() -> int:
    """Return the configured active user id used for trusted auth-bypass requests."""
    username = str(AUTH_BYPASS_USER or "admin").strip() or "admin"
    with connect() as conn:
        row = conn.execute("SELECT id FROM users WHERE username=? AND is_active=1", (username,)).fetchone()
        if row:
            return int(row["id"])
        row = conn.execute("SELECT id FROM users WHERE username='admin' AND is_active=1").fetchone()
        if row:
            return int(row["id"])
        row = conn.execute("SELECT id FROM users WHERE id=? AND is_active=1", (default_user_id(),)).fetchone()
        return int(row["id"]) if row else 0

def current_user_id() -> int:
    if not enabled():
        return default_user_id()
    if not has_request_context():
        return 0
    if auth_bypassed_request():
        return bypass_user_id()
    api_user_id = getattr(g, "api_user_id", None)
    if api_user_id:
        return int(api_user_id)
    external_user_id = getattr(g, "external_user_id", None)
    if external_user_id:
        return int(external_user_id)
    try:
        return int(session.get("user_id") or 0)
    except Exception:
        return 0


def current_user() -> dict[str, Any] | None:
    uid = current_user_id()
    if not uid:
        return None
    with connect() as conn:
        return conn.execute(
            "SELECT id, username, email, display_name, external_auth_provider, external_subject, role, is_active, created_at, updated_at FROM users WHERE id=?",
            (uid,),
        ).fetchone()


def is_admin(user: dict[str, Any] | None = None) -> bool:
    if not enabled():
        return True
    user = user or current_user()
    return bool(user and user.get("role") == "admin" and int(user.get("is_active") or 0))


def _permissions(user_id: int | None = None) -> list[dict[str, Any]]:
    if not enabled():
        return [{"profile_id": 0, "access_level": "full"}]
    uid = user_id or current_user_id()
    if not uid:
        return []
    with connect() as conn:
        return conn.execute(
            "SELECT profile_id, access_level FROM user_profile_permissions WHERE user_id=?",
            (uid,),
        ).fetchall()


def can_access_profile(profile_id: int | None, user_id: int | None = None) -> bool:
    if not enabled():
        return True
    uid = user_id or current_user_id()
    if not uid:
        return False
    with connect() as conn:
        user = conn.execute("SELECT role, is_active FROM users WHERE id=?", (uid,)).fetchone()
        if not user or not int(user.get("is_active") or 0):
            return False
        if user.get("role") == "admin":
            return True
        pid = int(profile_id or 0)
        row = conn.execute(
            "SELECT 1 FROM user_profile_permissions WHERE user_id=? AND (profile_id=0 OR profile_id=?) LIMIT 1",
            (uid, pid),
        ).fetchone()
        return bool(row)


def can_write_profile(profile_id: int | None, user_id: int | None = None) -> bool:
    if not enabled():
        return True
    uid = user_id or current_user_id()
    if not uid:
        return False
    with connect() as conn:
        user = conn.execute("SELECT role, is_active FROM users WHERE id=?", (uid,)).fetchone()
        if not user or not int(user.get("is_active") or 0):
            return False
        if user.get("role") == "admin":
            return True
        pid = int(profile_id or 0)
        row = conn.execute(
            "SELECT access_level FROM user_profile_permissions WHERE user_id=? AND (profile_id=0 OR profile_id=?) ORDER BY profile_id DESC LIMIT 1",
            (uid, pid),
        ).fetchone()
        return bool(row and row.get("access_level") == "full")


def visible_profile_ids(user_id: int | None = None) -> set[int] | None:
    if not enabled():
        return None
    uid = user_id or current_user_id()
    if not uid:
        return set()
    with connect() as conn:
        user = conn.execute("SELECT role, is_active FROM users WHERE id=?", (uid,)).fetchone()
        if not user or not int(user.get("is_active") or 0):
            return set()
        if user.get("role") == "admin":
            return None
        rows = conn.execute("SELECT profile_id FROM user_profile_permissions WHERE user_id=?", (uid,)).fetchall()
    if any(int(row.get("profile_id") or 0) == 0 for row in rows):
        return None
    return {int(row.get("profile_id") or 0) for row in rows}



def _origin_key(value: str) -> str:
    parsed = urlparse(str(value or "").strip())
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def _request_origin() -> str:
    return _origin_key(f"{request.scheme}://{request.host}")


def same_origin_request() -> bool:
    """Return False only when an unsafe API request clearly comes from an untrusted origin."""
    origin = request.headers.get("Origin") or request.headers.get("Referer")
    if not origin:
        return True
    try:
        source_origin = _origin_key(origin)
        if not source_origin:
            return False
        if source_origin == _request_origin():
            return True
        return source_origin in set(API_ALLOWED_ORIGINS)
    except Exception:
        return False


def writable_profile_ids(user_id: int | None = None) -> set[int] | None:
    if not enabled():
        return None
    uid = user_id or current_user_id()
    if not uid:
        return set()
    with connect() as conn:
        user = conn.execute("SELECT role, is_active FROM users WHERE id=?", (uid,)).fetchone()
        if not user or not int(user.get("is_active") or 0):
            return set()
        if user.get("role") == "admin":
            return None
        rows = conn.execute("SELECT profile_id FROM user_profile_permissions WHERE user_id=? AND access_level='full'", (uid,)).fetchall()
    if any(int(row.get("profile_id") or 0) == 0 for row in rows):
        return None
    return {int(row.get("profile_id") or 0) for row in rows}

def require_admin() -> None:
    if enabled() and not is_admin():
        abort(403)


def require_profile_read(profile_id: int | None) -> None:
    if enabled() and not can_access_profile(profile_id):
        abort(403)


def require_profile_write(profile_id: int | None) -> None:
    if enabled() and not can_write_profile(profile_id):
        abort(403)


def login_user(username: str, password: str) -> dict[str, Any] | None:
    if not enabled():
        return {"id": default_user_id(), "username": "default", "role": "admin", "is_active": 1}
    if uses_external_provider():
        return None
    with connect() as conn:
        user = conn.execute("SELECT * FROM users WHERE username=?", (username.strip(),)).fetchone()
    if not user or not int(user.get("is_active") or 0):
        return None
    if not user.get("password_hash") or not check_password_hash(user.get("password_hash"), password or ""):
        return None
    session.clear()
    session["user_id"] = int(user["id"])
    session["username"] = user["username"]
    session["role"] = user.get("role") or "user"
    return current_user()




def _clean_header_value(name: str) -> str:
    if not name:
        return ""
    value = request.headers.get(name) or request.headers.get(name.lower()) or request.headers.get(name.upper()) or ""
    return str(value).strip()


def _safe_username(value: str, fallback: str = "external-user") -> str:
    raw = str(value or "").strip()
    if "@" in raw:
        raw = raw.split("@", 1)[0]
    clean = "".join(ch for ch in raw if ch.isalnum() or ch in {".", "_", "-"}).strip("._-")
    return (clean or fallback)[:80]


def _external_identity_from_headers() -> dict[str, str] | None:
    # Note: Tinyauth and generic proxy auth use a single trusted username header.
    username = _clean_header_value(AUTH_PROXY_USER_HEADER)
    if not username:
        return None
    safe_username = _safe_username(username)
    return {
        "provider": provider(),
        "username": safe_username,
        "subject": safe_username,
    }


def _grant_default_external_permissions(conn, user_id: int, now: str) -> None:
    # Note: Admins can see and write all profiles through role-based access.
    if AUTH_PROXY_AUTO_CREATE_PERMISSION == "none" or AUTH_PROXY_AUTO_CREATE_ROLE == "admin":
        return
    conn.execute(
        "INSERT OR IGNORE INTO user_profile_permissions(user_id,profile_id,access_level,created_at,updated_at) VALUES(?,?,?,?,?)",
        (user_id, 0, AUTH_PROXY_AUTO_CREATE_PERMISSION, now, now),
    )


def _sync_external_auto_created_user(conn, user: dict[str, Any], now: str) -> None:
    # Note: Passwordless external users follow the external auto-create defaults on login.
    if not AUTH_PROXY_AUTO_CREATE or user.get("password_hash"):
        return
    if user.get("external_auth_provider") and user.get("external_auth_provider") != provider():
        return
    user_id = int(user["id"])
    conn.execute("UPDATE users SET role=?, updated_at=? WHERE id=?", (AUTH_PROXY_AUTO_CREATE_ROLE, now, user_id))
    if AUTH_PROXY_AUTO_CREATE_ROLE == "admin" or AUTH_PROXY_AUTO_CREATE_PERMISSION == "none":
        conn.execute("DELETE FROM user_profile_permissions WHERE user_id=?", (user_id,))
        return
    conn.execute(
        "INSERT OR REPLACE INTO user_profile_permissions(user_id,profile_id,access_level,created_at,updated_at) VALUES(?,?,?,?,?)",
        (user_id, 0, AUTH_PROXY_AUTO_CREATE_PERMISSION, now, now),
    )


def authenticate_external_user() -> dict[str, Any] | None:
    if not uses_external_provider():
        return None
    identity = _external_identity_from_headers()
    if not identity:
        return None
    now = utcnow()
    with connect() as conn:
        user = None
        if identity["subject"]:
            user = conn.execute(
                "SELECT * FROM users WHERE external_auth_provider=? AND external_subject=?",
                (identity["provider"], identity["subject"]),
            ).fetchone()
        if not user:
            user = conn.execute("SELECT * FROM users WHERE username=?", (identity["username"],)).fetchone()
        if not user:
            if not AUTH_PROXY_AUTO_CREATE:
                return None
            cur = conn.execute(
                """INSERT INTO users(username,password_hash,email,display_name,external_auth_provider,external_subject,role,is_active,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    identity["username"],
                    None,
                    None,
                    None,
                    identity["provider"],
                    identity["subject"] or identity["username"],
                    AUTH_PROXY_AUTO_CREATE_ROLE,
                    1,
                    now,
                    now,
                ),
            )
            user_id = int(cur.lastrowid)
            _grant_default_external_permissions(conn, user_id, now)
            user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        else:
            user_id = int(user["id"])
            conn.execute(
                """UPDATE users
                   SET external_auth_provider=?,
                       external_subject=COALESCE(NULLIF(?, ''), external_subject),
                       updated_at=?
                   WHERE id=?""",
                (identity["provider"], identity["subject"], now, user_id),
            )
            user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if user:
                _sync_external_auto_created_user(conn, user, now)
                user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not user or not int(user.get("is_active") or 0):
        return None
    g.external_user_id = int(user["id"])
    session["user_id"] = int(user["id"])
    session["username"] = user.get("username")
    session["role"] = user.get("role") or "user"
    return _public_user(user)


def ensure_request_user() -> int:
    # Note: Socket.IO events do not go through Flask before_request like normal REST calls,
    # so external proxy auth must be resolved explicitly during the Socket.IO handshake/events.
    if not enabled():
        return default_user_id()
    if auth_bypassed_request():
        return bypass_user_id()
    uid = current_user_id()
    if uid:
        return uid
    if uses_external_provider():
        authenticate_external_user()
    return current_user_id()


def logout_user() -> None:
    session.clear()


def ensure_admin_user() -> None:
    if not enabled():
        return
    now = utcnow()
    with connect() as conn:
        row = conn.execute("SELECT id FROM users WHERE username='admin'").fetchone()
        if not row:
            conn.execute(
                "INSERT INTO users(username,password_hash,role,is_active,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                ("admin", password_hash("admin"), "admin", 1, now, now),
            )
        else:
            conn.execute("UPDATE users SET role='admin', is_active=1, updated_at=? WHERE username='admin'", (now,))


def list_users() -> list[dict[str, Any]]:
    require_admin()
    with connect() as conn:
        users = conn.execute(
            "SELECT id, username, email, display_name, external_auth_provider, external_subject, role, is_active, created_at, updated_at FROM users ORDER BY username COLLATE NOCASE"
        ).fetchall()
        perms = conn.execute(
            "SELECT user_id, profile_id, access_level FROM user_profile_permissions ORDER BY user_id, profile_id"
        ).fetchall()
        token_counts = conn.execute(
            "SELECT user_id, COUNT(*) AS token_count FROM api_tokens WHERE revoked_at IS NULL GROUP BY user_id"
        ).fetchall()
    by_token_user = {int(row["user_id"]): int(row.get("token_count") or 0) for row in token_counts}
    by_user: dict[int, list[dict[str, Any]]] = {}
    for perm in perms:
        by_user.setdefault(int(perm["user_id"]), []).append({
            "profile_id": int(perm.get("profile_id") or 0),
            "access_level": perm.get("access_level") or "ro",
        })
    for user in users:
        user["permissions"] = by_user.get(int(user["id"]), [])
        user["api_tokens"] = by_token_user.get(int(user["id"]), 0)
    return users


def save_user(data: dict[str, Any], user_id: int | None = None) -> dict[str, Any]:
    require_admin()
    now = utcnow()
    username = str(data.get("username") or "").strip()
    role = "admin" if data.get("role") == "admin" else "user"
    is_active = 1 if data.get("is_active", True) else 0
    password_editable = not uses_external_provider()
    if not username:
        raise ValueError("Username is required")
    with connect() as conn:
        if user_id:
            row = conn.execute("SELECT id FROM users WHERE id=?", (user_id,)).fetchone()
            if not row:
                raise ValueError("User does not exist")
            conn.execute(
                "UPDATE users SET username=?, email=?, display_name=?, role=?, is_active=?, updated_at=? WHERE id=?",
                (username, str(data.get("email") or "").strip() or None, str(data.get("display_name") or "").strip() or None, role, is_active, now, user_id),
            )
        else:
            initial_password_hash = password_hash(str(data.get("password") or username)) if password_editable else None
            # Note: TinyAuth/proxy users are passwordless in pyTorrent; credentials stay with the auth provider.
            cur = conn.execute(
                "INSERT INTO users(username,password_hash,email,display_name,role,is_active,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (username, initial_password_hash, str(data.get("email") or "").strip() or None, str(data.get("display_name") or "").strip() or None, role, is_active, now, now),
            )
            user_id = int(cur.lastrowid)
        if data.get("password") and password_editable:
            # Note: Password changes are intentionally disabled for external auth providers.
            conn.execute("UPDATE users SET password_hash=?, updated_at=? WHERE id=?", (password_hash(str(data.get("password"))), now, user_id))
        if role != "admin":
            conn.execute("DELETE FROM user_profile_permissions WHERE user_id=?", (user_id,))
            for item in data.get("permissions") or []:
                profile_id = int(item.get("profile_id") or 0)
                access = "full" if item.get("access_level") == "full" else "ro"
                conn.execute(
                    "INSERT OR REPLACE INTO user_profile_permissions(user_id,profile_id,access_level,created_at,updated_at) VALUES(?,?,?,?,?)",
                    (user_id, profile_id, access, now, now),
                )
        else:
            conn.execute("DELETE FROM user_profile_permissions WHERE user_id=?", (user_id,))
        return conn.execute("SELECT id, username, email, display_name, external_auth_provider, external_subject, role, is_active, created_at, updated_at FROM users WHERE id=?", (user_id,)).fetchone()


def delete_user(user_id: int) -> None:
    require_admin()
    uid = int(user_id or 0)
    if uid == current_user_id():
        raise ValueError("Cannot delete current user")
    if uid == default_user_id():
        # Note: The built-in fallback account must stay in the database for auth-disabled and recovery flows.
        raise ValueError("Cannot delete the default user")
    with connect() as conn:
        row = conn.execute("SELECT username FROM users WHERE id=?", (uid,)).fetchone()
        if not row:
            raise ValueError("User does not exist")
        if str(row.get("username") or "").lower() in {"default", "admin"}:
            # Note: Protect bootstrap accounts by name as well as by id.
            raise ValueError("Cannot delete built-in user")
        conn.execute("DELETE FROM user_profile_permissions WHERE user_id=?", (uid,))
        conn.execute("UPDATE api_tokens SET revoked_at=COALESCE(revoked_at, ?), updated_at=? WHERE user_id=?", (utcnow(), utcnow(), uid))
        conn.execute("DELETE FROM users WHERE id=?", (uid,))


def _public_user(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "id": int(row["id"]),
        "username": row.get("username"),
        "email": row.get("email"),
        "display_name": row.get("display_name"),
        "external_auth_provider": row.get("external_auth_provider"),
        "external_subject": row.get("external_subject"),
        "role": row.get("role") or "user",
        "is_active": int(row.get("is_active") or 0),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def _token_response(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "user_id": int(row["user_id"]),
        "name": row.get("name") or "API token",
        "token_prefix": row.get("token_prefix") or "",
        "last_used_at": row.get("last_used_at"),
        "created_at": row.get("created_at"),
        "revoked_at": row.get("revoked_at"),
    }


def list_api_tokens(user_id: int) -> list[dict[str, Any]]:
    if not enabled():
        return []
    uid = int(user_id or 0)
    if not uid:
        return []
    if not is_admin() and current_user_id() != uid:
        abort(403)
    with connect() as conn:
        rows = conn.execute(
            "SELECT id,user_id,name,token_prefix,last_used_at,created_at,updated_at,revoked_at FROM api_tokens WHERE user_id=? AND revoked_at IS NULL ORDER BY created_at DESC",
            (uid,),
        ).fetchall()
    return [_token_response(row) for row in rows]


def create_api_token(user_id: int, name: str = "API token") -> dict[str, Any]:
    if not enabled():
        raise ValueError("API tokens are available only when authentication is enabled")
    uid = int(user_id or 0)
    if not uid:
        raise ValueError("User is required")
    if not is_admin() and current_user_id() != uid:
        abort(403)
    clean_name = str(name or "API token").strip()[:80] or "API token"
    secret = "pt_" + secrets.token_urlsafe(32)
    prefix = secret[:14]
    now = utcnow()
    with connect() as conn:
        user = conn.execute("SELECT id,is_active FROM users WHERE id=?", (uid,)).fetchone()
        if not user or not int(user.get("is_active") or 0):
            raise ValueError("User is inactive or does not exist")
        cur = conn.execute(
            "INSERT INTO api_tokens(user_id,name,token_hash,token_prefix,created_at,updated_at) VALUES(?,?,?,?,?,?)",
            (uid, clean_name, password_hash(secret), prefix, now, now),
        )
        row = conn.execute(
            "SELECT id,user_id,name,token_prefix,last_used_at,created_at,updated_at,revoked_at FROM api_tokens WHERE id=?",
            (int(cur.lastrowid),),
        ).fetchone()
    data = _token_response(row)
    data["token"] = secret
    return data


def revoke_api_token(user_id: int, token_id: int) -> None:
    if not enabled():
        abort(404)
    uid = int(user_id or 0)
    tid = int(token_id or 0)
    if not is_admin() and current_user_id() != uid:
        abort(403)
    now = utcnow()
    with connect() as conn:
        # Note: Report missing/already revoked tokens instead of showing a false success in the UI.
        cur = conn.execute(
            "UPDATE api_tokens SET revoked_at=COALESCE(revoked_at, ?), updated_at=? WHERE id=? AND user_id=? AND revoked_at IS NULL",
            (now, now, tid, uid),
        )
        if cur.rowcount <= 0:
            raise ValueError("Active API token not found")


def authenticate_api_token(token: str) -> dict[str, Any] | None:
    if not enabled():
        return None
    raw = str(token or "").strip()
    if not raw:
        return None
    prefix = raw[:14]
    with connect() as conn:
        rows = conn.execute(
            """SELECT t.id AS token_id,t.token_hash,t.user_id,u.username,u.role,u.is_active
               FROM api_tokens t JOIN users u ON u.id=t.user_id
               WHERE t.revoked_at IS NULL AND t.token_prefix=?""",
            (prefix,),
        ).fetchall()
        matched = None
        for row in rows:
            if check_password_hash(row.get("token_hash") or "", raw):
                matched = row
                break
        if not matched or not int(matched.get("is_active") or 0):
            return None
        conn.execute("UPDATE api_tokens SET last_used_at=?, updated_at=? WHERE id=?", (utcnow(), utcnow(), int(matched["token_id"])))
    return {"id": int(matched["user_id"]), "username": matched.get("username"), "role": matched.get("role") or "user", "is_active": 1}


def _request_api_token() -> str:
    header = request.headers.get("Authorization") or ""
    if header.lower().startswith("bearer "):
        return header.split(None, 1)[1].strip()
    return (request.headers.get("X-API-Key") or request.args.get("api_key") or "").strip()


def install_guards(app) -> None:
    @app.before_request
    def _auth_guard():
        if not enabled():
            return None

        endpoint = request.endpoint or ""
        if endpoint == "static" or endpoint.endswith(".static"):
            return None
            
        # Allow unauthenticated health checks for monitoring.
        if request.path == "/api/health" or request.path.startswith("/api/health/"):
            return None
        g.api_token_authenticated = False
        if auth_bypassed_request():
            return None
        if request.path.startswith("/api/"):
            token_user = authenticate_api_token(_request_api_token())
            if token_user:
                g.api_user_id = int(token_user["id"])
                g.api_token_authenticated = True
        if not getattr(g, "api_user_id", None):
            authenticate_external_user()
        if endpoint in PUBLIC_ENDPOINTS:
            return None
        if not current_user_id():
            if request.path.startswith("/api/"):
                return jsonify({"ok": False, "error": "Authentication required"}), 401
            return redirect(url_for("main.login", next=request.full_path if request.query_string else request.path))
        user = current_user()
        if not user or not int(user.get("is_active") or 0):
            logout_user()
            if request.path.startswith("/api/"):
                return jsonify({"ok": False, "error": "Authentication required"}), 401
            return redirect(url_for("main.login"))
        if request.path.startswith("/api/auth/users") and not is_admin(user):
            return jsonify({"ok": False, "error": "Admin only"}), 403
        if request.path.startswith(PROFILE_READ_PREFIXES):
            profile_id = _request_profile_id()
            if profile_id and not can_access_profile(profile_id):
                return jsonify({"ok": False, "error": "Profile access denied"}), 403
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            if request.path.startswith("/api/") and not getattr(g, "api_token_authenticated", False) and not same_origin_request():
                return jsonify({"ok": False, "error": "Cross-origin API request blocked"}), 403
            if request.path.startswith("/api/profiles") and not request.path.endswith("/activate") and not is_admin(user):
                return jsonify({"ok": False, "error": "Admin only"}), 403
            profile_id = _request_profile_id()
            if request.path.startswith(RTORRENT_CONFIG_PREFIXES) and not can_write_profile(profile_id):
                return jsonify({"ok": False, "error": "Read-only profile access"}), 403
            if request.path.startswith(RTORRENT_WRITE_PREFIXES) and not can_write_profile(profile_id):
                return jsonify({"ok": False, "error": "Read-only profile access"}), 403
        return None


def _request_profile_id() -> int | None:
    if request.view_args and request.view_args.get("profile_id"):
        return int(request.view_args["profile_id"])
    payload = {}
    try:
        payload = request.get_json(silent=True) or {}
    except Exception:
        payload = {}
    raw_id = request.args.get("profile_id") or request.form.get("profile_id") or payload.get("profile_id")
    if raw_id not in (None, ""):
        try:
            return int(raw_id)
        except (TypeError, ValueError):
            return None
    from . import preferences
    profile = preferences.active_profile()
    if profile:
        return int(profile["id"])
    return 1 if can_access_profile(1) else None
