from __future__ import annotations
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from ..db import connect, utcnow, default_user_id
from . import auth

# Note: Application backups are admin-only because they include users, permissions and all profiles.
APP_BACKUP_TABLES = [
    "users", "user_profile_permissions", "user_preferences", "profile_preferences", "rtorrent_profiles",
    "disk_monitor_preferences", "labels", "ratio_groups", "rss_feeds", "rss_rules",
    "smart_queue_settings", "smart_queue_exclusions", "automation_rules",
    "rtorrent_config_overrides", "poller_settings", "app_settings", "download_plan_settings",
]

# Note: Profile backups contain profile behavior plus user-specific view preferences for the user creating the backup.
PROFILE_BACKUP_TABLES = [
    "rtorrent_profiles", "profile_preferences", "disk_monitor_preferences", "labels", "ratio_groups",
    "rss_feeds", "rss_rules", "smart_queue_settings", "smart_queue_exclusions",
    "automation_rules", "rtorrent_config_overrides", "poller_settings", "download_plan_settings",
]

# Scope values:
# - profile: shared profile behavior, visible/restored by profile access.
# - user_profile: personal preferences for the backup creator/restorer.
PROFILE_TABLE_SCOPES = {
    "rtorrent_profiles": "profile_id",
    "profile_preferences": "user_profile",
    "disk_monitor_preferences": "profile",
    "labels": "profile",
    "ratio_groups": "profile",
    "rss_feeds": "profile",
    "rss_rules": "profile",
    "smart_queue_settings": "profile",
    "smart_queue_exclusions": "profile",
    "automation_rules": "profile",
    "rtorrent_config_overrides": "profile",
    "poller_settings": "profile",
    "download_plan_settings": "profile_singleton",
}

PROFILE_TABLE_FILTERS = {
    "rtorrent_profiles": "id=?",
    "profile_preferences": "user_id=? AND profile_id=?",
    "disk_monitor_preferences": "profile_id=?",
    "labels": "profile_id=?",
    "ratio_groups": "profile_id=?",
    "rss_feeds": "profile_id=?",
    "rss_rules": "profile_id=?",
    "smart_queue_settings": "profile_id=?",
    "smart_queue_exclusions": "profile_id=?",
    "automation_rules": "profile_id=?",
    "rtorrent_config_overrides": "profile_id=?",
    "poller_settings": "profile_id=?",
    "download_plan_settings": "profile_id=?",
}

DEFAULT_AUTO_BACKUP_SETTINGS = {
    "enabled": False,
    "interval_hours": 24,
    "retention_days": 30,
    "last_run_at": None,
}
BACKUP_PREVIEW_VALUE_LIMIT = 80
BACKUP_PREVIEW_ROW_LIMIT = 3
BACKUP_PREVIEW_SENSITIVE_KEYS = {"password", "password_hash", "token", "token_hash", "api_key", "secret"}
AUTO_BACKUP_SETTINGS_KEY = "backup:auto"
_scheduler_started = False
_scheduler_lock = threading.Lock()


def _is_admin_user(user_id: int | None = None) -> bool:
    if not auth.enabled():
        return True
    uid = user_id or auth.current_user_id()
    if not uid:
        return False
    with connect() as conn:
        row = conn.execute("SELECT role,is_active FROM users WHERE id=?", (uid,)).fetchone()
    return bool(row and row.get("role") == "admin" and int(row.get("is_active") or 0))


def _require_admin(user_id: int | None = None) -> None:
    if not _is_admin_user(user_id):
        raise PermissionError("Application backups are available only to admins")


