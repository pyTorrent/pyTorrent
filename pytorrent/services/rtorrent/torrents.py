from __future__ import annotations
import time
from .client import *
from .files import export_torrent_file, iter_remote_file_chunks, set_file_priorities
from .system import disk_usage_for_default_path

XMLRPC_DEFAULT_SIZE_LIMIT_BYTES = 512 * 1024


def _parse_xmlrpc_size_limit(value) -> int:
    """Parse rTorrent XML-RPC size values such as 524288, 16M or 8K."""
    text = str(value or '').strip().lower()
    if not text:
        return XMLRPC_DEFAULT_SIZE_LIMIT_BYTES
    multiplier = 1
    if text[-1:] in {'k', 'm', 'g'}:
        suffix = text[-1]
        text = text[:-1]
        multiplier = {'k': 1024, 'm': 1024 * 1024, 'g': 1024 * 1024 * 1024}[suffix]
    try:
        return max(1, int(float(text) * multiplier))
    except Exception:
        return XMLRPC_DEFAULT_SIZE_LIMIT_BYTES


def xmlrpc_size_limit(profile: dict) -> dict:
    """Return the current rTorrent XML-RPC request size limit."""
    try:
        raw = client_for(profile).call('network.xmlrpc.size_limit')
        limit = _parse_xmlrpc_size_limit(raw)
        return {'ok': True, 'raw': str(raw), 'bytes': limit, 'human': human_size(limit)}
    except Exception as exc:
        return {'ok': False, 'raw': '', 'bytes': XMLRPC_DEFAULT_SIZE_LIMIT_BYTES, 'human': human_size(XMLRPC_DEFAULT_SIZE_LIMIT_BYTES), 'error': str(exc)}


def estimate_torrent_upload_request_size(data: bytes, start: bool = True, directory: str = '', label: str = '', file_priorities: list[dict] | None = None) -> int:
    """Estimate the XML-RPC body size produced by rTorrent load.raw* for a .torrent file."""
    commands = []
    if directory:
        commands.append(f'd.directory.set={directory}')
    if label:
        commands.append(f'd.custom1.set={label}')
    method = 'load.raw' if file_priorities else ('load.raw_start' if start else 'load.raw')
    return len(dumps(("", Binary(data), *commands), methodname=method, allow_none=True).encode('utf-8'))


def validate_torrent_upload_size(profile: dict, data: bytes, start: bool = True, directory: str = '', label: str = '', file_priorities: list[dict] | None = None) -> dict:
    """Check whether a .torrent upload fits the active rTorrent XML-RPC size limit."""
    limit = xmlrpc_size_limit(profile)
    request_bytes = estimate_torrent_upload_request_size(data, start, directory, label, file_priorities)
    allowed = request_bytes <= int(limit.get('bytes') or XMLRPC_DEFAULT_SIZE_LIMIT_BYTES)
    return {
        'ok': allowed,
        'request_bytes': request_bytes,
        'request_h': human_size(request_bytes),
        'limit_bytes': int(limit.get('bytes') or XMLRPC_DEFAULT_SIZE_LIMIT_BYTES),
        'limit_h': limit.get('human') or human_size(XMLRPC_DEFAULT_SIZE_LIMIT_BYTES),
        'limit_raw': limit.get('raw') or '',
        'limit_read_ok': bool(limit.get('ok')),
        'limit_error': limit.get('error') or '',
        'setting': 'network.xmlrpc.size_limit',
        'suggested_value': '16M',
    }


def _mark_post_check_watch(profile_id: int, torrent_hash: str) -> None:
    if not torrent_hash:
        return
    _POST_CHECK_WATCH.setdefault(int(profile_id), {})[str(torrent_hash)] = time.time()


def _clear_post_check_watch(profile_id: int, torrent_hash: str) -> None:
    profile_watch = _POST_CHECK_WATCH.get(int(profile_id))
    if not profile_watch:
        return
    profile_watch.pop(str(torrent_hash), None)
    if not profile_watch:
        _POST_CHECK_WATCH.pop(int(profile_id), None)


def _is_post_check_watched(profile_id: int, torrent_hash: str) -> bool:
    profile_watch = _POST_CHECK_WATCH.get(int(profile_id)) or {}
    started_at = profile_watch.get(str(torrent_hash))
    if not started_at:
        return False
    age = time.time() - started_at
    if age > _POST_CHECK_WATCH_TTL_SECONDS:
        _clear_post_check_watch(profile_id, torrent_hash)
        return False
    return age >= _POST_CHECK_WATCH_MIN_SECONDS


def _label_names(value: str) -> list[str]:
    names: list[str] = []
    for part in str(value or "").replace(";", ",").replace("|", ",").split(","):
        label = part.strip()
        if label and label not in names:
            names.append(label)
    return names


def _label_value(labels: list[str]) -> str:
    return ", ".join([label for label in labels if str(label or "").strip()])


def _without_post_check_download_label(value: str | None) -> str:
    return _label_value([label for label in _label_names(str(value or "")) if label != POST_CHECK_DOWNLOAD_LABEL])


def clear_post_check_download_label(c: ScgiRtorrentClient, torrent_hash: str, current_label: str | None = None) -> bool:
    label_source = current_label
    if label_source is None:
        try:
            label_source = str(c.call("d.custom1", str(torrent_hash or "")) or "")
        except Exception:
            label_source = ""
    labels = _label_names(str(label_source or ""))
    if POST_CHECK_DOWNLOAD_LABEL not in labels:
        return False
    c.call("d.custom1.set", str(torrent_hash or ""), _label_value([label for label in labels if label != POST_CHECK_DOWNLOAD_LABEL]))
    return True


def _message_indicates_active_check(message: str) -> bool:
    msg = str(message or "").lower()
    if not msg:
        return False
    finished_markers = ("complete", "completed", "finished", "success", "succeeded", "failed", "done")
    if any(marker in msg for marker in finished_markers):
        return False
    active_markers = ("checking", "hashing", "hash check queued", "hash check scheduled", "check hash queued", "recheck queued", "rechecking")
    return any(marker in msg for marker in active_markers)


def _row_progress_complete(row: dict) -> bool:
    size = int(row.get("size") or 0)
    completed = int(row.get("completed_bytes") or 0)
    return bool(row.get("complete")) or (size > 0 and completed >= size) or float(row.get("progress") or 0) >= 100.0


