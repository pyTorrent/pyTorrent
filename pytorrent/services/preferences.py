from __future__ import annotations
import json
from ..db import connect, utcnow, default_user_id
from . import auth
from .frontend_assets import BOOTSTRAP_THEME_LABELS, PYTORRENT_FRAMEWORK_EXTRA_THEMES
from .deletion import purge_profile

# Theme availability is explicit: the legacy Bootstrap/Bootswatch/DevExpress and
# app palettes are universal, while exactly three themes belong only to the
# standalone PyTorrent framework. Keeping the universal keys identical lets a
# user recognize the same palette when switching frameworks.
BOOTSTRAP_THEMES = BOOTSTRAP_THEME_LABELS
UNIVERSAL_THEMES = BOOTSTRAP_THEMES
PYTORRENT_DEDICATED_THEMES = {
    "default-beta": "PyTorrent Default (Beta)",
    **{f"pytorrent-{key}": label.replace("pyTorrent", "PyTorrent") for key, label in PYTORRENT_FRAMEWORK_EXTRA_THEMES.items()},
}
PYTORRENT_THEMES = {
    **PYTORRENT_DEDICATED_THEMES,
    **UNIVERSAL_THEMES,
}

UI_FRAMEWORKS = {
    "bootstrap": "Bootstrap",
    "pytorrent": "PyTorrent (Beta)",
}

FONT_FAMILIES = {
    "default": "Theme default",
    "system-ui": "System UI / Apple-like",
    "figtree": "Figtree",
    "inter": "Inter",
    "geist": "Geist",
    "manrope": "Manrope",
    "dm-sans": "DM Sans",
    "source-sans-3": "Source Sans 3",
    "open-sans": "Open Sans",
    "roboto": "Roboto",
    "lato": "Lato",
    "nunito-sans": "Nunito Sans",
    "poppins": "Poppins",
    "montserrat": "Montserrat",
    "ibm-plex-sans": "IBM Plex Sans",
    "jetbrains-mono": "JetBrains Mono",
    "adwaita-mono": "Adwaita Mono",
}


FOOTER_ITEM_KEYS = (
    "cpu", "ram", "usage_chart", "disk",
    "speed_down", "speed_up", "speed_peaks", "limits", "totals",
    "version", "port_check", "sockets", "rt_downloads", "rt_uploads",
    "rt_http", "rt_files", "rt_port", "shown", "selected", "planner",
    "clock", "docs",
)
FOOTER_PINNED_KEY = "docs"


def _json_payload(value, fallback):
    if value is None:
        return fallback
    if isinstance(value, str):
        try:
            return json.loads(value or json.dumps(fallback))
        except (TypeError, ValueError, json.JSONDecodeError):
            return fallback
    return value


def _preference_bool(value) -> bool:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"0", "false", "no", "off", ""}:
            return False
        if normalized in {"1", "true", "yes", "on"}:
            return True
    return bool(value)


def normalize_footer_items(value) -> dict[str, bool]:
    parsed = _json_payload(value, {})
    if not isinstance(parsed, dict):
        return {}
    return {
        key: _preference_bool(parsed[key])
        for key in FOOTER_ITEM_KEYS
        if key in parsed
    }


def normalize_footer_order(value) -> list[str]:
    parsed = _json_payload(value, [])
    if not isinstance(parsed, list):
        parsed = []
    allowed = set(FOOTER_ITEM_KEYS)
    order: list[str] = []
    for item in parsed:
        key = str(item or "").strip()
        if key in allowed and key != FOOTER_PINNED_KEY and key not in order:
            order.append(key)
    for key in FOOTER_ITEM_KEYS:
        if key != FOOTER_PINNED_KEY and key not in order:
            order.append(key)
    order.append(FOOTER_PINNED_KEY)
    return order


def default_footer_order_json() -> str:
    return json.dumps(normalize_footer_order([]), separators=(",", ":"))


RECOMMENDED_TABLE_COLUMNS = {
    "hidden": ["hash", "priority", "hashing", "active", "message", "complete", "state", "ratio_group"],
    "shown": ["down_total", "to_download", "up_total", "created"],
    "mobile": {
        "status": True, "size": True, "progress": True, "down_rate": True, "up_rate": True,
        "eta": True, "seeds": True, "peers": True, "ratio": True, "path": True, "label": True,
        "ratio_group": False, "down_total": True, "to_download": True, "up_total": True,
        "created": False, "last_activity": False, "priority": False, "state": False, "active": False, "complete": False,
        "hashing": False, "message": False, "hash": False,
    },
    "mobileSortFilters": {
        "seeds:-1": True, "up_rate:-1": True, "down_rate:-1": True, "progress:-1": True,
    },
    "widths": {
        "select": 44, "name": 389, "status": 83, "size": 75, "progress": 177,
        "down_rate": 60, "up_rate": 55, "eta": 53, "seeds": 44, "peers": 49,
        "ratio": 47, "path": 135, "label": 67, "ratio_group": 87,
        "down_total": 82, "to_download": 89, "up_total": 44, "created": 150,
        "last_activity": 150, "priority": 80, "state": 70, "active": 70, "complete": 82, "hashing": 82,
        "message": 220, "hash": 280,
    },
}


def _normalize_table_columns_json(value) -> str:
    parsed = _json_payload(value, {})
    if not isinstance(parsed, dict):
        parsed = {}
    return json.dumps(parsed, separators=(",", ":"))