def _loads(value: str) -> dict:
    try:
        data = json.loads(value or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _table_columns(conn, table: str) -> set[str]:
    try:
        return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return set()


def _table_rows(conn, table: str, where: str | None = None, params: tuple = ()) -> list[dict]:
    try:
        sql = f"SELECT * FROM {table}" + (f" WHERE {where}" if where else "")
        return [dict(row) for row in conn.execute(sql, params).fetchall()]
    except Exception:
        return []


def _profile_filter_params(table: str, user_id: int, profile_id: int) -> tuple[object, ...]:
    scope = PROFILE_TABLE_SCOPES.get(table)
    if scope in {"profile", "profile_id", "profile_singleton"}:
        return (int(profile_id),)
    return (int(user_id), int(profile_id))


def _user_label(conn, user_id: int | None) -> str:
    if not user_id:
        return "system"
    try:
        row = conn.execute("SELECT display_name, username, email FROM users WHERE id=?", (int(user_id),)).fetchone()
        if row:
            return str(row.get("display_name") or row.get("username") or row.get("email") or f"user {user_id}")
    except Exception:
        pass
    return f"user {user_id}"


def _backup_row_visible(row: dict, user_id: int) -> bool:
    backup_type = str(row.get("backup_type") or "app")
    if backup_type == "app":
        return _is_admin_user(user_id)
    profile_id = int(row.get("profile_id") or 0)
    return bool(profile_id and auth.can_access_profile(profile_id, user_id))


def _backup_row_writable(row: dict, user_id: int) -> bool:
    backup_type = str(row.get("backup_type") or "app")
    if backup_type == "app":
        return _is_admin_user(user_id)
    profile_id = int(row.get("profile_id") or 0)
    return bool(profile_id and auth.can_write_profile(profile_id, user_id))


def _store_backup(user_id: int, name: str, backup_type: str, profile_id: int | None, payload: dict) -> dict:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO app_backups(user_id,name,backup_type,profile_id,payload_json,created_at) VALUES(?,?,?,?,?,?)",
            (user_id, name or f"Backup {payload['created_at']}", backup_type, profile_id, json.dumps(payload), payload["created_at"]),
        )
        backup_id = cur.lastrowid
    return {
        "id": backup_id,
        "name": name,
        "backup_type": backup_type,
        "profile_id": profile_id,
        "created_at": payload["created_at"],
        "automatic": bool(payload.get("automatic")),
        "tables": {k: len(v) for k, v in (payload.get("tables") or {}).items()},
    }


def create_app_backup(name: str, user_id: int | None = None, automatic: bool = False) -> dict:
    user_id = user_id or auth.current_user_id() or default_user_id()
    _require_admin(user_id)
    payload = {"version": 2, "backup_type": "app", "created_at": utcnow(), "automatic": bool(automatic), "tables": {}}
    with connect() as conn:
        for table in APP_BACKUP_TABLES:
            payload["tables"][table] = _table_rows(conn, table)
    return _store_backup(user_id, name, "app", None, payload)


def create_profile_backup(name: str, profile_id: int, user_id: int | None = None, automatic: bool = False) -> dict:
    user_id = user_id or auth.current_user_id() or default_user_id()
    if not auth.can_write_profile(profile_id, user_id):
        raise PermissionError("No write access to profile")
    payload = {"version": 2, "backup_type": "profile", "source_profile_id": int(profile_id), "created_at": utcnow(), "automatic": bool(automatic), "tables": {}}
    with connect() as conn:
        for table in PROFILE_BACKUP_TABLES:
            where = PROFILE_TABLE_FILTERS.get(table)
            payload["tables"][table] = _table_rows(conn, table, where, _profile_filter_params(table, user_id, int(profile_id)))
    return _store_backup(user_id, name, "profile", int(profile_id), payload)


def create_backup(name: str, user_id: int | None = None, automatic: bool = False) -> dict:
    return create_app_backup(name, user_id, automatic)


