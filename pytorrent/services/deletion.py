from __future__ import annotations

import json
import sqlite3
from typing import Iterable

from ..db import utcnow


class DeletionError(RuntimeError):
    """Raised when a destructive operation cannot be completed safely."""


def _quote_identifier(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _tables_with_column(conn: sqlite3.Connection, column: str) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    tables: list[str] = []
    for row in rows:
        table = str(row["name"] if isinstance(row, dict) else row[0])
        columns = conn.execute(f"PRAGMA table_info({_quote_identifier(table)})").fetchall()
        for info in columns:
            col_name = info["name"] if isinstance(info, dict) else info[1]
            if str(col_name) == column:
                tables.append(table)
                break
    return tables


def _delete_rows_by_column(
    conn: sqlite3.Connection,
    column: str,
    value: int,
    *,
    exclude: Iterable[str] = (),
) -> dict[str, int]:
    excluded = set(exclude)
    deleted: dict[str, int] = {}
    for table in _tables_with_column(conn, column):
        if table in excluded:
            continue
        cur = conn.execute(
            f"DELETE FROM {_quote_identifier(table)} WHERE {_quote_identifier(column)}=?",
            (int(value),),
        )
        if cur.rowcount and cur.rowcount > 0:
            deleted[table] = int(cur.rowcount)
    return deleted




def _foreign_key_children(conn: sqlite3.Connection, parent_table: str, parent_column: str) -> list[tuple[str, str]]:
    children: list[tuple[str, str]] = []
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    for row in rows:
        table = str(row["name"] if isinstance(row, dict) else row[0])
        for fk in conn.execute(f"PRAGMA foreign_key_list({_quote_identifier(table)})").fetchall():
            ref_table = str(fk["table"] if isinstance(fk, dict) else fk[2])
            from_column = str(fk["from"] if isinstance(fk, dict) else fk[3])
            to_column = str(fk["to"] if isinstance(fk, dict) else fk[4])
            if ref_table == parent_table and to_column == parent_column:
                children.append((table, from_column))
    return children


def _delete_fk_children(
    conn: sqlite3.Connection,
    parent_table: str,
    parent_column: str,
    value: int,
    *,
    exclude: Iterable[str] = (),
) -> dict[str, int]:
    excluded = set(exclude)
    deleted: dict[str, int] = {}
    for table, column in _foreign_key_children(conn, parent_table, parent_column):
        if table in excluded:
            continue
        cur = conn.execute(
            f"DELETE FROM {_quote_identifier(table)} WHERE {_quote_identifier(column)}=?",
            (int(value),),
        )
        if cur.rowcount and cur.rowcount > 0:
            deleted[table] = deleted.get(table, 0) + int(cur.rowcount)
    return deleted


def _merge_counts(target: dict[str, int], extra: dict[str, int]) -> None:
    for table, count in extra.items():
        target[table] = target.get(table, 0) + int(count)


def _run_savepoint(conn: sqlite3.Connection, name: str, callback):
    safe_name = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in str(name))
    conn.execute(f"SAVEPOINT {safe_name}")
    try:
        result = callback()
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {safe_name}")
        conn.execute(f"RELEASE SAVEPOINT {safe_name}")
        raise
    conn.execute(f"RELEASE SAVEPOINT {safe_name}")
    return result




def _job_touches_profile(row: dict, profile_id: int) -> bool:
    pid = int(profile_id)
    try:
        if int(row.get("profile_id") or 0) == pid:
            return True
    except (TypeError, ValueError):
        pass
    try:
        payload = json.loads(row.get("payload_json") or "{}")
    except Exception:
        payload = {}
    try:
        return int((payload or {}).get("target_profile_id") or 0) == pid
    except (TypeError, ValueError):
        return False