def _cleanup_post_check_label_if_ready(c: ScgiRtorrentClient, row: dict) -> bool:
    labels = _label_names(str(row.get("label") or ""))
    if POST_CHECK_DOWNLOAD_LABEL not in labels:
        return False
    status = str(row.get("status") or "").lower()
    started_after_wait = bool(int(row.get("state") or 0)) and bool(int(row.get("active") or 0)) and status != "checking"
    if not (_row_progress_complete(row) or status == "seeding" or started_after_wait):
        return False
    clear_post_check_download_label(c, str(row.get("hash") or ""), str(row.get("label") or ""))
    row["label"] = _without_post_check_download_label(str(row.get("label") or ""))
    return True


def apply_post_check_policy(profile: dict, rows: list[dict], previous_rows: dict[str, dict] | None = None) -> list[dict]:
    """Start complete torrents after check; stop and label incomplete ones for Smart Queue."""
    previous_rows = previous_rows or {}
    profile_id = int(profile.get("id") or 0)
    c = client_for(profile)
    changes: list[dict] = []
    for row in rows:
        h = str(row.get("hash") or "")
        prev = previous_rows.get(h) or {}
        try:
            if h and _cleanup_post_check_label_if_ready(c, row):
                changes.append({"hash": h, "action": "remove_post_check_label"})
        except Exception as exc:
            changes.append({"hash": h, "action": "remove_post_check_label_failed", "error": str(exc)})
        was_checking = str(prev.get("status") or "") == "Checking" or int(prev.get("hashing") or 0) > 0
        watched_recheck = _is_post_check_watched(profile_id, h)
        is_checking = str(row.get("status") or "") == "Checking" or int(row.get("hashing") or 0) > 0
        if not h or not (was_checking or watched_recheck) or is_checking:
            continue
        complete = _row_progress_complete(row)
        try:
            if complete:
                start_result = start_or_resume_hash(c, h)
                clear_post_check_download_label(c, h, str(row.get("label") or ""))
                row.update({"state": 1, "active": 1, "paused": False, "status": "Seeding", "label": _without_post_check_download_label(str(row.get("label") or ""))})
                changes.append({"hash": h, "action": "start_seed_after_check", "complete": True, "result": start_result})
            else:
                labels = _label_names(str(row.get("label") or ""))
                if POST_CHECK_DOWNLOAD_LABEL not in labels:
                    labels.append(POST_CHECK_DOWNLOAD_LABEL)
                label_value = _label_value(labels)
                c.call("d.stop", h)
                try:
                    c.call("d.close", h)
                except Exception:
                    pass
                c.call("d.custom1.set", h, label_value)
                row.update({"state": 0, "active": 0, "paused": False, "post_check": True, "status": "Post-check", "label": label_value})
                changes.append({"hash": h, "action": "mark_post_check_waiting", "complete": False, "label": POST_CHECK_DOWNLOAD_LABEL})
            _clear_post_check_watch(profile_id, h)
        except Exception as exc:
            changes.append({"hash": h, "action": "post_check_policy_failed", "error": str(exc)})
    return changes


TORRENT_FIELDS = [
    "d.hash=", "d.name=", "d.state=", "d.complete=", "d.size_bytes=", "d.completed_bytes=",
    "d.ratio=", "d.up.rate=", "d.down.rate=", "d.up.total=", "d.down.total=", "d.peers_connected=",
    "d.peers_complete=", "d.priority=", "d.directory=", "d.base_path=", "d.creation_date=", "d.custom1=",
    "d.custom=py_ratio_group", f"d.custom={PY_MANUAL_PAUSE_FIELD}", "d.message=", "d.hashing=", "d.is_active=", "d.is_open=", "d.is_multi_file=",
]

TORRENT_OPTIONAL_FIELDS = [
    "d.timestamp.last_active=",
    "d.timestamp.finished=",
]

LIVE_TORRENT_FIELDS = [
    "d.hash=", "d.state=", "d.complete=", "d.size_bytes=", "d.completed_bytes=",
    "d.ratio=", "d.up.rate=", "d.down.rate=", "d.up.total=", "d.down.total=",
    "d.peers_connected=", "d.peers_complete=", "d.message=", "d.hashing=", "d.is_active=",
    "d.is_open=", "d.custom1=", f"d.custom={PY_MANUAL_PAUSE_FIELD}",
]