def list_backups(user_id: int | None = None, backup_type: str | None = None, profile_id: int | None = None) -> list[dict]:
    user_id = user_id or auth.current_user_id() or default_user_id()
    clauses: list[str] = []
    params: list[object] = []
    if backup_type:
        clauses.append("COALESCE(backup_type,'app')=?")
        params.append(backup_type)
    if profile_id is not None:
        clauses.append("profile_id=?")
        params.append(int(profile_id))
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT b.id,b.name,b.user_id,b.created_at,b.payload_json,COALESCE(b.backup_type,'app') AS backup_type,b.profile_id,
                   u.display_name AS owner_display_name,u.username AS owner_username,u.email AS owner_email
            FROM app_backups b
            LEFT JOIN users u ON u.id=b.user_id
            {where}
            ORDER BY b.id DESC
            """,
            tuple(params),
        ).fetchall()
    result = []
    for row in rows:
        if not _backup_row_visible(row, user_id):
            continue
        payload = _loads(row.get("payload_json") or "{}")
        tables = payload.get("tables") or {}
        owner_name = str(row.get("owner_display_name") or row.get("owner_username") or row.get("owner_email") or f"user {row.get('user_id')}")
        result.append({
            "id": row.get("id"),
            "name": row.get("name"),
            "owner_user_id": row.get("user_id"),
            "owner_name": owner_name,
            "created_at": row.get("created_at"),
            "backup_type": row.get("backup_type") or payload.get("backup_type") or "app",
            "profile_id": row.get("profile_id") or payload.get("source_profile_id"),
            "automatic": bool(payload.get("automatic")),
            "tables": {key: len(value or []) for key, value in tables.items()},
        })
    return result

def payload_for_backup(backup_id: int, user_id: int | None = None, require_write: bool = False) -> dict:
    user_id = user_id or auth.current_user_id() or default_user_id()
    with connect() as conn:
        row = conn.execute("SELECT id,user_id,COALESCE(backup_type,'app') AS backup_type,profile_id,payload_json FROM app_backups WHERE id=?", (backup_id,)).fetchone()
    if not row or not (_backup_row_writable(row, user_id) if require_write else _backup_row_visible(row, user_id)):
        raise ValueError("Backup not found")
    return json.loads(row["payload_json"] or "{}")

def _backup_type(payload: dict) -> str:
    return str(payload.get("backup_type") or ("profile" if payload.get("source_profile_id") else "app"))


def restore_app_backup(backup_id: int, user_id: int | None = None) -> dict:
    user_id = user_id or auth.current_user_id() or default_user_id()
    _require_admin(user_id)
    payload = payload_for_backup(backup_id, user_id, require_write=True)
    if _backup_type(payload) != "app":
        raise ValueError("This is not an application backup")
    tables = payload.get("tables") or {}
    restored = {}
    with connect() as conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        try:
            for table in APP_BACKUP_TABLES:
                rows = tables.get(table) or []
                if not rows:
                    continue
                available = _table_columns(conn, table)
                columns = [col for col in rows[0].keys() if col in available]
                if not columns:
                    continue
                placeholders = ",".join("?" for _ in columns)
                conn.execute(f"DELETE FROM {table}")
                for row in rows:
                    conn.execute(f"INSERT INTO {table}({','.join(columns)}) VALUES({placeholders})", [row.get(col) for col in columns])
                restored[table] = len(rows)
        finally:
            conn.execute("PRAGMA foreign_keys = ON")
    return {"restored": restored, "backup_type": "app"}


def _single_profile_row(rows: list[dict]) -> list[dict]:
    if not rows:
        return []
    return [sorted(rows, key=lambda row: str(row.get("updated_at") or row.get("created_at") or ""), reverse=True)[0]]


def _rewrite_profile_row(table: str, row: dict, user_id: int, target_profile_id: int) -> dict:
    clean = dict(row)
    if table == "rtorrent_profiles":
        clean["id"] = target_profile_id
        clean["user_id"] = user_id
        clean["is_default"] = int(clean.get("is_default") or 0)
        return clean
    if "profile_id" in clean:
        clean["profile_id"] = target_profile_id
    if "user_id" in clean:
        clean["user_id"] = user_id
    if table == "poller_settings":
        clean["profile_id"] = target_profile_id
    if "id" in clean and table != "rtorrent_profiles":
        clean.pop("id", None)
    return clean


def restore_profile_backup(backup_id: int, target_profile_id: int, user_id: int | None = None) -> dict:
    user_id = user_id or auth.current_user_id() or default_user_id()
    if not auth.can_write_profile(target_profile_id, user_id):
        raise PermissionError("No write access to profile")
    payload = payload_for_backup(backup_id, user_id, require_write=True)
    if _backup_type(payload) != "profile":
        raise ValueError("This is not a profile backup")
    tables = payload.get("tables") or {}
    restored = {}
    with connect() as conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        try:
            for table in PROFILE_BACKUP_TABLES:
                rows = tables.get(table) or []
                if table == "disk_monitor_preferences":
                    rows = _single_profile_row([dict(row) for row in rows])
                where = PROFILE_TABLE_FILTERS.get(table)
                params = _profile_filter_params(table, user_id, int(target_profile_id))
                conn.execute(f"DELETE FROM {table} WHERE {where}", params)
                if not rows:
                    continue
                count = 0
                for row in rows:
                    clean = _rewrite_profile_row(table, dict(row), user_id, int(target_profile_id))
                    available = _table_columns(conn, table)
                    columns = [col for col in clean.keys() if col in available]
                    if not columns:
                        continue
                    placeholders = ",".join("?" for _ in columns)
                    conn.execute(f"INSERT INTO {table}({','.join(columns)}) VALUES({placeholders})", [clean.get(col) for col in columns])
                    count += 1
                restored[table] = count
        finally:
            conn.execute("PRAGMA foreign_keys = ON")
    return {"restored": restored, "backup_type": "profile", "profile_id": int(target_profile_id)}


def restore_backup(backup_id: int, user_id: int | None = None, profile_id: int | None = None) -> dict:
    payload = payload_for_backup(backup_id, user_id, require_write=True)
    if _backup_type(payload) == "profile":
        target = profile_id or payload.get("source_profile_id")
        if not target:
            raise ValueError("Missing target profile")
        return restore_profile_backup(backup_id, int(target), user_id)
    return restore_app_backup(backup_id, user_id)


def delete_backup(backup_id: int, user_id: int | None = None) -> dict:
    user_id = user_id or auth.current_user_id() or default_user_id()
    with connect() as conn:
        row = conn.execute("SELECT id,user_id,COALESCE(backup_type,'app') AS backup_type,profile_id FROM app_backups WHERE id=?", (backup_id,)).fetchone()
        if not row or not _backup_row_writable(row, user_id):
            raise ValueError("Backup not found")
        cur = conn.execute("DELETE FROM app_backups WHERE id=?", (backup_id,))
    if not cur.rowcount:
        raise ValueError("Backup not found")
    return {"deleted": backup_id}

def _settings_row_key(user_id: int | None = None, backup_type: str = "app", profile_id: int | None = None) -> str:
    uid = user_id or auth.current_user_id() or default_user_id()
    scope = "profile" if backup_type == "profile" else "app"
    if scope == "profile":
        return f"{AUTO_BACKUP_SETTINGS_KEY}:profile:{int(profile_id or 0)}"
    return f"{AUTO_BACKUP_SETTINGS_KEY}:app:{uid}"

def _latest_backup_created_at(user_id: int, backup_type: str = "app", profile_id: int | None = None) -> str | None:
    clauses = ["COALESCE(backup_type,'app')=?"]
    params: list[object] = [backup_type]
    if backup_type == "profile":
        clauses.append("profile_id=?")
        params.append(int(profile_id or 0))
    else:
        clauses.append("user_id=?")
        params.append(user_id)
    with connect() as conn:
        row = conn.execute(
            f"SELECT created_at FROM app_backups WHERE {' AND '.join(clauses)} ORDER BY created_at DESC, id DESC LIMIT 1",
            tuple(params),
        ).fetchone()
    return str(row["created_at"] or "") if row and row.get("created_at") else None

def _preview_value(value: object) -> object:
    if value is None or isinstance(value, (int, float, bool)):
        return value
    text = str(value)
    return text if len(text) <= BACKUP_PREVIEW_VALUE_LIMIT else f"{text[:BACKUP_PREVIEW_VALUE_LIMIT]}..."


def _preview_row(row: dict) -> dict:
    output = {}
    for key, value in row.items():
        lowered = str(key).lower()
        output[key] = "[hidden]" if any(secret in lowered for secret in BACKUP_PREVIEW_SENSITIVE_KEYS) else _preview_value(value)
    return output


def get_auto_backup_settings(user_id: int | None = None, backup_type: str = "app", profile_id: int | None = None) -> dict:
    user_id = user_id or auth.current_user_id() or default_user_id()
    key = _settings_row_key(user_id, backup_type, profile_id)
    with connect() as conn:
        row = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
        if not row and backup_type == "profile":
            legacy_key = f"{AUTO_BACKUP_SETTINGS_KEY}:profile:{int(user_id)}:{int(profile_id or 0)}"
            row = conn.execute("SELECT value FROM app_settings WHERE key=?", (legacy_key,)).fetchone()
    settings = {**DEFAULT_AUTO_BACKUP_SETTINGS, **_loads(row.get("value") if row else "{}")}
    settings["enabled"] = bool(settings.get("enabled"))
    settings["interval_hours"] = max(1, int(settings.get("interval_hours") or 24))
    settings["retention_days"] = max(1, int(settings.get("retention_days") or 30))
    settings["backup_type"] = "profile" if backup_type == "profile" else "app"
    if backup_type == "profile":
        settings["profile_id"] = int(profile_id or 0)
    settings["owner_user_id"] = user_id or auth.current_user_id() or default_user_id()
    with connect() as conn:
        settings["owner_name"] = _user_label(conn, settings["owner_user_id"])
    return settings


def save_auto_backup_settings(data: dict, user_id: int | None = None, backup_type: str = "app", profile_id: int | None = None) -> dict:
    user_id = user_id or auth.current_user_id() or default_user_id()
    backup_type = "profile" if backup_type == "profile" else "app"
    if backup_type == "app":
        _require_admin(user_id)
    else:
        # Note: Profile backup schedules affect profile operations, so read-only users may view/export backups but cannot change automation.
        if not profile_id or not auth.can_write_profile(int(profile_id), user_id):
            raise PermissionError("No write access to profile")
    current = get_auto_backup_settings(user_id, backup_type, profile_id)
    settings = {
        **current,
        "enabled": bool(data.get("enabled")),
        "interval_hours": max(1, int(data.get("interval_hours") or current["interval_hours"])),
        "retention_days": max(1, int(data.get("retention_days") or current["retention_days"])),
        "last_run_at": data.get("last_run_at", current.get("last_run_at")),
    }
    key = _settings_row_key(user_id, backup_type, profile_id)
    with connect() as conn:
        conn.execute("INSERT OR REPLACE INTO app_settings(key,value) VALUES(?,?)", (key, json.dumps(settings)))
    return settings



def _backup_owner_info(backup_id: int) -> dict:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT b.user_id,COALESCE(u.display_name,u.username,u.email,'user ' || b.user_id) AS owner_name
            FROM app_backups b
            LEFT JOIN users u ON u.id=b.user_id
            WHERE b.id=?
            """,
            (int(backup_id),),
        ).fetchone()
    return {"owner_user_id": row.get("user_id") if row else None, "owner_name": row.get("owner_name") if row else ""}

