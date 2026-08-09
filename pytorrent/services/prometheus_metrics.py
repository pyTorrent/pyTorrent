from __future__ import annotations

import hmac
import ipaddress
import math
import os
import re
import threading
import time
from typing import Any
from urllib.parse import urlparse

import psutil
from flask import Response, abort, request

from ..config import (
    METRICS_ALLOWED_IPS,
    METRICS_BASIC_PASSWORD,
    METRICS_BASIC_USER,
    METRICS_ENABLE,
    METRICS_INSTANCE,
)
from . import connection_diagnostics, poller_control

_LOCK = threading.RLock()
_PROFILE_META: dict[int, dict[str, Any]] = {}
_SYSTEM_STATS: dict[int, dict[str, float]] = {}
_SPEED_STATS: dict[int, dict[str, float]] = {}
_TORRENT_SUMMARIES: dict[int, dict[str, Any]] = {}
_LAST_CAPTURE_EPOCH: dict[int, float] = {}
_SYSTEM_CAPTURE_EPOCH: dict[int, float] = {}
_METRIC_NAME_RE = re.compile(r"[^a-zA-Z0-9_:]")
_ACL_CONFIGURED = bool(METRICS_ALLOWED_IPS)
_ALLOWED_NETWORKS = []
for _entry in METRICS_ALLOWED_IPS:
    try:
        _ALLOWED_NETWORKS.append(ipaddress.ip_network(_entry, strict=False))
    except ValueError:
        continue
_BASIC_CONFIGURED = bool(METRICS_BASIC_USER or METRICS_BASIC_PASSWORD)


def enabled() -> bool:
    return bool(METRICS_ENABLE)


def _profile_id(profile: dict | int) -> int:
    if isinstance(profile, dict):
        return int(profile.get("id") or 0)
    return int(profile or 0)


def _profile_host(profile: dict | int) -> str:
    if not isinstance(profile, dict) or not bool(profile.get("is_remote")):
        return METRICS_INSTANCE
    parsed = urlparse(str(profile.get("scgi_url") or ""))
    host = str(parsed.hostname or "").strip().lower().rstrip(".")
    return host or METRICS_INSTANCE


def _remember_profile(profile: dict | int) -> int:
    profile_id = _profile_id(profile)
    if not profile_id:
        return 0
    if isinstance(profile, dict):
        meta = {
            "profile_id": profile_id,
            "profile": str(profile.get("name") or f"rTorrent {profile_id}"),
            "remote": 1.0 if bool(profile.get("is_remote")) else 0.0,
            "host": _profile_host(profile),
        }
        with _LOCK:
            _PROFILE_META[profile_id] = meta
    else:
        with _LOCK:
            _PROFILE_META.setdefault(
                profile_id,
                {"profile_id": profile_id, "profile": f"rTorrent {profile_id}", "remote": 0.0, "host": METRICS_INSTANCE},
            )
    return profile_id


def observe_profile(profile: dict | int) -> None:
    """Remember profile labels only when metrics are enabled."""
    if enabled():
        _remember_profile(profile)


def _flatten_numeric(value: Any, prefix: str = "") -> dict[str, float]:
    result: dict[str, float] = {}
    if isinstance(value, bool):
        if prefix:
            result[prefix] = 1.0 if value else 0.0
        return result
    if isinstance(value, (int, float)):
        number = float(value)
        if prefix and math.isfinite(number):
            result[prefix] = number
        return result
    if isinstance(value, dict):
        for key, item in value.items():
            clean_key = str(key or "value").strip().lower()
            child = f"{prefix}_{clean_key}" if prefix else clean_key
            result.update(_flatten_numeric(item, child))
    return result


def observe_system_stats(profile: dict | int, status: dict[str, Any]) -> None:
    """Capture numeric values that were already produced by the normal system-stats poll."""
    if not enabled():
        return
    profile_id = _remember_profile(profile)
    if not profile_id:
        return
    values = _flatten_numeric(status or {})
    captured_at = time.time()
    with _LOCK:
        _SYSTEM_STATS[profile_id] = values
        _SYSTEM_CAPTURE_EPOCH[profile_id] = captured_at
        _LAST_CAPTURE_EPOCH[profile_id] = captured_at


def observe_speed_status(profile: dict | int, status: dict[str, Any]) -> None:
    """Capture aggregate transfer values from the existing live poll without another torrent scan."""
    if not enabled():
        return
    profile_id = _remember_profile(profile)
    if not profile_id:
        return
    values = _flatten_numeric(status or {})
    with _LOCK:
        _SPEED_STATS[profile_id] = values
        _LAST_CAPTURE_EPOCH[profile_id] = time.time()


