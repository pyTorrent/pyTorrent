from __future__ import annotations
import errno
import os
import posixpath
import socket
import time
import uuid
from urllib.parse import urlparse
from xmlrpc.client import Binary, dumps, loads
from pathlib import Path as LocalPath
from ...utils import human_rate, human_size
from ...db import connect, default_user_id, utcnow
from ...config import PYTORRENT_TMP_DIR, REMOTE_READ_CHUNK_BYTES


class ScgiMethod:
    def __init__(self, client: "ScgiRtorrentClient", name: str):
        self.client = client
        self.name = name

    def __getattr__(self, name: str):
        return ScgiMethod(self.client, f"{self.name}.{name}")

    def __call__(self, *args):
        return self.client.call(self.name, *args)


class ScgiRtorrentClient:
    """XML-RPC over SCGI client for rTorrent network.scgi.open_port."""

    def __init__(self, url: str, timeout: int = 5):
        parsed = urlparse(url)
        if parsed.scheme != "scgi":
            raise ValueError("SCGI URL must start with scgi://")
        if not parsed.hostname or not parsed.port:
            raise ValueError("SCGI URL must include host and port, e.g. scgi://127.0.0.1:5000/RPC2")
        self.host = parsed.hostname
        self.port = parsed.port
        self.timeout = timeout
        self.path = parsed.path or "/RPC2"

    def __getattr__(self, name: str):
        return ScgiMethod(self, name)

    def call(self, method_name: str, *args):
        body = dumps(args, methodname=method_name, allow_none=True).encode("utf-8")
        headers = {
            "CONTENT_LENGTH": str(len(body)),
            "SCGI": "1",
            "REQUEST_METHOD": "POST",
            "REQUEST_URI": self.path,
            "SCRIPT_NAME": self.path,
            "SERVER_PROTOCOL": "HTTP/1.1",
            "CONTENT_TYPE": "text/xml",
        }
        header_blob = b"".join(k.encode() + b"\0" + v.encode() + b"\0" for k, v in headers.items())
        payload = str(len(header_blob)).encode("ascii") + b":" + header_blob + b"," + body
        attempts = _scgi_retry_attempts()
        last_exc = None
        for attempt in range(1, attempts + 1):
            try:
                with socket.create_connection((self.host, self.port), timeout=self.timeout) as sock:
                    sock.settimeout(self.timeout)
                    sock.sendall(payload)
                    chunks: list[bytes] = []
                    while True:
                        chunk = sock.recv(65536)
                        if not chunk:
                            break
                        chunks.append(chunk)
                response = b"".join(chunks)
                if not response:
                    raise ConnectionError("Empty response from rTorrent SCGI")
                if b"\r\n\r\n" in response:
                    response = response.split(b"\r\n\r\n", 1)[1]
                elif b"\n\n" in response:
                    response = response.split(b"\n\n", 1)[1]
                result, _ = loads(response)
                return result[0] if len(result) == 1 else result
            except Exception as exc:
                last_exc = exc
                if attempt >= attempts or not _is_transient_scgi_error(exc):
                    raise
                time.sleep(_scgi_retry_delay(attempt))
        raise last_exc or ConnectionError("rTorrent SCGI call failed")




# Note: Shared runtime caches and post-check state live in the client module so split service modules keep the same process-wide behavior as the old monolith.
_DISK_USAGE_CACHE: dict[str, tuple[float, dict]] = {}
_DISK_USAGE_TTL_SECONDS = 30.0
_REMOTE_USAGE_CACHE: dict[int, tuple[float, dict]] = {}
_REMOTE_USAGE_TTL_SECONDS = 60.0
_REMOTE_PUBLIC_IP_CACHE: dict[int, tuple[float, str]] = {}
_REMOTE_PUBLIC_IP_TTL_SECONDS = 6 * 60 * 60.0
PY_MANUAL_PAUSE_FIELD = "py_manual_pause"
POST_CHECK_DOWNLOAD_LABEL = "To download after check"
_POST_CHECK_WATCH_TTL_SECONDS = 48 * 60 * 60
_POST_CHECK_WATCH_MIN_SECONDS = 2.0
_POST_CHECK_WATCH: dict[int, dict[str, float]] = {}

def _scgi_retry_attempts() -> int:
    # Note: Short retry/backoff protects bulk operations from temporary Errno 111 during high rTorrent load.
    try:
        return max(1, min(10, int(os.environ.get("PYTORRENT_SCGI_RETRIES", "5"))))
    except Exception:
        return 5


