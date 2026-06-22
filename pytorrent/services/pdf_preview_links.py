from __future__ import annotations
import secrets
import threading
import time

_LINK_TTL_SECONDS = 10 * 60
_TEMPORARY_LINKS: dict[str, dict] = {}
_TEMPORARY_LINK_LOCK = threading.Lock()


def _cleanup_expired(now: float | None = None) -> None:
    now = time.time() if now is None else float(now)
    expired = [token for token, item in _TEMPORARY_LINKS.items() if float(item.get("expires_at") or 0) <= now]
    for token in expired:
        _TEMPORARY_LINKS.pop(token, None)


def _create_temporary_link(kind: str, profile_id: int, user_id: int, payload: dict) -> dict:
    """Create a short-lived in-app link target used by preview and download routes."""
    now = time.time()
    token = secrets.token_urlsafe(24)
    with _TEMPORARY_LINK_LOCK:
        _cleanup_expired(now)
        _TEMPORARY_LINKS[token] = {
            "kind": str(kind),
            "profile_id": int(profile_id),
            "user_id": int(user_id),
            "expires_at": now + _LINK_TTL_SECONDS,
            **payload,
        }
    return {"token": token, "expires_in": _LINK_TTL_SECONDS}


def create_pdf_preview_link(torrent_hash: str, file_index: int, profile_id: int, user_id: int) -> dict:
    """Create a short-lived in-app PDF preview link without exposing the API download URL."""
    return _create_temporary_link(
        "pdf_preview",
        profile_id,
        user_id,
        {"torrent_hash": str(torrent_hash), "file_index": int(file_index)},
    )


def create_file_download_link(torrent_hash: str, file_index: int, profile_id: int, user_id: int) -> dict:
    """Create a temporary in-app download link for one torrent file."""
    return _create_temporary_link(
        "file_download",
        profile_id,
        user_id,
        {"torrent_hash": str(torrent_hash), "file_index": int(file_index)},
    )


def create_file_zip_download_link(torrent_hash: str, indexes: list[int] | None, profile_id: int, user_id: int) -> dict:
    """Create a temporary in-app download link for a ZIP of torrent files."""
    clean_indexes = None if indexes is None else [int(index) for index in indexes]
    return _create_temporary_link(
        "file_zip_download",
        profile_id,
        user_id,
        {"torrent_hash": str(torrent_hash), "indexes": clean_indexes},
    )


def create_torrent_file_download_link(torrent_hash: str, profile_id: int, user_id: int) -> dict:
    """Create a temporary in-app download link for an exported .torrent file."""
    return _create_temporary_link(
        "torrent_file_download",
        profile_id,
        user_id,
        {"torrent_hash": str(torrent_hash)},
    )


def create_torrent_files_zip_download_link(hashes: list[str], profile_id: int, user_id: int) -> dict:
    """Create a temporary in-app download link for a ZIP of exported .torrent files."""
    return _create_temporary_link(
        "torrent_files_zip_download",
        profile_id,
        user_id,
        {"hashes": [str(item) for item in hashes]},
    )


def get_temporary_link(token: str) -> dict | None:
    """Return a temporary target if the link is still valid."""
    clean = str(token or "").strip()
    if not clean:
        return None
    with _TEMPORARY_LINK_LOCK:
        _cleanup_expired()
        item = _TEMPORARY_LINKS.get(clean)
        return dict(item) if item else None


def get_pdf_preview_link(token: str) -> dict | None:
    """Return a temporary PDF preview target if the link is still valid."""
    item = get_temporary_link(token)
    if not item or item.get("kind") != "pdf_preview":
        return None
    return item