def preview_backup(backup_id: int, user_id: int | None = None) -> dict:
    payload = payload_for_backup(backup_id, user_id)
    tables = payload.get("tables") or {}
    owner = _backup_owner_info(backup_id)
    return {
        "version": payload.get("version"),
        "owner_user_id": owner.get("owner_user_id"),
        "owner_name": owner.get("owner_name"),
        "created_at": payload.get("created_at"),
        "backup_type": _backup_type(payload),
        "source_profile_id": payload.get("source_profile_id"),
        "automatic": bool(payload.get("automatic")),
        "tables": [
            {
                "name": table,
                "rows": len(rows or []),
                "columns": list((rows[0] or {}).keys()) if rows else [],
                "sample": [_preview_row(dict(row)) for row in (rows or [])[:BACKUP_PREVIEW_ROW_LIMIT]],
            }
            for table, rows in tables.items()
        ],
    }


def prune_old_backups(user_id: int | None = None, retention_days: int = 30, backup_type: str = "app", profile_id: int | None = None) -> int:
    user_id = user_id or auth.current_user_id() or default_user_id()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, int(retention_days)))).isoformat(timespec="seconds")
    clauses = ["COALESCE(backup_type,'app')=?", "created_at<?"]
    params: list[object] = [backup_type, cutoff]
    if backup_type == "profile":
        clauses.append("profile_id=?")
        params.append(int(profile_id or 0))
    else:
        clauses.append("user_id=?")
        params.append(user_id)
    with connect() as conn:
        cur = conn.execute(f"DELETE FROM app_backups WHERE {' AND '.join(clauses)}", tuple(params))
    return int(cur.rowcount or 0)

