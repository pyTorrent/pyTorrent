from __future__ import annotations
from typing import Any
from threading import RLock
import time
from .client import *
from .config import default_download_path
from ...utils import human_size


_PATH_BROWSE_EMPTY_CHECK_THRESHOLD = 500
_PATH_BROWSE_DEFAULT_MAX_DIRS = 3000
_PATH_BROWSE_HARD_MAX_DIRS = 50000
_PATH_BROWSE_SEARCH_MIN_CHARS = 2
_PATH_BROWSE_LISTING_CACHE_TTL_SECONDS = 900
_PATH_BROWSE_LISTING_CACHE_MAX_ENTRIES = 50000
_PATH_BROWSE_LISTING_CACHE: dict[tuple[int, str], tuple[float, dict[str, Any]]] = {}
_PATH_BROWSE_LISTING_CACHE_LOCK = RLock()


def _rtorrent_home_path(profile: dict) -> str:
    # Note: This reads the remote rTorrent process home, not the pyTorrent server home.
    try:
        c = client_for(profile)
        return _remote_clean_path(str(_rt_execute(c, "execute.capture", "sh", "-c", 'printf "%s" "${HOME:-}"') or "").strip())
    except Exception:
        return ""


def _append_path_browse_candidate(candidates: list[str], value: str) -> None:
    clean = _remote_clean_path(value or "")
    if clean and clean.startswith("/") and clean != "/" and clean not in candidates:
        candidates.append(clean)


def _path_browse_fallback_candidates(profile: dict) -> list[str]:
    candidates: list[str] = []
    download_path = _remote_clean_path(default_download_path(profile) or "")
    download_parent = _remote_clean_path(posixpath.dirname(download_path.rstrip("/")) if download_path else "")

    # Note: Fallback prefers the configured download area, then its parent, then the rTorrent user home.
    _append_path_browse_candidate(candidates, download_path)
    _append_path_browse_candidate(candidates, download_parent)
    _append_path_browse_candidate(candidates, _rtorrent_home_path(profile))
    return candidates


def _remote_accessible_directory(profile: dict, paths: list[str]) -> str:
    c = client_for(profile)
    script = (
        'for base in "$@"; do '
        '[ -n "$base" ] || continue; '
        '[ "$base" = "/" ] && continue; '
        '[ -d "$base" ] || continue; '
        '[ -L "$base" ] && continue; '
        '[ -r "$base" ] || continue; '
        '[ -x "$base" ] || continue; '
        'physical=$(cd -P -- "$base" 2>/dev/null && pwd -P) || continue; '
        '[ -n "$physical" ] || continue; '
        '[ "$physical" = "/" ] && continue; '
        'printf "%s" "$physical"; exit 0; '
        'done'
    )
    clean_paths = [_remote_clean_path(path or "") for path in paths if str(path or "").strip()]
    output = _rt_execute(c, "execute.capture", "sh", "-c", script, "pytorrent-access-check", *clean_paths)
    return _remote_clean_path(str(output or "").strip())


def _safe_browse_base(profile: dict, requested_path: str | None) -> tuple[str, str, bool]:
    fallback_candidates = _path_browse_fallback_candidates(profile)
    fallback = _remote_accessible_directory(profile, fallback_candidates)
    if not fallback:
        raise RuntimeError("Cannot determine an accessible rTorrent browse fallback")

    requested = _remote_clean_path(requested_path or fallback)
    if requested == "/":
        return fallback, fallback, True

    allowed = _remote_accessible_directory(profile, [requested])
    return (allowed or fallback), fallback, not bool(allowed)

def _shell_single_quote(value: str) -> str:
    return "'" + str(value).replace("'", "'\\''") + "'"


def _path_browse_script(search_mode: bool) -> str:
    # Note: Directory browsing runs through rTorrent RPC by design. Keep the remote command
    # simple and non-destructive: one first-level listing, no per-directory empty checks,
    # no internal max-seconds timeout. The UI shows a long-running notice after 59 seconds
    # while the backend request is still allowed to finish and populate cache.
    return (
        'base=$1; empty_threshold=$2; max_dirs=$3; query=$4; '
        'case "$max_dirs" in ""|*[!0-9]*) max_dirs=0;; esac; '
        '[ -d "$base" ] || { printf "E\tDirectory does not exist\n"; exit 0; }; '
        'dfline=$(df -Pk "$base" 2>/dev/null | awk "NR==2{print \\$2,\\$3,\\$4,\\$5}" || true); '
        'dir_count=0; file_count=0; printed=0; '
        'for p in "$base"/* "$base"/.[!.]* "$base"/..?*; do '
        '[ -e "$p" ] || continue; '
        '[ -L "$p" ] && continue; '
        'if [ -d "$p" ]; then '
        'dir_count=$((dir_count+1)); '
        'if { [ "$max_dirs" -le 0 ] || [ "$printed" -lt "$max_dirs" ]; } && [ -r "$p" ] && [ -x "$p" ]; then '
        'name=${p##*/}; printf "D\t%s\t%s\tU\n" "$name" "$p"; printed=$((printed+1)); '
        'fi; '
        'elif [ -f "$p" ]; then file_count=$((file_count+1)); fi; '
        'done; '
        'printf "M\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n" "$dir_count" "$file_count" "0" "$empty_threshold" "$max_dirs" "$dir_count" "$query" "0" "0"; '
        '[ -n "$dfline" ] && printf "F\t%s\n" "$dfline"; '
        'exit 0'
    )