def _guard_profile_jobs(conn: sqlite3.Connection, profile_id: int) -> dict[str, int]:
    """Prevent a profile from disappearing underneath an executing worker.

    A no-op write acquires SQLite's writer lock before the running-job check.
    Pending source-profile jobs are removed by the normal profile purge. Pending
    cross-profile transfer jobs are cancelled so a queued worker cannot start
    against a target profile that is being deleted.
    """
    pid = int(profile_id)
    conn.execute("UPDATE rtorrent_profiles SET updated_at=updated_at WHERE id=?", (pid,))
    active = conn.execute(
        "SELECT id,profile_id,payload_json,status FROM jobs WHERE status IN ('pending','running') ORDER BY created_at,id"
    ).fetchall()
    running = [row for row in active if row.get("status") == "running" and _job_touches_profile(row, pid)]
    if running:
        raise DeletionError(
            f"Profile has {len(running)} running job(s). Finish or cancel them before deleting the profile."
        )
    cancelled = 0
    for row in active:
        if row.get("status") != "pending" or not _job_touches_profile(row, pid):
            continue
        if int(row.get("profile_id") or 0) == pid:
            continue
        cur = conn.execute(
            "UPDATE jobs SET status='cancelled', error=?, finished_at=COALESCE(finished_at,CURRENT_TIMESTAMP), updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='pending'",
            ("Cancelled because the target profile was deleted", row["id"]),
        )
        cancelled += max(0, int(cur.rowcount or 0))
    return {"running": 0, "cancelled_cross_profile_pending": cancelled}


def _guard_user_jobs(conn: sqlite3.Connection, user_id: int) -> None:
    uid = int(user_id)
    conn.execute("UPDATE users SET updated_at=updated_at WHERE id=?", (uid,))
    row = conn.execute(
        "SELECT COUNT(*) AS count FROM jobs WHERE user_id=? AND status='running'",
        (uid,),
    ).fetchone()
    count = int((row or {}).get("count") or 0)
    if count:
        raise DeletionError(
            f"User has {count} running job(s). Finish or cancel them before deleting the user."
        )


def _clear_user_audit_references(conn: sqlite3.Connection, user_id: int) -> dict[str, int]:
    """Keep shared profile configuration when a creator/editor account is removed."""
    uid = int(user_id)
    cleared: dict[str, int] = {}
    audit_columns = (
        ("disk_monitor_preferences", "updated_by_user_id"),
        ("labels", "created_by_user_id"),
        ("labels", "updated_by_user_id"),
        ("ratio_groups", "created_by_user_id"),
        ("ratio_groups", "updated_by_user_id"),
        ("automation_rules", "created_by_user_id"),
        ("automation_rules", "updated_by_user_id"),
        ("download_plan_settings", "updated_by_user_id"),
        ("operation_log_settings", "updated_by_user_id"),
    )
    for table, column in audit_columns:
        if table not in _tables_with_column(conn, column):
            continue
        cur = conn.execute(
            f"UPDATE {_quote_identifier(table)} SET {_quote_identifier(column)}=NULL WHERE {_quote_identifier(column)}=?",
            (uid,),
        )
        count = max(0, int(cur.rowcount or 0))
        if count:
            cleared[f"{table}.{column}"] = count
    return cleared


def _delete_profile_app_settings(conn: sqlite3.Connection, profile_id: int) -> int:
    pid = int(profile_id)
    keys = (
        f"poller.settings.{pid}",
        f"download_planner.history.{pid}",
        f"download_planner.override_until.{pid}",
        f"backup:auto:profile:{pid}",
    )
    deleted = 0
    for key in keys:
        deleted += max(0, int(conn.execute("DELETE FROM app_settings WHERE key=?", (key,)).rowcount or 0))
    deleted += max(
        0,
        int(
            conn.execute(
                "DELETE FROM app_settings WHERE key LIKE ?",
                (f"backup:auto:profile:%:{pid}",),
            ).rowcount
            or 0
        ),
    )
    deleted += max(
        0,
        int(conn.execute("DELETE FROM app_settings WHERE key LIKE ?", (f"port_check:{pid}:%",)).rowcount or 0),
    )
    return deleted