def _normalize_active_filter(value) -> str:
    raw = str(value or "all").strip()
    if not raw or len(raw) > 180:
        return "all"
    allowed_static = {
        "all", "downloading", "queued", "seeding", "paused", "checking",
        "error", "post_check", "stopped", "moving",
    }
    if raw in allowed_static or raw.startswith("label:") or raw.startswith("tracker:"):
        return raw
    return "all"


def recommended_table_columns_json() -> str:
    return _normalize_table_columns_json(RECOMMENDED_TABLE_COLUMNS)


def apply_recommended_table_columns(user_id: int | None = None, profile_id: int | None = None):
    user_id = user_id or auth.current_user_id() or default_user_id()
    profile_id = profile_id or _active_profile_id_for_user(user_id)
    if not profile_id:
        return get_preferences(user_id)
    get_preferences(user_id, profile_id)
    now = utcnow()
    value = recommended_table_columns_json()
    with connect() as conn:
        conn.execute(
            "INSERT INTO profile_preferences(user_id,profile_id,table_columns_json,created_at,updated_at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(user_id,profile_id) DO UPDATE SET table_columns_json=excluded.table_columns_json, updated_at=excluded.updated_at",
            (user_id, profile_id, value, now, now),
        )
    return get_preferences(user_id, profile_id)

def bootstrap_css_url(theme: str | None) -> str:
    from .frontend_assets import bootstrap_css_path

    return bootstrap_css_path(theme)


def _int_setting(data: dict, key: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(data.get(key) if data.get(key) is not None else default)
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _url_setting(data: dict, key: str, default: str = "") -> str:
    value = str(data.get(key) if data.get(key) is not None else default).strip()
    if len(value) > 2048:
        value = value[:2048]
    if value and not (value.startswith("https://") or value.startswith("http://")):
        return ""
    return value


def list_profiles(user_id: int | None = None):
    user_id = user_id or auth.current_user_id() or default_user_id()
    visible = auth.visible_profile_ids(user_id)
    with connect() as conn:
        if visible is None:
            return conn.execute(
                "SELECT * FROM rtorrent_profiles ORDER BY is_default DESC, name COLLATE NOCASE"
            ).fetchall()
        if not visible:
            return []
        placeholders = ",".join("?" for _ in visible)
        return conn.execute(
            f"SELECT * FROM rtorrent_profiles WHERE id IN ({placeholders}) ORDER BY is_default DESC, name COLLATE NOCASE",
            tuple(visible),
        ).fetchall()


def get_profile(profile_id: int, user_id: int | None = None):
    user_id = user_id or auth.current_user_id() or default_user_id()
    if not auth.can_access_profile(profile_id, user_id):
        return None
    with connect() as conn:
        return conn.execute("SELECT * FROM rtorrent_profiles WHERE id=?", (profile_id,)).fetchone()


def active_profile(user_id: int | None = None):
    user_id = user_id or auth.current_user_id() or default_user_id()
    with connect() as conn:
        pref = conn.execute("SELECT active_rtorrent_id FROM user_preferences WHERE user_id=?", (user_id,)).fetchone()
        if pref and pref.get("active_rtorrent_id") and auth.can_access_profile(int(pref["active_rtorrent_id"]), user_id):
            row = conn.execute("SELECT * FROM rtorrent_profiles WHERE id=?", (pref["active_rtorrent_id"],)).fetchone()
            if row:
                return row
        profiles = list_profiles(user_id)
        # Note: Trusted auth-bypass access must choose a profile explicitly on first entry,
        # instead of silently reusing the first configured profile.
        if auth.auth_bypassed_request() and profiles:
            return None
        return profiles[0] if profiles else None


def _profile_values(data: dict) -> dict:
    name = str(data.get("name") or "rTorrent").strip() or "rTorrent"
    scgi_url = str(data.get("scgi_url") or "").strip()
    if not scgi_url.startswith("scgi://"):
        raise ValueError("SCGI URL must start with scgi://")
    return {
        "name": name,
        "scgi_url": scgi_url,
        "timeout_seconds": _int_setting(data, "timeout_seconds", 5, 1, 300),
        "max_parallel_jobs": _int_setting(data, "max_parallel_jobs", 5, 1, 64),
        "light_parallel_jobs": _int_setting(data, "light_parallel_jobs", 4, 1, 64),
        "light_job_timeout_seconds": _int_setting(data, "light_job_timeout_seconds", 300, 30, 86400),
        "heavy_job_timeout_seconds": _int_setting(data, "heavy_job_timeout_seconds", 7200, 300, 172800),
        "pending_job_timeout_seconds": _int_setting(data, "pending_job_timeout_seconds", 900, 60, 86400),
        "is_remote": 1 if data.get("is_remote") else 0,
        "is_default": 1 if data.get("is_default") else 0,
    }


def _save_profile_conn(conn, data: dict, user_id: int, now: str | None = None):
    values = _profile_values(data)
    now = now or utcnow()
    if values["is_default"]:
        conn.execute("UPDATE rtorrent_profiles SET is_default=0 WHERE user_id=?", (user_id,))
    cur = conn.execute(
        "INSERT INTO rtorrent_profiles(user_id,name,scgi_url,is_default,timeout_seconds,max_parallel_jobs,light_parallel_jobs,light_job_timeout_seconds,heavy_job_timeout_seconds,pending_job_timeout_seconds,is_remote,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            user_id, values["name"], values["scgi_url"], values["is_default"],
            values["timeout_seconds"], values["max_parallel_jobs"], values["light_parallel_jobs"],
            values["light_job_timeout_seconds"], values["heavy_job_timeout_seconds"], values["pending_job_timeout_seconds"],
            values["is_remote"], now, now,
        ),
    )
    profile_id = int(cur.lastrowid)
    pref = conn.execute("SELECT active_rtorrent_id FROM user_preferences WHERE user_id=?", (user_id,)).fetchone()
    if not pref or not pref.get("active_rtorrent_id") or values["is_default"]:
        conn.execute(
            "UPDATE user_preferences SET active_rtorrent_id=?, updated_at=? WHERE user_id=?",
            (profile_id, now, user_id),
        )
    return conn.execute("SELECT * FROM rtorrent_profiles WHERE id=?", (profile_id,)).fetchone()