def _path_listing_cache_key(profile: dict, base: str) -> tuple[int, str]:
    return (int(profile.get("id") or 0), _remote_clean_path(base or ""))


def _clone_path_listing(payload: dict[str, Any], *, include_dirs: bool = True) -> dict[str, Any]:
    cloned = dict(payload)
    if include_dirs:
        cloned["dirs"] = [dict(item) for item in payload.get("dirs") or []]
    else:
        cloned.pop("dirs", None)
    return cloned


def _cached_path_listing(profile: dict, base: str) -> dict[str, Any] | None:
    # Return the cached object directly. Callers must not mutate it; slicing creates
    # a fresh response with only the requested visible directories. This avoids copying
    # tens of thousands of directory dictionaries on every cache hit.
    key = _path_listing_cache_key(profile, base)
    now = time.monotonic()
    with _PATH_BROWSE_LISTING_CACHE_LOCK:
        cached = _PATH_BROWSE_LISTING_CACHE.get(key)
        if not cached:
            return None
        created, payload = cached
        if now - created > _PATH_BROWSE_LISTING_CACHE_TTL_SECONDS:
            _PATH_BROWSE_LISTING_CACHE.pop(key, None)
            return None
        return payload


def _store_path_listing(profile: dict, base: str, payload: dict[str, Any]) -> None:
    key = _path_listing_cache_key(profile, base)
    cached_payload = _clone_path_listing(payload)
    cached_payload["dirs"] = cached_payload.get("dirs", [])[:_PATH_BROWSE_LISTING_CACHE_MAX_ENTRIES]
    with _PATH_BROWSE_LISTING_CACHE_LOCK:
        _PATH_BROWSE_LISTING_CACHE[key] = (time.monotonic(), cached_payload)
        if len(_PATH_BROWSE_LISTING_CACHE) > 128:
            oldest = sorted(_PATH_BROWSE_LISTING_CACHE.items(), key=lambda item: item[1][0])[:32]
            for old_key, _ in oldest:
                _PATH_BROWSE_LISTING_CACHE.pop(old_key, None)


def _slice_path_listing(payload: dict[str, Any], *, limit: int, query: str, search_too_short: bool) -> dict[str, Any]:
    # Build the response from cached metadata plus only the visible directory rows.
    # Do not clone or JSON-build the full cached list unless the caller explicitly
    # asks for all=1. This keeps normal cached browse calls cheap.
    result = _clone_path_listing(payload, include_dirs=False)
    all_dirs = payload.get("dirs") or []
    effective_query = "" if search_too_short else str(query or "").strip()
    if effective_query:
        query_lc = effective_query.lower()
        matched_count = 0
        visible_dirs: list[dict[str, Any]] = []
        for item in all_dirs:
            if query_lc not in str(item.get("name") or "").lower():
                continue
            matched_count += 1
            if limit == 0 or len(visible_dirs) < limit:
                visible_dirs.append(dict(item))
        result["matched_dir_count"] = matched_count
    else:
        result["matched_dir_count"] = None
        selected = all_dirs if limit == 0 else all_dirs[:limit]
        visible_dirs = [dict(item) for item in selected]
    result["dirs"] = visible_dirs
    result["displayed_dir_count"] = len(visible_dirs)
    result["max_dirs"] = limit
    result["search"] = query
    result["effective_search"] = effective_query
    result["search_too_short"] = search_too_short
    result["search_timed_out"] = False
    result["search_partial"] = False
    result["search_min_chars"] = _PATH_BROWSE_SEARCH_MIN_CHARS
    result["listing_cache_ttl_seconds"] = _PATH_BROWSE_LISTING_CACHE_TTL_SECONDS
    result["warning"] = f"Type at least {_PATH_BROWSE_SEARCH_MIN_CHARS} characters to search this folder." if search_too_short else ""
    return result


