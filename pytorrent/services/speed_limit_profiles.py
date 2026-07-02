from __future__ import annotations

from ..db import connect, utcnow
from . import auth
from .profile_speed_limits import normalize_limit


def _user_id(user_id: int | None = None) -> int:
    return int(user_id or auth.current_user_id() or 1)


def _row_to_profile(row: dict) -> dict:
    return {
        "id": int(row.get("id") or 0),
        "name": str(row.get("name") or ""),
        "down": int(row.get("down_limit") or 0),
        "up": int(row.get("up_limit") or 0),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def list_profiles(user_id: int | None = None) -> list[dict]:
    uid = _user_id(user_id)
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, name, down_limit, up_limit, created_at, updated_at
            FROM speed_limit_profiles
            WHERE user_id=?
            ORDER BY lower(name), id
            """,
            (uid,),
        ).fetchall()
    return [_row_to_profile(row) for row in rows]


def save_profile(name: object, down: object, up: object, profile_id: int | None = None, user_id: int | None = None) -> dict:
    uid = _user_id(user_id)
    clean_name = str(name or "").strip()
    if not clean_name:
        raise ValueError("Profile name is required")
    if len(clean_name) > 80:
        raise ValueError("Profile name is too long")
    clean = {
        "name": clean_name,
        "down": normalize_limit(down),
        "up": normalize_limit(up),
    }
    now = utcnow()
    with connect() as conn:
        if profile_id:
            row = conn.execute(
                "SELECT id FROM speed_limit_profiles WHERE id=? AND user_id=?",
                (int(profile_id), uid),
            ).fetchone()
            if not row:
                raise ValueError("Speed profile not found")
            conn.execute(
                """
                UPDATE speed_limit_profiles
                SET name=?, down_limit=?, up_limit=?, updated_at=?
                WHERE id=? AND user_id=?
                """,
                (clean["name"], clean["down"], clean["up"], now, int(profile_id), uid),
            )
            saved_id = int(profile_id)
        else:
            cur = conn.execute(
                """
                INSERT INTO speed_limit_profiles(user_id, name, down_limit, up_limit, created_at, updated_at)
                VALUES(?,?,?,?,?,?)
                """,
                (uid, clean["name"], clean["down"], clean["up"], now, now),
            )
            saved_id = int(cur.lastrowid)
        row = conn.execute(
            "SELECT id, name, down_limit, up_limit, created_at, updated_at FROM speed_limit_profiles WHERE id=? AND user_id=?",
            (saved_id, uid),
        ).fetchone()
    return _row_to_profile(row)


def delete_profile(profile_id: int, user_id: int | None = None) -> None:
    uid = _user_id(user_id)
    with connect() as conn:
        conn.execute("DELETE FROM speed_limit_profiles WHERE id=? AND user_id=?", (int(profile_id), uid))