def _should_run(settings: dict, last_value: str | None) -> bool:
    now = datetime.now(timezone.utc)
    try:
        last = datetime.fromisoformat(str(last_value).replace("Z", "+00:00")) if last_value else None
    except Exception:
        last = None
    return not last or now - last >= timedelta(hours=settings["interval_hours"])


def maybe_create_automatic_backup(user_id: int | None = None, backup_type: str = "app", profile_id: int | None = None) -> dict | None:
    user_id = user_id or default_user_id()
    backup_type = "profile" if backup_type == "profile" else "app"
    if backup_type == "app" and not _is_admin_user(user_id):
        return None
    if backup_type == "profile" and (not profile_id or not auth.can_access_profile(int(profile_id), user_id)):
        return None
    settings = get_auto_backup_settings(user_id, backup_type, profile_id)
    if not settings.get("enabled"):
        return None
    last_value = settings.get("last_run_at") or _latest_backup_created_at(user_id, backup_type, profile_id)
    if not _should_run(settings, last_value):
        if settings.get("last_run_at") != last_value:
            settings["last_run_at"] = last_value
            save_auto_backup_settings(settings, user_id, backup_type, profile_id)
        return None
    now = datetime.now(timezone.utc)
    if backup_type == "profile":
        backup = create_profile_backup(f"Automatic profile backup {now.isoformat(timespec='seconds')}", int(profile_id or 0), user_id, automatic=True)
    else:
        backup = create_app_backup(f"Automatic application backup {now.isoformat(timespec='seconds')}", user_id, automatic=True)
    settings["last_run_at"] = backup.get("created_at") or now.isoformat(timespec="seconds")
    save_auto_backup_settings(settings, user_id, backup_type, profile_id)
    prune_old_backups(user_id, settings["retention_days"], backup_type, profile_id)
    return backup