def browse_path(profile: dict, path: str | None = None, *, max_dirs: int | None = None, search: str | None = None, cache_only: bool = False) -> dict:
    """List allowed rTorrent directories through one lightweight remote listing and cache it for search/show-all."""
    c = client_for(profile)
    base, fallback_root, used_fallback = _safe_browse_base(profile, path)
    try:
        limit = int(max_dirs if max_dirs is not None else _PATH_BROWSE_DEFAULT_MAX_DIRS)
    except Exception:
        limit = _PATH_BROWSE_DEFAULT_MAX_DIRS
    if limit < 0:
        limit = 0
    if limit > _PATH_BROWSE_HARD_MAX_DIRS:
        limit = _PATH_BROWSE_HARD_MAX_DIRS
    query = str(search or "").strip()
    search_too_short = bool(query) and len(query) < _PATH_BROWSE_SEARCH_MIN_CHARS

    cached = _cached_path_listing(profile, base)
    if cached is not None:
        cached["listing_cached"] = True
        return _slice_path_listing(cached, limit=limit, query=query, search_too_short=search_too_short)
    if cache_only:
        return {
            "path": base,
            "parent": posixpath.dirname(base.rstrip("/")) or "/",
            "root": fallback_root,
            "allowed_roots": [fallback_root],
            "access_policy": "rtorrent-permissions",
            "fallback": used_fallback,
            "dirs": [],
            "source": "rtorrent",
            "dir_count": None,
            "file_count": None,
            "displayed_dir_count": 0,
            "matched_dir_count": None,
            "search": query,
            "effective_search": "" if search_too_short else query,
            "search_too_short": search_too_short,
            "search_timed_out": False,
            "search_partial": False,
            "search_min_chars": _PATH_BROWSE_SEARCH_MIN_CHARS,
            "listing_cached": False,
            "listing_cache_ttl_seconds": _PATH_BROWSE_LISTING_CACHE_TTL_SECONDS,
            "warning": "No cached folder listing is ready yet. Wait for the running browse request to finish or try again.",
            "empty_check_performed": False,
            "empty_check_skipped": True,
            "empty_check_threshold": _PATH_BROWSE_EMPTY_CHECK_THRESHOLD,
            "max_dirs": limit,
            "default_max_dirs": _PATH_BROWSE_DEFAULT_MAX_DIRS,
            "hard_max_dirs": _PATH_BROWSE_HARD_MAX_DIRS,
            "total": 0,
            "used": 0,
            "free": 0,
            "total_h": human_size(0),
            "used_h": human_size(0),
            "free_h": human_size(0),
            "used_percent": 0,
        }

    # Note: The first browse intentionally collects the first-level directory list once. Search and show-all then
    # filter this cached list in Python instead of starting another remote filesystem scan.
    script = _path_browse_script(False)
    output = _rt_execute(
        c,
        "execute.capture",
        "sh",
        "-c",
        script,
        "pytorrent-browse",
        base,
        str(_PATH_BROWSE_EMPTY_CHECK_THRESHOLD),
        str(_PATH_BROWSE_LISTING_CACHE_MAX_ENTRIES),
        "",
    )
    dirs = []
    dir_count = 0
    file_count = 0
    empty_check_performed = True
    search_timed_out = False
    disk_total = disk_used = disk_free = 0
    disk_percent = 0
    remote_error = ""
    for line in str(output or "").splitlines():
        if "\t" not in line:
            continue
        marker, rest = line.split("\t", 1)
        if marker == "D" and "\t" in rest:
            parts = rest.split("\t", 2)
            name, full_path = parts[0], parts[1]
            empty_token = parts[2] if len(parts) > 2 else "U"
            is_empty = None if empty_token == "U" else empty_token == "1"
            if name not in {".", ".."}:
                dirs.append({"name": name, "path": full_path, "empty": is_empty, "empty_check_skipped": is_empty is None})
        elif marker == "M" and "\t" in rest:
            parts = rest.split("\t")
            try:
                dir_count = int(parts[0] or 0)
                file_count = int(parts[1] or 0) if len(parts) > 1 else 0
                empty_check_performed = (parts[2] if len(parts) > 2 else "1") == "1"
                if len(parts) > 7:
                    search_timed_out = (parts[7] or "0") == "1"
            except Exception:
                dir_count = file_count = 0
                empty_check_performed = True
        elif marker == "E":
            remote_error = rest.strip()
        elif marker == "F":
            parts = rest.split()
            if len(parts) >= 4:
                try:
                    disk_total = int(parts[0]) * 1024
                    disk_used = int(parts[1]) * 1024
                    disk_free = int(parts[2]) * 1024
                    disk_percent = int(str(parts[3]).rstrip("%") or 0)
                except Exception:
                    disk_total = disk_used = disk_free = disk_percent = 0
    dirs.sort(key=lambda x: x["name"].lower())
    parent = posixpath.dirname(base.rstrip("/")) or "/"
    if parent == base or parent == "/" or not _remote_accessible_directory(profile, [parent]):
        parent = base
    full_payload = {
        "path": base,
        "parent": parent,
        "root": fallback_root,
        "allowed_roots": [fallback_root],
        "access_policy": "rtorrent-permissions",
        "fallback": used_fallback,
        "dirs": dirs,
        "source": "rtorrent",
        "dir_count": dir_count,
        "file_count": file_count,
        "displayed_dir_count": len(dirs),
        "matched_dir_count": None,
        "search": "",
        "effective_search": "",
        "search_too_short": False,
        "search_timed_out": search_timed_out,
        "search_partial": search_timed_out,
        "search_min_chars": _PATH_BROWSE_SEARCH_MIN_CHARS,
        "listing_cached": False,
        "listing_cache_ttl_seconds": _PATH_BROWSE_LISTING_CACHE_TTL_SECONDS,
        "warning": remote_error or ("Folder listing timed out. Showing partial results." if search_timed_out else ""),
        "empty_check_performed": empty_check_performed,
        "empty_check_skipped": not empty_check_performed,
        "empty_check_threshold": _PATH_BROWSE_EMPTY_CHECK_THRESHOLD,
        "max_dirs": 0,
        "default_max_dirs": _PATH_BROWSE_DEFAULT_MAX_DIRS,
        "hard_max_dirs": _PATH_BROWSE_HARD_MAX_DIRS,
        "total": disk_total,
        "used": disk_used,
        "free": disk_free,
        "total_h": human_size(disk_total),
        "used_h": human_size(disk_used),
        "free_h": human_size(disk_free),
        "used_percent": disk_percent,
    }
    if not search_timed_out and not remote_error:
        _store_path_listing(profile, base, full_payload)
    return _slice_path_listing(full_payload, limit=limit, query=query, search_too_short=search_too_short)


