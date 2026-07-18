from __future__ import annotations
from .client import *

RTORRENT_CONFIG_FIELDS = [
    {
        "group": "Directories",
        "key": "directory.default",
        "label": "Default download directory",
        "type": "text",
        "description": "Main destination for new downloads added without an explicit directory.",
        "recommendation": "Use a stable absolute path on storage with enough free space; avoid changing it while active torrents use relative paths.",
    },
    {
        "group": "Directories",
        "key": "session.path",
        "label": "Session path",
        "type": "text",
        "description": "Directory where rTorrent stores session state, resume data and internal torrent metadata.",
        "recommendation": "Keep it on reliable local storage and include it in backups before maintenance.",
    },
    {
        "group": "Directories",
        "key": "system.cwd",
        "label": "Working directory",
        "type": "text",
        "readonly": True,
        "description": "Current rTorrent process working directory reported by rTorrent.",
        "recommendation": "Read-only diagnostic value; change it in the service or startup configuration if needed.",
    },
    {
        "group": "Network",
        "key": "network.port_range",
        "label": "Incoming port range",
        "type": "text",
        "placeholder": "49164-49164",
        "description": "TCP port or range used for incoming peer connections.",
        "recommendation": "Use a fixed forwarded port, for example 49164-49164, for stable connectivity.",
    },
    {
        "group": "Network",
        "key": "network.port_random",
        "label": "Random incoming port",
        "type": "bool",
        "description": "Lets rTorrent select a random incoming port on startup.",
        "recommendation": "Disable it when using router/NAT forwarding; fixed ports are easier to monitor.",
    },
    {
        "group": "Network",
        "key": "network.bind_address",
        "label": "Bind address",
        "type": "text",
        "placeholder": "0.0.0.0",
        "description": "Local interface address used for peer traffic binding.",
        "recommendation": "Leave empty unless the host has multiple interfaces or policy routing.",
    },
    {
        "group": "Network",
        "key": "network.local_address",
        "label": "Announced local address",
        "type": "text",
        "description": "Address rTorrent may announce as its local network address.",
        "recommendation": "Usually leave empty; set only when a specific advertised address is required.",
    },
    {
        "group": "Network",
        "key": "network.max_open_files",
        "label": "Max open files",
        "type": "number",
        "description": "Maximum number of files rTorrent can keep open at once.",
        "recommendation": "Raise together with the OS file descriptor limit on large seeds.",
    },
    {
        "group": "Network",
        "key": "network.max_open_sockets",
        "label": "Max open sockets",
        "type": "number",
        "description": "Upper bound for peer and tracker sockets opened by rTorrent.",
        "recommendation": "Keep below OS limits; increase gradually when many torrents are active.",
    },
    {
        "group": "Network",
        "key": "network.http.max_open",
        "label": "Max HTTP connections",
        "type": "number",
        "description": "Maximum simultaneous HTTP connections for tracker and metadata requests.",
        "recommendation": "Moderate values reduce tracker pressure; increase only if tracker requests queue up.",
    },
    {
        "group": "Network",
        "key": "network.http.dns_cache_timeout",
        "label": "HTTP DNS cache timeout",
        "type": "number",
        "description": "Seconds rTorrent keeps DNS results for tracker and HTTP requests.",
        "recommendation": "Use a small positive value, for example 25, when many tracker hostnames are queried repeatedly.",
        "runtime_note": "Applied through SCGI immediately; new HTTP lookups use the updated timeout.",
    },
    {
        "group": "Network",
        "key": "network.http.ssl_verify_peer",
        "label": "Verify SSL peers",
        "type": "bool",
        "description": "Controls certificate verification for HTTPS tracker connections.",
        "recommendation": "Keep enabled unless a private tracker has a known certificate problem.",
    },
    {
        "group": "Network",
        "key": "network.xmlrpc.size_limit",
        "label": "XML-RPC upload size limit",
        "type": "text",
        "placeholder": "16M",
        "description": "Maximum XML-RPC payload size accepted by rTorrent.",
        "recommendation": "Keep enough headroom for large UI responses; avoid very high values on public endpoints.",
    },
    {
        "group": "Peers",
        "key": "throttle.min_peers.normal",
        "label": "Min peers while downloading",
        "type": "number",
        "description": "Minimum peer target for incomplete torrents.",
        "recommendation": "Use a conservative floor; too high values can waste sockets on weak swarms.",
    },
    {
        "group": "Peers",
        "key": "throttle.max_peers.normal",
        "label": "Max peers while downloading",
        "type": "number",
        "description": "Maximum peer target for incomplete torrents.",
        "recommendation": "Increase for fast lines, but keep total sockets and CPU usage under control.",
    },
    {
        "group": "Peers",
        "key": "throttle.min_peers.seed",
        "label": "Min peers while seeding",
        "type": "number",
        "description": "Minimum peer target for complete torrents.",
        "recommendation": "Lower than download min peers is usually enough for long-term seeding.",
    },
    {
        "group": "Peers",
        "key": "throttle.max_peers.seed",
        "label": "Max peers while seeding",
        "type": "number",
        "description": "Maximum peer target for complete torrents.",
        "recommendation": "Avoid excessive values on many seeding torrents because sockets multiply quickly.",
    },
    {
        "group": "Peers",
        "key": "trackers.numwant",
        "label": "Tracker numwant",
        "type": "number",
        "description": "Number of peers requested from trackers per announce where supported.",
        "recommendation": "Use moderate values; many trackers cap this server-side anyway.",
    },
    {
        "group": "Throttle",
        "key": "throttle.global_down.max_rate",
        "label": "Global download limit B/s",
        "type": "number",
        "description": "Global download speed cap in bytes per second. Zero usually means unlimited.",
        "recommendation": "Leave unlimited or cap below line speed if other services share the connection.",
    },
    {
        "group": "Throttle",
        "key": "throttle.global_up.max_rate",
        "label": "Global upload limit B/s",
        "type": "number",
        "description": "Global upload speed cap in bytes per second. Zero usually means unlimited.",
        "recommendation": "Keep below real upstream capacity to avoid bufferbloat and slow downloads.",
    },
    {
        "group": "Throttle",
        "key": "throttle.max_downloads.global",
        "label": "Global download slots",
        "type": "number",
        "description": "Global number of peer download slots across all torrents; this is not the active torrent count.",
        "recommendation": "Raise this on large instances so a few busy torrents do not starve the rest.",
        "runtime_note": "Applied through SCGI immediately; existing peer scheduling catches up gradually.",
    },
    {
        "group": "Throttle",
        "key": "throttle.max_uploads.global",
        "label": "Global upload slots",
        "type": "number",
        "description": "Global number of peer upload slots across all torrents; this is not the active torrent count.",
        "recommendation": "Keep enough slots for many seeds, but stay below socket and file descriptor limits.",
        "runtime_note": "Applied through SCGI immediately; current peer connections may rebalance over time.",
    },
    {
        "group": "Throttle",
        "key": "throttle.max_downloads",
        "label": "Per-torrent download slots",
        "type": "number",
        "description": "Maximum peer download slots allowed for a single torrent in the default throttle group.",
        "recommendation": "Use values like 5-20 to prevent one torrent from consuming all global download slots.",
        "runtime_note": "Applied through SCGI immediately; it affects new and rebalanced peer slot allocation.",
    },
    {
        "group": "Throttle",
        "key": "throttle.max_uploads",
        "label": "Per-torrent upload slots",
        "type": "number",
        "description": "Maximum peer upload slots allowed for a single torrent in the default throttle group.",
        "recommendation": "Use conservative values on very large seedboxes so many seeds can stay reachable.",
        "runtime_note": "Applied through SCGI immediately; it affects new and rebalanced peer slot allocation.",
    },
    {
        "group": "Throttle",
        "key": "throttle.max_downloads.div",
        "label": "Download slot divisor",
        "type": "number",
        "description": "Per-throttle download slot divisor used by rTorrent throttling logic.",
        "recommendation": "Keep at 1 unless you intentionally use advanced throttle groups.",
        "runtime_note": "Applied through SCGI immediately for the default throttle scheduler.",
    },
    {
        "group": "Throttle",
        "key": "throttle.max_uploads.div",
        "label": "Upload slot divisor",
        "type": "number",
        "description": "Per-throttle upload slot divisor used by rTorrent throttling logic.",
        "recommendation": "Keep at 1 unless you intentionally use advanced throttle groups.",
        "runtime_note": "Applied through SCGI immediately for the default throttle scheduler.",
    },
    {
        "group": "Ratio",
        "key": "ratio.max",
        "label": "Global ratio max",
        "type": "number",
        "description": "Global maximum ratio value used by rTorrent ratio logic where enabled.",
        "recommendation": "Use -1 for no global cap, or manage per-profile ratio policies from pyTorrent when possible.",
        "runtime_note": "Applied through SCGI immediately when the rTorrent ratio method is available.",
    },
    {
        "group": "DHT / PEX",
        "key": "dht.mode",
        "label": "DHT mode",
        "type": "text",
        "placeholder": "disable/off/auto/on",
        "description": "Controls Distributed Hash Table usage for peer discovery.",
        "recommendation": "Private-tracker setups often disable DHT; public torrents usually benefit from auto/on.",
    },
    {
        "group": "DHT / PEX",
        "key": "dht.port",
        "label": "DHT port",
        "type": "number",
        "description": "UDP port used by DHT traffic.",
        "recommendation": "Use the same forwarded port strategy as incoming TCP when DHT is enabled.",
    },
    {
        "group": "DHT / PEX",
        "key": "protocol.pex",
        "label": "Peer exchange",
        "type": "bool",
        "description": "Enables Peer Exchange peer discovery between connected peers.",
        "recommendation": "Disable for strict private-tracker policies; enable for public swarms if allowed.",
    },
    {
        "group": "DHT / PEX",
        "key": "trackers.use_udp",
        "label": "UDP trackers",
        "type": "bool",
        "description": "Allows rTorrent to use UDP trackers where supported.",
        "recommendation": "Keep enabled for public torrents unless the network blocks UDP tracker traffic.",
    },
    {
        "group": "Protocol",
        "key": "protocol.encryption.set",
        "label": "Encryption flags",
        "type": "text",
        "placeholder": "allow_incoming,try_outgoing,enable_retry",
        "description": "Encryption policy flags for peer connections.",
        "recommendation": "Prefer permissive settings unless a tracker or network requires strict encryption.",
    },
    {
        "group": "Protocol",
        "key": "protocol.connection.leech",
        "label": "Leech connection type",
        "type": "text",
        "placeholder": "leech",
        "description": "Connection behavior profile used by incomplete torrents.",
        "recommendation": "Leave default unless tuning advanced libTorrent behavior.",
    },
    {
        "group": "Protocol",
        "key": "protocol.connection.seed",
        "label": "Seed connection type",
        "type": "text",
        "placeholder": "seed",
        "description": "Connection behavior profile used by complete torrents.",
        "recommendation": "Leave default unless tuning advanced libTorrent behavior.",
    },
    {
        "group": "Files",
        "key": "pieces.hash.on_completion",
        "label": "Hash check on completion",
        "type": "bool",
        "description": "Runs a hash verification after a torrent completes.",
        "recommendation": "Enable for data integrity when storage is unreliable; disable if completion checks are too expensive.",
    },
    {
        "group": "Files",
        "key": "pieces.preload.type",
        "label": "Pieces preload type",
        "type": "number",
        "description": "Controls how rTorrent preloads torrent pieces from disk.",
        "recommendation": "Keep default unless you are tuning disk cache behavior for a known workload.",
    },
    {
        "group": "Files",
        "key": "pieces.preload.min_size",
        "label": "Pieces preload min size",
        "type": "number",
        "description": "Minimum piece size threshold for preload behavior.",
        "recommendation": "Keep default unless large-piece torrents show disk latency issues.",
    },
    {
        "group": "Files",
        "key": "pieces.preload.min_rate",
        "label": "Pieces preload min rate",
        "type": "number",
        "description": "Minimum transfer rate threshold for preloading pieces.",
        "recommendation": "Tune only after measuring disk read pressure.",
    },
    {
        "group": "Files",
        "key": "pieces.memory.max",
        "label": "Pieces memory max",
        "type": "text",
        "placeholder": "512M",
        "description": "Maximum memory rTorrent may use for piece handling where supported.",
        "recommendation": "Avoid values that compete with OS page cache; increase only on hosts with spare RAM.",
    },
    {
        "group": "Files",
        "key": "system.file.allocate",
        "label": "File allocation",
        "type": "number",
        "description": "Controls preallocation behavior for downloaded files.",
        "recommendation": "Preallocation can reduce fragmentation but may slow adding very large torrents.",
    },
    {
        "group": "Files",
        "key": "system.file.max_size",
        "label": "Max file size",
        "type": "number",
        "description": "Maximum single file size rTorrent accepts where supported.",
        "recommendation": "Leave default unless you intentionally need to block oversized files.",
    },
    {
        "group": "System",
        "key": "system.umask",
        "label": "File umask",
        "type": "text",
        "placeholder": "0002",
        "description": "Permission mask applied to files created by rTorrent.",
        "recommendation": "Use 0002 for shared media groups, 0022 for private single-user setups.",
    },
    {
        "group": "System",
        "key": "system.hostname",
        "label": "Hostname",
        "type": "text",
        "readonly": True,
        "description": "Hostname reported by the rTorrent runtime.",
        "recommendation": "Read-only diagnostic value.",
    },
    {
        "group": "System",
        "key": "system.client_version",
        "label": "Client version",
        "type": "text",
        "readonly": True,
        "description": "rTorrent client version reported through XML-RPC.",
        "recommendation": "Read-only diagnostic value useful when checking compatibility.",
    },
    {
        "group": "System",
        "key": "system.library_version",
        "label": "Library version",
        "type": "text",
        "readonly": True,
        "description": "libTorrent library version used by rTorrent.",
        "recommendation": "Read-only diagnostic value useful when checking compatibility.",
    },
]