def save_profile(data: dict, user_id: int | None = None):
    user_id = user_id or auth.current_user_id() or default_user_id()
    with connect() as conn:
        return _save_profile_conn(conn, data, int(user_id))


def update_profile(profile_id: int, data: dict, user_id: int | None = None):
    user_id = user_id or auth.current_user_id() or default_user_id()
    values = _profile_values(data)
    now = utcnow()
    with connect() as conn:
        row = conn.execute("SELECT id,user_id FROM rtorrent_profiles WHERE id=?", (profile_id,)).fetchone()
        if not row or not auth.can_write_profile(profile_id, user_id):
            raise ValueError("Profil nie istnieje")
        owner_user_id = int(row["user_id"])
        if values["is_default"]:
            # The default flag belongs to the profile owner, not to the admin currently editing it.
            conn.execute("UPDATE rtorrent_profiles SET is_default=0 WHERE user_id=?", (owner_user_id,))
        conn.execute(
            "UPDATE rtorrent_profiles SET name=?, scgi_url=?, is_default=?, timeout_seconds=?, max_parallel_jobs=?, light_parallel_jobs=?, light_job_timeout_seconds=?, heavy_job_timeout_seconds=?, pending_job_timeout_seconds=?, is_remote=?, updated_at=? WHERE id=?",
            (
                values["name"], values["scgi_url"], values["is_default"], values["timeout_seconds"],
                values["max_parallel_jobs"], values["light_parallel_jobs"], values["light_job_timeout_seconds"],
                values["heavy_job_timeout_seconds"], values["pending_job_timeout_seconds"], values["is_remote"],
                now, profile_id,
            ),
        )
        return conn.execute("SELECT * FROM rtorrent_profiles WHERE id=?", (profile_id,)).fetchone()


def delete_profile(profile_id: int, user_id: int | None = None):
    user_id = user_id or auth.current_user_id() or default_user_id()
    auth.require_profile_write(profile_id)
    with connect() as conn:
        result = purge_profile(conn, int(profile_id))

    # Note: Database cleanup is atomic. Runtime caches are best-effort and must never turn a successful DB delete into a 500.
    try:
        from .torrent_cache import torrent_cache
        from .torrent_summary import invalidate_summary
        from .rtorrent.system import clear_profile_runtime_caches

        torrent_cache.clear_profile(int(profile_id))
        invalidate_summary(int(profile_id))
        clear_profile_runtime_caches(int(profile_id))
    except Exception:
        pass
    return result


def activate_profile(profile_id: int, user_id: int | None = None):
    user_id = user_id or auth.current_user_id() or default_user_id()
    with connect() as conn:
        row = conn.execute("SELECT id FROM rtorrent_profiles WHERE id=?", (profile_id,)).fetchone()
        if not row or not auth.can_access_profile(profile_id, user_id):
            raise ValueError("Profil nie istnieje")
        conn.execute(
            "UPDATE user_preferences SET active_rtorrent_id=?, updated_at=? WHERE user_id=?",
            (profile_id, utcnow(), user_id),
        )
    return get_profile(profile_id, user_id)



def export_profiles(user_id: int | None = None) -> dict:
    profiles = [dict(row) for row in list_profiles(user_id)]
    for p in profiles:
        p.pop("id", None)
        p.pop("user_id", None)
        p.pop("created_at", None)
        p.pop("updated_at", None)
    return {"version": 1, "profiles": profiles}


def import_profiles(payload: dict, user_id: int | None = None) -> list[dict]:
    user_id = int(user_id or auth.current_user_id() or default_user_id())
    rows = payload.get("profiles") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError("Invalid profiles export")

    valid_rows = [item for item in rows if isinstance(item, dict)]
    # Validate the complete import before the first write so a bad later row cannot leave a partial import.
    for item in valid_rows:
        _profile_values(item)

    imported: list[dict] = []
    with connect() as conn:
        for item in valid_rows:
            imported.append(dict(_save_profile_conn(conn, item, user_id)))
    return imported


def _active_profile_id_for_user(user_id: int) -> int | None:
    profile = active_profile(user_id)
    try:
        return int(profile["id"]) if profile else None
    except Exception:
        return None


def _clean_disk_paths(value) -> list[str]:
    try:
        parsed = json.loads(value if isinstance(value, str) else json.dumps(value or []))
    except Exception:
        parsed = []
    if not isinstance(parsed, list):
        parsed = []
    clean: list[str] = []
    for item in parsed:
        path = str(item or "").strip()
        if path and path not in clean:
            clean.append(path)
    return clean