def check_directory_rename_state(profile: dict, path: str) -> dict:
    """Check a single remote directory before inline rename."""
    c = client_for(profile)
    source = _remote_clean_path(path or "")
    if not source or source == "/":
        raise ValueError("Cannot rename this directory")
    script = (
        'src=$1; '
        'if [ ! -d "$src" ]; then printf "ERR\tDirectory does not exist"; exit 0; fi; '
        'if [ -L "$src" ]; then printf "ERR\tCannot rename this directory"; exit 0; fi; '
        '[ -r "$src" ] || { printf "ERR\tCannot read directory"; exit 0; }; '
        '[ -x "$src" ] || { printf "ERR\tCannot access directory"; exit 0; }; '
        'physical=$(cd -P -- "$src" 2>/dev/null && pwd -P) || { printf "ERR\tCannot access directory"; exit 0; }; '
        '[ -n "$physical" ] || { printf "ERR\tCannot access directory"; exit 0; }; '
        '[ "$physical" = "/" ] && { printf "ERR\tCannot rename this directory"; exit 0; }; '
        'empty=1; '
        'if find "$physical" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null | grep -q .; then empty=0; fi; '
        'printf "OK\t%s\t%s" "$physical" "$empty"'
    )
    output = str(_rt_execute(c, "execute.capture", "sh", "-c", script, "pytorrent-rename-check", source) or "").strip()
    if not output.startswith("OK\t"):
        raise RuntimeError(output.split("\t", 1)[1] if "\t" in output else "Cannot check directory")
    parts = output.split("\t")
    full_path = parts[1] if len(parts) > 1 else source
    is_empty = len(parts) > 2 and parts[2] == "1"
    return {"path": full_path, "name": posixpath.basename(full_path.rstrip("/")), "empty": is_empty, "empty_check_skipped": False}


def _safe_directory_name(name: str) -> str:
    value = str(name or "").strip()
    if not value or value in {".", ".."} or "/" in value or "\x00" in value:
        raise ValueError("Invalid directory name")
    return value


def create_directory(profile: dict, parent: str, name: str) -> dict:
    """Create a remote directory without changing existing path-picker behavior."""
    # Note: Directory creation is remote-side, so Add/Move sees the same filesystem as rTorrent.
    c = client_for(profile)
    clean_parent = _remote_clean_path(parent or default_download_path(profile))
    clean_name = _safe_directory_name(name)
    target = _remote_join(clean_parent, clean_name)
    script = (
        'parent=$1; target=$2; '
        'if [ ! -d "$parent" ]; then printf "ERR\tParent directory does not exist"; exit 0; fi; '
        'if [ -e "$target" ] || [ -L "$target" ]; then printf "ERR\tDirectory already exists"; exit 0; fi; '
        'mkdir -- "$target" 2>/dev/null || { printf "ERR\tCannot create directory"; exit 0; }; '
        'printf "OK\t%s" "$target"'
    )
    output = str(_rt_execute(c, "execute.capture", "sh", "-c", script, "pytorrent-mkdir", clean_parent, target) or "").strip()
    if not output.startswith("OK\t"):
        raise RuntimeError(output.split("\t", 1)[1] if "\t" in output else "Cannot create directory")
    return {"path": output.split("\t", 1)[1], "name": clean_name}


def rename_empty_directory(profile: dict, path: str, new_name: str) -> dict:
    """Rename an empty remote directory in place."""
    # Note: Rename is intentionally limited to empty folders to avoid invalidating active torrent paths.
    c = client_for(profile)
    source = _remote_clean_path(path or "")
    clean_name = _safe_directory_name(new_name)
    if not source or source == "/":
        raise ValueError("Cannot rename this directory")
    parent = posixpath.dirname(source.rstrip("/")) or "/"
    target = _remote_join(parent, clean_name)
    if source == target:
        return {"path": target, "name": clean_name, "parent": parent}
    script = (
        'src=$1; dst=$2; '
        'if [ ! -d "$src" ]; then printf "ERR\tDirectory does not exist"; exit 0; fi; '
        'if [ -e "$dst" ] || [ -L "$dst" ]; then printf "ERR\tTarget directory already exists"; exit 0; fi; '
        'if [ -n "$(find "$src" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]; then printf "ERR\tOnly empty directories can be renamed"; exit 0; fi; '
        'mv -- "$src" "$dst" 2>/dev/null || { printf "ERR\tCannot rename directory"; exit 0; }; '
        'printf "OK\t%s" "$dst"'
    )
    output = str(_rt_execute(c, "execute.capture", "sh", "-c", script, "pytorrent-rename-dir", source, target) or "").strip()
    if not output.startswith("OK\t"):
        raise RuntimeError(output.split("\t", 1)[1] if "\t" in output else "Cannot rename directory")
    return {"path": output.split("\t", 1)[1], "name": clean_name, "parent": parent}

