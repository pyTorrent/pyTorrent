from __future__ import annotations
import base64
import os
import platform
import sys
import time
import re
from datetime import datetime, timezone
import urllib.request
import urllib.parse
import socket
import json
import psutil
import zipfile
import tempfile
import queue
import threading
from pathlib import Path
from urllib.parse import quote
from flask import Blueprint, jsonify, request, abort, send_file, redirect, Response, stream_with_context, url_for
from ..config import DB_PATH, JOBS_RETENTION_DAYS, SMART_QUEUE_HISTORY_RETENTION_DAYS, LOG_RETENTION_DAYS, WORKERS, PYTORRENT_TMP_DIR
from ..db import connect, utcnow
from ..services.auth import current_user_id as default_user_id, current_user, list_users, save_user, delete_user, login_user, logout_user, enabled as auth_enabled, require_profile_write, require_admin, is_admin
from ..services import auth, preferences, rtorrent, torrent_stats, speed_peaks, tracker_cache, rss as rss_service, ratio_rules, backup as backup_service, download_planner, operation_logs, poller_control, database_maintenance
from ..services.torrent_cache import torrent_cache
from ..services.torrent_summary import cached_summary
from ..services.workers import enqueue, list_jobs, cancel_job, retry_job, force_job, clear_jobs, emergency_clear_jobs
from ..services.geoip import lookup_ip
from ..services.torrent_meta import parse_torrent

bp = Blueprint("api", __name__, url_prefix="/api")

MOVE_BULK_MAX_HASHES = 100

from .auth_api import register_auth_routes
register_auth_routes(bp)


def _request_profile_selector() -> int | None:
    """Return the explicit rTorrent profile_id supplied by API clients.

    The API contract is intentionally uniform: clients pass profile_id in
    query parameters, form data or JSON payload. Profile headers and legacy
    alias fields are not accepted.
    """
    payload = {}
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        try:
            payload = request.get_json(silent=True) or {}
        except Exception:
            payload = {}

    profile_id = request.args.get("profile_id") or request.form.get("profile_id") or payload.get("profile_id")
    if profile_id in (None, ""):
        return None
    try:
        return int(profile_id)
    except (TypeError, ValueError):
        raise ValueError("profile_id must be an integer")



def _profile_selector_present() -> bool:
    if request.args.get("profile_id") or request.form.get("profile_id"):
        return True
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        try:
            payload = request.get_json(silent=True) or {}
        except Exception:
            payload = {}
        return bool(payload.get("profile_id"))
    return False


def _requires_explicit_profile(require_write: bool) -> bool:
    profile_write_path = request.path.startswith(auth.RTORRENT_WRITE_PREFIXES) or request.path.startswith(auth.RTORRENT_CONFIG_PREFIXES)
    return bool((require_write or profile_write_path) and request.method in {"POST", "PUT", "PATCH", "DELETE"})

def request_profile(require_write: bool = False):
    """Resolve API profile context from explicit profile_id, then active profile for read-only UI compatibility."""
    if _requires_explicit_profile(require_write) and not _profile_selector_present():
        abort(400, description="profile_id is required for profile-scoped write API requests")
    try:
        profile_id = _request_profile_selector()
    except ValueError:
        raise
    user_id = default_user_id()
    profile = None
    if profile_id:
        profile = preferences.get_profile(int(profile_id), user_id)
    elif not _requires_explicit_profile(require_write):
        profile = preferences.active_profile(user_id)
        if not profile and auth.can_access_profile(1, user_id):
            profile = preferences.get_profile(1, user_id)
    if not profile and profile_id:
        abort(404)
    if not profile:
        return None
    pid = int(profile["id"])
    if require_write and not auth.can_write_profile(pid, user_id):
        abort(403)
    if not require_write and not auth.can_access_profile(pid, user_id):
        abort(403)
    return profile


def request_profile_id(require_write: bool = False) -> int | None:
    profile = request_profile(require_write=require_write)
    return int(profile["id"]) if profile else None


def _job_profile_id(job_id: str) -> int | None:
    with connect() as conn:
        row = conn.execute("SELECT profile_id FROM jobs WHERE id=?", (job_id,)).fetchone()
    return int(row.get("profile_id") or 0) if row else None

def ok(payload=None):
    data = {"ok": True}
    if payload:
        data.update(payload)
    return jsonify(data)


from ..services.port_check import port_check_status


def _safe_len(callable_obj) -> int | None:
    try:
        return len(callable_obj())
    except Exception:
        return None