def _normalize_disk_monitor(data: dict | None) -> dict:
    data = data or {}
    mode = str(data.get("mode") or data.get("disk_monitor_mode") or "default")
    if mode not in {"default", "selected", "aggregate"}:
        mode = "default"
    try:
        threshold = int(data.get("stop_threshold") if data.get("stop_threshold") is not None else data.get("disk_monitor_stop_threshold") or 98)
    except (TypeError, ValueError):
        threshold = 98
    threshold = max(1, min(100, threshold))
    return {
        "disk_monitor_paths_json": json.dumps(_clean_disk_paths(data.get("paths_json") if data.get("paths_json") is not None else data.get("disk_monitor_paths_json"))),
        "disk_monitor_mode": mode,
        "disk_monitor_selected_path": str(data.get("selected_path") if data.get("selected_path") is not None else data.get("disk_monitor_selected_path") or "").strip(),
        "disk_monitor_stop_enabled": 1 if (data.get("stop_enabled") if data.get("stop_enabled") is not None else data.get("disk_monitor_stop_enabled")) else 0,
        "disk_monitor_stop_threshold": threshold,
    }


def legacy_disk_monitor_preferences(user_id: int | None = None) -> dict:
    user_id = user_id or auth.current_user_id() or default_user_id()
    with connect() as conn:
        row = conn.execute("SELECT * FROM user_preferences WHERE user_id=?", (user_id,)).fetchone() or {}
    return _normalize_disk_monitor(row)


def _disk_monitor_owner_label(row: dict | None) -> str:
    if not row:
        return ""
    return str(row.get("owner_display_name") or row.get("owner_username") or row.get("owner_email") or (f"user #{row.get('user_id')}" if row.get("user_id") else "")).strip()


def get_disk_monitor_preferences(profile_id: int | None = None, user_id: int | None = None) -> dict:
    user_id = user_id or auth.current_user_id() or default_user_id()
    profile_id = int(profile_id or _active_profile_id_for_user(user_id) or 0)
    if not profile_id:
        return legacy_disk_monitor_preferences(user_id)
    if not auth.can_access_profile(profile_id, user_id):
        return legacy_disk_monitor_preferences(user_id)
    with connect() as conn:
        row = conn.execute(
            """
            SELECT d.*, u.username AS owner_username, u.display_name AS owner_display_name, u.email AS owner_email
            FROM disk_monitor_preferences d
            LEFT JOIN users u ON u.id=d.user_id
            WHERE d.profile_id=?
            """,
            (profile_id,),
        ).fetchone()
    if row:
        clean = _normalize_disk_monitor(row)
        clean["disk_monitor_owner_user_id"] = int(row.get("user_id") or 0)
        clean["disk_monitor_owner_label"] = _disk_monitor_owner_label(row)
        return clean
    # Backward-compatible seed: existing global disk monitor values become defaults for first use of a profile.
    clean = legacy_disk_monitor_preferences(user_id)
    clean["disk_monitor_owner_user_id"] = 0
    clean["disk_monitor_owner_label"] = ""
    return clean


def save_disk_monitor_preferences(profile_id: int | None, data: dict, user_id: int | None = None) -> dict:
    user_id = user_id or auth.current_user_id() or default_user_id()
    profile_id = int(profile_id or _active_profile_id_for_user(user_id) or 0)
    if not profile_id:
        return legacy_disk_monitor_preferences(user_id)
    if not auth.can_write_profile(profile_id, user_id):
        raise PermissionError("No write access to profile")
    current = get_disk_monitor_preferences(profile_id, user_id)
    merged = dict(current)
    for key in ("disk_monitor_paths_json", "disk_monitor_mode", "disk_monitor_selected_path", "disk_monitor_stop_enabled", "disk_monitor_stop_threshold"):
        if key in data:
            merged[key] = data.get(key)
    clean = _normalize_disk_monitor(merged)
    now = utcnow()
    with connect() as conn:
        conn.execute(
            "INSERT INTO disk_monitor_preferences(profile_id,user_id,paths_json,mode,selected_path,stop_enabled,stop_threshold,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(profile_id) DO UPDATE SET user_id=excluded.user_id, paths_json=excluded.paths_json, mode=excluded.mode, selected_path=excluded.selected_path, stop_enabled=excluded.stop_enabled, stop_threshold=excluded.stop_threshold, updated_at=excluded.updated_at",
            (profile_id, user_id, clean["disk_monitor_paths_json"], clean["disk_monitor_mode"], clean["disk_monitor_selected_path"], clean["disk_monitor_stop_enabled"], clean["disk_monitor_stop_threshold"], now, now),
        )
    clean["disk_monitor_owner_user_id"] = int(user_id)
    with connect() as conn:
        row = conn.execute("SELECT display_name AS owner_display_name, username AS owner_username, email AS owner_email, id AS user_id FROM users WHERE id=?", (user_id,)).fetchone()
    clean["disk_monitor_owner_label"] = _disk_monitor_owner_label(row)
    return clean


PROFILE_PREFERENCE_COLUMNS = frozenset({
    "table_columns_json",
    "torrent_sort_json",
    "active_filter",
    "peers_refresh_seconds",
    "port_check_enabled",
    "tracker_favicons_enabled",
    "reverse_dns_enabled",
    "sidebar_labels_expanded",
    "sidebar_shortcuts_expanded",
    "footer_items_json",
    "footer_order_json",
    "system_usage_chart_mode",
    "system_usage_chart_expanded",
})