def human_duration(seconds: int) -> str:
    seconds = max(0, int(seconds or 0))
    if seconds <= 0:
        return '-'
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def normalize_row(row: list) -> dict:
    size = int(row[4] or 0)
    completed = int(row[5] or 0)
    progress = 100.0 if size <= 0 and int(row[3] or 0) else round((completed / size) * 100, 2) if size else 0.0
    ratio_raw = int(row[6] or 0)
    down_rate = int(row[8] or 0)
    up_rate = int(row[7] or 0)
    remaining_bytes = max(0, size - completed)
    eta_seconds = int(remaining_bytes / down_rate) if down_rate > 0 and not int(row[3] or 0) else 0
    directory = str(row[14] or "")
    base_path = str(row[15] or "")
    state = int(row[2] or 0)
    complete = int(row[3] or 0)
    is_multi_file = int(row[24] or 0) if len(row) > 24 else 0

    if base_path and base_path != "/":
        display_parent = posixpath.dirname(base_path.rstrip("/")) or "/"
        display_path = display_parent.rstrip("/") + "/" if display_parent != "/" else display_parent
    elif directory and is_multi_file and directory != "/":
        display_parent = posixpath.dirname(directory.rstrip("/")) or "/"
        display_path = display_parent.rstrip("/") + "/" if display_parent != "/" else display_parent
    elif directory:
        display_path = directory.rstrip("/") + "/" if directory != "/" else directory
    else:
        display_path = ""
    manual_pause = str(row[19] or "").strip() == "1"
    msg = str(row[20] or "")
    msg_l = msg.lower()
    hashing = int(row[21] or 0) if len(row) > 21 else 0
    is_active = int(row[22] or 0) if len(row) > 22 else int(state)
    is_open = int(row[23] or 0) if len(row) > 23 else int(is_active or state)
    last_activity = int(row[25] or 0) if len(row) > 25 else 0
    if not last_activity and (down_rate > 0 or up_rate > 0):
        last_activity = int(time.time())
    completed_at = int(row[26] or 0) if len(row) > 26 else 0
    is_checking = bool(hashing) or _message_indicates_active_check(msg_l)
    post_check = POST_CHECK_DOWNLOAD_LABEL in _label_names(str(row[17] or "")) and not is_checking and not bool(is_active)
    is_paused = manual_pause and not is_checking and not post_check
    is_queued = bool(state) and bool(is_open) and not bool(is_active) and not bool(complete) and not is_paused and not is_checking and not post_check
    status = "Checking" if is_checking else "Post-check" if post_check else "Paused" if is_paused else "Queued" if is_queued else "Seeding" if complete and state else "Downloading" if state else "Stopped"
    to_download_bytes = remaining_bytes if not complete else 0

    return {
        "hash": str(row[0] or ""),
        "name": str(row[1] or ""),
        "state": state,
        "active": is_active,
        "open": is_open,
        "paused": is_paused,
        "queued": is_queued,
        "complete": complete,
        "size": size,
        "size_h": human_size(size),
        "completed_bytes": completed,
        "progress": progress,
        "ratio": round(ratio_raw / 1000, 3),
        "up_rate": up_rate,
        "up_rate_h": human_rate(up_rate),
        "down_rate": down_rate,
        "down_rate_h": human_rate(down_rate),
        "eta_seconds": eta_seconds,
        "eta_h": human_duration(eta_seconds) if eta_seconds else "-",
        "up_total": int(row[9] or 0),
        "up_total_h": human_size(row[9] or 0),
        "down_total": int(row[10] or 0),
        "down_total_h": human_size(row[10] or 0),
        "to_download": to_download_bytes,
        "to_download_h": human_size(to_download_bytes) if to_download_bytes else "",
        "peers": int(row[11] or 0),
        "seeds": int(row[12] or 0),
        "priority": int(row[13] or 0),
        "path": display_path,
        "created": int(row[16] or 0),
        "last_activity": last_activity,
        "completed_at": completed_at,
        "label": str(row[17] or ""),
        "ratio_group": str(row[18] or ""),
        "message": msg,
        "status": status,
        "post_check": post_check,
        "hashing": hashing,
    }


def normalize_live_row(row: list) -> dict:
    """Normalize the small row used by the fast live stats poller."""
    size = int(row[3] or 0)
    completed = int(row[4] or 0)
    complete = int(row[2] or 0)
    state = int(row[1] or 0)
    down_rate = int(row[7] or 0)
    up_rate = int(row[6] or 0)
    ratio_raw = int(row[5] or 0)
    remaining_bytes = max(0, size - completed)
    eta_seconds = int(remaining_bytes / down_rate) if down_rate > 0 and not complete else 0
    msg = str(row[12] or "")
    hashing = int(row[13] or 0)
    is_active = int(row[14] or 0)
    is_open = int(row[15] or 0) if len(row) > 15 else int(is_active or state)
    labels = str(row[16] or "") if len(row) > 16 else ""
    manual_pause = str(row[17] or "").strip() == "1" if len(row) > 17 else False
    is_checking = bool(hashing) or _message_indicates_active_check(msg.lower())
    post_check = POST_CHECK_DOWNLOAD_LABEL in _label_names(labels) and not is_checking and not bool(is_active)
    # Note: Live patches keep Queued separate from explicit user Paused using the same app marker as full snapshots.
    is_paused = manual_pause and not is_checking and not post_check
    is_queued = bool(state) and bool(is_open) and not bool(is_active) and not bool(complete) and not is_paused and not is_checking and not post_check
    status = "Checking" if is_checking else "Post-check" if post_check else "Paused" if is_paused else "Queued" if is_queued else "Seeding" if complete and state else "Downloading" if state else "Stopped"
    progress = 100.0 if size <= 0 and complete else round((completed / size) * 100, 2) if size else 0.0
    to_download_bytes = remaining_bytes if not complete else 0
    return {
        "hash": str(row[0] or ""),
        "state": state,
        "active": is_active,
        "open": is_open,
        "paused": is_paused,
        "queued": is_queued,
        "complete": complete,
        "completed_bytes": completed,
        "progress": progress,
        "ratio": round(ratio_raw / 1000, 3),
        "up_rate": up_rate,
        "up_rate_h": human_rate(up_rate),
        "down_rate": down_rate,
        "down_rate_h": human_rate(down_rate),
        "eta_seconds": eta_seconds,
        "eta_h": human_duration(eta_seconds) if eta_seconds else "-",
        "up_total": int(row[8] or 0),
        "up_total_h": human_size(row[8] or 0),
        "down_total": int(row[9] or 0),
        "down_total_h": human_size(row[9] or 0),
        "to_download": to_download_bytes,
        "to_download_h": human_size(to_download_bytes) if to_download_bytes else "",
        "peers": int(row[10] or 0),
        "seeds": int(row[11] or 0),
        "message": msg,
        "status": status,
        "post_check": post_check,
        "hashing": hashing,
    }


def list_torrent_live_stats(profile: dict) -> list[dict]:
    """Return lightweight live torrent stats for the fast poller."""
    # Note: This avoids the full torrent row multicall on every speed/status tick.
    rows = client_for(profile).d.multicall2("", "main", *LIVE_TORRENT_FIELDS)
    return [normalize_live_row(list(row)) for row in rows]


def list_torrents(profile: dict) -> list[dict]:
    c = client_for(profile)
    try:
        rows = c.d.multicall2("", "main", *(TORRENT_FIELDS + TORRENT_OPTIONAL_FIELDS))
    except Exception:
        rows = c.d.multicall2("", "main", *TORRENT_FIELDS)
    return [normalize_row(list(row)) for row in rows]