def _normalize_config_value(meta: dict, value):
    if meta.get("type") == "bool":
        return "1" if str(value).lower() in {"1", "true", "yes", "on"} or value is True else "0"
    if meta.get("type") == "number":
        return str(int(value or 0))
    return str(value or "").strip()


def saved_config_overrides(profile_id: int, user_id: int | None = None) -> dict[str, dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT key,value,baseline_value,apply_on_start,updated_at FROM rtorrent_config_overrides WHERE profile_id=?",
            (int(profile_id),),
        ).fetchall()
    return {r["key"]: r for r in rows}


def get_config(profile: dict) -> dict:
    c = client_for(profile)
    saved = saved_config_overrides(int(profile["id"]))
    fields = []
    for meta in RTORRENT_CONFIG_FIELDS:
        item = dict(meta)
        saved_item = saved.get(meta["key"])
        try:
            item["value"] = _normalize_config_value(meta, c.call(meta["key"]))
            item["current_value"] = item["value"]
            item["ok"] = True
        except Exception as exc:
            item["value"] = ""
            item["current_value"] = ""
            item["ok"] = False
            item["error"] = str(exc)
        if saved_item:
            saved_value = _normalize_config_value(meta, saved_item.get("value"))
            baseline_raw = saved_item.get("baseline_value")
            if baseline_raw not in (None, ""):
                baseline_value = _normalize_config_value(meta, baseline_raw)
            else:
                baseline_value = _normalize_config_value(meta, item.get("current_value"))
            item["saved"] = True
            item["saved_value"] = saved_value
            item["baseline_value"] = baseline_value
            item["apply_on_start"] = bool(saved_item.get("apply_on_start"))
            item["changed"] = saved_value != baseline_value
        fields.append(item)
    return {"fields": fields, "apply_on_start": any(bool(v.get("apply_on_start")) for v in saved.values())}



