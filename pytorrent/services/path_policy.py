from __future__ import annotations
import posixpath
from pathlib import Path


def normalize_remote_path(value: str) -> str:
    """Normalize an absolute rTorrent-host path without allowing parent traversal."""
    # Note: Remote filesystem checks use POSIX semantics regardless of the pyTorrent host OS.
    raw = str(value or "").strip().replace("\\", "/")
    if not raw:
        return ""
    if not raw.startswith("/"):
        raise ValueError("Path must be absolute")
    return posixpath.normpath(raw)


def remote_path_inside_roots(path: str, roots: list[str]) -> bool:
    """Return true when a remote path is equal to or contained by one approved root."""
    # Note: Root comparisons are component-aware so /downloads2 never matches /downloads.
    clean = normalize_remote_path(path)
    for root in roots or []:
        try:
            base = normalize_remote_path(root)
        except ValueError:
            continue
        if not base:
            continue
        if clean == base or clean.startswith(base.rstrip("/") + "/"):
            return True
    return False


def require_remote_path(path: str, roots: list[str], *, default_path: str = "") -> str:
    """Resolve and authorize a remote path against backend-owned profile roots."""
    # Note: Empty browse paths resolve to the profile root rather than the process/rTorrent current directory.
    candidate = str(path or "").strip() or str(default_path or "").strip()
    clean = normalize_remote_path(candidate)
    if not clean or not remote_path_inside_roots(clean, roots):
        raise PermissionError("Path is outside the profile download root")
    return clean


def require_local_path(path: str, roots: list[str]) -> Path:
    """Resolve a local source and require it to stay inside an approved local profile root."""
    # Note: Resolving both sides also prevents a symlink from escaping the approved download root.
    source = Path(str(path or "").strip()).expanduser().resolve()
    if not source.exists():
        raise ValueError("Source must be an existing file or directory")
    for root in roots or []:
        try:
            base = Path(str(root or "").strip()).expanduser().resolve()
            source.relative_to(base)
            return source
        except (ValueError, OSError):
            continue
    raise PermissionError("Source path is outside the profile download root")