def torrent_peers(profile: dict, torrent_hash: str) -> list[dict]:
    fields = [
        "p.address=", "p.client_version=", "p.completed_percent=", "p.down_rate=",
        "p.up_rate=", "p.port=", "p.is_encrypted=", "p.is_incoming=",
        "p.is_snubbed=", "p.is_banned=",
    ]
    try:
        rows = client_for(profile).p.multicall(torrent_hash, "", *fields)
    except Exception:
        fields = ["p.address=", "p.client_version=", "p.completed_percent=", "p.down_rate=", "p.up_rate=", "p.port=", "p.is_encrypted="]
        rows = client_for(profile).p.multicall(torrent_hash, "", *fields)
    peers = []
    for idx, r in enumerate(rows):
        peers.append({
            "index": idx,
            "ip": r[0],
            "client": r[1],
            "completed": int(r[2] or 0),
            "down_rate": int(r[3] or 0),
            "down_rate_h": human_rate(r[3] or 0),
            "up_rate": int(r[4] or 0),
            "up_rate_h": human_rate(r[4] or 0),
            "port": int(r[5] or 0),
            "encrypted": bool(r[6]) if len(r) > 6 else False,
            "incoming": bool(r[7]) if len(r) > 7 else False,
            "snubbed": bool(r[8]) if len(r) > 8 else False,
            "banned": bool(r[9]) if len(r) > 9 else False,
        })
    return peers


def _call_first(c: ScgiRtorrentClient, candidates: list[tuple[str, tuple]]) -> dict:
    errors = []
    for method, args in candidates:
        try:
            result = c.call(method, *args)
            return {"ok": True, "method": method, "result": result}
        except Exception as exc:
            errors.append(f"{method}: {exc}")
    raise RuntimeError("; ".join(errors))


def _tracker_domain(url: str) -> str:
    raw = str(url or '').strip()
    if not raw:
        return ''
    parsed = urlparse(raw if '://' in raw else f'http://{raw}')
    host = (parsed.hostname or '').lower().strip('.')
    if host.startswith('www.'):
        host = host[4:]
    return host


def tracker_summary(profile: dict, torrent_hashes: list[str] | None = None, limit: int = 1000) -> dict:
    """Return tracker domains grouped by torrent for the sidebar filter."""
    hashes = [str(h or '').strip() for h in (torrent_hashes or []) if str(h or '').strip()]
    if not hashes:
        hashes = [t.get('hash') for t in list_torrents(profile) if t.get('hash')]
    hashes = hashes[:max(1, int(limit or 1000))]
    by_hash: dict[str, list[dict]] = {}
    counts: dict[str, dict] = {}
    errors = []
    for h in hashes:
        try:
            items = []
            seen = set()
            for tr in torrent_trackers(profile, h):
                url = str(tr.get('url') or '')
                domain = _tracker_domain(url)
                if not domain or domain in seen:
                    continue
                seen.add(domain)
                item = {'domain': domain, 'url': url}
                items.append(item)
                row = counts.setdefault(domain, {'domain': domain, 'url': url, 'count': 0})
                row['count'] += 1
            by_hash[h] = items
        except Exception as exc:
            errors.append({'hash': h, 'error': str(exc)})
            by_hash[h] = []
    trackers = sorted(counts.values(), key=lambda x: (-int(x.get('count') or 0), str(x.get('domain') or '')))
    return {'hashes': by_hash, 'trackers': trackers, 'errors': errors, 'scanned': len(hashes)}

def _safe_tracker_call(c: ScgiRtorrentClient, method: str, target: str, default=None):
    try:
        return c.call(method, target)
    except Exception:
        return default


def _tracker_target(torrent_hash: str, index: int) -> str:
    return f"{torrent_hash}:t{int(index)}"

def _tracker_int(value, default=None):
    try:
        if value is None or value == "":
            return default
        return int(value)
    except Exception:
        return default


def _tracker_rows(c: ScgiRtorrentClient, torrent_hash: str) -> list[list]:
    fields = ("t.url=", "t.is_enabled=", "t.scrape_complete=", "t.scrape_incomplete=", "t.scrape_downloaded=")
    errors: list[str] = []
    for args in ((torrent_hash, "", *fields), ("", torrent_hash, *fields)):
        try:
            rows = c.call("t.multicall", *args)
            return [list(r) for r in (rows or [])]
        except Exception as exc:
            errors.append(f"t.multicall{args[:2]}: {exc}")
    # Note: Fallback keeps the sidebar tracker filter usable on rTorrent builds without t.multicall scrape fields.
    total = _tracker_int(_safe_tracker_call(c, "d.tracker_size", torrent_hash, 0), 0) or 0
    rows: list[list] = []
    for index in range(max(0, total)):
        target = _tracker_target(torrent_hash, index)
        url = _safe_tracker_call(c, "t.url", target, "")
        if not url:
            for args in ((torrent_hash, index), ("", torrent_hash, index)):
                try:
                    url = c.call("t.url", *args)
                    break
                except Exception:
                    continue
        if url:
            enabled = _safe_tracker_call(c, "t.is_enabled", target, 1)
            rows.append([url, enabled, None, None, None])
    if rows:
        return rows
    raise RuntimeError("Cannot read trackers: " + "; ".join(errors))


def torrent_trackers(profile: dict, torrent_hash: str) -> list[dict]:
    c = client_for(profile)
    rows = _tracker_rows(c, torrent_hash)
    trackers = []
    for idx, r in enumerate(rows):
        target = _tracker_target(torrent_hash, idx)
        last_announce = _safe_tracker_call(c, "t.activity_time_last", target, 0)
        scrape_time = _safe_tracker_call(c, "t.scrape_time_last", target, 0)
        if not last_announce:
            last_announce = scrape_time
        next_announce = _safe_tracker_call(c, "t.activity_time_next", target, 0)
        raw_seeds = _tracker_int(r[2], None)
        raw_peers = _tracker_int(r[3], None)
        raw_downloaded = _tracker_int(r[4], None)
        has_scrape = bool(_tracker_int(scrape_time, 0)) or raw_seeds not in (None, 0) or raw_peers not in (None, 0) or raw_downloaded not in (None, 0)
        trackers.append({
            "index": idx,
            "url": str(r[0] or ""),
            "enabled": bool(r[1]),
            "seeds": raw_seeds if has_scrape else None,
            "peers": raw_peers if has_scrape else None,
            "downloaded": raw_downloaded if has_scrape else None,
            "has_scrape": has_scrape,
            "last_announce": int(last_announce or 0),
            "next_announce": int(next_announce or 0),
        })
    return trackers