def default_download_path(profile: dict) -> str:
    """Return rTorrent default download directory for the active profile."""
    c = client_for(profile)
    errors = []
    for method in ("directory.default", "system.cwd"):
        try:
            value = str(c.call(method) or "").strip()
            if value:
                return value
        except Exception as exc:
            errors.append(f"{method}: {exc}")
    raise RuntimeError("Cannot read rTorrent default download directory: " + "; ".join(errors))

def _rtorrent_set_method(key: str, meta: dict) -> str:
    # Note: Most runtime values use the conventional <method>.set setter.
    # Some rTorrent commands, such as protocol.encryption.set, are already
    # setter commands and must not receive another .set suffix.
    return str(meta.get("set_method") or (key if key.endswith(".set") else f"{key}.set"))


def _rtorrent_config_line_key(key: str, meta: dict) -> str:
    # Note: Generated snippets must match rTorrent config syntax and avoid
    # producing invalid protocol.encryption.set.set lines.
    return str(meta.get("config_key") or _rtorrent_set_method(key, meta))


def generate_config_text(values: dict) -> str:
    known = {f["key"]: f for f in RTORRENT_CONFIG_FIELDS}
    lines = []
    for key, value in (values or {}).items():
        meta = known.get(key)
        if not meta or meta.get("readonly"):
            continue
        normalized = _normalize_config_value(meta, value)
        if meta.get("type") == "text" and any(ch.isspace() for ch in normalized):
            normalized = '"' + normalized.replace('\\', '\\\\').replace('"', '\\"') + '"'
        lines.append(f"{_rtorrent_config_line_key(key, meta)} = {normalized}")
    return "\n".join(lines) + ("\n" if lines else "")


