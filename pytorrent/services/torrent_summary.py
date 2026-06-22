from __future__ import annotations
from copy import deepcopy
from threading import RLock
from time import time

SUMMARY_CACHE_TTL_SECONDS = 60

_ERROR_PATTERNS = (
    "error",
    "failed",
    "failure",
    "timeout",
    "timed out",
    "tracker",
    "could not",
    "cannot",
    "refused",
    "unreachable",
    "denied",
)
_SUMMARY_TYPES = ("all", "downloading", "queued", "seeding", "paused", "checking", "error", "post_check", "stopped")
_summary_cache: dict[int, dict] = {}
_summary_lock = RLock()


def _number(row: dict, key: str) -> int:
    try:
        return int(float(row.get(key) or 0))
    except (TypeError, ValueError):
        return 0


def _has_error(row: dict) -> bool:
    message = str(row.get("message") or "").strip().lower()
    return bool(message and any(pattern in message for pattern in _ERROR_PATTERNS))


def _is_checking(row: dict) -> bool:
    return str(row.get("status") or "") == "Checking" or _number(row, "hashing") > 0


def _matches(row: dict, summary_type: str) -> bool:
    status = str(row.get("status") or "")
    checking = _is_checking(row)
    if summary_type == "all":
        return True
    if summary_type == "downloading":
        return not checking and not bool(row.get("complete")) and bool(row.get("state")) and not bool(row.get("paused")) and str(row.get("status") or "") != "Queued"
    if summary_type == "queued":
        return not checking and (bool(row.get("queued")) or str(row.get("status") or "") == "Queued")
    if summary_type == "seeding":
        return not checking and bool(row.get("complete")) and bool(row.get("state")) and not bool(row.get("paused"))
    if summary_type == "paused":
        return not checking and (bool(row.get("paused")) or status == "Paused")
    if summary_type == "checking":
        return checking
    if summary_type == "error":
        return _has_error(row)
    if summary_type == "post_check":
        # Note: Post-check is counted separately from Stopped so automation can target it safely.
        return str(row.get("status") or "") == "Post-check" or bool(row.get("post_check"))
    if summary_type == "stopped":
        # Note: Stopped count follows the UI filter exactly and excludes app-level post-check waiting rows.
        return not checking and not bool(row.get("state")) and str(row.get("status") or "") != "Post-check" and not bool(row.get("post_check"))
    return False


def _empty_bucket() -> dict:
    return {
        "count": 0,
        "size": 0,
        "disk_bytes": 0,
        "completed_bytes": 0,
        "remaining_bytes": 0,
        "progress_percent": 0.0,
        "remaining_percent": 100.0,
        # Kept for backward compatibility with older clients; not used by the filters UI.
        "down_total": 0,
        "up_total": 0,
    }


def build_summary(rows: list[dict]) -> dict:
    filters = {summary_type: _empty_bucket() for summary_type in _SUMMARY_TYPES}
    for row in rows:
        for summary_type in _SUMMARY_TYPES:
            if not _matches(row, summary_type):
                continue
            bucket = filters[summary_type]
            bucket["count"] += 1
            size = _number(row, "size")
            completed = min(size, _number(row, "completed_bytes")) if size else _number(row, "completed_bytes")
            bucket["size"] += size
            bucket["completed_bytes"] += completed
            bucket["disk_bytes"] += completed
            bucket["down_total"] += _number(row, "down_total")
            bucket["up_total"] += _number(row, "up_total")
    for bucket in filters.values():
        bucket["remaining_bytes"] = max(0, bucket["size"] - bucket["completed_bytes"])
        if bucket["size"] > 0:
            bucket["progress_percent"] = round((bucket["completed_bytes"] / bucket["size"]) * 100, 1)
            bucket["remaining_percent"] = round(100 - bucket["progress_percent"], 1)
        else:
            bucket["progress_percent"] = 0.0
            bucket["remaining_percent"] = 0.0
    now = time()
    return {
        "filters": filters,
        "cache_ttl_seconds": SUMMARY_CACHE_TTL_SECONDS,
        "generated_at_epoch": now,
        "cached": False,
    }


def cached_summary(profile_id: int, rows: list[dict], force: bool = False) -> dict:
    now = time()
    with _summary_lock:
        cached = _summary_cache.get(int(profile_id))
        rows_count = len(rows or [])
        cached_count = int(((cached or {}).get("filters") or {}).get("all", {}).get("count") or 0)
        cache_is_fresh = cached and now - float(cached.get("generated_at_epoch") or 0) < SUMMARY_CACHE_TTL_SECONDS
        cache_is_usable = cache_is_fresh and not (cached_count == 0 and rows_count > 0)
        if not force and cache_is_usable:
            result = deepcopy(cached)
            result["cached"] = True
            return result
        result = build_summary(rows or [])
        # Do not cache an empty cold-start snapshot. On first connection the cache may be populated
        # before rTorrent refresh finishes, which would otherwise show zeros for the full TTL.
        if rows_count > 0 or force:
            _summary_cache[int(profile_id)] = deepcopy(result)
        return result


def invalidate_summary(profile_id: int | None = None) -> None:
    with _summary_lock:
        if profile_id is None:
            _summary_cache.clear()
        else:
            _summary_cache.pop(int(profile_id), None)