def tracker_action(profile: dict, torrent_hash: str, action_name: str, payload: dict | None = None) -> dict:
    payload = payload or {}
    c = client_for(profile)
    if action_name == "reannounce":
        return _call_first(c, [
            ("d.tracker_announce", (torrent_hash,)),
            ("d.tracker_announce", ("", torrent_hash)),
            ("d.tracker_announce.force", (torrent_hash,)),
        ])
    if action_name == "add":
        url = str(payload.get("url") or "").strip()
        if not url:
            raise ValueError("Missing tracker URL")
        return _call_first(c, [
            ("d.tracker.insert", (torrent_hash, "", url)),
            ("d.tracker.insert", (torrent_hash, 0, url)),
            ("d.tracker.insert", ("", torrent_hash, "", url)),
        ])
    if action_name in {"delete", "remove"}:
        # Note: Deleting trackers is guarded to keep at least one tracker attached to the torrent.
        index = int(payload.get("index", -1))
        if index < 0:
            raise ValueError("Invalid tracker index")
        total = _tracker_int(_safe_tracker_call(c, "d.tracker_size", torrent_hash, 0), 0) or len(torrent_trackers(profile, torrent_hash))
        if total <= 1:
            raise ValueError("Cannot delete the last tracker")
        if index >= total:
            raise ValueError("Invalid tracker index")
        return _call_first(c, [
            ("d.tracker.remove", (torrent_hash, index)),
            ("d.tracker.remove", (torrent_hash, "", index)),
            ("d.tracker.erase", (torrent_hash, index)),
            ("d.tracker.erase", (torrent_hash, "", index)),
            ("d.tracker.delete", (torrent_hash, index)),
            ("d.tracker.delete", (torrent_hash, "", index)),
        ])
    raise ValueError(f"Unknown tracker action: {action_name}")



def _int_rpc(c: ScgiRtorrentClient, method: str, h: str, default: int = 0) -> int:
    try:
        return int(c.call(method, h) or 0)
    except Exception:
        return default


def _str_rpc(c: ScgiRtorrentClient, method: str, h: str, default: str = '') -> str:
    try:
        return str(c.call(method, h) or '')
    except Exception:
        return default



def _set_manual_pause(c: ScgiRtorrentClient, torrent_hash: str, enabled: bool) -> None:
    """Persist the user Pause intent without touching the visible label field."""
    # Note: rTorrent has no reliable queued-vs-user-paused flag, so pyTorrent stores that intent in d.custom.
    c.call('d.custom.set', str(torrent_hash or ''), PY_MANUAL_PAUSE_FIELD, '1' if enabled else '')


def _manual_pause_enabled(c: ScgiRtorrentClient, torrent_hash: str) -> bool:
    h = str(torrent_hash or '')
    for method, args in (
        (f'd.custom={PY_MANUAL_PAUSE_FIELD}', (h,)),
        ('d.custom', (h, PY_MANUAL_PAUSE_FIELD)),
    ):
        try:
            if str(c.call(method, *args) or '').strip() == '1':
                return True
        except Exception:
            continue
    return False

def _download_runtime_state(c: ScgiRtorrentClient, h: str) -> dict:
    """Read rTorrent state using the native pause model: stopped, paused or active."""
    state = _int_rpc(c, 'd.state', h)
    active = _int_rpc(c, 'd.is_active', h)
    opened = _int_rpc(c, 'd.is_open', h)
    label = _str_rpc(c, 'd.custom1', h)
    manual_pause = _manual_pause_enabled(c, h)
    post_check = POST_CHECK_DOWNLOAD_LABEL in _label_names(label) and not bool(active)
    paused = bool(manual_pause and not post_check)
    queued = bool(state and opened and not active and not paused and not post_check)
    return {
        'state': state,
        'open': opened,
        'active': active,
        'paused': paused,
        'queued': queued,
        'stopped': not bool(state),
        'post_check': post_check,
        'label': label,
        'manual_pause': manual_pause,
        'message': _str_rpc(c, 'd.message', h),
    }


def pause_hash(c: ScgiRtorrentClient, torrent_hash: str) -> dict:
    """Mark a torrent as user-paused and ask rTorrent to pause it."""
    h = str(torrent_hash or '')
    if not h:
        return {'hash': h, 'ok': False, 'error': 'missing hash'}
    before = _download_runtime_state(c, h)
    result = {'hash': h, 'before': before, 'commands': []}
    try:
        _set_manual_pause(c, h, True)
        result['commands'].append('set_py_manual_pause')
        if before.get('stopped'):
            # Note: A stopped torrent has no native paused flag; opening it first lets the UI and later Resume follow the same path.
            try:
                c.call('d.open', h)
                result['commands'].append('d.open')
            except Exception as exc:
                result.setdefault('ignored_errors', []).append(f'd.open: {exc}')
            try:
                c.call('d.start', h)
                result['commands'].append('d.start')
            except Exception as exc:
                result.setdefault('ignored_errors', []).append(f'd.start: {exc}')
        try:
            c.call('d.pause', h)
            result['commands'].append('d.pause')
        except Exception as exc:
            result.setdefault('ignored_errors', []).append(f'd.pause: {exc}')
        result['after'] = _download_runtime_state(c, h)
        result['ok'] = True
    except Exception as exc:
        result.update({'ok': False, 'error': str(exc), 'after': _download_runtime_state(c, h)})
    return result


def stop_hash(c: ScgiRtorrentClient, torrent_hash: str) -> dict:
    """Stop an active rTorrent item without using pause semantics."""
    h = str(torrent_hash or '')
    if not h:
        return {'hash': h, 'ok': False, 'error': 'missing hash'}
    before = _download_runtime_state(c, h)
    result = {'hash': h, 'before': before, 'commands': []}
    if before.get('stopped') and not before.get('post_check'):
        if before.get('manual_pause'):
            _set_manual_pause(c, h, False)
            result['commands'].append('clear_py_manual_pause')
            before = _download_runtime_state(c, h)
        result.update({'ok': True, 'skipped': 'already_stopped', 'after': before})
        return result
    try:
        if before.get('manual_pause'):
            _set_manual_pause(c, h, False)
            result['commands'].append('clear_py_manual_pause')
        # Note: User Stop converts the app-level Post-check state into a regular stopped torrent.
        if before.get('post_check'):
            clear_post_check_download_label(c, h, before.get('label'))
            result['commands'].append('clear_post_check_label')
        # Note: Smart Queue now enforces the queue with d.stop only; user-paused torrents stay untouched.
        c.call('d.stop', h)
        result['commands'].append('d.stop')
        result['after'] = _download_runtime_state(c, h)
        result['ok'] = True
    except Exception as exc:
        result.update({'ok': False, 'error': str(exc), 'after': _download_runtime_state(c, h)})
    return result