def remote_public_ip(profile: dict, force: bool = False) -> str:
    profile_id = int(profile.get("id") or 0)
    now = time.monotonic()
    cached = _REMOTE_PUBLIC_IP_CACHE.get(profile_id)
    if cached and not force and now - cached[0] < _REMOTE_PUBLIC_IP_TTL_SECONDS:
        return cached[1]
    script = (
        'for url in https://ifconfig.co https://ifconfig.me http://ifconfig.co http://ifconfig.me; do '
        'ip=$(curl -fsS --max-time 8 "$url" 2>/dev/null | tr -d "\r" | head -n 1 | sed "s/[^0-9a-fA-F:.]//g"); '
        'if [ -n "$ip" ]; then printf "%s" "$ip"; exit 0; fi; '
        'done; exit 1'
    )
    value = str(_rt_execute(client_for(profile), "execute.capture", "sh", "-c", script) or "").strip()
    if not value:
        raise RuntimeError("Cannot read remote public IP")
    _REMOTE_PUBLIC_IP_CACHE[profile_id] = (now, value)
    return value


def remote_system_usage(profile: dict, force: bool = False) -> dict:
    profile_id = int(profile.get("id") or 0)
    now = time.monotonic()
    cached = _REMOTE_USAGE_CACHE.get(profile_id)
    if cached and not force and now - cached[0] < _REMOTE_USAGE_TTL_SECONDS:
        usage = dict(cached[1])
        usage["cached"] = True
        return usage
    script = (
        'read cpu user nice system idle iowait irq softirq steal guest guest_nice < /proc/stat; '
        'total1=$((user+nice+system+idle+iowait+irq+softirq+steal)); idle1=$((idle+iowait)); '
        'sleep 1; '
        'read cpu user nice system idle iowait irq softirq steal guest guest_nice < /proc/stat; '
        'total2=$((user+nice+system+idle+iowait+irq+softirq+steal)); idle2=$((idle+iowait)); '
        'dt=$((total2-total1)); di=$((idle2-idle1)); '
        'cpu_pct=$(awk -v dt="$dt" -v di="$di" "BEGIN { if (dt > 0) printf \"%.1f\", (dt-di)*100/dt; else printf \"0.0\" }"); '
        "mem_total=$(awk '/^MemTotal:/ {print $2}' /proc/meminfo); "
        "mem_avail=$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo); "
        'ram_pct=$(awk -v t="$mem_total" -v a="$mem_avail" "BEGIN { if (t > 0) printf \"%.1f\", (t-a)*100/t; else printf \"0.0\" }"); '
        'printf "%s %s" "$cpu_pct" "$ram_pct"'
    )
    output = str(_rt_execute(client_for(profile), "execute.capture", "sh", "-c", script) or "").strip()
    parts = output.split()
    if len(parts) < 2:
        raise RuntimeError(f"Cannot read remote CPU/RAM usage: {output}")
    usage = {"cpu": float(parts[0]), "ram": float(parts[1]), "source": "rtorrent-remote", "usage_source": "rtorrent-remote", "cached": False}
    _REMOTE_USAGE_CACHE[profile_id] = (now, usage)
    return dict(usage)


def _usage_dict(total: int, used: int, free: int) -> dict:
    total = max(0, int(total or 0))
    used = max(0, int(used or 0))
    free = max(0, int(free or 0))
    pct = round((used / total) * 100, 1) if total else 0.0
    return {
        "ok": True,
        "total": total,
        "used": used,
        "free": free,
        "total_h": human_size(total),
        "used_h": human_size(used),
        "free_h": human_size(free),
        "percent": pct,
    }


def _statvfs_usage(path: str) -> dict:
    stat = os.statvfs(path)
    total = int(stat.f_blocks * stat.f_frsize)
    free = int(stat.f_bavail * stat.f_frsize)
    used = max(0, total - free)
    return _usage_dict(total, used, free)


