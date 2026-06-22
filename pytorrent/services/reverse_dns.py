from __future__ import annotations
import ipaddress
import socket
import time
from concurrent.futures import ThreadPoolExecutor, wait
from threading import Lock
from typing import Any

_CACHE_TTL_SECONDS = 24 * 60 * 60
_NEGATIVE_TTL_SECONDS = 60 * 60
_CACHE_LIMIT = 2048
_LOOKUP_LIMIT_PER_REQUEST = 24
_LOOKUP_TIMEOUT_SECONDS = 0.8

_cache: dict[str, tuple[str, float]] = {}
_pending: dict[str, Any] = {}
_lock = Lock()
_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="reverse-dns")


def _is_resolvable_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(str(value or "").strip())
        return True
    except ValueError:
        return False


def _lookup_host(ip: str) -> str:
    try:
        host = socket.gethostbyaddr(ip)[0]
        return str(host or "").rstrip(".")
    except Exception:
        return ""


def _trim_cache(now: float) -> None:
    expired = [ip for ip, (_, expires_at) in _cache.items() if expires_at <= now]
    for ip in expired:
        _cache.pop(ip, None)
    if len(_cache) <= _CACHE_LIMIT:
        return
    for ip, _ in sorted(_cache.items(), key=lambda item: item[1][1])[: len(_cache) - _CACHE_LIMIT]:
        _cache.pop(ip, None)


def _store(ip: str, host: str, now: float | None = None) -> None:
    now = now or time.monotonic()
    ttl = _CACHE_TTL_SECONDS if host else _NEGATIVE_TTL_SECONDS
    _cache[ip] = (host, now + ttl)


def attach_reverse_dns(peers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach cached or newly resolved PTR hostnames to peer rows with a small request budget."""
    now = time.monotonic()
    missing: list[str] = []
    with _lock:
        _trim_cache(now)
        for peer in peers:
            ip = str(peer.get("ip") or "").strip()
            if not ip or not _is_resolvable_ip(ip):
                peer["host"] = ""
                continue
            cached = _cache.get(ip)
            if cached and cached[1] > now:
                peer["host"] = cached[0]
                continue
            peer["host"] = ""
            if ip not in _pending and ip not in missing and len(missing) < _LOOKUP_LIMIT_PER_REQUEST:
                missing.append(ip)
        for ip in missing:
            _pending[ip] = _executor.submit(_lookup_host, ip)
        futures = list(_pending.items())

    if futures:
        wait([future for _, future in futures], timeout=_LOOKUP_TIMEOUT_SECONDS)

    done_hosts: dict[str, str] = {}
    with _lock:
        now = time.monotonic()
        for ip, future in list(_pending.items()):
            if not future.done():
                continue
            try:
                host = str(future.result() or "")
            except Exception:
                host = ""
            _store(ip, host, now)
            done_hosts[ip] = host
            _pending.pop(ip, None)

    for peer in peers:
        ip = str(peer.get("ip") or "").strip()
        if ip in done_hosts:
            peer["host"] = done_hosts[ip]
        elif not peer.get("host") and ip in _pending:
            peer["host_pending"] = True
    return peers