def resume_paused_hash(c: ScgiRtorrentClient, torrent_hash: str) -> dict:
    """Resume a user-paused torrent and clear pyTorrent's pause marker."""
    h = str(torrent_hash or '')
    if not h:
        return {'hash': h, 'ok': False, 'error': 'missing hash'}
    before = _download_runtime_state(c, h)
    result: dict = {'hash': h, 'before': before, 'commands': []}
    if before.get('active') and not before.get('manual_pause'):
        result.update({'ok': True, 'skipped': 'already_active', 'after': before})
        return result
    try:
        if before.get('manual_pause'):
            _set_manual_pause(c, h, False)
            result['commands'].append('clear_py_manual_pause')
        try:
            c.call('d.resume', h)
            result['commands'].append('d.resume')
        except Exception as exc:
            result.setdefault('ignored_errors', []).append(f'd.resume: {exc}')
        try:
            c.call('d.open', h)
            result['commands'].append('d.open')
        except Exception as exc:
            result.setdefault('ignored_errors', []).append(f'd.open: {exc}')
        try:
            c.call('d.start', h)
            result['commands'].append('d.start')
        except Exception as exc:
            result.setdefault('ignored_errors', []).append(f'd.start: {exc}')
        result['after'] = _download_runtime_state(c, h)
        result['ok'] = True
    except Exception as exc:
        result.update({'ok': False, 'error': str(exc), 'after': _download_runtime_state(c, h)})
    return result


def start_or_resume_hash(c: ScgiRtorrentClient, torrent_hash: str, prefer_start: bool = False) -> dict:
    """Start stopped torrents and recover open/inactive paused torrents.

    rTorrent can expose a torrent as state=1, open=1 and active=0 while d.resume/d.start
    alone does not wake it up. Manual Start uses the same recovery path users already
    perform by hand: d.stop followed by d.open and d.start.
    """
    h = str(torrent_hash or '')
    if not h:
        return {'hash': h, 'ok': False, 'error': 'missing hash'}
    before = _download_runtime_state(c, h)
    result: dict = {'hash': h, 'before': before, 'commands': []}
    if before.get('manual_pause'):
        _set_manual_pause(c, h, False)
        result['commands'].append('clear_py_manual_pause')
        before = _download_runtime_state(c, h)

    if before.get('active'):
        if before.get('post_check'):
            clear_post_check_download_label(c, h, before.get('label'))
            before = _download_runtime_state(c, h)
        result.update({'ok': True, 'skipped': 'already_active', 'after': before})
        return result

    if (before.get('paused') and not prefer_start) or before.get('queued') or before.get('post_check'):
        try:
            # Note: Start intentionally normalizes open/inactive torrents through Stop -> Start because d.resume can leave them stuck.
            c.call('d.stop', h)
            result['commands'].append('d.stop')
        except Exception as exc:
            result.setdefault('ignored_errors', []).append(f'd.stop: {exc}')
    try:
        c.call('d.open', h)
        result['commands'].append('d.open')
    except Exception as exc:
        result.setdefault('ignored_errors', []).append(f'd.open: {exc}')
    try:
        c.call('d.start', h)
        result['commands'].append('d.start')
    except Exception as exc:
        result.setdefault('ignored_errors', []).append(f'd.start: {exc}')
        try:
            c.call('d.try_start', h)
            result['commands'].append('d.try_start')
        except Exception as exc2:
            result.setdefault('ignored_errors', []).append(f'd.try_start: {exc2}')
            result['ok'] = False
    after = _download_runtime_state(c, h)
    if before.get('post_check') and after.get('active'):
        # Note: The marker stays in place when start fails so the row remains visible in the Post-check filter.
        clear_post_check_download_label(c, h, before.get('label'))
        result['commands'].append('clear_post_check_label')
        after = _download_runtime_state(c, h)
    result['after'] = after
    result['ok'] = result.get('ok', True)
    return result


def _read_exported_torrent_bytes(profile: dict, torrent_hash: str) -> tuple[bytes, dict]:
    item = export_torrent_file(profile, torrent_hash)
    if item.get("local"):
        return LocalPath(str(item.get("path") or "")).read_bytes(), item
    data = b"".join(bytes(chunk) for chunk in iter_remote_file_chunks(profile, str(item.get("path") or "")) if chunk)
    if not data:
        raise RuntimeError(f"Cannot read exported torrent file for {torrent_hash}")
    return data, item


def _move_profile_transfer_data(source_client: ScgiRtorrentClient, torrent_hash: str, target_path: str) -> dict:
    """Move one torrent data path for a profile transfer after backend permission checks."""
    src = _remote_clean_path(_torrent_data_path(source_client, torrent_hash))
    if not src:
        raise ValueError(f"Cannot determine source path for {torrent_hash}")
    dst = _remote_join(target_path, posixpath.basename(src.rstrip("/")))
    try:
        source_client.call("d.stop", torrent_hash)
    except Exception:
        pass
    try:
        source_client.call("d.close", torrent_hash)
    except Exception:
        pass
    if src == dst:
        return {"skipped_data_move": "source and destination are the same"}
    _run_remote_move(source_client, src, dst)
    return {"moved_from": src, "moved_to": dst}