def observe_torrent_summary(profile: dict | int, summary: dict[str, Any]) -> None:
    """Capture the summary only when the application has already built it for the UI."""
    if not enabled():
        return
    profile_id = _remember_profile(profile)
    if not profile_id:
        return
    filters = (summary or {}).get("filters") or {}
    clean: dict[str, dict[str, float]] = {}
    for state, bucket in filters.items():
        values = _flatten_numeric(bucket or {})
        if values:
            clean[str(state)] = values
    with _LOCK:
        _TORRENT_SUMMARIES[profile_id] = clean
        _LAST_CAPTURE_EPOCH[profile_id] = time.time()


def _escape_label(value: Any) -> str:
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _labels(**values: Any) -> str:
    parts = [f'{key}="{_escape_label(value)}"' for key, value in values.items()]
    return "{" + ",".join(parts) + "}" if parts else ""


def _metric_name(value: str) -> str:
    name = _METRIC_NAME_RE.sub("_", str(value or "metric"))
    if name and name[0].isdigit():
        name = "_" + name
    return name


def _number(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    if not math.isfinite(number):
        number = 0.0
    if number.is_integer():
        return str(int(number))
    return format(number, ".12g")


class _PrometheusText:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.types: set[str] = set()

    def gauge(self, name: str, help_text: str, value: Any, labels: dict[str, Any] | None = None) -> None:
        metric = _metric_name(name)
        if metric not in self.types:
            self.lines.append(f"# HELP {metric} {help_text}")
            self.lines.append(f"# TYPE {metric} gauge")
            self.types.add(metric)
        self.lines.append(f"{metric}{_labels(**(labels or {}))} {_number(value)}")

    def render(self) -> str:
        return "\n".join(self.lines) + "\n"


def _base_labels(profile_id: int, meta: dict[str, Any]) -> dict[str, Any]:
    return {
        "server": METRICS_INSTANCE,
        "profile_id": int(profile_id),
        "profile": str(meta.get("profile") or f"rTorrent {profile_id}"),
    }


def _poller_metrics(text: _PrometheusText, profile_id: int, meta: dict[str, Any]) -> None:
    state = poller_control.state_for(profile_id)
    labels = _base_labels(profile_id, meta)
    metrics = {
        "pytorrent_poller_ticks_total": ("Poller ticks completed since process start.", state.tick_count),
        "pytorrent_poller_last_tick_duration_ms": ("Duration of the latest poller tick in milliseconds.", state.last_tick_ms),
        "pytorrent_poller_last_tick_gap_ms": ("Gap between the latest two poller ticks in milliseconds.", state.last_tick_gap_ms),
        "pytorrent_poller_effective_interval_seconds": ("Current effective live poll interval.", state.effective_interval_seconds),
        "pytorrent_poller_last_ok": ("Whether the latest poller tick succeeded.", 1 if state.last_ok else 0),
        "pytorrent_poller_consecutive_errors": ("Current consecutive poller error count.", state.error_count),
        "pytorrent_poller_slow_count": ("Current adaptive slow-response counter.", state.slow_count),
        "pytorrent_poller_connected_clients": ("Connected browser clients for this profile.", state.connected_clients),
        "pytorrent_poller_rtorrent_calls_last_tick": ("rTorrent calls made during the latest tick.", state.rtorrent_call_count),
        "pytorrent_poller_emitted_payload_bytes_last_tick": ("Socket payload bytes emitted by the latest tick.", state.emitted_payload_size),
        "pytorrent_poller_skipped_emissions_total": ("No-op socket emissions skipped since process start.", state.skipped_emissions),
        "pytorrent_poller_live_polls_total": ("Live torrent polls completed since process start.", state.live_poll_count),
        "pytorrent_poller_list_polls_total": ("Full torrent-list polls completed since process start.", state.list_poll_count),
        "pytorrent_poller_last_live_duration_ms": ("Duration of the latest live torrent poll.", state.last_live_duration_ms),
        "pytorrent_poller_last_list_duration_ms": ("Duration of the latest full torrent-list poll.", state.last_list_duration_ms),
        "pytorrent_poller_live_updated_total": ("Torrent live updates observed since process start.", state.live_updated_total),
        "pytorrent_poller_full_refresh_requests_total": ("Full refreshes requested by live polling since process start.", state.live_full_refresh_requested_total),
        "pytorrent_poller_list_added_total": ("Torrents added through list refreshes since process start.", state.list_added_total),
        "pytorrent_poller_list_updated_total": ("Torrents updated through list refreshes since process start.", state.list_updated_total),
        "pytorrent_poller_list_removed_total": ("Torrents removed through list refreshes since process start.", state.list_removed_total),
        "pytorrent_poller_background_mode": ("Whether background polling mode is active.", 1 if bool((state.stats or {}).get("background_mode_active")) else 0),
        "pytorrent_poller_slow_task_running": ("Whether the slow task bundle is currently running.", 1 if state.slow_task_running else 0),
        "pytorrent_poller_queue_task_running": ("Whether the queue task bundle is currently running.", 1 if state.queue_task_running else 0),
        "pytorrent_poller_system_task_running": ("Whether the system task bundle is currently running.", 1 if state.system_task_running else 0),
    }
    for name, (help_text, value) in metrics.items():
        text.gauge(name, help_text, value, labels)


def _connection_metrics(text: _PrometheusText, profile_id: int, meta: dict[str, Any]) -> None:
    labels = _base_labels(profile_id, meta)
    observed = connection_diagnostics.metrics_snapshot(profile_id)
    last_ok = observed.get("last_ok")
    text.gauge("pytorrent_scgi_last_ok", "Whether the latest observed SCGI request succeeded.", 1 if last_ok else 0, labels)
    text.gauge("pytorrent_scgi_last_duration_ms", "Duration of the latest observed SCGI request.", observed.get("last_duration_ms") or 0, labels)
    text.gauge("pytorrent_scgi_last_success_age_seconds", "Seconds since the latest successful SCGI response.", observed.get("last_success_age_seconds") or 0, labels)
    text.gauge("pytorrent_scgi_last_attempt_age_seconds", "Seconds since the latest observed SCGI attempt.", observed.get("last_attempt_age_seconds") or 0, labels)
    probe = observed.get("last_probe") or {}
    for key in ("connect_ms", "first_byte_ms", "send_ms", "total_ms", "request_bytes", "response_bytes", "xml_bytes"):
        if key in probe and isinstance(probe.get(key), (int, float)):
            unit = "bytes" if key.endswith("bytes") else "milliseconds"
            text.gauge(f"pytorrent_scgi_probe_{key}", f"Latest explicit SCGI probe {key.replace('_', ' ')} in {unit}.", probe[key], labels)


def _captured_metrics(
    text: _PrometheusText,
    profile_id: int,
    meta: dict[str, Any],
    system_values: dict[str, float],
    speed_values: dict[str, float],
    summary: dict[str, dict[str, float]],
    captured_at: float,
) -> None:
    labels = _base_labels(profile_id, meta)
    text.gauge("pytorrent_profile_info", "Static profile information.", 1, labels)
    text.gauge("pytorrent_profile_remote", "Whether the rTorrent profile is remote.", meta.get("remote") or 0, labels)
    text.gauge("pytorrent_metrics_data_age_seconds", "Age of the newest passively captured metric data.", max(0.0, time.time() - captured_at) if captured_at else 0, labels)

    for key, value in sorted(system_values.items()):
        if key in {"cpu", "ram"}:
            continue
        text.gauge(f"pytorrent_system_{key}", f"Passively captured system statistic: {key.replace('_', ' ')}.", value, labels)
    for key, value in sorted(speed_values.items()):
        text.gauge(f"pytorrent_live_{key}", f"Passively captured live statistic: {key.replace('_', ' ')}.", value, labels)

    aliases = {
        "down_rate": "pytorrent_transfer_download_bytes_per_second",
        "up_rate": "pytorrent_transfer_upload_bytes_per_second",
        "total_down": "pytorrent_transfer_downloaded_bytes",
        "total_up": "pytorrent_transfer_uploaded_bytes",
    }
    combined = {**system_values, **speed_values}
    for source_key, metric_name in aliases.items():
        if source_key in combined:
            text.gauge(metric_name, f"pyTorrent {source_key.replace('_', ' ')}.", combined[source_key], labels)

    for state, bucket in sorted(summary.items()):
        state_labels = {**labels, "state": state}
        if "count" in bucket:
            text.gauge("pytorrent_torrents", "Torrent count by UI state.", bucket["count"], state_labels)
        for key in ("size", "disk_bytes", "completed_bytes", "remaining_bytes", "down_total", "up_total", "progress_percent", "remaining_percent"):
            if key in bucket:
                suffix = "bytes" if key not in {"progress_percent", "remaining_percent"} else "percent"
                text.gauge(f"pytorrent_torrents_{key}_{suffix}" if not key.endswith(suffix) else f"pytorrent_torrents_{key}", f"Torrent summary {key.replace('_', ' ')} by state.", bucket[key], state_labels)


def _host_metrics(
    text: _PrometheusText,
    profile_ids: list[int],
    meta_copy: dict[int, dict[str, Any]],
    system_copy: dict[int, dict[str, float]],
    system_capture_copy: dict[int, float],
) -> None:
    # Note: CPU/RAM belong to the host, not an rTorrent profile. Keep only the newest sample for each SCGI hostname.
    hosts: dict[str, tuple[float, dict[str, float]]] = {}
    for profile_id in profile_ids:
        values = system_copy.get(profile_id) or {}
        if "cpu" not in values and "ram" not in values:
            continue
        meta = meta_copy.get(profile_id) or {}
        host = str(meta.get("host") or METRICS_INSTANCE)
        captured_at = float(system_capture_copy.get(profile_id) or 0.0)
        current = hosts.get(host)
        if current is None or captured_at >= current[0]:
            hosts[host] = (captured_at, values)

    for host, (_, values) in sorted(hosts.items()):
        labels = {"server": METRICS_INSTANCE, "host": host}
        if "cpu" in values:
            text.gauge("pytorrent_host_cpu_percent", "Host CPU utilization percent.", values["cpu"], labels)
        if "ram" in values:
            text.gauge("pytorrent_host_ram_percent", "Host RAM utilization percent.", values["ram"], labels)


def render_metrics() -> str:
    """Render only cached/in-memory data; scraping never calls rTorrent or starts work."""
    text = _PrometheusText()
    labels = {"server": METRICS_INSTANCE}
    process = psutil.Process(os.getpid())
    try:
        text.gauge("pytorrent_process_uptime_seconds", "pyTorrent process uptime.", max(0.0, time.time() - process.create_time()), labels)
        text.gauge("pytorrent_process_resident_memory_bytes", "pyTorrent resident memory size.", process.memory_info().rss, labels)
        text.gauge("pytorrent_process_threads", "pyTorrent process thread count.", process.num_threads(), labels)
        text.gauge("pytorrent_process_cpu_percent", "pyTorrent process CPU percent as observed at scrape time.", process.cpu_percent(interval=None), labels)
    except Exception:
        pass

    with _LOCK:
        profile_ids = sorted(set(_PROFILE_META) | set(_SYSTEM_STATS) | set(_SPEED_STATS) | set(_TORRENT_SUMMARIES))
        meta_copy = {pid: dict(_PROFILE_META.get(pid) or {}) for pid in profile_ids}
        system_copy = {pid: dict(_SYSTEM_STATS.get(pid) or {}) for pid in profile_ids}
        speed_copy = {pid: dict(_SPEED_STATS.get(pid) or {}) for pid in profile_ids}
        summary_copy = {pid: {state: dict(values) for state, values in (_TORRENT_SUMMARIES.get(pid) or {}).items()} for pid in profile_ids}
        capture_copy = {pid: float(_LAST_CAPTURE_EPOCH.get(pid) or 0.0) for pid in profile_ids}
        system_capture_copy = {pid: float(_SYSTEM_CAPTURE_EPOCH.get(pid) or 0.0) for pid in profile_ids}

    _host_metrics(text, profile_ids, meta_copy, system_copy, system_capture_copy)

    for profile_id in profile_ids:
        meta = meta_copy.get(profile_id) or {"profile_id": profile_id, "profile": f"rTorrent {profile_id}", "remote": 0.0, "host": METRICS_INSTANCE}
        _captured_metrics(text, profile_id, meta, system_copy.get(profile_id, {}), speed_copy.get(profile_id, {}), summary_copy.get(profile_id, {}), capture_copy.get(profile_id, 0.0))
        _poller_metrics(text, profile_id, meta)
        _connection_metrics(text, profile_id, meta)
    return text.render()


def _client_allowed() -> bool:
    if not _ACL_CONFIGURED:
        return True
    if not _ALLOWED_NETWORKS:
        return False
    try:
        client = ipaddress.ip_address(str(request.remote_addr or ""))
    except ValueError:
        return False
    return any(client in network for network in _ALLOWED_NETWORKS)


def _basic_allowed() -> bool:
    if not _BASIC_CONFIGURED:
        return True
    if not METRICS_BASIC_USER or not METRICS_BASIC_PASSWORD:
        return False
    supplied = request.authorization
    if not supplied or str(supplied.type or "").lower() != "basic":
        return False
    return hmac.compare_digest(str(supplied.username or ""), METRICS_BASIC_USER) and hmac.compare_digest(str(supplied.password or ""), METRICS_BASIC_PASSWORD)


def response() -> Response:
    """Serve Prometheus text with optional IP ACL and Basic Auth, without triggering application work."""
    if not enabled():
        abort(404)
    if not _client_allowed():
        return Response("Forbidden\n", status=403, content_type="text/plain; charset=utf-8")
    if _BASIC_CONFIGURED and (not METRICS_BASIC_USER or not METRICS_BASIC_PASSWORD):
        return Response("Metrics Basic Auth is misconfigured\n", status=503, content_type="text/plain; charset=utf-8")
    if not _basic_allowed():
        response = Response("Authentication required\n", status=401, content_type="text/plain; charset=utf-8")
        response.headers["WWW-Authenticate"] = 'Basic realm="pyTorrent metrics", charset="UTF-8"'
        return response
    response = Response(render_metrics(), status=200, content_type="text/plain; version=0.0.4; charset=utf-8")
    response.headers["Cache-Control"] = "no-store"
    return response
