from __future__ import annotations
import json
import re
import socket
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any
from ..db import connect
from . import preferences, rtorrent

PORT_CHECK_CACHE_SECONDS = 6 * 60 * 60
MAX_PORT_CHECK_CANDIDATES = 256


def _app_setting_get(key: str) -> str | None:
    with connect() as conn:
        row = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
        return row.get("value") if row else None


def _app_setting_set(key: str, value: str) -> None:
    with connect() as conn:
        conn.execute("INSERT OR REPLACE INTO app_settings(key,value) VALUES(?,?)", (key, value))


def _iso_from_epoch(value: Any) -> str | None:
    try:
        return datetime.fromtimestamp(float(value), timezone.utc).isoformat(timespec="seconds")
    except Exception:
        return None


def _public_ip(profile: dict | None = None, force: bool = False) -> str:
    if profile and bool(profile.get("is_remote")):
        return rtorrent.remote_public_ip(profile, force=force)
    req = urllib.request.Request("https://api.ipify.org", headers={"User-Agent": "pyTorrent/port-check"})
    with urllib.request.urlopen(req, timeout=8) as res:
        return res.read(64).decode("utf-8", "replace").strip()


def _parse_port_candidates(value: str, limit: int = MAX_PORT_CHECK_CANDIDATES) -> tuple[list[int], bool]:
    """Return valid incoming port candidates from rTorrent network.port_range."""
    ports: list[int] = []
    seen: set[int] = set()
    truncated = False

    def add(port: int) -> None:
        nonlocal truncated
        if not 1 <= port <= 65535 or port in seen:
            return
        if len(ports) >= limit:
            truncated = True
            return
        seen.add(port)
        ports.append(port)

    for start, end in re.findall(r"(\d{1,5})\s*-\s*(\d{1,5})", value or ""):
        a, b = int(start), int(end)
        if a > b:
            a, b = b, a
        for port in range(a, b + 1):
            add(port)
            if truncated:
                break

    without_ranges = re.sub(r"\d{1,5}\s*-\s*\d{1,5}", " ", value or "")
    for item in re.findall(r"\d{1,5}", without_ranges):
        add(int(item))

    return ports, truncated


def _incoming_ports(profile: dict) -> dict:
    try:
        raw_value = str(rtorrent.client_for(profile).call("network.port_range") or "")
    except Exception:
        raw_value = ""
    ports, truncated = _parse_port_candidates(raw_value)
    return {"ports": ports, "raw": raw_value, "truncated": truncated}


def _yougetsignal_check(public_ip: str, port: int) -> dict:
    body = urllib.parse.urlencode({"remoteAddress": public_ip, "portNumber": str(port)}).encode("utf-8")
    req = urllib.request.Request(
        "https://ports.yougetsignal.com/check-port.php",
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "User-Agent": "pyTorrent/port-check",
            "Accept": "text/html,application/json,*/*",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=12) as res:
        text = res.read(8192).decode("utf-8", "replace")
    low = text.lower()
    if "is open" in low:
        return {"status": "open", "source": "yougetsignal", "raw": text[:500]}
    if "is closed" in low:
        return {"status": "closed", "source": "yougetsignal", "raw": text[:500]}
    return {"status": "unknown", "source": "yougetsignal", "raw": text[:500]}


def _local_port_fallback(public_ip: str, port: int) -> dict:
    try:
        with socket.create_connection((public_ip, port), timeout=3):
            return {"status": "open", "source": "local-fallback"}
    except Exception as exc:
        return {"status": "unknown", "source": "local-fallback", "error": f"Local fallback inconclusive: {exc}"}


def _check_ports(public_ip: str, ports: list[int], checker) -> dict:
    checked: list[int] = []
    first_closed: dict | None = None
    last_result: dict = {"status": "unknown"}

    for port in ports:
        checked.append(port)
        current = checker(public_ip, port)
        last_result = current
        if current.get("status") == "open":
            current.update({"port": port, "open_port": port, "checked_ports": checked})
            return current
        if current.get("status") == "closed" and first_closed is None:
            first_closed = current

    result = first_closed or last_result
    result.update({"port": ports[0] if ports else None, "open_port": None, "checked_ports": checked})
    return result


def port_check_status(profile: dict | None = None, force: bool = False, user_id: int | None = None) -> dict:
    """Return cached or freshly checked incoming-port status for one rTorrent profile."""
    profile = profile or preferences.active_profile(user_id)
    prefs = preferences.get_preferences(user_id, int(profile.get("id"))) if profile else preferences.get_preferences(user_id)
    enabled = bool((prefs or {}).get("port_check_enabled"))
    if not profile:
        return {"status": "unknown", "enabled": enabled, "error": "No profile"}

    port_info = _incoming_ports(profile)
    ports = port_info["ports"]
    if not ports:
        return {"status": "unknown", "enabled": enabled, "error": "Cannot read rTorrent network.port_range"}

    ports_key = ",".join(str(port) for port in ports)
    cache_key = f"port_check:{profile['id']}:{ports_key}:{int(bool(port_info['truncated']))}"
    if not force:
        cached = _app_setting_get(cache_key)
        if cached:
            try:
                data = json.loads(cached)
                if time.time() - float(data.get("checked_at_epoch") or 0) < PORT_CHECK_CACHE_SECONDS:
                    data["cached"] = True
                    data["enabled"] = enabled
                    if not data.get("checked_at"):
                        data["checked_at"] = _iso_from_epoch(data.get("checked_at_epoch"))
                    return data
            except Exception:
                pass

    checked_at_epoch = time.time()
    result = {
        "status": "unknown",
        "enabled": enabled,
        "port": ports[0],
        "ports": ports,
        "port_range": port_info["raw"],
        "ports_truncated": port_info["truncated"],
        "checked_at_epoch": checked_at_epoch,
        "checked_at": _iso_from_epoch(checked_at_epoch),
        "cached": False,
    }
    try:
        public_ip = _public_ip(profile, force=force)
        result["public_ip"] = public_ip
        result["remote"] = bool(profile.get("is_remote"))
        result.update(_check_ports(public_ip, ports, _yougetsignal_check))
    except Exception as exc:
        result["error"] = f"YouGetSignal failed: {exc}"
        try:
            public_ip = result.get("public_ip") or _public_ip(profile, force=force)
            result["public_ip"] = public_ip
            result["remote"] = bool(profile.get("is_remote"))
            result.update(_check_ports(public_ip, ports, _local_port_fallback))
        except Exception as fallback_exc:
            result["fallback_error"] = str(fallback_exc)
            result["source"] = "none"
    _app_setting_set(cache_key, json.dumps(result))
    return result