def transfer_profile(source_profile: dict, target_profile: dict, torrent_hashes: list[str], payload: dict | None = None, checkpoint=None, resume_state: dict | None = None) -> dict:
    """Move torrent entries between rTorrent profiles; data moving is delegated to a separate helper."""
    payload = payload or {}
    resume_state = resume_state or {}
    target_path = _remote_clean_path(payload.get("target_path") or payload.get("path") or "")
    move_data = bool(payload.get("move_data"))
    post_action = str(payload.get("post_action") or "none").strip().lower()
    if post_action not in {"none", "current", "start", "stop", "pause", "check", "recheck"}:
        raise ValueError("Unsupported post-transfer action")
    label_mode = str(payload.get("label_mode") or "none").strip().lower()
    label_value = str(payload.get("label_value") or "").strip()
    if label_mode not in {"none", "custom", "moved_from", "moved_to"}:
        label_mode = "none"
    if label_mode == "moved_from":
        label_value = f"Moved from {source_profile.get('name') or source_profile.get('id') or 'profile'}"
    elif label_mode == "moved_to":
        label_value = f"Moved to {target_profile.get('name') or target_profile.get('id') or 'profile'}"
    elif label_mode != "custom":
        label_value = ""
    if len(label_value) > 120:
        label_value = label_value[:120]
    if not target_path or not target_path.startswith("/") or target_path == "/":
        raise ValueError("Missing or unsafe target path")
    completed_hashes = set(str(x) for x in (resume_state.get("completed_hashes") or []))
    previous_results = list(resume_state.get("results") or [])
    source_client = client_for(source_profile)
    target_client = client_for(target_profile)

    def mark_done(torrent_hash: str, results: list) -> None:
        completed_hashes.add(str(torrent_hash))
        if checkpoint:
            checkpoint({"completed_hashes": sorted(completed_hashes), "results": results}, len(completed_hashes), len(torrent_hashes))

    results = previous_results
    for h in [x for x in torrent_hashes if str(x) not in completed_hashes]:
        item = {
            "hash": h,
            "source_profile_id": int(source_profile.get("id") or 0),
            "target_profile_id": int(target_profile.get("id") or 0),
            "target_path": target_path,
            "move_data": move_data,
            "move_data_requested": bool(payload.get("move_data_requested")),
            "move_data_downgraded": bool(payload.get("move_data_downgraded")),
        }
        try:
            data, exported = _read_exported_torrent_bytes(source_profile, h)
        except RuntimeError as export_exc:
            if "Cannot find torrent source file in rTorrent" not in str(export_exc):
                raise
            item["ok"] = False
            item["error"] = str(export_exc)
            item["skipped"] = "missing_torrent_metadata"
            item["hint"] = "rTorrent did not expose a readable .torrent source and could not save one from its session."
            results.append(item)
            mark_done(h, results)
            continue
        item["exported_from"] = exported.get("path")
        if exported.get("session_saved_by"):
            # Note: This identifies transfers recovered from rTorrent session state rather than an original watch/source file.
            item["metadata_recovered_by"] = exported.get("session_saved_by")
        limit = validate_torrent_upload_size(target_profile, data, False, target_path, "")
        if not limit.get("ok"):
            raise RuntimeError(f"Target profile XML-RPC limit is too small for {h}: {limit.get('request_h')} > {limit.get('limit_h')}")
        try:
            label = str(source_client.call("d.custom1", h) or "")
        except Exception:
            label = ""
        target_label = label_value if label_value else label
        try:
            was_state = int(source_client.call("d.state", h) or 0)
        except Exception:
            was_state = 0
        try:
            was_active = int(source_client.call("d.is_active", h) or 0)
        except Exception:
            was_active = was_state
        moved_to = ""
        if move_data:
            move_result = _move_profile_transfer_data(source_client, h, target_path)
            item.update(move_result)
            moved_to = str(move_result.get("moved_to") or "")
        # Note: The default keeps the torrent status from the source profile; explicit actions override it.
        start_on_target = bool(was_state or was_active) if post_action in {"none", "current"} else post_action == "start"
        try:
            added = add_torrent_raw(target_profile, data, start_on_target, target_path, target_label)
            if not added.get("ok"):
                raise RuntimeError(added.get("error") or "target add failed")
        except Exception:
            if move_data and moved_to:
                try:
                    source_client.call("d.directory.set", h, target_path)
                    if was_state or was_active:
                        source_client.call("d.start", h)
                    item["rollback"] = "source torrent kept and pointed at moved data"
                except Exception as rollback_exc:
                    item["rollback_error"] = str(rollback_exc)
            raise
        if post_action in {"stop", "pause", "check", "recheck"}:
            try:
                if post_action == "stop":
                    target_client.call("d.stop", h)
                elif post_action == "pause":
                    pause_hash(target_client, h)
                else:
                    target_client.call("d.check_hash", h)
                item["post_action_applied"] = post_action
            except Exception as post_exc:
                item["post_action_error"] = str(post_exc)
        source_client.call("d.erase", h)
        item["target_started"] = start_on_target
        item["label"] = target_label
        item["previous_label"] = label
        item["post_action"] = post_action
        results.append(item)
        mark_done(h, results)
    errors = [item for item in results if item.get("error")]
    moved_count = len([item for item in results if not item.get("error")])
    return {"ok": True, "count": moved_count, "requested_count": len(torrent_hashes), "move_data": move_data, "target_profile_id": int(target_profile.get("id") or 0), "target_path": target_path, "label": label_value, "post_action": post_action, "results": results, "errors": errors}

