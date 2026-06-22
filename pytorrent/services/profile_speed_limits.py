from __future__ import annotations
from ..db import connect, utcnow


def normalize_limit(value: object) -> int:
    try:
        limit = int(float(value or 0))
    except (TypeError, ValueError):
        return 0
    return max(0, limit)


def get_limits(profile_id: int | None) -> dict:
    profile_id = int(profile_id or 0)
    if not profile_id:
        return {"down": 0, "up": 0, "configured": False}
    with connect() as conn:
        row = conn.execute("SELECT down_limit, up_limit FROM profile_speed_limits WHERE profile_id=?", (profile_id,)).fetchone()
    if not row:
        return {"down": 0, "up": 0, "configured": False}
    return {"down": int(row.get("down_limit") or 0), "up": int(row.get("up_limit") or 0), "configured": True}


def save_limits(profile_id: int, down: object, up: object) -> dict:
    profile_id = int(profile_id or 0)
    if not profile_id:
        raise ValueError("Missing profile id")
    clean = {"down": normalize_limit(down), "up": normalize_limit(up), "configured": True}
    now = utcnow()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO profile_speed_limits(profile_id, down_limit, up_limit, created_at, updated_at)
            VALUES(?,?,?,?,?)
            ON CONFLICT(profile_id) DO UPDATE SET
              down_limit=excluded.down_limit,
              up_limit=excluded.up_limit,
              updated_at=excluded.updated_at
            """,
            (profile_id, clean["down"], clean["up"], now, now),
        )
    return clean


def delete_limits(profile_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM profile_speed_limits WHERE profile_id=?", (int(profile_id or 0),))