def _remote_df_usage(profile: dict, path: str) -> dict:
    # Note: Disk paths belong to the rTorrent host. Query df through rTorrent so NFS/Btrfs mounts are measured correctly.
    clean_path = _remote_clean_path(path or os.sep)
    cache_key = f"remote-df:{profile.get('id')}:{clean_path}"
    now = time.monotonic()
    cached = _DISK_USAGE_CACHE.get(cache_key)
    if cached and now - cached[0] < _DISK_USAGE_TTL_SECONDS:
        return dict(cached[1])
    script = (
        'path=$1; '
        'if [ ! -e "$path" ]; then echo "ERR\tmissing path"; exit 0; fi; '
        'line=$(df -Pk "$path" 2>/dev/null | tail -n 1); '
        'if [ -z "$line" ]; then echo "ERR\tdf failed"; exit 0; fi; '
        'set -- $line; pct=${5%\\%}; '
        'if [ -z "$2" ] || [ -z "$3" ] || [ -z "$4" ]; then echo "ERR\tdf parse failed"; exit 0; fi; '
        'printf "OK\t%s\t%s\t%s\t%s\t%s\n" "$2" "$3" "$4" "$pct" "$6"'
    )
    output = str(_rt_execute(client_for(profile), "execute.capture", "sh", "-c", script, "pytorrent-df", clean_path) or "").strip()
    first_line = output.splitlines()[0] if output else ""
    parts = first_line.split("\t")
    if len(parts) >= 6 and parts[0] == "OK":
        total = int(parts[1]) * 1024
        used = int(parts[2]) * 1024
        free = int(parts[3]) * 1024
        usage = _usage_dict(total, used, free)
        usage.update({"path": clean_path, "source_path": parts[5] or clean_path, "fallback": False, "measure_source": "rtorrent-df"})
    else:
        error = parts[1] if len(parts) > 1 else (output or "df returned no data")
        usage = {"ok": False, "path": clean_path, "source_path": clean_path, "error": error, "percent": 0, "measure_source": "rtorrent-df"}
    _DISK_USAGE_CACHE[cache_key] = (now, dict(usage))
    return usage


def _disk_usage_for_path(profile: dict, path: str, allow_parent_fallback: bool = False) -> dict:
    clean_path = _remote_clean_path(path or os.sep)
    try:
        return _remote_df_usage(profile, clean_path)
    except Exception as remote_exc:
        try:
            usage = _statvfs_usage(clean_path)
            usage.update({"path": clean_path, "source_path": clean_path, "fallback": False, "measure_source": "local-statvfs", "warning": str(remote_exc)})
            return usage
        except Exception as first_exc:
            usage = {"ok": False, "path": clean_path, "source_path": clean_path, "error": str(first_exc), "warning": str(remote_exc), "percent": 0}
            if not allow_parent_fallback:
                return usage
            probe = os.path.abspath(clean_path or os.sep)
            seen = set()
            while probe and probe not in seen:
                seen.add(probe)
                parent = os.path.dirname(probe)
                if parent == probe:
                    break
                probe = parent
                try:
                    usage = _statvfs_usage(probe)
                    usage.update({"path": clean_path, "source_path": probe, "fallback": True, "measure_source": "local-statvfs", "warning": str(first_exc)})
                    break
                except Exception:
                    continue
            return usage


def disk_usage_for_default_path(profile: dict) -> dict:
    """Filesystem usage for the rTorrent default download directory."""
    path = default_download_path(profile)
    cache_key = f"default-disk:{profile.get('id')}:{path}"
    now = time.monotonic()
    cached = _DISK_USAGE_CACHE.get(cache_key)
    if cached and now - cached[0] < _DISK_USAGE_TTL_SECONDS:
        return dict(cached[1])
    usage = _disk_usage_for_path(profile, path, allow_parent_fallback=True)
    _DISK_USAGE_CACHE[cache_key] = (now, dict(usage))
    return usage


def disk_usage_for_paths(profile: dict, paths: list[str] | None = None, mode: str = 'default', selected_path: str = '') -> dict:
    # Note: Aggregate/selected modes measure exact user paths on the rTorrent host; they do not fall back to parent/root partitions.
    default_path = default_download_path(profile)
    mode = mode if mode in {'default', 'selected', 'aggregate'} else 'default'
    user_paths: list[str] = []
    for item in paths or []:
        path = _remote_clean_path(str(item or '').strip())
        if path and path not in user_paths:
            user_paths.append(path)
    selected_path = _remote_clean_path(str(selected_path or '').strip())
    if mode == 'selected':
        source_paths = [selected_path] if selected_path else list(user_paths)
    elif mode == 'aggregate':
        source_paths = list(user_paths)
    else:
        source_paths = [default_path]
    if mode in {'selected', 'aggregate'} and not source_paths:
        source_paths = [default_path]
    clean_paths: list[str] = []
    for item in source_paths:
        path = _remote_clean_path(str(item or '').strip())
        if path and path not in clean_paths:
            clean_paths.append(path)
    entries = [_disk_usage_for_path(profile, path, allow_parent_fallback=(mode == 'default')) for path in clean_paths]
    chosen = entries[0] if entries else _disk_usage_for_path(profile, default_path, allow_parent_fallback=True)
    if mode == 'selected' and selected_path:
        chosen = next((x for x in entries if x.get('path') == selected_path), chosen)
    elif mode == 'aggregate':
        ok_entries = [x for x in entries if x.get('ok')]
        total = sum(int(x.get('total') or 0) for x in ok_entries)
        used = sum(int(x.get('used') or 0) for x in ok_entries)
        free = sum(int(x.get('free') or 0) for x in ok_entries)
        chosen = _usage_dict(total, used, free) if ok_entries else {"ok": False, "total": 0, "used": 0, "free": 0, "total_h": "0 B", "used_h": "0 B", "free_h": "0 B", "percent": 0}
        chosen.update({'path': 'aggregate', 'source_path': 'aggregate', 'fallback': False, 'measure_source': 'rtorrent-df'})
    chosen = dict(chosen)
    chosen['mode'] = mode
    chosen['paths'] = entries
    return chosen