def _scgi_retry_delay(attempt: int) -> float:
    return min(5.0, 0.35 * (2 ** max(0, attempt - 1)))


def _is_transient_scgi_error(exc: Exception) -> bool:
    # Note: Retry covers common temporary SCGI/socket errors but does not hide semantic XML-RPC errors.
    if isinstance(exc, (ConnectionRefusedError, ConnectionResetError, TimeoutError, socket.timeout)):
        return True
    err_no = getattr(exc, "errno", None)
    if err_no in {errno.ECONNREFUSED, errno.ECONNRESET, errno.ETIMEDOUT, errno.EHOSTUNREACH, errno.ENETUNREACH}:
        return True
    msg = str(exc).lower()
    return any(text in msg for text in ("connection refused", "connection reset", "timed out", "timeout", "empty response", "pipe creation failed", "resource temporarily unavailable", "try again", "temporarily unavailable"))


def client_for(profile: dict) -> ScgiRtorrentClient:
    return ScgiRtorrentClient(profile["scgi_url"], int(profile.get("timeout_seconds") or 5))


_UNSUPPORTED_EXEC_METHODS: set[str] = set()
_EXEC_TARGET_STYLE: dict[str, int] = {}

def _rt_execute_preview(method_name: str, call_args: tuple) -> str:
    # Note: The compact RPC summary removes long scripts from error messages while keeping the method and first arguments for diagnostics.
    preview = ", ".join(repr(x) for x in call_args[:3])
    if len(call_args) > 3:
        preview += ", ..."
    return f"{method_name}({preview})"


def _rt_execute_target_variants(method: str, args: tuple) -> list[tuple]:
    # Note: Depending on version, rTorrent XML-RPC either requires or rejects an empty target; cache the working variant per method.
    variants = [("", *args), args]
    preferred = _EXEC_TARGET_STYLE.get(method)
    if preferred is not None and 0 <= preferred < len(variants):
        return [variants[preferred]] + [v for i, v in enumerate(variants) if i != preferred]
    return variants