def action(profile: dict, torrent_hashes: list[str], name: str, payload: dict | None = None, checkpoint=None, resume_state: dict | None = None) -> dict:
    payload = payload or {}
    resume_state = resume_state or {}
    completed_hashes = set(str(x) for x in (resume_state.get("completed_hashes") or []))
    previous_results = list(resume_state.get("results") or [])

    def mark_done(torrent_hash: str, item: dict, results: list) -> None:
        completed_hashes.add(str(torrent_hash))
        state = {"completed_hashes": sorted(completed_hashes), "results": results}
        if checkpoint:
            checkpoint(state, len(completed_hashes), len(torrent_hashes))

    def pending_hashes() -> list[str]:
        return [h for h in torrent_hashes if str(h) not in completed_hashes]

    c = client_for(profile)
    methods = {
        "stop": "d.stop",
        "recheck": "d.check_hash",
        "reannounce": "d.tracker_announce",
        "remove": "d.erase",
    }
    if name == "set_label":
        label = str(payload.get("label") or "").strip()
        results = previous_results
        for h in pending_hashes():
            c.call("d.custom1.set", h, label)
            item = {"hash": h, "label": label}
            results.append(item)
            mark_done(h, item, results)
        return {"ok": True, "count": len(torrent_hashes), "label": label, "results": results}
    if name == "set_ratio_group":
        group = str(payload.get("ratio_group") or "").strip()
        results = previous_results
        for h in pending_hashes():
            c.call("d.custom.set", h, "py_ratio_group", group)
            item = {"hash": h, "ratio_group": group}
            results.append(item)
            mark_done(h, item, results)
        return {"ok": True, "count": len(torrent_hashes), "ratio_group": group, "results": results}
    if name == "move":
        path = _remote_clean_path(payload.get("path") or "")
        move_data = bool(payload.get("move_data"))
        recheck = bool(payload.get("recheck", move_data))
        keep_seeding = bool(payload.get("keep_seeding"))
        # Note: Automations can force seeding after a physical move even if the torrent was not active before.
        if not path:
            raise ValueError("Missing path")
        results = previous_results
        if move_data:
            _rt_execute_allow_timeout(c, "execute.throw", "mkdir", "-p", path)
        for h in pending_hashes():
            item = {"hash": h, "path": path, "move_data": move_data, "keep_seeding": keep_seeding}
            try:
                was_state = int(c.call("d.state", h) or 0)
            except Exception:
                was_state = 0
            try:
                was_active = int(c.call("d.is_active", h) or 0)
            except Exception:
                was_active = was_state
            if move_data:
                if was_state == 0:
                    c.call("d.directory.set", h, path)
                    item["move_data"] = False
                    item["skipped"] = "state is 0; data is not present, only directory updated"
                    results.append(item)
                    mark_done(h, item, results)
                    continue
                src = _remote_clean_path(_torrent_data_path(c, h))
                if not src:
                    raise ValueError(f"Cannot determine source path for {h}")
                dst = _remote_join(path, posixpath.basename(src.rstrip("/")))
                if src != dst:
                    try:
                        c.call("d.stop", h)
                    except Exception:
                        pass
                    try:
                        c.call("d.close", h)
                    except Exception:
                        pass
                    _run_remote_move(c, src, dst)
                    item["moved_from"] = src
                    item["moved_to"] = dst
                else:
                    item["skipped"] = "source and destination are the same"
                c.call("d.directory.set", h, path)
                if recheck:
                    try:
                        c.call("d.check_hash", h)
                    except Exception as exc:
                        item["recheck_error"] = str(exc)
                if keep_seeding or was_state or was_active:
                    try:
                        c.call("d.start", h)
                        item["started_after_move"] = True
                    except Exception as exc:
                        item["start_after_move_error"] = str(exc)
            else:
                c.call("d.directory.set", h, path)
            results.append(item)
            mark_done(h, item, results)
        return {"ok": True, "count": len(torrent_hashes), "move_data": move_data, "keep_seeding": keep_seeding, "results": results}
    if name == "pause":
        # Note: The app pause action is now a pure d.pause so later resume works without stop/start.
        results = previous_results
        for h in pending_hashes():
            item = pause_hash(c, h)
            results.append(item)
            mark_done(h, item, results)
        return {"ok": True, "count": len(torrent_hashes), "remove_data": False, "results": results}
    if name in {"resume", "unpause"}:
        # Note: Resume/Unpause keeps native rTorrent resume semantics; Start is the recovery action for stuck open/inactive torrents.
        results = previous_results
        for h in pending_hashes():
            item = resume_paused_hash(c, h)
            results.append(item)
            mark_done(h, item, results)
        return {"ok": True, "count": len(torrent_hashes), "remove_data": False, "results": results}
    if name == "start":
        # Note: Start recovers stuck Paused/open-inactive rows with Stop -> Start while keeping normal stopped rows on d.start.
        results = previous_results
        for h in pending_hashes():
            item = start_or_resume_hash(c, h)
            results.append(item)
            mark_done(h, item, results)
        return {"ok": True, "count": len(torrent_hashes), "remove_data": False, "results": results}

    method = methods.get(name)
    if not method:
        raise ValueError(f"Unknown action: {name}")
    remove_data = bool(payload.get("remove_data")) if name == "remove" else False
    results = previous_results
    for h in pending_hashes():
        item = {"hash": h}
        if remove_data:
            item = _remove_torrent_data(c, h)
        c.call(method, h)
        if name == "recheck":
            # Note: Recheck is tracked so even very fast checks still receive the after-check start/stop policy.
            _mark_post_check_watch(int(profile.get("id") or 0), h)
        results.append(item)
        mark_done(h, item, results)
    return {"ok": True, "count": len(torrent_hashes), "remove_data": remove_data, "results": results}

def add_magnet(profile: dict, uri: str, start: bool = True, directory: str = "", label: str = "") -> dict:
    c = client_for(profile)
    commands = []
    if directory:
        commands.append(f"d.directory.set={directory}")
    if label:
        commands.append(f"d.custom1.set={label}")
    if start:
        c.load.start_verbose("", uri, *commands)
    else:
        c.load.normal("", uri, *commands)
    return {"ok": True}


def set_limits(profile: dict, down: int | None, up: int | None):
    """Set global speed limits in bytes/s.

    rTorrent XML-RPC setters need an empty target string as the first
    argument. Without it rTorrent returns: target must be a string.
    """
    c = client_for(profile)
    if down is not None:
        c.call("throttle.global_down.max_rate.set", "", int(down))
    if up is not None:
        c.call("throttle.global_up.max_rate.set", "", int(up))
    return {"ok": True, "down": int(down or 0), "up": int(up or 0)}


def add_torrent_raw(profile: dict, data: bytes, start: bool = True, directory: str = "", label: str = "", file_priorities: list[dict] | None = None) -> dict:
    c = client_for(profile)
    commands = []
    if directory:
        commands.append(f"d.directory.set={directory}")
    if label:
        commands.append(f"d.custom1.set={label}")
    # Note: File selection before start loads the torrent stopped, changes priorities, then starts it if requested.
    method = "load.raw" if file_priorities else ("load.raw_start" if start else "load.raw")
    c.call(method, "", Binary(data), *commands)
    info_hash = ""
    if file_priorities:
        try:
            from ..torrent_meta import parse_torrent
            info_hash = parse_torrent(data).get("info_hash") or ""
            set_file_priorities(profile, info_hash, file_priorities)
            if start:
                c.call("d.start", info_hash)
        except Exception as exc:
            return {"ok": False, "info_hash": info_hash, "error": str(exc)}
    return {"ok": True, "info_hash": info_hash}



# Note: Export all service functions, including compatibility helpers used by routes and older imports.
__all__ = [
    name for name in globals()
    if not name.startswith("__") and name not in {"annotations"}
]