_STATUS_META_CACHE: dict[int, dict[str, Any]] = {}
_STATUS_META_LOCK = RLock()


def _profile_cache_key(profile: dict) -> int:
    return int(profile.get("id") or 0)


def _adaptive_meta_ttl(duration_ms: float) -> float:
    # Note: Slow rTorrent metadata calls get a longer TTL, while fast servers keep the footer fresh.
    if duration_ms >= 5000:
        return 30.0
    if duration_ms >= 2000:
        return 15.0
    if duration_ms >= 800:
        return 8.0
    return 3.0


def _cached_rtorrent_meta(profile: dict, c: Any) -> dict[str, Any]:
    profile_id = _profile_cache_key(profile)
    now = time.monotonic()
    with _STATUS_META_LOCK:
        cached = _STATUS_META_CACHE.get(profile_id)
        if cached and now < float(cached.get("expires_at") or 0):
            meta = dict(cached.get("value") or {})
            meta["status_meta_cache"] = {"hit": True, "ttl_seconds": cached.get("ttl_seconds"), "duration_ms": cached.get("duration_ms")}
            return meta
    started = time.monotonic()
    version = str(c.system.client_version())
    try:
        down_limit = int(c.throttle.global_down.max_rate())
    except Exception:
        down_limit = 0
    try:
        up_limit = int(c.throttle.global_up.max_rate())
    except Exception:
        up_limit = 0
    meta = {
        "version": version,
        "down_limit": down_limit,
        "up_limit": up_limit,
        "down_limit_h": human_rate(down_limit) if down_limit else "∞",
        "up_limit_h": human_rate(up_limit) if up_limit else "∞",
        "open_sockets": _safe_rtorrent_first_int(c, ("network.open_sockets",)),
        "max_open_sockets": _safe_rtorrent_first_int(c, ("network.max_open_sockets",)),
        "open_files": _safe_rtorrent_first_int(c, ("network.open_files", "network.current_open_files", "network.open_file_count")),
        "max_open_files": _safe_rtorrent_first_int(c, ("network.max_open_files",)),
        "open_http": _safe_rtorrent_first_int(c, ("network.http.open", "network.http.current_open", "network.http.current_opened", "network.http.open_sockets")),
        "max_open_http": _safe_rtorrent_first_int(c, ("network.http.max_open",)),
        "max_downloads_global": _safe_rtorrent_first_int(c, ("throttle.max_downloads.global",)),
        "max_uploads_global": _safe_rtorrent_first_int(c, ("throttle.max_uploads.global",)),
        "listen_port": _rtorrent_listen_port(c),
        "rtorrent_time": _safe_rtorrent_time(c),
    }
    duration_ms = round((time.monotonic() - started) * 1000.0, 2)
    ttl = _adaptive_meta_ttl(duration_ms)
    with _STATUS_META_LOCK:
        _STATUS_META_CACHE[profile_id] = {"value": dict(meta), "expires_at": now + ttl, "ttl_seconds": ttl, "duration_ms": duration_ms}
    meta["status_meta_cache"] = {"hit": False, "ttl_seconds": ttl, "duration_ms": duration_ms}
    return meta


def clear_profile_runtime_caches(profile_id: int) -> dict[str, int]:
    """Clear rTorrent runtime caches that are scoped to a single profile."""
    # Note: This is used by Cleanup to force fresh disk/status/remote readings without restarting pyTorrent.
    profile_id = int(profile_id or 0)
    removed = {"disk_usage": 0, "remote_usage": 0, "remote_public_ip": 0, "status_meta": 0}
    prefix_candidates = (f"default-disk:{profile_id}:", f"remote-df:{profile_id}:")
    for key in list(_DISK_USAGE_CACHE.keys()):
        if any(str(key).startswith(prefix) for prefix in prefix_candidates):
            _DISK_USAGE_CACHE.pop(key, None)
            removed["disk_usage"] += 1
    if _REMOTE_USAGE_CACHE.pop(profile_id, None) is not None:
        removed["remote_usage"] += 1
    if _REMOTE_PUBLIC_IP_CACHE.pop(profile_id, None) is not None:
        removed["remote_public_ip"] += 1
    with _STATUS_META_LOCK:
        if _STATUS_META_CACHE.pop(profile_id, None) is not None:
            removed["status_meta"] += 1
    return removed

def _safe_rtorrent_int(callable_obj, default=None):
    """Return an integer rTorrent metric without failing the whole status poll."""
    try:
        value = callable_obj()
        return int(value)
    except Exception:
        return default