def _seed_profile_preferences(conn, user_id: int, profile_id: int) -> dict:
    now = utcnow()
    legacy = conn.execute("SELECT * FROM user_preferences WHERE user_id=?", (user_id,)).fetchone() or {}
    row = conn.execute("SELECT * FROM profile_preferences WHERE user_id=? AND profile_id=?", (user_id, profile_id)).fetchone()
    footer_items = json.dumps(normalize_footer_items(legacy.get("footer_items_json")), separators=(",", ":"))
    footer_order = default_footer_order_json()
    if row:
        missing: dict[str, str] = {}
        if row.get("footer_items_json") is None:
            missing["footer_items_json"] = footer_items
        if row.get("footer_order_json") is None:
            missing["footer_order_json"] = footer_order
        clean_active_filter = _normalize_active_filter(row.get("active_filter"))
        if clean_active_filter != str(row.get("active_filter") or "all"):
            missing["active_filter"] = clean_active_filter
        clean_table_columns = _normalize_table_columns_json(
            row.get("table_columns_json") or recommended_table_columns_json()
        )
        if clean_table_columns != str(row.get("table_columns_json") or ""):
            missing["table_columns_json"] = clean_table_columns
        if missing:
            assignments = ", ".join(f"{column}=?" for column in missing)
            conn.execute(
                f"UPDATE profile_preferences SET {assignments}, updated_at=? WHERE user_id=? AND profile_id=?",
                (*missing.values(), now, user_id, profile_id),
            )
            row = conn.execute("SELECT * FROM profile_preferences WHERE user_id=? AND profile_id=?", (user_id, profile_id)).fetchone()
        return dict(row or {})
    # Note: Profile-scoped footer state is seeded from the legacy user preference once, then evolves independently per profile.
    conn.execute(
        "INSERT INTO profile_preferences(user_id,profile_id,table_columns_json,torrent_sort_json,active_filter,peers_refresh_seconds,port_check_enabled,tracker_favicons_enabled,reverse_dns_enabled,sidebar_labels_expanded,sidebar_shortcuts_expanded,footer_items_json,footer_order_json,system_usage_chart_mode,system_usage_chart_expanded,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            user_id,
            profile_id,
            _normalize_table_columns_json(legacy.get("table_columns_json") or recommended_table_columns_json()),
            legacy.get("torrent_sort_json"),
            _normalize_active_filter(legacy.get("active_filter")),
            int(legacy.get("peers_refresh_seconds") or 0),
            int(legacy.get("port_check_enabled") or 0),
            int(legacy["tracker_favicons_enabled"]) if legacy.get("tracker_favicons_enabled") is not None else 1,
            int(legacy["reverse_dns_enabled"]) if legacy.get("reverse_dns_enabled") is not None else 1,
            int(legacy.get("sidebar_labels_expanded") or 0),
            int(legacy.get("sidebar_shortcuts_expanded") or 0),
            footer_items,
            footer_order,
            str(legacy.get("system_usage_chart_mode") or "combined"),
            int(legacy.get("system_usage_chart_expanded") or 0),
            now,
            now,
        ),
    )
    return dict(conn.execute("SELECT * FROM profile_preferences WHERE user_id=? AND profile_id=?", (user_id, profile_id)).fetchone() or {})


def get_profile_preferences(user_id: int, profile_id: int | None) -> dict:
    if not profile_id:
        return {}
    with connect() as conn:
        return _seed_profile_preferences(conn, user_id, int(profile_id))


def save_profile_preferences(user_id: int, profile_id: int | None, data: dict) -> None:
    if not profile_id or not any(key in data for key in PROFILE_PREFERENCE_COLUMNS):
        return
    profile_id = int(profile_id)
    now = utcnow()
    with connect() as conn:
        _seed_profile_preferences(conn, user_id, profile_id)
        updates: dict[str, object] = {}
        if data.get("table_columns_json") is not None:
            updates["table_columns_json"] = _normalize_table_columns_json(data.get("table_columns_json"))
        if data.get("peers_refresh_seconds") is not None:
            try:
                sec = int(data.get("peers_refresh_seconds") or 0)
            except (TypeError, ValueError):
                sec = 0
            updates["peers_refresh_seconds"] = sec if sec in {0, 10, 15, 30, 60} else 0
        if data.get("port_check_enabled") is not None:
            updates["port_check_enabled"] = 1 if data.get("port_check_enabled") else 0
        if data.get("tracker_favicons_enabled") is not None:
            updates["tracker_favicons_enabled"] = 1 if data.get("tracker_favicons_enabled") else 0
        if data.get("reverse_dns_enabled") is not None:
            updates["reverse_dns_enabled"] = 1 if data.get("reverse_dns_enabled") else 0
        if data.get("sidebar_labels_expanded") is not None:
            updates["sidebar_labels_expanded"] = 1 if data.get("sidebar_labels_expanded") else 0
        if data.get("sidebar_shortcuts_expanded") is not None:
            updates["sidebar_shortcuts_expanded"] = 1 if data.get("sidebar_shortcuts_expanded") else 0
        if data.get("footer_items_json") is not None:
            updates["footer_items_json"] = json.dumps(normalize_footer_items(data.get("footer_items_json")), separators=(",", ":"))
        if data.get("footer_order_json") is not None:
            updates["footer_order_json"] = json.dumps(normalize_footer_order(data.get("footer_order_json")), separators=(",", ":"))
        if data.get("system_usage_chart_mode") is not None:
            mode = str(data.get("system_usage_chart_mode") or "combined").strip().lower()
            updates["system_usage_chart_mode"] = mode if mode in {"combined", "split"} else "combined"
        if data.get("system_usage_chart_expanded") is not None:
            updates["system_usage_chart_expanded"] = 1 if data.get("system_usage_chart_expanded") else 0
        if data.get("torrent_sort_json") is not None:
            value = data.get("torrent_sort_json") if isinstance(data.get("torrent_sort_json"), str) else json.dumps(data.get("torrent_sort_json"))
            parsed = json.loads(value or "{}")
            if not isinstance(parsed, dict):
                parsed = {}
            try:
                direction = int(parsed.get("dir") or 1)
            except (TypeError, ValueError):
                direction = 1
            allowed_sort_keys = {"name", "status", "size", "progress", "down_rate", "up_rate", "eta", "seeds", "peers", "ratio", "path", "label", "ratio_group", "down_total", "to_download", "up_total", "created", "last_activity", "priority", "state", "active", "complete", "hashing", "message", "hash"}
            sort_key = str(parsed.get("key") or "name")
            if sort_key not in allowed_sort_keys:
                sort_key = "name"
            updates["torrent_sort_json"] = json.dumps({"key": sort_key, "dir": 1 if direction >= 0 else -1})
        if data.get("active_filter") is not None:
            updates["active_filter"] = _normalize_active_filter(data.get("active_filter"))
        if not updates:
            return
        assignments = ", ".join(f"{column}=?" for column in updates)
        conn.execute(
            f"UPDATE profile_preferences SET {assignments}, updated_at=? WHERE user_id=? AND profile_id=?",
            (*updates.values(), now, user_id, profile_id),
        )