def _profile_schedule_keys() -> list[tuple[int, int]]:
    prefix = f"{AUTO_BACKUP_SETTINGS_KEY}:profile:"
    keys: set[tuple[int, int]] = set()
    with connect() as conn:
        rows = conn.execute("SELECT key FROM app_settings WHERE key LIKE ?", (prefix + "%",)).fetchall()
    for row in rows:
        parts = str(row.get("key") or "").split(":")
        try:
            if len(parts) >= 5:
                # Legacy key: backup:auto:profile:{uid}:{profile_id}
                keys.add((int(parts[-2]), int(parts[-1])))
            elif len(parts) >= 4:
                profile_id = int(parts[-1])
                keys.add((_profile_owner_for_backup(profile_id), profile_id))
        except Exception:
            continue
    return sorted(keys)


def _profile_owner_for_backup(profile_id: int) -> int:
    with connect() as conn:
        row = conn.execute("SELECT user_id FROM rtorrent_profiles WHERE id=?", (int(profile_id),)).fetchone()
        if row and row.get("user_id"):
            return int(row["user_id"])
        row = conn.execute("SELECT user_id FROM user_profile_permissions WHERE profile_id=? AND access_level='full' ORDER BY user_id LIMIT 1", (int(profile_id),)).fetchone()
        if row and row.get("user_id"):
            return int(row["user_id"])
    return default_user_id()

def start_scheduler() -> None:
    global _scheduler_started
    with _scheduler_lock:
        if _scheduler_started:
            return
        _scheduler_started = True

    def loop() -> None:
        while True:
            try:
                with connect() as conn:
                    rows = conn.execute("SELECT id FROM users WHERE is_active=1 AND role='admin'").fetchall()
                user_ids = [int(row["id"]) for row in rows] or [default_user_id()]
                for uid in user_ids:
                    maybe_create_automatic_backup(uid, "app")
                for uid, pid in _profile_schedule_keys():
                    maybe_create_automatic_backup(uid, "profile", pid)
            except Exception:
                pass
            time.sleep(300)

    threading.Thread(target=loop, daemon=True, name="pytorrent-backup-scheduler").start()
