from __future__ import annotations
import json
import time
from datetime import datetime, timezone
from ..db import connect, utcnow, default_user_id
from . import auth, rtorrent
from .workers import enqueue


def _age_minutes_from_epoch(value) -> int:
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


def _record(user_id: int, profile_id: int, group: dict, torrent: dict, action: str, status: str, reason: str, details: dict | None = None) -> None:
    now = utcnow()
    with connect() as conn:
        conn.execute(
            "INSERT INTO ratio_history(user_id,profile_id,group_id,group_name,torrent_hash,torrent_name,action,status,reason,details_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (user_id, profile_id, group.get("id"), group.get("name"), torrent.get("hash"), torrent.get("name"), action, status, reason, json.dumps(details or {}), now),
        )
        conn.execute(
            "INSERT INTO ratio_assignments(profile_id,torrent_hash,group_id,group_name,applied_at,last_status,updated_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(profile_id,torrent_hash) DO UPDATE SET group_id=excluded.group_id,group_name=excluded.group_name,applied_at=excluded.applied_at,last_status=excluded.last_status,updated_at=excluded.updated_at",
            (profile_id, torrent.get("hash"), group.get("id"), group.get("name"), now if status == "applied" else None, status, now),
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
    seed_ok = _age_minutes_from_epoch(torrent.get("created")) >= seed_time if seed_time else True
    if not ratio_ok:
        return False, "ratio threshold not reached"
    if not seed_ok:
        return False, "minimum seed time not reached"
    min_upload = int(group.get("active_upload_min_bytes") or 1024)
    if int(group.get("ignore_active_upload") or 0) and int(torrent.get("up_rate") or 0) >= min_upload:
        return False, "active upload is above exception threshold"
    return True, "ratio rule applied"


def check(profile: dict, user_id: int | None = None) -> dict:
    viewer_user_id = user_id or default_user_id()
    profile_id = int(profile["id"])
    with connect() as conn:
        groups = conn.execute("SELECT * FROM ratio_groups WHERE profile_id=? AND enabled=1 ORDER BY lower(name), id", (profile_id,)).fetchall()
        already = {row["torrent_hash"] for row in conn.execute("SELECT torrent_hash FROM ratio_assignments WHERE profile_id=? AND last_status='applied'", (profile_id,)).fetchall()}
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
        owner_user_id = int(group.get("user_id") or viewer_user_id)
        if not auth.can_write_profile(profile_id, owner_user_id):
            skipped += 1
            _record(owner_user_id, profile_id, group, torrent, action, "skipped", "owner has no write access to profile")
            continue
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
        job_id = enqueue(api_action, profile_id, payload, user_id=owner_user_id)
        queued_jobs.append(job_id)
        applied += 1
        _record(owner_user_id, profile_id, group, torrent, action, "applied", reason, {"job_id": job_id, "api_action": api_action})
    return {"applied": applied, "skipped": skipped, "job_ids": queued_jobs}


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
                from .preferences import get_profile
                with connect() as conn:
                    profiles = conn.execute("SELECT DISTINCT profile_id FROM ratio_groups WHERE enabled=1 AND profile_id IS NOT NULL").fetchall()
                for row in profiles:
                    profile_id = int(row["profile_id"])
                    with connect() as conn:
                        owner = conn.execute("SELECT user_id FROM rtorrent_profiles WHERE id=?", (profile_id,)).fetchone()
                    owner_id = int(owner["user_id"] if owner and owner.get("user_id") else default_user_id())
                    profile = get_profile(profile_id, owner_id)
                    if not profile:
                        continue
                    # Note: Ratio rules are evaluated per profile owner, not the active browser user.
                    result = check(profile, user_id=owner_id)
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