def _safe_rtorrent_value(callable_obj, default=None):
    """Return any rTorrent metric without failing the whole status poll."""
    try:
        value = callable_obj()
        return default if value is None else value
    except Exception:
        return default



def _rtorrent_read_candidates(method_name: str) -> tuple[str, ...]:
    """Return getter variants used by different rTorrent XMLRPC builds."""
    name = str(method_name or "").strip()
    if not name:
        return tuple()
    candidates = [name]
    if not name.endswith("="):
        candidates.append(f"{name}=")
    else:
        candidates.append(name.rstrip("="))
    return tuple(dict.fromkeys(candidates))


def _safe_rtorrent_first_int(c, method_names, default=None):
    """Try several rTorrent XMLRPC getter names and return the first integer value."""
    for method_name in method_names:
        for candidate in _rtorrent_read_candidates(method_name):
            value = _safe_rtorrent_int(lambda name=candidate: c.call(name), None)
            if value is not None:
                return value
    return default


def _safe_rtorrent_first_value(c, method_names, default=None):
    """Try several rTorrent XMLRPC getter names and return the first non-empty value."""
    for method_name in method_names:
        for candidate in _rtorrent_read_candidates(method_name):
            value = _safe_rtorrent_value(lambda name=candidate: c.call(name), None)
            if value not in (None, ""):
                return value
    return default


def _rtorrent_listen_port(c):
    """Return the configured incoming port, preferring network.port_range over port-open state."""
    port_range = _safe_rtorrent_first_value(c, ("network.port_range",))
    if port_range:
        first = str(port_range).split("-", 1)[0].strip()
        if first:
            return first
    value = _safe_rtorrent_first_value(c, ("network.port_open", "network.open_port"))
    if value not in (None, ""):
        return value
    return None

def _safe_rtorrent_time(c):
    """Read rTorrent server time when supported; otherwise let the browser clock remain authoritative."""
    candidates = (
        lambda: c.system.time_seconds(),
        lambda: c.system.time(),
    )
    for candidate in candidates:
        value = _safe_rtorrent_int(candidate)
        if value:
            return value
    return None

def system_status(profile: dict, rows: list[dict] | None = None) -> dict:
    c = client_for(profile)
    meta = _cached_rtorrent_meta(profile, c)
    if rows is None:
        from .torrents import list_torrents
        rows = list_torrents(profile)
    else:
        rows = list(rows)
    # Note: ruTorrent-style footer metadata is cached adaptively; live speeds still come from fresh torrent rows.
    checking_count = sum(1 for t in rows if t.get("status") == "Checking" or int(t.get("hashing") or 0) > 0)
    active_downloads = sum(1 for t in rows if not t["complete"] and t["state"] and not t.get("paused") and t.get("status") != "Checking")
    active_uploads = sum(1 for t in rows if t["complete"] and t["state"] and not t.get("paused"))
    return {
        "ok": True,
        "version": meta.get("version"),
        "total": len(rows),
        "active": sum(1 for t in rows if t["state"]),
        "seeding": sum(1 for t in rows if t["complete"] and t["state"] and not t.get("paused")),
        "leeching": sum(1 for t in rows if not t["complete"] and t["state"] and not t.get("paused") and t.get("status") != "Checking"),
        "checking": checking_count,
        "paused": sum(1 for t in rows if t.get("paused")),
        "stopped": sum(1 for t in rows if not t["state"]),
        "down_rate": sum(t["down_rate"] for t in rows),
        "down_rate_h": human_rate(sum(t["down_rate"] for t in rows)),
        "up_rate": sum(t["up_rate"] for t in rows),
        "up_rate_h": human_rate(sum(t["up_rate"] for t in rows)),
        "down_limit": meta.get("down_limit", 0),
        "up_limit": meta.get("up_limit", 0),
        "down_limit_h": meta.get("down_limit_h", "∞"),
        "up_limit_h": meta.get("up_limit_h", "∞"),
        "total_down": sum(t["down_total"] for t in rows),
        "total_up": sum(t["up_total"] for t in rows),
        "total_down_h": human_size(sum(t["down_total"] for t in rows)),
        "total_up_h": human_size(sum(t["up_total"] for t in rows)),
        "open_sockets": meta.get("open_sockets"),
        "max_open_sockets": meta.get("max_open_sockets"),
        "open_files": meta.get("open_files"),
        "max_open_files": meta.get("max_open_files"),
        "open_http": meta.get("open_http"),
        "max_open_http": meta.get("max_open_http"),
        "active_downloads": active_downloads,
        "max_downloads_global": meta.get("max_downloads_global"),
        "active_uploads": active_uploads,
        "max_uploads_global": meta.get("max_uploads_global"),
        "listen_port": meta.get("listen_port"),
        "rtorrent_time": meta.get("rtorrent_time"),
        "status_meta_cache": meta.get("status_meta_cache", {}),
        "disk": disk_usage_for_default_path(profile),
    }


__all__ = [
    name for name in globals()
    if not name.startswith("__") and name not in {"annotations"}
]