def _delete_user_app_settings(conn: sqlite3.Connection, user_id: int) -> int:
    uid = int(user_id)
    deleted = 0
    deleted += max(
        0,
        int(conn.execute("DELETE FROM app_settings WHERE key=?", (f"backup:auto:app:{uid}",)).rowcount or 0),
    )
    # Legacy automatic-profile-backup key: backup:auto:profile:{user_id}:{profile_id}
    deleted += max(
        0,
        int(
            conn.execute(
                "DELETE FROM app_settings WHERE key LIKE ?",
                (f"backup:auto:profile:{uid}:%",),
            ).rowcount
            or 0
        ),
    )
    return deleted


def _fallback_profile_for_user(conn: sqlite3.Connection, user_id: int, excluded_profile_id: int) -> int | None:
    uid = int(user_id)
    excluded = int(excluded_profile_id)
    user = conn.execute("SELECT role,is_active FROM users WHERE id=?", (uid,)).fetchone()
    if not user or not int(user.get("is_active") or 0):
        return None

    if str(user.get("role") or "user") == "admin":
        row = conn.execute(
            "SELECT id FROM rtorrent_profiles WHERE id<>? ORDER BY is_default DESC, name COLLATE NOCASE, id LIMIT 1",
            (excluded,),
        ).fetchone()
        return int(row["id"]) if row else None

    global_permission = conn.execute(
        "SELECT 1 FROM user_profile_permissions WHERE user_id=? AND profile_id=0 LIMIT 1",
        (uid,),
    ).fetchone()
    if global_permission:
        row = conn.execute(
            "SELECT id FROM rtorrent_profiles WHERE id<>? ORDER BY is_default DESC, name COLLATE NOCASE, id LIMIT 1",
            (excluded,),
        ).fetchone()
        return int(row["id"]) if row else None

    row = conn.execute(
        """
        SELECT p.id
        FROM rtorrent_profiles p
        JOIN user_profile_permissions upp ON upp.profile_id=p.id
        WHERE upp.user_id=? AND p.id<>?
        ORDER BY p.is_default DESC, p.name COLLATE NOCASE, p.id
        LIMIT 1
        """,
        (uid, excluded),
    ).fetchone()
    return int(row["id"]) if row else None


def purge_profile(conn: sqlite3.Connection, profile_id: int) -> dict[str, object]:
    """Delete a profile and every database row scoped to it inside the caller transaction."""
    pid = int(profile_id or 0)
    row = conn.execute("SELECT id,user_id,name,is_default FROM rtorrent_profiles WHERE id=?", (pid,)).fetchone()
    if not row:
        raise ValueError("Profile does not exist")

    affected_users = conn.execute(
        "SELECT user_id FROM user_preferences WHERE active_rtorrent_id=?",
        (pid,),
    ).fetchall()
    fallbacks = {
        int(item["user_id"]): _fallback_profile_for_user(conn, int(item["user_id"]), pid)
        for item in affected_users
    }

    def _purge() -> dict[str, object]:
        job_guard = _guard_profile_jobs(conn, pid)
        deleted = _delete_rows_by_column(conn, "profile_id", pid, exclude={"rtorrent_profiles"})
        _merge_counts(
            deleted,
            _delete_fk_children(conn, "rtorrent_profiles", "id", pid, exclude={"rtorrent_profiles"}),
        )
        app_settings_deleted = _delete_profile_app_settings(conn, pid)
        cur = conn.execute("DELETE FROM rtorrent_profiles WHERE id=?", (pid,))
        if int(cur.rowcount or 0) != 1:
            raise DeletionError("Profile could not be removed")

        if int(row.get("is_default") or 0):
            replacement = conn.execute(
                "SELECT id FROM rtorrent_profiles WHERE user_id=? ORDER BY is_default DESC, name COLLATE NOCASE, id LIMIT 1",
                (int(row["user_id"]),),
            ).fetchone()
            if replacement:
                conn.execute("UPDATE rtorrent_profiles SET is_default=0 WHERE user_id=?", (int(row["user_id"]),))
                conn.execute("UPDATE rtorrent_profiles SET is_default=1 WHERE id=?", (int(replacement["id"]),))

        now = conn.execute("SELECT CURRENT_TIMESTAMP AS value").fetchone()["value"]
        for uid, fallback_id in fallbacks.items():
            conn.execute(
                "UPDATE user_preferences SET active_rtorrent_id=?, updated_at=? WHERE user_id=? AND active_rtorrent_id=?",
                (fallback_id, now, uid, pid),
            )
        return {
            "profile_id": pid,
            "owner_user_id": int(row["user_id"]),
            "profile_name": row.get("name") or "",
            "deleted_rows": deleted,
            "deleted_app_settings": app_settings_deleted,
            "active_profile_fallbacks": fallbacks,
            "job_guard": job_guard,
        }

    try:
        return _run_savepoint(conn, f"purge_profile_{pid}", _purge)
    except sqlite3.IntegrityError as exc:
        raise DeletionError(
            "Profile is still referenced by dependent data. The deletion was rolled back."
        ) from exc


