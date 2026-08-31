from __future__ import annotations
import json
import time
from datetime import datetime, timezone
from ..db import connect, utcnow, default_user_id
from . import auth, rtorrent
from .workers import prepare_jobs, insert_prepared_jobs, dispatch_prepared_jobs


def _age_minutes_from_epoch(value) -> int:
    # Note: Seed-age calculations consume the torrent completion timestamp, never its creation timestamp.
    try:
        created = datetime.fromtimestamp(int(value or 0), timezone.utc)
        return max(0, int((datetime.now(timezone.utc) - created).total_seconds() // 60))
    except Exception:
        return 0


def _is_private(profile: dict, torrent_hash: str) -> bool:
    try:
        value = rtorrent.client_for(profile).call("d.is_private", torrent_hash)
        return bool(int(value or 0))
    except Exception:
        return False


def _group_for_torrent(groups_by_name: dict[str, dict], torrent: dict) -> dict | None:
    name = str(torrent.get("ratio_group") or "").strip()
    return groups_by_name.get(name) if name else None


def _record(user_id: int, profile_id: int, group: dict, torrent: dict, action: str, status: str, reason: str, details: dict | None = None, *, actor_type: str = "user") -> None:
    """Record Ratio Rules history without advancing assignment success state implicitly."""
    # Note: Queueing and successful application are distinct states; only the worker completion callback marks an assignment applied.
    now = utcnow()
    with connect() as conn:
        conn.execute(
            "INSERT INTO ratio_history(user_id,profile_id,group_id,group_name,torrent_hash,torrent_name,action,status,reason,details_json,actor_type,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (user_id, profile_id, group.get("id"), group.get("name"), torrent.get("hash"), torrent.get("name"), action, status, reason, json.dumps(details or {}), "system" if actor_type == "system" else "user", now),
        )


def _should_apply(profile: dict, group: dict, torrent: dict) -> tuple[bool, str]:
    if not int(group.get("enabled") or 0):
        return False, "group disabled"
    if not torrent.get("complete"):
        return False, "torrent is not complete"
    if int(group.get("ignore_private") or 0) and _is_private(profile, torrent["hash"]):
        return False, "private torrent is excluded"
    min_ratio = float(group.get("min_ratio") or 0)
    max_ratio = float(group.get("max_ratio") or 0)
    wanted_ratio = max(min_ratio, max_ratio)
    seed_time = max(int(group.get("seed_time_minutes") or 0), int(group.get("min_seed_time_minutes") or 0))
    ratio_ok = float(torrent.get("ratio") or 0) >= wanted_ratio if wanted_ratio else True
    seed_ok = _age_minutes_from_epoch(torrent.get("completed_at")) >= seed_time if seed_time and int(torrent.get("completed_at") or 0) > 0 else not seed_time
    if not ratio_ok:
        return False, "ratio threshold not reached"
    if seed_time and int(torrent.get("completed_at") or 0) <= 0:
        return False, "seed start time is not available yet"
    if not seed_ok:
        return False, "minimum seed time not reached"
    min_upload = int(group.get("active_upload_min_bytes") or 1024)
    if int(group.get("ignore_active_upload") or 0) and int(torrent.get("up_rate") or 0) >= min_upload:
        return False, "active upload is above exception threshold"
    return True, "ratio rule applied"


def check(profile: dict, user_id: int | None = None, system_execution: bool = False) -> dict:
    """Evaluate ratio rules under user or trusted scheduler authority."""
    # Note: Manual checks require current RW access; scheduled checks persist as system jobs after configuration was authorized.
    executor_user_id = int(user_id or auth.current_user_id() or profile.get("user_id") or default_user_id())
    profile_id = int(profile["id"])
    if not system_execution and not auth.can_write_profile(profile_id, executor_user_id):
        raise PermissionError("No write access to profile")
    with connect() as conn:
        groups = conn.execute("SELECT * FROM ratio_groups WHERE profile_id=? AND enabled=1 ORDER BY lower(name), id", (profile_id,)).fetchall()
        already = {row["torrent_hash"] for row in conn.execute("SELECT torrent_hash FROM ratio_assignments WHERE profile_id=? AND (last_status='applied' OR pending_job_id IS NOT NULL)", (profile_id,)).fetchall()}
    groups_by_name: dict[str, dict] = {}
    for group in groups:
        groups_by_name.setdefault(str(group.get("name") or ""), group)
    applied = 0
    skipped = 0
    queued_jobs = []
    for torrent in rtorrent.list_torrents(profile):
        group = _group_for_torrent(groups_by_name, torrent)
        if not group:
            continue
        if torrent.get("hash") in already:
            skipped += 1
            continue
        ok, reason = _should_apply(profile, group, torrent)
        if not ok:
            skipped += 1
            with connect() as conn:
                conn.execute(
                    "INSERT INTO ratio_assignments(profile_id,torrent_hash,group_id,group_name,last_status,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(profile_id,torrent_hash) DO UPDATE SET group_id=excluded.group_id,group_name=excluded.group_name,last_status=excluded.last_status,updated_at=excluded.updated_at",
                    (profile_id, torrent.get("hash"), group.get("id"), group.get("name"), reason, utcnow()),
                )
            continue
        action = str(group.get("action") or "stop")
        payload = {"hashes": [torrent["hash"]], "source": "ratio", "job_context": {"source": "ratio", "rule_name": group.get("name"), "hash_count": 1}}
        if action == "remove_data":
            api_action = "remove"
            payload["remove_data"] = True
        elif action == "move":
            api_action = "move"
            payload.update({"path": group.get("move_path") or torrent.get("path") or "", "move_data": True, "recheck": False, "keep_seeding": False})
        elif action == "set_label":
            api_action = "set_label"
            payload["label"] = group.get("set_label") or group.get("name") or ""
        else:
            api_action = action if action in {"stop", "remove", "pause"} else "stop"
        payload["job_context"].update({"ratio_group_id": group.get("id"), "ratio_action": action, "torrent_name": torrent.get("name")})
        # Note: Persist the job, pending assignment and queued audit row in one transaction before dispatch so a fast worker cannot be overwritten back to queued.
        prepared = prepare_jobs([{
            "action_name": api_action,
            "profile_id": profile_id,
            "payload": payload,
            "user_id": executor_user_id,
            "system_managed": system_execution,
        }])
        job_id = str(prepared[0]["job_id"])
        now = utcnow()
        with connect() as conn:
            insert_prepared_jobs(conn, prepared)
            conn.execute(
                "INSERT INTO ratio_assignments(profile_id,torrent_hash,group_id,group_name,applied_at,last_status,pending_job_id,updated_at) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(profile_id,torrent_hash) DO UPDATE SET group_id=excluded.group_id,group_name=excluded.group_name,applied_at=NULL,last_status=excluded.last_status,pending_job_id=excluded.pending_job_id,updated_at=excluded.updated_at",
                (profile_id, torrent.get("hash"), group.get("id"), group.get("name"), None, "queued", job_id, now),
            )
            conn.execute(
                "INSERT INTO ratio_history(user_id,profile_id,group_id,group_name,torrent_hash,torrent_name,action,status,reason,details_json,actor_type,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (executor_user_id, profile_id, group.get("id"), group.get("name"), torrent.get("hash"), torrent.get("name"), action, "queued", reason, json.dumps({"job_id": job_id, "api_action": api_action}), "system" if system_execution else "user", now),
            )
        dispatch_prepared_jobs(prepared)
        queued_jobs.append(job_id)
        applied += 1
    return {"applied": applied, "skipped": skipped, "job_ids": queued_jobs}


def record_job_result(job_id: str, job: dict, payload: dict, success: bool, *, result: dict | None = None, error: str = "") -> None:
    """Finalize a queued Ratio Rules assignment from the worker terminal state."""
    # Note: A failed job clears pending_job_id and leaves applied_at empty, allowing the scheduler to retry on its next pass.
    ctx = (payload or {}).get("job_context") or {}
    if str(ctx.get("source") or "") != "ratio":
        return
    hashes = [str(value) for value in ((payload or {}).get("hashes") or []) if str(value or "").strip()]
    if not hashes:
        return
    profile_id = int((job or {}).get("profile_id") or 0)
    user_id = int((job or {}).get("user_id") or default_user_id())
    actor_type = "system" if bool(int((job or {}).get("system_managed") or 0)) else "user"
    group_id = int(ctx.get("ratio_group_id") or 0) or None
    group_name = str(ctx.get("rule_name") or "")
    action = str(ctx.get("ratio_action") or (job or {}).get("action") or "ratio")
    now = utcnow()
    with connect() as conn:
        for torrent_hash in hashes:
            row = conn.execute("SELECT pending_job_id FROM ratio_assignments WHERE profile_id=? AND torrent_hash=?", (profile_id, torrent_hash)).fetchone()
            if row and str(row.get("pending_job_id") or "") not in {"", str(job_id)}:
                continue
            conn.execute(
                "INSERT INTO ratio_assignments(profile_id,torrent_hash,group_id,group_name,applied_at,last_status,pending_job_id,updated_at) VALUES(?,?,?,?,?,?,NULL,?) ON CONFLICT(profile_id,torrent_hash) DO UPDATE SET group_id=excluded.group_id,group_name=excluded.group_name,applied_at=excluded.applied_at,last_status=excluded.last_status,pending_job_id=NULL,updated_at=excluded.updated_at",
                (profile_id, torrent_hash, group_id, group_name, now if success else None, "applied" if success else "failed", now),
            )
            conn.execute(
                "INSERT INTO ratio_history(user_id,profile_id,group_id,group_name,torrent_hash,torrent_name,action,status,reason,details_json,actor_type,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (user_id, profile_id, group_id, group_name, torrent_hash, str(ctx.get("torrent_name") or ""), action, "applied" if success else "failed", "job completed" if success else (error or "job failed"), json.dumps({"job_id": job_id, "result": result or {}}), actor_type, now),
            )


_scheduler_started = False


def start_scheduler(socketio=None) -> None:
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True

    def loop() -> None:
        # Note: Ratio rules are evaluated periodically and actions are executed through the existing safe job queue.
        while True:
            try:
                from .preferences import get_profile_for_system
                with connect() as conn:
                    profiles = conn.execute("SELECT DISTINCT profile_id FROM ratio_groups WHERE enabled=1 AND profile_id IS NOT NULL").fetchall()
                for row in profiles:
                    profile_id = int(row["profile_id"])
                    profile = get_profile_for_system(profile_id)
                    if not profile:
                        continue
                    # Note: The owner id is audit metadata only; scheduler authorization is bound to the saved profile configuration.
                    audit_user_id = int(profile.get("user_id") or default_user_id())
                    result = check(profile, user_id=audit_user_id, system_execution=True)
                    if socketio and result.get("applied"):
                        socketio.emit("ratio_rules_checked", {"profile_id": profile["id"], **result}, to=f"profile:{profile['id']}")
            except Exception:
                pass
            time.sleep(300)

    if socketio:
        socketio.start_background_task(loop)
    else:
        import threading
        threading.Thread(target=loop, daemon=True, name="pytorrent-ratio-scheduler").start()