def get_preferences(user_id: int | None = None, profile_id: int | None = None):
    user_id = user_id or auth.current_user_id() or default_user_id()
    profile_id = profile_id or _active_profile_id_for_user(user_id)
    with connect() as conn:
        pref = conn.execute("SELECT * FROM user_preferences WHERE user_id=?", (user_id,)).fetchone()
        if not pref:
            now = utcnow()
            conn.execute("INSERT INTO user_preferences(user_id, theme, created_at, updated_at) VALUES(?, 'dark', ?, ?)", (user_id, now, now))
            pref = conn.execute("SELECT * FROM user_preferences WHERE user_id=?", (user_id,)).fetchone()
        merged = dict(pref or {})
        if profile_id:
            merged.update(_seed_profile_preferences(conn, user_id, int(profile_id)))
    merged.update(get_disk_monitor_preferences(profile_id, user_id))
    return merged

def save_preferences(data: dict, user_id: int | None = None, profile_id: int | None = None):
    user_id = user_id or auth.current_user_id() or default_user_id()
    profile_id = profile_id or _active_profile_id_for_user(user_id)
    allowed_theme = data.get("theme") if data.get("theme") in {"light", "dark"} else None
    bootstrap_theme = data.get("bootstrap_theme") if data.get("bootstrap_theme") in BOOTSTRAP_THEMES else None
    pytorrent_theme = data.get("pytorrent_theme") if data.get("pytorrent_theme") in PYTORRENT_THEMES else None
    ui_framework = data.get("ui_framework") if data.get("ui_framework") in UI_FRAMEWORKS else None
    font_family = data.get("font_family") if data.get("font_family") in FONT_FAMILIES else None
    title_speed_enabled = data.get("title_speed_enabled")
    automation_toasts_enabled = data.get("automation_toasts_enabled")
    smart_queue_toasts_enabled = data.get("smart_queue_toasts_enabled")
    toast_notification_mode = data.get("toast_notification_mode")
    notification_history_enabled = data.get("notification_history_enabled")
    notification_history_mode = data.get("notification_history_mode")
    easter_egg_enabled = data.get("easter_egg_enabled")
    easter_egg_loading_image_url = data.get("easter_egg_loading_image_url")
    easter_egg_click_image_url = data.get("easter_egg_click_image_url")
    disk_monitor_paths_json = data.get("disk_monitor_paths_json")
    disk_monitor_mode = data.get("disk_monitor_mode")
    disk_monitor_selected_path = data.get("disk_monitor_selected_path")
    disk_monitor_stop_enabled = data.get("disk_monitor_stop_enabled")
    disk_monitor_stop_threshold = data.get("disk_monitor_stop_threshold")
    interface_scale = data.get("interface_scale")
    torrent_list_font_size = data.get("torrent_list_font_size")
    compact_torrent_list_enabled = data.get("compact_torrent_list_enabled")
    pytorrent_animations_enabled = data.get("pytorrent_animations_enabled")
    detail_panel_height = data.get("detail_panel_height")
    default_download_path = data.get("default_download_path")
    download_location_mode = data.get("download_location_mode")
    download_last_path = data.get("download_last_path")
    download_remember_last_enabled = data.get("download_remember_last_enabled")
    # Note: Normalize remember-last server-side so the Add action and direct API callers persist the same location behavior.
    if download_remember_last_enabled is True and download_location_mode is None:
        download_location_mode = "remember_last"
    if download_location_mode == "remember_last" and download_remember_last_enabled is None:
        download_remember_last_enabled = True
    drop_location_mode = data.get("drop_location_mode")
    free_space_check_enabled = data.get("free_space_check_enabled")
    disk_payload = None
    if any(value is not None for value in (disk_monitor_paths_json, disk_monitor_mode, disk_monitor_selected_path, disk_monitor_stop_enabled, disk_monitor_stop_threshold)):
        disk_payload = {
            "disk_monitor_paths_json": disk_monitor_paths_json,
            "disk_monitor_mode": disk_monitor_mode,
            "disk_monitor_selected_path": disk_monitor_selected_path,
            "disk_monitor_stop_enabled": disk_monitor_stop_enabled,
            "disk_monitor_stop_threshold": disk_monitor_stop_threshold,
        }
    with connect() as conn:
        now = utcnow()
        current_pref = conn.execute(
            "SELECT easter_egg_loading_image_url, easter_egg_click_image_url FROM user_preferences WHERE user_id=?",
            (user_id,),
        ).fetchone()
        effective_easter_loading_url = _url_setting(data, "easter_egg_loading_image_url") if easter_egg_loading_image_url is not None else ((current_pref or {}).get("easter_egg_loading_image_url") or "")
        effective_easter_click_url = _url_setting(data, "easter_egg_click_image_url") if easter_egg_click_image_url is not None else ((current_pref or {}).get("easter_egg_click_image_url") or "")
        easter_egg_has_image = bool(effective_easter_loading_url or effective_easter_click_url)
        if allowed_theme:
            conn.execute("UPDATE user_preferences SET theme=?, updated_at=? WHERE user_id=?", (allowed_theme, now, user_id))
        if bootstrap_theme:
            conn.execute("UPDATE user_preferences SET bootstrap_theme=?, updated_at=? WHERE user_id=?", (bootstrap_theme, now, user_id))
        if pytorrent_theme:
            conn.execute("UPDATE user_preferences SET pytorrent_theme=?, updated_at=? WHERE user_id=?", (pytorrent_theme, now, user_id))
        if ui_framework:
            # Note: Bootstrap remains the safe default while the standalone PyTorrent CSS framework is developed as an opt-in beta.
            conn.execute("UPDATE user_preferences SET ui_framework=?, updated_at=? WHERE user_id=?", (ui_framework, now, user_id))
        if font_family:
            conn.execute("UPDATE user_preferences SET font_family=?, updated_at=? WHERE user_id=?", (font_family, now, user_id))
        if title_speed_enabled is not None:
            conn.execute("UPDATE user_preferences SET title_speed_enabled=?, updated_at=? WHERE user_id=?", (1 if title_speed_enabled else 0, now, user_id))
        if automation_toasts_enabled is not None:
            # Note: Lets users silence automation-created toast noise without hiding job/history data.
            conn.execute("UPDATE user_preferences SET automation_toasts_enabled=?, updated_at=? WHERE user_id=?", (1 if automation_toasts_enabled else 0, now, user_id))
        if smart_queue_toasts_enabled is not None:
            # Note: Smart Queue toast noise can be disabled independently from automation notifications.
            conn.execute("UPDATE user_preferences SET smart_queue_toasts_enabled=?, updated_at=? WHERE user_id=?", (1 if smart_queue_toasts_enabled else 0, now, user_id))
        if toast_notification_mode is not None:
            # Note: Visible Toast filtering is separate from history filtering so either surface can stay verbose or quiet independently.
            toast_mode = str(toast_notification_mode or "all").strip().lower()
            if toast_mode not in {"important", "all"}:
                toast_mode = "all"
            conn.execute("UPDATE user_preferences SET toast_notification_mode=?, updated_at=? WHERE user_id=?", (toast_mode, now, user_id))
        if notification_history_enabled is not None:
            # Note: Toast history is an opt-in UX preference; message contents remain browser-local in localStorage.
            conn.execute("UPDATE user_preferences SET notification_history_enabled=?, updated_at=? WHERE user_id=?", (1 if notification_history_enabled else 0, now, user_id))
        if notification_history_mode is not None:
            # Note: History filtering is persisted separately from the enable flag so Toast visibility never depends on archive mode.
            history_mode = str(notification_history_mode or "important").strip().lower()
            if history_mode not in {"important", "all"}:
                history_mode = "important"
            conn.execute("UPDATE user_preferences SET notification_history_mode=?, updated_at=? WHERE user_id=?", (history_mode, now, user_id))
        if easter_egg_enabled is not None:
            conn.execute("UPDATE user_preferences SET easter_egg_enabled=?, updated_at=? WHERE user_id=?", (1 if (easter_egg_enabled and easter_egg_has_image) else 0, now, user_id))
        elif (easter_egg_loading_image_url is not None or easter_egg_click_image_url is not None) and not easter_egg_has_image:
            conn.execute("UPDATE user_preferences SET easter_egg_enabled=0, updated_at=? WHERE user_id=?", (now, user_id))
        if easter_egg_loading_image_url is not None:
            conn.execute("UPDATE user_preferences SET easter_egg_loading_image_url=?, updated_at=? WHERE user_id=?", (effective_easter_loading_url, now, user_id))
        if easter_egg_click_image_url is not None:
            conn.execute("UPDATE user_preferences SET easter_egg_click_image_url=?, updated_at=? WHERE user_id=?", (effective_easter_click_url, now, user_id))
        if interface_scale is not None:
            # Note: Keep backend validation aligned with the UI so the new 70% minimum persists after saving.
            try:
                scale = int(interface_scale or 80)
            except (TypeError, ValueError):
                scale = 80
            if scale < 70: scale = 70
            if scale > 140: scale = 140
            conn.execute("UPDATE user_preferences SET interface_scale=?, updated_at=? WHERE user_id=?", (scale, now, user_id))
        if torrent_list_font_size is not None:
            # Note: Torrent list font size is clamped so dense rows cannot break the virtualized list layout.
            try:
                list_font_size = int(torrent_list_font_size or 12)
            except (TypeError, ValueError):
                list_font_size = 12
            if list_font_size < 11: list_font_size = 11
            if list_font_size > 16: list_font_size = 16
            conn.execute("UPDATE user_preferences SET torrent_list_font_size=?, updated_at=? WHERE user_id=?", (list_font_size, now, user_id))
        if compact_torrent_list_enabled is not None:
            # Note: Compact torrent list is a visual-only preference for desktop and mobile list density.
            conn.execute("UPDATE user_preferences SET compact_torrent_list_enabled=?, updated_at=? WHERE user_id=?", (1 if compact_torrent_list_enabled else 0, now, user_id))
        if pytorrent_animations_enabled is not None:
            # Note: Motion is a PyTorrent-framework preference; Bootstrap keeps its native behavior.
            conn.execute("UPDATE user_preferences SET pytorrent_animations_enabled=?, updated_at=? WHERE user_id=?", (1 if pytorrent_animations_enabled else 0, now, user_id))
        if detail_panel_height is not None:
            try:
                height = int(detail_panel_height or 255)
            except (TypeError, ValueError):
                height = 255
            if height < 160: height = 160
            if height > 720: height = 720
            conn.execute("UPDATE user_preferences SET detail_panel_height=?, updated_at=? WHERE user_id=?", (height, now, user_id))
        if default_download_path is not None:
            # Note: The user-level default path is additive; an empty value falls back to the active rTorrent profile default.
            conn.execute("UPDATE user_preferences SET default_download_path=?, updated_at=? WHERE user_id=?", (str(default_download_path or '').strip(), now, user_id))
        if download_location_mode is not None:
            mode = str(download_location_mode or 'profile_default').strip()
            if mode not in {'profile_default', 'choose_each_time', 'remember_last'}:
                mode = 'profile_default'
            conn.execute("UPDATE user_preferences SET download_location_mode=?, updated_at=? WHERE user_id=?", (mode, now, user_id))
        if download_last_path is not None:
            conn.execute("UPDATE user_preferences SET download_last_path=?, updated_at=? WHERE user_id=?", (str(download_last_path or '').strip(), now, user_id))
        if download_remember_last_enabled is not None:
            conn.execute("UPDATE user_preferences SET download_remember_last_enabled=?, updated_at=? WHERE user_id=?", (1 if download_remember_last_enabled else 0, now, user_id))
        if drop_location_mode is not None:
            mode = str(drop_location_mode or 'default').strip()
            if mode not in {'default', 'ask_batch', 'ask_each'}:
                mode = 'default'
            conn.execute("UPDATE user_preferences SET drop_location_mode=?, updated_at=? WHERE user_id=?", (mode, now, user_id))
        if free_space_check_enabled is not None:
            conn.execute("UPDATE user_preferences SET free_space_check_enabled=?, updated_at=? WHERE user_id=?", (1 if free_space_check_enabled else 0, now, user_id))
    save_profile_preferences(user_id, profile_id, data)
    if disk_payload is not None:
        save_disk_monitor_preferences(profile_id, disk_payload, user_id)
    return get_preferences(user_id, profile_id)


