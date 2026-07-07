from __future__ import annotations
import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from . import rtorrent, auth, disk_guard, operation_logs
from .preferences import get_profile
from ..config import WORKERS
from ..db import connect, utcnow, default_user_id
from .torrent_cache import torrent_cache
from .torrent_summary import cached_summary

LIGHT_ACTIONS = {"start", "stop", "pause", "resume", "unpause", "set_label", "set_ratio_group", "reannounce", "set_limits"}
WATCHDOG_INTERVAL_SECONDS = 30

_heavy_executor = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="pytorrent-heavy-job")
_light_executor = ThreadPoolExecutor(max_workers=max(4, min(WORKERS, 16)), thread_name_prefix="pytorrent-light-job")
_socketio = None
_heavy_semaphores: dict[int, tuple[int, threading.Semaphore]] = {}
_light_semaphores: dict[int, tuple[int, threading.Semaphore]] = {}
_exclusive_locks: dict[int, threading.Lock] = {}
_active_runners: set[str] = set()
_sem_lock = threading.Lock()
_runner_lock = threading.Lock()
_watchdog_started = False
_watchdog_lock = threading.Lock()
_disk_refresh_delays = (30, 90)
_disk_refresh_min_immediate_seconds = 5
_disk_refresh_lock = threading.Lock()
_disk_refresh_timers: dict[tuple[int, int], threading.Timer] = {}
_disk_refresh_last_immediate: dict[int, float] = {}


def set_socketio(socketio):
    global _socketio
    _socketio = socketio


def _emit(name: str, payload: dict):
    if not _socketio:
        return
    profile_id = payload.get("profile_id")
    if profile_id:
        _socketio.emit(name, payload, to=f"profile:{int(profile_id)}")
    else:
        _socketio.emit(name, payload)


def _bounded_int(value, default: int, minimum: int = 1) -> int:
    try:
        parsed = int(value if value is not None else default)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, parsed)


def _is_light_action(action_name: str) -> bool:
    return str(action_name or "") in LIGHT_ACTIONS


def _profile_heavy_limit(profile: dict) -> int:
    return _bounded_int(profile.get("max_parallel_jobs"), 5)


def _profile_light_limit(profile: dict) -> int:
    return _bounded_int(profile.get("light_parallel_jobs"), 4)


def _get_sem(profile: dict, light: bool = False) -> threading.Semaphore:
    profile_id = int(profile["id"])
    limit = _profile_light_limit(profile) if light else _profile_heavy_limit(profile)
    registry = _light_semaphores if light else _heavy_semaphores
    with _sem_lock:
        current = registry.get(profile_id)
        if not current or current[0] != limit:
            registry[profile_id] = (limit, threading.Semaphore(limit))
        return registry[profile_id][1]


def _get_exclusive_lock(profile_id: int) -> threading.Lock:
    with _sem_lock:
        if profile_id not in _exclusive_locks:
            _exclusive_locks[profile_id] = threading.Lock()
        return _exclusive_locks[profile_id]


def _job_row(job_id: str):
    with connect() as conn:
        return conn.execute("SELECT rowid AS _rowid, * FROM jobs WHERE id=?", (job_id,)).fetchone()


def _job_payload(row) -> dict:
    try:
        return json.loads((row or {}).get("payload_json") or "{}")
    except Exception:
        return {}


def _is_ordered_job(row) -> bool:
    payload = _job_payload(row)
    action = str((row or {}).get("action") or "")
    return action in {"move", "remove", "profile_transfer", "add_magnet", "add_torrent_raw"} or bool(payload.get("requires_order"))


def _is_priority_job(row) -> bool:
    payload = _job_payload(row)
    return bool(payload.get('priority_job') or payload.get('force_job')) or str((row or {}).get('action') or '') == 'set_limits'


def _is_light_job(row) -> bool:
    return _is_light_action(str((row or {}).get("action") or ""))


def _ordered_profile_ids(row) -> set[int]:
    """Return every profile touched by an ordered job."""
    # Note: Profile-transfer jobs touch both source and target profiles, so they must be ordered across both sides.
    ids: set[int] = set()
    try:
        profile_id = int((row or {}).get("profile_id") or 0)
        if profile_id:
            ids.add(profile_id)
    except Exception:
        pass
    try:
        payload = _job_payload(row)
        target_id = int(payload.get("target_profile_id") or 0)
        if str((row or {}).get("action") or "") == "profile_transfer" and target_id:
            ids.add(target_id)
    except Exception:
        pass
    return ids