def purge_user(conn: sqlite3.Connection, user_id: int, reassign_profile_owner_to: int | None = None) -> dict[str, object]:
    """Delete a user while explicitly reassigning or cascading any profiles they own."""
    # Note: Account deletion no longer implies profile deletion; callers must provide a replacement owner or explicitly opt into the legacy cascade.
    uid = int(user_id or 0)
    row = conn.execute("SELECT id,username FROM users WHERE id=?", (uid,)).fetchone()
    if not row:
        raise ValueError("User does not exist")

    def _purge() -> dict[str, object]:
        _guard_user_jobs(conn, uid)
        profile_rows = conn.execute("SELECT id FROM rtorrent_profiles WHERE user_id=? ORDER BY id", (uid,)).fetchall()
        deleted_profiles: list[int] = []
        profile_results: list[dict[str, object]] = []
        reassigned_profiles: list[int] = []
        replacement_id = int(reassign_profile_owner_to or 0)
        if profile_rows and replacement_id:
            replacement = conn.execute("SELECT id FROM users WHERE id=? AND is_active=1", (replacement_id,)).fetchone()
            if not replacement:
                raise DeletionError("Replacement profile owner does not exist or is inactive")
            for profile_row in profile_rows:
                pid = int(profile_row["id"])
                conn.execute("UPDATE rtorrent_profiles SET user_id=?, is_default=0, updated_at=? WHERE id=?", (replacement_id, utcnow(), pid))
                reassigned_profiles.append(pid)
        elif profile_rows:
            raise DeletionError("User owns rTorrent profiles; reassign them before deleting the account")

        audit_references_cleared = _clear_user_audit_references(conn, uid)
        deleted = _delete_rows_by_column(conn, "user_id", uid, exclude={"users", "rtorrent_profiles"})
        _merge_counts(
            deleted,
            _delete_fk_children(conn, "users", "id", uid, exclude={"users", "rtorrent_profiles"}),
        )
        app_settings_deleted = _delete_user_app_settings(conn, uid)
        cur = conn.execute("DELETE FROM users WHERE id=?", (uid,))
        if int(cur.rowcount or 0) != 1:
            raise DeletionError("User could not be removed")
        return {
            "user_id": uid,
            "username": row.get("username") or "",
            "deleted_profiles": deleted_profiles,
            "reassigned_profiles": reassigned_profiles,
            "profile_results": profile_results,
            "deleted_rows": deleted,
            "audit_references_cleared": audit_references_cleared,
            "deleted_app_settings": app_settings_deleted,
        }

    try:
        return _run_savepoint(conn, f"purge_user_{uid}", _purge)
    except sqlite3.IntegrityError as exc:
        raise DeletionError(
            "User is still referenced by dependent data. The deletion was rolled back."
        ) from exc
