from __future__ import annotations
from ..db import connect, utcnow


def normalize_limit(value: object) -> int:
    try:
        limit = int(float(value or 0))
    except (TypeError, ValueError):
        return 0
    return max(0, limit)


def _get_limits_conn(conn, profile_id: int) -> dict:
    row = conn.execute("SELECT down_limit, up_limit FROM profile_speed_limits WHERE profile_id=?", (int(profile_id),)).fetchone()
    if not row:
        return {"down": 0, "up": 0, "configured": False}
    return {"down": int(row.get("down_limit") or 0), "up": int(row.get("up_limit") or 0), "configured": True}


def get_limits(profile_id: int | None) -> dict:
    profile_id = int(profile_id or 0)
    if not profile_id:
        return {"down": 0, "up": 0, "configured": False}
    with connect() as conn:
        return _get_limits_conn(conn, profile_id)


def _save_limits_conn(conn, profile_id: int, down: object, up: object) -> dict:
    profile_id = int(profile_id or 0)
    if not profile_id:
        raise ValueError("Missing profile id")
    clean = {"down": normalize_limit(down), "up": normalize_limit(up), "configured": True}
    now = utcnow()
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


def save_limits(profile_id: int, down: object, up: object) -> dict:
    with connect() as conn:
        return _save_limits_conn(conn, int(profile_id or 0), down, up)


def queue_limits(profile_id: int, down: object, up: object, user_id: int | None = None) -> dict:
    """Store desired limits and their worker job in one SQLite transaction."""
    from . import workers

    pid = int(profile_id or 0)
    if not pid:
        raise ValueError("Missing profile id")
    requested = {"down": normalize_limit(down), "up": normalize_limit(up), "configured": True}
    prepared = workers.prepare_jobs([{
        "action_name": "set_limits",
        "profile_id": pid,
        "user_id": user_id,
        "payload": {"down": requested["down"], "up": requested["up"]},
    }])
    with connect() as conn:
        previous = _get_limits_conn(conn, pid)
        prepared[0]["payload"]["previous_limits"] = previous
        prepared[0]["payload"]["requested_limits"] = requested
        clean = _save_limits_conn(conn, pid, requested["down"], requested["up"])
        workers.insert_prepared_jobs(conn, prepared)
    workers.dispatch_prepared_jobs(prepared)
    return {"limits": clean, "job_id": str(prepared[0]["job_id"])}


def restore_limits_if_current(profile_id: int, requested: dict | None, previous: dict | None) -> bool:
    """Revert a failed set-limits job only if no newer limit change replaced it."""
    pid = int(profile_id or 0)
    if not pid or not isinstance(requested, dict) or not isinstance(previous, dict):
        return False
    expected_down = normalize_limit(requested.get("down"))
    expected_up = normalize_limit(requested.get("up"))
    with connect() as conn:
        current = _get_limits_conn(conn, pid)
        if not current.get("configured") or current["down"] != expected_down or current["up"] != expected_up:
            return False
        if previous.get("configured"):
            _save_limits_conn(conn, pid, previous.get("down"), previous.get("up"))
        else:
            conn.execute("DELETE FROM profile_speed_limits WHERE profile_id=?", (pid,))
    return True


def delete_limits(profile_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM profile_speed_limits WHERE profile_id=?", (int(profile_id or 0),))