def _row_int(row: dict, key: str) -> int:
    try:
        return int(float(row.get(key) or 0))
    except (TypeError, ValueError):
        return 0


def profile_runtime_stats_from_rows(profile: dict, rows: list[dict], user_id: int | None = None) -> dict:
    # Note: Stored profile stats are intentionally approximate and updated only when the user switches to that profile.
    user_id = user_id or auth.current_user_id() or default_user_id()
    total_size = completed = downloaded = uploaded = active = seeding = downloading = stopped = 0
    for row in rows or []:
        size = _row_int(row, 'size')
        total_size += size
        completed += min(size, _row_int(row, 'completed_bytes')) if size else _row_int(row, 'completed_bytes')
        downloaded += _row_int(row, 'down_total')
        uploaded += _row_int(row, 'up_total')
        status = str(row.get('status') or '').strip().lower()
        state = bool(row.get('state'))
        complete = bool(row.get('complete'))
        if state:
            active += 1
        if complete and state:
            seeding += 1
        if not complete and state and status != 'queued':
            downloading += 1
        if not state:
            stopped += 1
    return {
        'profile_id': int(profile.get('id') or 0),
        'user_id': int(user_id),
        'torrent_count': len(rows or []),
        'total_size_bytes': total_size,
        'completed_bytes': completed,
        'downloaded_bytes': downloaded,
        'uploaded_bytes': uploaded,
        'active_count': active,
        'seeding_count': seeding,
        'downloading_count': downloading,
        'stopped_count': stopped,
        'updated_at': utcnow(),
    }


