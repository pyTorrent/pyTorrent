from __future__ import annotations
from threading import RLock
from time import time
from . import rtorrent, operation_logs

_LIVE_KEYS = {"state", "active", "paused", "complete", "completed_bytes", "progress", "ratio", "up_rate", "up_rate_h", "down_rate", "down_rate_h", "eta_seconds", "eta_h", "up_total", "up_total_h", "down_total", "down_total_h", "to_download", "to_download_h", "peers", "seeds", "message", "status", "post_check", "hashing"}
_VOLATILE = {"down_rate", "down_rate_h", "up_rate", "up_rate_h", "progress", "completed_bytes", "peers", "seeds", "ratio", "state", "status", "message", "down_total", "down_total_h", "to_download", "to_download_h", "up_total", "up_total_h"}


class TorrentCache:
    def __init__(self):
        self._lock = RLock()
        self._data: dict[int, dict[str, dict]] = {}
        self._errors: dict[int, str] = {}
        self._updated_at: dict[int, float] = {}

    def snapshot(self, profile_id: int) -> list[dict]:
        with self._lock:
            return list(self._data.get(profile_id, {}).values())

    def error(self, profile_id: int) -> str:
        with self._lock:
            return self._errors.get(profile_id, "")

    def age_seconds(self, profile_id: int) -> float | None:
        with self._lock:
            updated = self._updated_at.get(int(profile_id))
        return None if not updated else max(0.0, time() - updated)

    def is_stale(self, profile_id: int, max_age_seconds: float) -> bool:
        age = self.age_seconds(profile_id)
        return age is None or age >= max(0.0, float(max_age_seconds or 0))

    def refresh_if_stale(self, profile: dict, max_age_seconds: float) -> dict:
        profile_id = int(profile["id"])
        if self.is_stale(profile_id, max_age_seconds):
            return self.refresh(profile)
        return {"ok": True, "profile_id": profile_id, "skipped": True, "age_seconds": self.age_seconds(profile_id)}

    def clear_profile(self, profile_id: int) -> int:
        """Clear cached torrent rows for one profile and return removed row count."""
        # Note: Cleanup clears only in-memory rows for the selected profile; rTorrent data is untouched.
        profile_id = int(profile_id or 0)
        with self._lock:
            removed = len(self._data.get(profile_id, {}))
            self._data.pop(profile_id, None)
            self._errors.pop(profile_id, None)
            self._updated_at.pop(profile_id, None)
            return removed


    def refresh_live(self, profile: dict) -> dict:
        """Refresh only volatile live fields without replacing the full cached torrent rows."""
        # Note: The fast poller uses this lightweight path so speeds/statuses can update often while the full list poller stays slower.
        profile_id = int(profile["id"])
        try:
            rows = rtorrent.list_torrent_live_stats(profile)
            live = {t["hash"]: t for t in rows if t.get("hash")}
            with self._lock:
                old = dict(self._data.get(profile_id, {}))
                old_for_logs = {h: dict(row) for h, row in old.items()}
                if not old:
                    self._errors[profile_id] = ""
                    return {"ok": True, "profile_id": profile_id, "updated": [], "missing": [], "unknown": list(live.keys()), "requires_full_refresh": bool(live)}
                updated = []
                for h, live_row in live.items():
                    current = old.get(h)
                    if not current:
                        continue
                    patch = {"hash": h}
                    for key in _LIVE_KEYS:
                        if key in live_row and current.get(key) != live_row.get(key):
                            patch[key] = live_row.get(key)
                    if len(patch) > 1:
                        current.update({k: v for k, v in patch.items() if k != "hash"})
                        updated.append(patch)
                missing = [h for h in old.keys() if h not in live]
                unknown = [h for h in live.keys() if h not in old]
                self._data[profile_id] = old
                self._errors[profile_id] = ""
                self._updated_at[profile_id] = time()
            if updated:
                # Note: Low CPU mode can postpone full-list polling, so live completion transitions must still reach operation logs.
                operation_logs.record_cache_diff(profile_id, [], [], updated, old_for_logs)
            return {"ok": True, "profile_id": profile_id, "updated": updated, "missing": missing, "unknown": unknown, "requires_full_refresh": bool(missing or unknown)}
        except Exception as exc:
            with self._lock:
                self._errors[profile_id] = str(exc)
            return {"ok": False, "profile_id": profile_id, "error": str(exc), "updated": [], "missing": [], "unknown": [], "requires_full_refresh": False}

    def refresh(self, profile: dict) -> dict:
        profile_id = int(profile["id"])
        try:
            rows = rtorrent.list_torrents(profile)
            with self._lock:
                old = dict(self._data.get(profile_id, {}))
            post_check_changes = rtorrent.apply_post_check_policy(profile, rows, old)
            fresh = {t["hash"]: t for t in rows}
            with self._lock:
                added = [v for h, v in fresh.items() if h not in old]
                removed = [h for h in old.keys() if h not in fresh]
                updated = []
                for h, new in fresh.items():
                    prev = old.get(h)
                    if not prev:
                        continue
                    patch = {"hash": h}
                    for key, value in new.items():
                        if prev.get(key) != value:
                            patch[key] = value
                    if len(patch) > 1:
                        updated.append(patch)
                self._data[profile_id] = fresh
                self._errors[profile_id] = ""
                self._updated_at[profile_id] = time()
            if old:
                operation_logs.record_cache_diff(profile_id, added, removed, updated, old)
            return {"ok": True, "profile_id": profile_id, "added": added, "updated": updated, "removed": removed, "post_check_changes": post_check_changes}
        except Exception as exc:
            with self._lock:
                self._errors[profile_id] = str(exc)
            return {"ok": False, "profile_id": profile_id, "error": str(exc), "added": [], "updated": [], "removed": []}


torrent_cache = TorrentCache()