def _is_rt_method_missing(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "not defined" in msg or "no such method" in msg or "unknown method" in msg


def _rt_execute_methods(method: str) -> list[str]:
    # Note: execute2.* is tried only when the base execute.* method does not exist to avoid false retry errors.
    methods = [method]
    if method.startswith("execute."):
        fallback = method.replace("execute.", "execute2.", 1)
        if fallback not in _UNSUPPORTED_EXEC_METHODS:
            methods.append(fallback)
    return methods


def _rt_execute(c: ScgiRtorrentClient, method: str, *args):
    """Run rTorrent execute.* as the rTorrent user across XML-RPC variants."""
    errors: list[str] = []
    attempts = _scgi_retry_attempts()
    for attempt in range(1, attempts + 1):
        errors.clear()
        transient_seen = False
        primary_missing = False
        for method_index, method_name in enumerate(_rt_execute_methods(method)):
            if method_name in _UNSUPPORTED_EXEC_METHODS:
                continue
            if method_index > 0 and not primary_missing:
                continue
            for call_args in _rt_execute_target_variants(method_name, args):
                try:
                    result = c.call(method_name, *call_args)
                    if method_name == method:
                        _EXEC_TARGET_STYLE[method_name] = 0 if call_args and call_args[0] == "" else 1
                    return result
                except Exception as exc:
                    if _is_rt_method_missing(exc):
                        _UNSUPPORTED_EXEC_METHODS.add(method_name)
                        if method_name == method:
                            primary_missing = True
                        errors.append(f"{method_name}: method not defined")
                        break
                    transient_seen = transient_seen or _is_transient_scgi_error(exc)
                    errors.append(f"{_rt_execute_preview(method_name, call_args)}: {exc}")
        if transient_seen and attempt < attempts:
            time.sleep(_scgi_retry_delay(attempt))
            continue
        break
    raise RuntimeError("rTorrent execute failed: " + "; ".join(errors))


def _is_rt_timeout_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return isinstance(exc, (TimeoutError, socket.timeout)) or "timed out" in msg or "timeout" in msg


def _rt_execute_allow_timeout(c: ScgiRtorrentClient, method: str, *args):
    try:
        return _rt_execute(c, method, *args)
    except Exception as exc:
        if _is_rt_timeout_error(exc):
            return None
        raise


def _remote_clean_path(path: str) -> str:
    path = str(path or "").strip()
    return posixpath.normpath(path) if path else path


def _remote_join(*parts: str) -> str:
    cleaned = [str(p).strip().rstrip("/") for p in parts if str(p).strip()]
    return posixpath.normpath(posixpath.join(*cleaned)) if cleaned else ""


def _run_remote_move(c: ScgiRtorrentClient, src: str, dst: str, poll_interval: float = 2.0) -> None:
    """Run a remote mv without binding the transfer time to the SCGI timeout."""
    token = uuid.uuid4().hex
    status_path = f"/tmp/pytorrent-move-{token}.status"
    start_script = (
        'src=$1; dst=$2; status=$3; tmp=${status}.tmp; '
        'rm -f "$status" "$tmp"; '
        '( '
        'rc=0; '
        'parent=${dst%/*}; '
        'if [ -z "$dst" ] || [ "$dst" = "/" ]; then echo "unsafe destination: $dst" >&2; rc=5; fi; '
        'if [ $rc -eq 0 ] && [ -n "$parent" ] && [ "$parent" != "$dst" ]; then mkdir -p "$parent" || rc=$?; fi; '
        'if [ $rc -eq 0 ] && [ "$src" = "$dst" ]; then :; '
        'elif [ $rc -eq 0 ] && { [ -e "$dst" ] || [ -L "$dst" ]; } && [ ! -e "$src" ] && [ ! -L "$src" ]; then :; '
        'elif [ $rc -eq 0 ] && [ ! -e "$src" ] && [ ! -L "$src" ]; then echo "source missing: $src" >&2; rc=3; '
        'elif [ $rc -eq 0 ] && { [ -e "$dst" ] || [ -L "$dst" ]; }; then rm -rf -- "$dst" && mv -f -- "$src" "$dst" || rc=$?; '
        'elif [ $rc -eq 0 ]; then mv -f -- "$src" "$dst" || rc=$?; '
        'fi; '
        'if [ $rc -eq 0 ]; then printf "OK\n" > "$status"; '
        'else printf "ERR %s\n" "$rc" > "$status"; fi; '
        'if [ -s "$tmp" ]; then cat "$tmp" >> "$status"; fi; '
        'rm -f "$tmp" '
        ') > "$tmp" 2>&1 &'
    )
    poll_script = 'status=$1; [ -f "$status" ] && cat "$status" || true'
    cleanup_script = 'rm -f "$1"'

    _rt_execute_allow_timeout(c, "execute.throw", "sh", "-c", start_script, "pytorrent-move-start", src, dst, status_path)

    while True:
        time.sleep(max(0.25, poll_interval))
        try:
            output = str(_rt_execute(c, "execute.capture", "sh", "-c", poll_script, "pytorrent-move-poll", status_path) or "").strip()
        except Exception as exc:
            # Note: During bulk moves, rTorrent may briefly not create the execute.capture pipe; polling waits and retries.
            if _is_rt_timeout_error(exc) or _is_transient_scgi_error(exc):
                continue
            raise
        if not output:
            continue
        try:
            _rt_execute(c, "execute.throw", "sh", "-c", cleanup_script, "pytorrent-move-clean", status_path)
        except Exception:
            pass
        first_line = output.splitlines()[0].strip()
        if first_line == "OK":
            return
        if first_line.startswith("ERR"):
            details = "\n".join(output.splitlines()[1:]).strip()
            raise RuntimeError(details or first_line)
        raise RuntimeError(output)


def _torrent_data_path(c: ScgiRtorrentClient, torrent_hash: str) -> str:
    """Return data path as rTorrent sees it; do not touch pyTorrent local FS."""
    try:
        src = str(c.call("d.base_path", torrent_hash) or "").strip()
        if src:
            return src
    except Exception:
        pass
    directory = str(c.call("d.directory", torrent_hash) or "").strip()
    name = str(c.call("d.name", torrent_hash) or "").strip()
    try:
        is_multi = int(c.call("d.is_multi_file", torrent_hash) or 0)
    except Exception:
        is_multi = 0
    if is_multi:
        return directory
    if directory and name:
        return _remote_join(directory, name)
    return directory


def _safe_rm_rf_path(path: str) -> str:
    path = _remote_clean_path(path)
    if not path or path in {"/", "."}:
        raise ValueError("Refusing to remove an unsafe data path")
    if path.rstrip("/").count("/") < 1:
        raise ValueError(f"Refusing to remove an unsafe data path: {path}")
    return path


def _run_remote_rm(c: ScgiRtorrentClient, path: str, poll_interval: float = 2.0) -> None:
    # Note: rm -rf runs in the background on the rTorrent side, so long deletes do not hold a single SCGI connection.
    token = uuid.uuid4().hex
    status_path = f"/tmp/pytorrent-rm-{token}.status"
    script = (
        'target=$1; status=$2; tmp=${status}.tmp; '
        'rm -f "$status" "$tmp"; '
        '( rc=0; '
        'if [ -z "$target" ] || [ "$target" = "/" ] || [ "$target" = "." ]; then echo "unsafe remove target: $target" >&2; rc=5; '
        'else rm -rf -- "$target" || rc=$?; fi; '
        'if [ $rc -eq 0 ]; then printf "OK\n" > "$status"; else printf "ERR %s\n" "$rc" > "$status"; fi; '
        'if [ -s "$tmp" ]; then cat "$tmp" >> "$status"; fi; '
        'rm -f "$tmp" ) > "$tmp" 2>&1 &'
    )
    poll_script = 'status=$1; [ -f "$status" ] && cat "$status" || true'
    cleanup_script = 'rm -f "$1"'
    _rt_execute_allow_timeout(c, "execute.throw", "sh", "-c", script, "pytorrent-rm-start", path, status_path)
    while True:
        time.sleep(max(0.25, poll_interval))
        try:
            output = str(_rt_execute(c, "execute.capture", "sh", "-c", poll_script, "pytorrent-rm-poll", status_path) or "").strip()
        except Exception as exc:
            # Note: Remove uses the same safe polling as move, so a temporary missing pipe does not fail the whole queue.
            if _is_rt_timeout_error(exc) or _is_transient_scgi_error(exc):
                continue
            raise
        if not output:
            continue
        try:
            _rt_execute(c, "execute.throw", "sh", "-c", cleanup_script, "pytorrent-rm-clean", status_path)
        except Exception:
            pass
        first_line = output.splitlines()[0].strip()
        if first_line == "OK":
            return
        if first_line.startswith("ERR"):
            details = "\n".join(output.splitlines()[1:]).strip()
            raise RuntimeError(details or first_line)
        raise RuntimeError(output)



def remote_can_write_directory(profile: dict, path: str) -> dict:
    """Return whether the source rTorrent OS user can write to a remote directory safely."""
    clean = _remote_clean_path(path)
    # Note: Profile transfers may touch filesystem paths, so only absolute non-root directories are probed.
    if not clean.startswith("/") or clean in {"/", "."}:
        return {"ok": False, "path": clean, "error": "unsafe destination path"}
    script = (
        'p=$1; '
        'case "$p" in /*) ;; *) echo "NO\tunsafe path"; exit 0;; esac; '
        'if [ -d "$p" ]; then '
        '  if [ -w "$p" ]; then echo "OK\tdirectory writable"; else echo "NO\tdirectory not writable"; fi; '
        '  exit 0; '
        'fi; '
        'parent=${p%/*}; [ -n "$parent" ] || parent=/; '
        'if [ -d "$parent" ] && [ -w "$parent" ]; then echo "OK\tparent writable"; else echo "NO\tparent not writable"; fi'
    )
    try:
        output = str(_rt_execute(client_for(profile), "execute.capture", "sh", "-c", script, "pytorrent-transfer-write-check", clean) or "").strip()
    except Exception as exc:
        return {"ok": False, "path": clean, "error": str(exc)}
    ok = output.startswith("OK")
    return {"ok": ok, "path": clean, "message": output.split("\t", 1)[1] if "\t" in output else output}

def _remove_torrent_data(c: ScgiRtorrentClient, torrent_hash: str) -> dict:
    data_path = _safe_rm_rf_path(_torrent_data_path(c, torrent_hash))
    try:
        c.call("d.stop", torrent_hash)
    except Exception:
        pass
    try:
        c.call("d.close", torrent_hash)
    except Exception:
        pass
    _run_remote_rm(c, data_path)
    return {"hash": torrent_hash, "removed_path": data_path}



# Note: Focused rTorrent modules share low-level helpers with wildcard imports; keep private helper names available internally.
__all__ = [name for name in globals() if not name.startswith('__')]