def _table_count(table: str, where: str = "", params: tuple = (), conn=None) -> int:
    """Count rows with one SQL statement; schema-created tables do not need a sqlite_master pre-check."""
    try:
        if conn is None:
            with connect() as owned_conn:
                row = owned_conn.execute(f"SELECT COUNT(*) AS n FROM {table} {where}", params).fetchone()
        else:
            row = conn.execute(f"SELECT COUNT(*) AS n FROM {table} {where}", params).fetchone()
        return int((row or {}).get("n") or 0)
    except Exception:
        return 0


def _db_size() -> dict:
    try:
        return database_maintenance.database_status()
    except Exception as exc:
        try:
            size = DB_PATH.stat().st_size if DB_PATH.exists() else 0
        except Exception:
            size = 0
        return {"path": str(DB_PATH), "size": size, "size_h": rtorrent.human_size(size), "error": str(exc)}


def _active_profile_cache_summary(profile_id: int | None = None, conn=None) -> dict:
    profile = preferences.active_profile() if profile_id is None else {"id": profile_id}
    profile_id = int((profile or {}).get("id") or 0)
    if not profile_id:
        return {"profile_id": 0, "profile_rows": 0, "runtime_items": 0}
    tracker_rows = _table_count("tracker_summary_cache", "WHERE profile_id=?", (profile_id,), conn=conn)
    stats_rows = _table_count("torrent_stats_cache", "WHERE profile_id=?", (profile_id,), conn=conn)
    runtime_items = 0
    try:
        runtime_items += len(torrent_cache.snapshot(profile_id))
    except Exception:
        pass
    return {"profile_id": profile_id, "profile_rows": tracker_rows + stats_rows, "tracker_rows": tracker_rows, "torrent_stats_rows": stats_rows, "runtime_items": runtime_items}


def cleanup_summary() -> dict:
    active_profile = preferences.active_profile()
    profile_id = int((active_profile or {}).get("id") or 0)
    with connect() as conn:
        operation_logs_total = _table_count(
            "operation_logs",
            "WHERE profile_id=? OR profile_id IS NULL",
            (profile_id,),
            conn=conn,
        ) if profile_id else _table_count("operation_logs", conn=conn)
        # Note: Cleanup counters are profile-scoped to match the cleanup buttons shown in the UI.
        jobs_total = _table_count("jobs", "WHERE profile_id=?", (profile_id,), conn=conn) if profile_id else 0
        jobs_clearable = _table_count("jobs", "WHERE profile_id=? AND status NOT IN ('pending', 'running')", (profile_id,), conn=conn) if profile_id else 0
        jobs_global_total = _table_count("jobs", conn=conn)
        jobs_global_clearable = _table_count("jobs", "WHERE status NOT IN ('pending', 'running')", conn=conn)
        smart_queue_history_total = _table_count("smart_queue_history", "WHERE profile_id=?", (profile_id,), conn=conn) if profile_id else 0
        automation_history_total = _table_count("automation_history", "WHERE profile_id=?", (profile_id,), conn=conn) if profile_id else 0
        cache_summary = _active_profile_cache_summary(profile_id if profile_id else None, conn=conn)
    operation_log_retention = operation_logs.get_settings(profile_id) if profile_id else operation_logs.get_settings(0)
    poller_runtime = poller_control.snapshot(profile_id) if profile_id else {}
    return {
        "jobs_total": jobs_total,
        "jobs_clearable": jobs_clearable,
        "jobs_global_total": jobs_global_total,
        "jobs_global_clearable": jobs_global_clearable,
        "profile_id": profile_id,
        "smart_queue_history_total": smart_queue_history_total,
        "operation_logs_total": operation_logs_total,
        "automation_history_total": automation_history_total,
        "planner_history_total": download_planner.history_count(profile_id) if profile_id else 0,
        "cache": cache_summary,
        "poller_runtime": poller_runtime,
        "retention_days": {
            "jobs": JOBS_RETENTION_DAYS,
            "smart_queue_history": SMART_QUEUE_HISTORY_RETENTION_DAYS,
            "operation_logs": operation_log_retention.get("retention_days", LOG_RETENTION_DAYS),
            "automation_history": SMART_QUEUE_HISTORY_RETENTION_DAYS,
            "planner_history": SMART_QUEUE_HISTORY_RETENTION_DAYS,
        },
        "operation_log_retention": operation_log_retention,
        "retention_labels": {
            "operation_logs": operation_logs.retention_label(operation_log_retention),
        },
        "database": _db_size(),
        "admin": is_admin(current_user()),
    }

def active_default_download_path(profile: dict | None) -> str:
    if not profile:
        return ""
    try:
        prefs = preferences.get_preferences(profile_id=int(profile.get("id") or 0))
        custom = str((prefs or {}).get("default_download_path") or "").strip()
        if custom:
            return custom
    except Exception:
        pass
    try:
        return rtorrent.default_download_path(profile)
    except Exception:
        return ""