def _read_rtorrent_config_value(client, key: str, meta: dict) -> str:
    return _normalize_config_value(meta, client.call(key))


def store_config_overrides(profile: dict, values: dict, apply_on_start: bool, baseline_values: dict | None = None, clear_keys: list[str] | None = None) -> list[str]:
    known = {f["key"]: f for f in RTORRENT_CONFIG_FIELDS}
    now = utcnow()
    profile_id = int(profile["id"])
    baseline_values = baseline_values or {}
    clear_set = set(clear_keys or [])
    stored = []
    with connect() as conn:
        for key in clear_set:
            if key in known:
                conn.execute(
                    "DELETE FROM rtorrent_config_overrides WHERE profile_id=? AND key=?",
                    (profile_id, key),
                )
        for key, value in (values or {}).items():
            if key in clear_set:
                continue
            meta = known.get(key)
            if not meta or meta.get("readonly"):
                continue
            normalized = _normalize_config_value(meta, value)
            existing = conn.execute(
                "SELECT baseline_value FROM rtorrent_config_overrides WHERE profile_id=? AND key=?",
                (profile_id, key),
            ).fetchone()
            existing_baseline = existing.get("baseline_value") if existing else None

            # Keep the first reference value forever until the override is cleared.
            # Without this, a second save could treat already-overridden rTorrent
            # values as the new baseline and the UI would stop marking them as changed.
            if existing_baseline not in (None, ""):
                baseline = _normalize_config_value(meta, existing_baseline)
            else:
                baseline = _normalize_config_value(meta, baseline_values.get(key)) if key in baseline_values else None

            if baseline not in (None, "") and normalized == baseline:
                conn.execute(
                    "DELETE FROM rtorrent_config_overrides WHERE profile_id=? AND key=?",
                    (profile_id, key),
                )
                continue
            conn.execute(
                "INSERT OR REPLACE INTO rtorrent_config_overrides(profile_id,key,value,baseline_value,apply_on_start,updated_at) VALUES(?,?,?,?,?,?)",
                (profile_id, key, normalized, baseline, 1 if apply_on_start else 0, now),
            )
            stored.append(key)
        conn.execute(
            "UPDATE rtorrent_config_overrides SET apply_on_start=?, updated_at=? WHERE profile_id=?",
            (1 if apply_on_start else 0, now, profile_id),
        )
    return stored


