from __future__ import annotations
from .client import *
from .. import poller_control

def scgi_diagnostics(profile: dict) -> dict:
    c = client_for(profile)
    started = time.perf_counter()
    body = dumps((), methodname="system.client_version", allow_none=True).encode("utf-8")
    headers = {
        "CONTENT_LENGTH": str(len(body)),
        "SCGI": "1",
        "REQUEST_METHOD": "POST",
        "REQUEST_URI": c.path,
        "SCRIPT_NAME": c.path,
        "SERVER_PROTOCOL": "HTTP/1.1",
        "CONTENT_TYPE": "text/xml",
    }
    header_blob = b"".join(k.encode() + b"\0" + v.encode() + b"\0" for k, v in headers.items())
    payload = str(len(header_blob)).encode("ascii") + b":" + header_blob + b"," + body
    metrics = {
        "url": profile.get("scgi_url"),
        "host": c.host,
        "port": c.port,
        "path": c.path,
        "timeout_seconds": c.timeout,
        "request_bytes": len(payload),
    }
    connect_started = time.perf_counter()
    with socket.create_connection((c.host, c.port), timeout=c.timeout) as sock:
        sock.settimeout(c.timeout)
        metrics["connect_ms"] = round((time.perf_counter() - connect_started) * 1000, 2)
        send_started = time.perf_counter()
        sock.sendall(payload)
        metrics["send_ms"] = round((time.perf_counter() - send_started) * 1000, 2)
        chunks: list[bytes] = []
        first_byte_at = None
        while True:
            chunk = sock.recv(65536)
            if chunk and first_byte_at is None:
                first_byte_at = time.perf_counter()
            if not chunk:
                break
            chunks.append(chunk)
    response = b"".join(chunks)
    metrics["response_bytes"] = len(response)
    metrics["first_byte_ms"] = round(((first_byte_at or time.perf_counter()) - started) * 1000, 2)
    metrics["total_ms"] = round((time.perf_counter() - started) * 1000, 2)
    if not response:
        raise ConnectionError("Empty response from rTorrent SCGI")
    xml_response = response
    if b"\r\n\r\n" in xml_response:
        xml_response = xml_response.split(b"\r\n\r\n", 1)[1]
    elif b"\n\n" in xml_response:
        xml_response = xml_response.split(b"\n\n", 1)[1]
    result, _ = loads(xml_response)
    metrics["xml_bytes"] = len(xml_response)
    metrics["client_version"] = str(result[0]) if result else ""
    metrics["ok"] = True
    return metrics



def profile_diagnostics(profile: dict) -> dict:
    """Lightweight per-profile diagnostics for save/test UI."""
    started = time.perf_counter()
    profile_id = profile.get("id")
    try:
        slow_threshold_ms = float(poller_control.get_settings(int(profile_id)).get("slow_response_threshold_ms") or poller_control.DEFAULTS["slow_response_threshold_ms"])
    except Exception:
        slow_threshold_ms = float(poller_control.DEFAULTS["slow_response_threshold_ms"])
    result = {"profile_id": profile_id, "ok": False, "checks": {}, "slow_threshold_ms": slow_threshold_ms}
    try:
        c = client_for(profile)
        version = str(c.call("system.client_version") or "")
        library = ""
        try:
            library = str(c.call("system.library_version") or "")
        except Exception:
            library = ""
        paths = {}
        for key, method in (("default_directory", "directory.default"), ("cwd", "system.cwd")):
            try:
                paths[key] = str(c.call(method) or "")
            except Exception as exc:
                paths[key] = {"error": str(exc)}
        write_permissions = {}
        free_disk = {}
        base = paths.get("default_directory") if isinstance(paths.get("default_directory"), str) else ""
        if base:
            try:
                out = _rt_execute(c, "execute.capture", "sh", "-c", 'if test -w "$1"; then printf writable; else printf readonly; fi', "pytorrent-diagnostics-write", base)
                write_permissions[base] = str(out or "").strip() or "unknown"
            except Exception as exc:
                write_permissions[base] = f"error: {exc}"
            try:
                out = _rt_execute(c, "execute.capture", "sh", "-c", "df -Pk \"$1\" 2>/dev/null | awk 'END {print $4}'", "pytorrent-diagnostics-df", base)
                kb = int(str(out or "0").strip() or 0)
                free_disk[base] = {"free_bytes": kb * 1024, "free_h": human_size(kb * 1024)}
            except Exception as exc:
                free_disk[base] = {"error": str(exc)}
        result.update({
            "ok": True,
            "status": "normal",
            "version": version,
            "library_version": library,
            "base_paths": paths,
            "write_permissions": write_permissions,
            "free_disk": free_disk,
            "response_time_ms": round((time.perf_counter() - started) * 1000, 2),
        })
    except Exception as exc:
        result.update({"ok": False, "status": "error", "error": str(exc), "response_time_ms": round((time.perf_counter() - started) * 1000, 2)})
    # Note: Profile diagnostics uses the same slow-response threshold as Tools -> Poller for this profile.
    if result.get("ok") and result.get("response_time_ms", 0) > slow_threshold_ms:
        result["status"] = "slow"
    return result


__all__ = [
    name for name in globals()
    if not name.startswith("__") and name not in {"annotations"}
]