def _unique_hashes(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values or []:
        torrent_hash = str(value or "").strip()
        if not torrent_hash or torrent_hash in seen:
            continue
        seen.add(torrent_hash)
        unique.append(torrent_hash)
    return unique


def enrich_bulk_payload(profile: dict, action_name: str, data: dict) -> dict:
    payload = dict(data or {})
    hashes = payload.get("hashes") or []
    if isinstance(hashes, str):
        hashes = [hashes]
    hashes = _unique_hashes([str(h) for h in hashes if h])
    payload["hashes"] = hashes
    payload["job_context"] = {
        "source": "api",
        "action": action_name,
        "bulk": len(hashes) > 1,
        "hash_count": len(hashes),
        "requested_at": utcnow(),
    }
    if hashes:
        try:
            by_hash = {str(t.get("hash")): t for t in torrent_cache.snapshot(profile["id"])}
            payload["job_context"]["items"] = [
                {
                    "hash": h,
                    "name": str((by_hash.get(h) or {}).get("name") or ""),
                    "path": str((by_hash.get(h) or {}).get("path") or ""),
                }
                for h in hashes
            ]
        except Exception as exc:
            payload["job_context"]["items_error"] = str(exc)
    if action_name == "move":
        payload["job_context"]["target_path"] = str(payload.get("path") or "")
        payload["job_context"]["move_data"] = bool(payload.get("move_data"))
    if action_name == "remove":
        payload["job_context"]["remove_data"] = bool(payload.get("remove_data"))
    if action_name == "recreate_files":
        payload["job_context"]["mode"] = "rtorrent_stop_close_queue_flags_check_hash_restore"
    if action_name == "profile_transfer":
        payload["job_context"]["target_profile_id"] = int(payload.get("target_profile_id") or 0)
        payload["job_context"]["target_path"] = str(payload.get("target_path") or payload.get("path") or "")
        payload["job_context"]["move_data"] = bool(payload.get("move_data"))
        payload["job_context"]["move_data_downgraded"] = bool(payload.get("move_data_downgraded"))
    return payload


def _chunk_hashes(hashes: list[str], size: int = MOVE_BULK_MAX_HASHES) -> list[list[str]]:
    safe_size = max(1, int(size or MOVE_BULK_MAX_HASHES))
    return [hashes[index:index + safe_size] for index in range(0, len(hashes), safe_size)]


def enqueue_bulk_parts(profile: dict, action_name: str, data: dict) -> list[dict]:
    base_payload = enrich_bulk_payload(profile, action_name, data)
    hashes = base_payload.get("hashes") or []
    chunks = _chunk_hashes(hashes)
    if len(chunks) <= 1:
        job_id = enqueue(action_name, profile["id"], base_payload)
        return [{"job_id": job_id, "label": "bulk-1", "part": 1, "parts": 1, "hashes": hashes, "hash_count": len(hashes)}]

    jobs = []
    items_by_hash = {str(item.get("hash")): item for item in (base_payload.get("job_context") or {}).get("items") or []}
    for index, chunk in enumerate(chunks, start=1):
        payload = dict(base_payload)
        payload["hashes"] = chunk
        context = dict(base_payload.get("job_context") or {})
        context.update({
            "bulk": True,
            "bulk_label": f"bulk-{index}",
            "bulk_part": index,
            "bulk_parts": len(chunks),
            "hash_count": len(chunk),
            "parent_hash_count": len(hashes),
            "items": [items_by_hash[h] for h in chunk if h in items_by_hash],
        })
        payload["job_context"] = context
        job_id = enqueue(action_name, profile["id"], payload)
        jobs.append({"job_id": job_id, "label": context["bulk_label"], "part": index, "parts": len(chunks), "hashes": chunk, "hash_count": len(chunk)})
    return jobs


def enqueue_move_bulk_parts(profile: dict, data: dict) -> list[dict]:
    return enqueue_bulk_parts(profile, "move", data)


def enqueue_remove_bulk_parts(profile: dict, data: dict) -> list[dict]:
    return enqueue_bulk_parts(profile, "remove", data)


def _user_disk_status(profile: dict) -> dict:
    prefs = preferences.get_disk_monitor_preferences(profile.get("id") if profile else None)
    try:
        paths = json.loads((prefs or {}).get("disk_monitor_paths_json") or "[]") if prefs else []
    except Exception:
        paths = []
    return rtorrent.disk_usage_for_paths(
        profile,
        paths,
        (prefs or {}).get("disk_monitor_mode") or "default",
        (prefs or {}).get("disk_monitor_selected_path") or "",
    )


__all__ = [name for name in globals() if not name.startswith('__')]