def _ordered_locks_for(row) -> list[threading.Lock]:
    """Acquire locks in stable order to avoid deadlocks between cross-profile jobs."""
    return [_get_exclusive_lock(profile_id) for profile_id in sorted(_ordered_profile_ids(row))]


def _has_prior_ordered_jobs(profile_ids: set[int], rowid: int) -> bool:
    if not profile_ids:
        return False
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT rowid AS _rowid, profile_id, action, payload_json
            FROM jobs
            WHERE rowid<?
              AND status IN ('pending', 'running')
            ORDER BY rowid
            """,
            (rowid,),
        ).fetchall()
    for row in rows:
        if not _is_ordered_job(row) or _is_priority_job(row):
            continue
        if profile_ids.intersection(_ordered_profile_ids(row)):
            return True
    return False


def _wait_for_prior_ordered_jobs(job_id: str, profile_ids: set[int], rowid: int) -> bool:
    while _has_prior_ordered_jobs(profile_ids, rowid):
        fresh = _job_row(job_id)
        if not fresh or fresh["status"] == "cancelled":
            return False
        if _is_priority_job(fresh):
            return True
        time.sleep(0.5)
    return True


def _set_job(job_id: str, status: str, error: str = "", result: dict | None = None, started: bool = False, finished: bool = False):
    now = utcnow()
    fields = ["status=?", "error=?", "updated_at=?"]
    values: list = [status, error, now]
    if result is not None:
        fields.append("result_json=?")
        values.append(json.dumps(result))
    if started:
        fields.append("started_at=?")
        values.append(now)
    if finished:
        fields.append("finished_at=?")
        values.append(now)
    values.append(job_id)
    with connect() as conn:
        conn.execute(f"UPDATE jobs SET {', '.join(fields)} WHERE id=?", values)


def _job_state(row) -> dict:
    try:
        return json.loads((row or {}).get("state_json") or "{}")
    except Exception:
        return {}


def _checkpoint_job(job_id: str, state: dict, progress_current: int | None = None, progress_total: int | None = None) -> None:
    now = utcnow()
    fields = ["state_json=?", "heartbeat_at=?", "updated_at=?"]
    values: list = [json.dumps(state), now, now]
    if progress_current is not None:
        fields.append("progress_current=?")
        values.append(int(progress_current))
    if progress_total is not None:
        fields.append("progress_total=?")
        values.append(int(progress_total))
    values.append(job_id)
    with connect() as conn:
        conn.execute(f"UPDATE jobs SET {', '.join(fields)} WHERE id=? AND status='running'", values)


def _submit_job(job_id: str, action_name: str | None = None):
    if action_name is None:
        row = _job_row(job_id)
        action_name = str((row or {}).get("action") or "")
    executor = _light_executor if _is_light_action(str(action_name or "")) else _heavy_executor
    executor.submit(_run, job_id)


def enqueue(action_name: str, profile_id: int, payload: dict, user_id: int | None = None, max_attempts: int = 2, force: bool = False) -> str:
    user_id = user_id or auth.current_user_id() or default_user_id()
    job_id = uuid.uuid4().hex
    if force:
        payload = dict(payload or {})
        payload['force_job'] = True
        payload['priority_job'] = True
    now = utcnow()
    progress_total = len((payload or {}).get("hashes") or [])
    with connect() as conn:
        conn.execute(
            "INSERT INTO jobs(id,user_id,profile_id,action,payload_json,status,attempts,max_attempts,progress_total,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (job_id, user_id, profile_id, action_name, json.dumps(payload), "pending", 0, max_attempts, progress_total, now, now),
        )
    operation_logs.record_job_event(profile_id, action_name, "queued", payload, job_id=job_id, user_id=user_id)
    _emit("job_update", {"id": job_id, "action": action_name, "profile_id": profile_id, "status": "pending"})
    _submit_job(job_id, action_name)
    return job_id


def _job_event_meta(payload: dict) -> dict:
    ctx = payload.get("job_context") or {}
    source = str(ctx.get("source") or payload.get("source") or "user")
    meta = {"source": source}
    if source == "automation":
        meta["automation"] = True
        meta["source_label"] = str(ctx.get("rule_name") or "automation")
        if ctx.get("rule_id") is not None:
            meta["rule_id"] = ctx.get("rule_id")
    return meta



def _remove_job_deletes_data(action_name: str, payload: dict, result: dict | None = None) -> bool:
    # Note: Disk usage refreshes only when a remove job actually requested data deletion.
    if str(action_name or "") != "remove":
        return False
    if bool((payload or {}).get("remove_data")):
        return True
    ctx = (payload or {}).get("job_context") or {}
    return bool(ctx.get("remove_data") or (result or {}).get("remove_data"))


def _clear_disk_refresh_cache(profile_id: int) -> None:
    try:
        rtorrent.clear_profile_runtime_caches(int(profile_id))
    except Exception:
        pass


def _emit_profile_disk_refresh(profile_id: int, reason: str, hash_count: int = 0, delay_seconds: int = 0) -> None:
    _clear_disk_refresh_cache(profile_id)
    _emit("disk_refresh_requested", {
        "profile_id": int(profile_id),
        "hash_count": int(hash_count or 0),
        "reason": reason,
        "delay_seconds": int(delay_seconds or 0),
    })


def _run_delayed_disk_refresh(profile_id: int, delay_seconds: int) -> None:
    key = (int(profile_id), int(delay_seconds))
    try:
        _emit_profile_disk_refresh(profile_id, "remove_data_settled", delay_seconds=delay_seconds)
    finally:
        with _disk_refresh_lock:
            current = _disk_refresh_timers.get(key)
            if current is threading.current_thread():
                _disk_refresh_timers.pop(key, None)


def _schedule_profile_disk_refresh(profile_id: int, hash_count: int = 0) -> None:
    profile_id = int(profile_id)
    now = time.monotonic()
    emit_immediately = False
    timers_to_start: list[threading.Timer] = []
    with _disk_refresh_lock:
        last_immediate = float(_disk_refresh_last_immediate.get(profile_id) or 0)
        if now - last_immediate >= _disk_refresh_min_immediate_seconds:
            _disk_refresh_last_immediate[profile_id] = now
            emit_immediately = True
        for delay_seconds in _disk_refresh_delays:
            key = (profile_id, int(delay_seconds))
            old_timer = _disk_refresh_timers.get(key)
            if old_timer:
                old_timer.cancel()
            timer = threading.Timer(float(delay_seconds), _run_delayed_disk_refresh, args=(profile_id, int(delay_seconds)))
            timer.daemon = True
            _disk_refresh_timers[key] = timer
            timers_to_start.append(timer)
    if emit_immediately:
        _emit_profile_disk_refresh(profile_id, "remove_data_done", hash_count=hash_count, delay_seconds=0)
    for timer in timers_to_start:
        timer.start()


def _emit_disk_refresh_requested(profile_id: int, action_name: str, payload: dict, result: dict | None = None) -> None:
    if not _remove_job_deletes_data(action_name, payload, result):
        return
    _schedule_profile_disk_refresh(int(profile_id), len((payload or {}).get("hashes") or []))

def _execute(profile: dict, action_name: str, payload: dict, user_id: int | None = None):
    def checkpoint(next_state: dict, current: int, total: int):
        # Note: Checkpoint is defined before every action branch so profile-transfer jobs can resume safely.
        job_id = payload.get("__job_id")
        if job_id:
            _checkpoint_job(str(job_id), next_state, current, total)

    if action_name == "smart_queue_check":
        from . import smart_queue
        return smart_queue.check(profile, user_id=user_id or default_user_id(), force=True)
    if action_name == "add_magnet":
        if bool(payload.get("start", True)):
            disk_guard.assert_can_start_download(profile)
        return rtorrent.add_magnet(profile, payload["uri"], bool(payload.get("start", True)), str(payload.get("directory") or ""), str(payload.get("label") or ""))
    if action_name == "add_torrent_raw":
        import base64
        raw = base64.b64decode(payload["data_b64"])
        if bool(payload.get("start", True)):
            disk_guard.assert_can_start_download(profile)
        return rtorrent.add_torrent_raw(profile, raw, bool(payload.get("start", True)), str(payload.get("directory") or ""), str(payload.get("label") or ""), payload.get("file_priorities") or None)
    if action_name == "profile_transfer":
        # Note: Target profile is resolved inside the worker with the original user's permissions, not trusted from the request payload.
        target_profile = get_profile(int(payload.get("target_profile_id") or 0), user_id or default_user_id())
        if not target_profile:
            raise ValueError("Target profile does not exist or is not accessible")
        return rtorrent.transfer_profile(profile, target_profile, payload.get("hashes") or [], payload, checkpoint=checkpoint, resume_state=payload.get("__resume_state") or {})
    if action_name == "set_limits":
        return rtorrent.set_limits(profile, payload.get("down"), payload.get("up"))
    hashes = payload.get("hashes") or []
    if action_name in {"start", "resume", "unpause"}:
        disk_guard.assert_can_start_download(profile)
    state = payload.get("__resume_state") or {}

    return rtorrent.action(profile, hashes, action_name, payload, checkpoint=checkpoint, resume_state=state)


def _claim_runner(job_id: str) -> bool:
    with _runner_lock:
        if job_id in _active_runners:
            return False
        _active_runners.add(job_id)
        return True


def _release_runner(job_id: str) -> None:
    with _runner_lock:
        _active_runners.discard(job_id)


def _mark_running(job_id: str, attempts: int) -> bool:
    now = utcnow()
    with connect() as conn:
        cur = conn.execute(
            "UPDATE jobs SET status='running', attempts=?, started_at=COALESCE(started_at, ?), updated_at=? WHERE id=? AND status='pending'",
            (attempts, now, now, job_id),
        )
        return int(cur.rowcount or 0) == 1


def _emit_torrent_refresh(profile: dict, action_name: str) -> None:
    if action_name not in {"add_magnet", "add_torrent_raw", "remove", "move", "profile_transfer", "start", "stop", "pause", "resume", "unpause", "set_label", "set_ratio_group", "recheck", "recreate_files"}:
        return
    try:
        diff = torrent_cache.refresh(profile)
        profile_id = int(profile["id"])
        if diff.get("ok"):
            rows = torrent_cache.snapshot(profile_id)
            _emit("torrent_patch", {**diff, "profile_id": profile_id, "summary": cached_summary(profile_id, rows, force=True)})
        else:
            _emit("rtorrent_error", {**diff, "profile_id": profile_id})
    except Exception as exc:
        _emit("rtorrent_error", {"profile_id": int(profile.get("id") or 0), "error": str(exc)})


def _schedule_delayed_torrent_refresh(profile: dict, action_name: str) -> None:
    if action_name not in {"start", "stop", "pause", "resume", "unpause", "recheck", "recreate_files"} or not _socketio:
        return

    def delayed_refresh():
        sleep_fn = getattr(_socketio, "sleep", time.sleep)
        for delay in (0.75, 1.75, 4.0):
            sleep_fn(delay)
            _emit_torrent_refresh(profile, action_name)

    _socketio.start_background_task(delayed_refresh)


def _run(job_id: str):
    if not _claim_runner(job_id):
        return
    sem = None
    ordered_locks: list[threading.Lock] = []
    job = {}
    payload = {}
    try:
        job = _job_row(job_id)
        if not job or job["status"] == "cancelled":
            return
        profile = get_profile(int(job["profile_id"]), int(job["user_id"]))
        if not profile:
            _set_job(job_id, "failed", "rTorrent profile does not exist", finished=True)
            operation_logs.record_worker_event(int(job.get("profile_id") or 0), str(job.get("action") or ""), "failed", "Job failed: rTorrent profile does not exist", job_id=job_id, user_id=int(job.get("user_id") or 0), error="profile not found")
            _emit("job_update", {"id": job_id, "profile_id": job.get("profile_id"), "status": "failed", "error": "profile not found"})
            return
        profile_id = int(profile["id"])
        if _is_ordered_job(job) and not _is_priority_job(job):
            involved_profile_ids = _ordered_profile_ids(job)
            if not _wait_for_prior_ordered_jobs(job_id, involved_profile_ids, int(job["_rowid"])):
                return
            ordered_locks = _ordered_locks_for(job)
            for lock in ordered_locks:
                lock.acquire()
        sem = _get_sem(profile, light=_is_light_job(job))
        sem.acquire()
        job = _job_row(job_id)
        if not job or job["status"] == "cancelled":
            return
        payload = json.loads(job.get("payload_json") or "{}")
        payload["__job_id"] = job_id
        payload["__resume_state"] = _job_state(job)
        attempts = int(job.get("attempts") or 0) + 1
        if not _mark_running(job_id, attempts):
            return
        event_meta = _job_event_meta(payload)
        operation_logs.record_job_event(profile["id"], job["action"], "started", payload, job_id=job_id, user_id=int(job.get("user_id") or 0))
        _emit("operation_started", {"job_id": job_id, "action": job["action"], "profile_id": profile["id"], "hashes": payload.get("hashes") or [], "hash_count": len(payload.get("hashes") or []), "bulk": len(payload.get("hashes") or []) > 1, **event_meta})
        _emit("job_update", {"id": job_id, "profile_id": profile["id"], "status": "running", "attempts": attempts})
        result = _execute(profile, job["action"], payload, user_id=int(job.get("user_id") or 0))
        fresh = _job_row(job_id)
        if fresh and fresh["status"] != "running":
            return
        _set_job(job_id, "done", result=result, finished=True)
        operation_logs.record_job_event(profile["id"], job["action"], "done", payload, result=result or {}, job_id=job_id, user_id=int(job.get("user_id") or 0))
        _emit("operation_finished", {"job_id": job_id, "action": job["action"], "profile_id": profile["id"], "hashes": payload.get("hashes") or [], "hash_count": len(payload.get("hashes") or []), "bulk": len(payload.get("hashes") or []) > 1, "result": result, **event_meta})
        action_name = str(job["action"] or "")
        _emit_disk_refresh_requested(int(profile["id"]), action_name, payload, result or {})
        _emit_torrent_refresh(profile, action_name)
        if action_name == "profile_transfer":
            # Note: Refresh the destination profile cache as well so users see transferred torrents immediately after switching.
            try:
                target_profile = get_profile(int(payload.get("target_profile_id") or 0), int(job.get("user_id") or 0))
                if target_profile:
                    _emit_torrent_refresh(target_profile, action_name)
            except Exception:
                pass
        _schedule_delayed_torrent_refresh(profile, action_name)
        _emit("job_update", {"id": job_id, "profile_id": profile["id"], "status": "done", "result": result})
    except Exception as exc:
        fresh = _job_row(job_id) or {}
        attempts = int(fresh.get("attempts") or 1)
        max_attempts = int(fresh.get("max_attempts") or 2)
        # Note: Emergency cancel keeps an exception from a cancelled job from moving it back to retry or failed.
        if fresh and fresh.get("status") != "running":
            return
        status = "pending" if attempts < max_attempts else "failed"
        _set_job(job_id, status, str(exc), finished=(status == "failed"))
        if status == "failed":
            operation_logs.record_job_event(int(job.get("profile_id") or 0), job.get("action"), "failed", payload, error=str(exc), job_id=job_id, user_id=int(job.get("user_id") or 0))
        else:
            # Note: Retried attempts are logged explicitly so transient failures are not lost between final states.
            operation_logs.record_job_event(int(job.get("profile_id") or 0), job.get("action"), "retry", payload, error=str(exc), job_id=job_id, user_id=int(job.get("user_id") or 0))
        _emit("operation_failed", {"job_id": job_id, "action": job.get("action"), "profile_id": job.get("profile_id"), "hashes": payload.get("hashes") or [], "error": str(exc), **_job_event_meta(payload)})
        _emit("job_update", {"id": job_id, "profile_id": job.get("profile_id"), "status": status, "error": str(exc), "attempts": attempts})
        if status == "pending":
            _submit_job(job_id, job.get("action"))
    finally:
        if sem:
            sem.release()
        for lock in reversed(ordered_locks):
            lock.release()
        _release_runner(job_id)



def _parse_ts(value: str | None) -> float | None:
    if not value:
        return None
    try:
        from datetime import datetime
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _job_timeout_seconds(profile: dict, row) -> int:
    key = "light_job_timeout_seconds" if _is_light_job(row) else "heavy_job_timeout_seconds"
    default = 300 if _is_light_job(row) else 7200
    return _bounded_int(profile.get(key), default, 30)


def _pending_timeout_seconds(profile: dict) -> int:
    return _bounded_int(profile.get("pending_job_timeout_seconds"), 900, 60)


def _timeout_running_jobs() -> None:
    now_ts = time.time()
    with connect() as conn:
        rows = conn.execute("SELECT id,user_id,profile_id,action,started_at FROM jobs WHERE status='running'").fetchall()
    for row in rows:
        profile = get_profile(int(row["profile_id"]), int(row["user_id"]))
        if not profile:
            continue
        started_ts = _parse_ts(row.get("started_at"))
        if started_ts is None or now_ts - started_ts < _job_timeout_seconds(profile, row):
            continue
        message = f"Watchdog timeout after {_job_timeout_seconds(profile, row)} seconds"
        _set_job(row["id"], "failed", message, finished=True)
        operation_logs.record_worker_event(int(row.get("profile_id") or 0), str(row.get("action") or ""), "timeout", message, job_id=row["id"], user_id=int(row.get("user_id") or 0), error=message)
        _emit("operation_failed", {"job_id": row["id"], "action": row.get("action"), "profile_id": row.get("profile_id"), "hashes": [], "error": message, "source": "watchdog"})
        _emit("job_update", {"id": row["id"], "profile_id": row.get("profile_id"), "status": "failed", "error": message})


def _resubmit_interrupted_running_jobs() -> None:
    now_ts = time.time()
    with connect() as conn:
        rows = conn.execute("SELECT id,user_id,profile_id,action,heartbeat_at,updated_at FROM jobs WHERE status='running'").fetchall()
    for row in rows:
        with _runner_lock:
            active = row["id"] in _active_runners
        if active:
            continue
        profile = get_profile(int(row["profile_id"]), int(row["user_id"]))
        if not profile:
            continue
        last_seen_ts = _parse_ts(row.get("heartbeat_at") or row.get("updated_at"))

        if last_seen_ts is not None and now_ts - last_seen_ts < 90:
            continue
        with connect() as conn:
            cur = conn.execute(
                "UPDATE jobs SET status='pending', error=?, updated_at=? WHERE id=? AND status='running'",
                ("Resuming interrupted job from last checkpoint", utcnow(), row["id"]),
            )
        if int(cur.rowcount or 0):
            operation_logs.record_worker_event(int(row.get("profile_id") or 0), str(row.get("action") or ""), "resubmitted", "Interrupted job resubmitted from checkpoint", job_id=row["id"], user_id=int(row.get("user_id") or 0))
            _emit("job_update", {"id": row["id"], "profile_id": row.get("profile_id"), "status": "pending", "resumed": True})
            _submit_job(row["id"], row.get("action"))


def _resubmit_stale_pending_jobs() -> None:
    now_ts = time.time()
    with connect() as conn:
        rows = conn.execute("SELECT id,user_id,profile_id,action,updated_at FROM jobs WHERE status='pending'").fetchall()
    for row in rows:
        with _runner_lock:
            active = row["id"] in _active_runners
        if active:
            continue
        profile = get_profile(int(row["profile_id"]), int(row["user_id"]))
        if not profile:
            continue
        updated_ts = _parse_ts(row.get("updated_at"))
        if updated_ts is None or now_ts - updated_ts < _pending_timeout_seconds(profile):
            continue
        with connect() as conn:
            conn.execute("UPDATE jobs SET error=?, updated_at=? WHERE id=? AND status='pending'", ("Watchdog resubmitted stale pending job", utcnow(), row["id"]))
        operation_logs.record_worker_event(int(row.get("profile_id") or 0), str(row.get("action") or ""), "resubmitted", "Stale pending job resubmitted by watchdog", job_id=row["id"], user_id=int(row.get("user_id") or 0))
        _emit("job_update", {"id": row["id"], "profile_id": row.get("profile_id"), "status": "pending", "watchdog": True})
        _submit_job(row["id"], row.get("action"))


def _watchdog_loop() -> None:
    while True:
        try:
            _resubmit_interrupted_running_jobs()
            _timeout_running_jobs()
            _resubmit_stale_pending_jobs()
        except Exception:
            pass
        time.sleep(WATCHDOG_INTERVAL_SECONDS)


def start_watchdog() -> None:
    global _watchdog_started
    with _watchdog_lock:
        if _watchdog_started:
            return
        _watchdog_started = True
        thread = threading.Thread(target=_watchdog_loop, name="pytorrent-job-watchdog", daemon=True)
        thread.start()


def _safe_json(value, fallback):
    try:
        return json.loads(value or "")
    except Exception:
        return fallback


def _job_skipped_items(result: dict) -> list[dict]:
    """Return per-torrent items skipped during a job, including broken info-hashes."""
    rows = result.get("results") if isinstance((result or {}).get("results"), list) else []
    return [item for item in rows if isinstance(item, dict) and item.get("skipped")]


def _job_summary(row: dict, payload: dict, result: dict) -> str:
    ctx = payload.get("job_context") or {}
    count = int(ctx.get("hash_count") or len(payload.get("hashes") or []) or result.get("count") or 0)
    skipped_items = _job_skipped_items(result)
    parts = []
    if ctx.get("bulk_label"):
        parts.append(f"{ctx.get('bulk_label')} of {ctx.get('bulk_parts')}")
    if count:
        parts.append(("bulk " if count > 1 else "single ") + f"{count} torrent(s)")
    if ctx.get("target_path"):
        parts.append(f"target: {ctx.get('target_path')}")
    if ctx.get("remove_data"):
        parts.append("remove data")
    if ctx.get("move_data"):
        parts.append("move data")
    if result.get("count") is not None:
        parts.append(f"done: {result.get('count')}")
    if skipped_items:
        parts.append(f"skipped broken hash: {len(skipped_items)}")
    if result.get("errors"):
        parts.append(f"errors: {len(result.get('errors') or [])}")
    return "; ".join(parts)


def _public_job(row) -> dict:
    d = dict(row)
    payload = _safe_json(d.get("payload_json"), {})
    result = _safe_json(d.get("result_json"), {})
    ctx = payload.get("job_context") or {}
    d["payload"] = payload
    state = _safe_json(d.get("state_json"), {})
    d["result"] = result
    d["state"] = state
    d["progress_current"] = int(d.get("progress_current") or len(state.get("completed_hashes") or []))
    d["progress_total"] = int(d.get("progress_total") or len(payload.get("hashes") or []) or result.get("count") or 0)
    d["hash_count"] = int(ctx.get("hash_count") or len(payload.get("hashes") or []) or result.get("count") or 0)
    d["is_bulk"] = bool(ctx.get("bulk") or d["hash_count"] > 1)
    d["summary"] = _job_summary(d, payload, result)
    d["skipped_items"] = _job_skipped_items(result)
    d["source"] = str(ctx.get("source") or "user")
    d["source_label"] = str(ctx.get("rule_name") or ctx.get("source") or "user")
    d["is_forced"] = bool(payload.get("force_job") or payload.get("priority_job"))
    items = ctx.get("items") or []
    if d["is_bulk"]:
        d["items_preview"] = ""
    else:
        d["items_preview"] = ", ".join([str((x or {}).get("name") or (x or {}).get("hash") or "") for x in items[:1] if x])
    return d


def _job_scope_sql(writable: bool = False, profile_id: int | None = None) -> tuple[str, tuple]:
    clauses: list[str] = []
    params: list[int] = []
    visible = auth.writable_profile_ids() if writable else auth.visible_profile_ids()
    if visible is not None:
        if not visible:
            return " WHERE 1=0", ()
        placeholders = ",".join("?" for _ in visible)
        clauses.append(f"profile_id IN ({placeholders})")
        params.extend(int(pid) for pid in visible)
    if profile_id is not None:
        clauses.append("profile_id=?")
        params.append(int(profile_id))
    if not clauses:
        return "", ()
    return " WHERE " + " AND ".join(clauses), tuple(params)


def list_jobs(limit: int = 200, offset: int = 0, profile_id: int | None = None):
    limit = max(1, min(int(limit or 50), 500))
    offset = max(0, int(offset or 0))
    where, params = _job_scope_sql(profile_id=profile_id)
    with connect() as conn:
        rows = conn.execute(f"SELECT * FROM jobs{where} ORDER BY created_at DESC LIMIT ? OFFSET ?", (*params, limit, offset)).fetchall()
        total = conn.execute(f"SELECT COUNT(*) AS n FROM jobs{where}", params).fetchone()["n"]
    return {"rows": [_public_job(r) for r in rows], "total": total, "limit": limit, "offset": offset, "profile_id": profile_id}


def cancel_job(job_id: str) -> bool:
    row = _job_row(job_id)
    if not row or row["status"] not in {"pending", "running"}:
        return False
    _set_job(job_id, "cancelled", finished=True)
    payload = _job_payload(row)
    operation_logs.record_job_event(int(row.get("profile_id") or 0), row.get("action"), "cancelled", payload, error="Cancelled by user", job_id=job_id, user_id=int(row.get("user_id") or 0))
    _emit("job_update", {"id": job_id, "profile_id": row.get("profile_id"), "status": "cancelled"})
    return True


def clear_jobs(profile_id: int | None = None) -> int:
    where, params = _job_scope_sql(writable=True, profile_id=profile_id)
    status_clause = "status NOT IN ('pending', 'running')"
    # Note: Job cleanup is profile-scoped by default at the API layer; profile_id=None preserves admin/global maintenance callers.
    sql = f"DELETE FROM jobs{where} AND {status_clause}" if where else f"DELETE FROM jobs WHERE {status_clause}"
    with connect() as conn:
        cur = conn.execute(sql, params)
        return int(cur.rowcount or 0)


def emergency_clear_jobs(profile_id: int | None = None) -> int:
    now = utcnow()
    where, params = _job_scope_sql(writable=True, profile_id=profile_id)
    status_clause = "status IN ('pending', 'running')"
    update_sql = f"UPDATE jobs SET status='cancelled', error='Emergency cancelled by user', finished_at=COALESCE(finished_at, ?), updated_at=?{where} AND {status_clause}" if where else "UPDATE jobs SET status='cancelled', error='Emergency cancelled by user', finished_at=COALESCE(finished_at, ?), updated_at=? WHERE status IN ('pending', 'running')"
    with connect() as conn:
        conn.execute(update_sql, (now, now, *params) if where else (now, now))
        cur = conn.execute(f"DELETE FROM jobs{where}", params) if where else conn.execute("DELETE FROM jobs")
        deleted = int(cur.rowcount or 0)
    _emit("job_update", {"status": "cleared", "emergency": True})
    return deleted


def force_job(job_id: str) -> bool:
    row = _job_row(job_id)
    if not row or row['status'] != 'pending':
        return False
    payload = _job_payload(row)
    payload['force_job'] = True
    payload['priority_job'] = True
    with connect() as conn:
        conn.execute("UPDATE jobs SET payload_json=?, updated_at=? WHERE id=?", (json.dumps(payload), utcnow(), job_id))
    operation_logs.record_job_event(int(row.get('profile_id') or 0), row.get('action'), 'forced', payload, job_id=job_id, user_id=int(row.get('user_id') or 0))
    _emit('job_update', {'id': job_id, 'profile_id': row.get('profile_id'), 'status': 'pending', 'forced': True})
    _submit_job(job_id, row.get('action'))
    return True

def retry_job(job_id: str) -> bool:
    row = _job_row(job_id)
    if not row or row["status"] not in {"failed", "cancelled"}:
        return False
    with connect() as conn:
        conn.execute("UPDATE jobs SET status='pending', error='', finished_at=NULL, state_json=NULL, progress_current=0, heartbeat_at=NULL, updated_at=? WHERE id=?", (utcnow(), job_id))
    payload = _job_payload(row)
    operation_logs.record_job_event(int(row.get("profile_id") or 0), row.get("action"), "retry", payload, job_id=job_id, user_id=int(row.get("user_id") or 0))
    _emit("job_update", {"id": job_id, "profile_id": row.get("profile_id"), "status": "pending"})
    _submit_job(job_id, row.get("action"))
    return True