def set_config(profile: dict, values: dict, apply_now: bool = True, apply_on_start: bool = False, clear_keys: list[str] | None = None) -> dict:
    updated, errors = [], []
    known = {f["key"]: f for f in RTORRENT_CONFIG_FIELDS}
    c = client_for(profile)
    baseline_values = {}
    for key, raw_value in (values or {}).items():
        meta = known.get(key)
        if not meta or meta.get("readonly"):
            continue
        try:
            baseline_values[key] = _read_rtorrent_config_value(c, key, meta)
        except Exception:
            pass
    stored = store_config_overrides(profile, values, apply_on_start, baseline_values, clear_keys)
    if not apply_now:
        return {"ok": True, "updated": [], "stored": stored, "errors": []}
    for key, raw_value in (values or {}).items():
        if key not in known:
            continue
        meta = known[key]
        if meta.get("readonly"):
            continue
        value = _normalize_config_value(meta, raw_value)
        rpc_value = int(value) if meta.get("type") in {"bool", "number"} else value
        try:
            method = _rtorrent_set_method(key, meta)
            try:
                c.call(method, "", rpc_value)
            except Exception:
                c.call(method, rpc_value)
            updated.append(key)
        except Exception as exc:
            errors.append({"key": key, "error": str(exc)})
    return {"ok": not errors, "updated": updated, "stored": stored, "errors": errors}



def reset_config_overrides(profile: dict, user_id: int | None = None) -> dict:
    """Remove saved UI overrides and return the freshly read rTorrent config."""
    # Note: Reset means "forget pyTorrent UI overrides"; it does not write defaults back to rTorrent.
    profile_id = int(profile["id"])
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS count FROM rtorrent_config_overrides WHERE profile_id=?",
            (profile_id,),
        ).fetchone()
        removed = int((row or {}).get("count") or 0)
        conn.execute(
            "DELETE FROM rtorrent_config_overrides WHERE profile_id=?",
            (profile_id,),
        )
    config = get_config(profile)
    config["reset_removed"] = removed
    return config


def apply_startup_overrides(profile: dict) -> dict:
    rows = saved_config_overrides(int(profile["id"]))
    values = {k: v.get("value") for k, v in rows.items() if v.get("apply_on_start")}
    if not values:
        return {"ok": True, "updated": [], "errors": [], "skipped": True}
    return set_config(profile, values, apply_now=True, apply_on_start=True)


__all__ = [
    name for name in globals()
    if not name.startswith("__") and name not in {"annotations"}
]