def save_profile_runtime_stats(profile: dict, rows: list[dict], user_id: int | None = None) -> dict:
    stats = profile_runtime_stats_from_rows(profile, rows, user_id=user_id)
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO profile_runtime_stats(
              profile_id,user_id,torrent_count,total_size_bytes,completed_bytes,downloaded_bytes,uploaded_bytes,
              active_count,seeding_count,downloading_count,stopped_count,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(profile_id) DO UPDATE SET
              user_id=excluded.user_id, torrent_count=excluded.torrent_count, total_size_bytes=excluded.total_size_bytes,
              completed_bytes=excluded.completed_bytes, downloaded_bytes=excluded.downloaded_bytes, uploaded_bytes=excluded.uploaded_bytes,
              active_count=excluded.active_count, seeding_count=excluded.seeding_count, downloading_count=excluded.downloading_count,
              stopped_count=excluded.stopped_count, updated_at=excluded.updated_at
            """,
            (
                stats['profile_id'], stats['user_id'], stats['torrent_count'], stats['total_size_bytes'], stats['completed_bytes'],
                stats['downloaded_bytes'], stats['uploaded_bytes'], stats['active_count'], stats['seeding_count'],
                stats['downloading_count'], stats['stopped_count'], stats['updated_at'],
            ),
        )
    return stats


def get_profile_runtime_stats(profile_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM profile_runtime_stats WHERE profile_id=?", (int(profile_id),)).fetchone()
    return dict(row) if row else None
