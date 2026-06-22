from __future__ import annotations
from typing import Any
from . import download_planner


def check(profile: dict, force: bool = False) -> dict[str, Any]:
    """Compatibility check for disk protection.

    Disk protection is now configured in Download Planner. The planner performs
    the pause/resume action; this helper only reports whether the current disk
    source is over the planner threshold.
    """
    profile_id = int(profile.get("id") or 0)
    if not profile_id:
        return {"ok": False, "enabled": False, "error": "Missing profile id"}
    settings = download_planner.get_settings(profile_id)
    enabled = bool(settings.get("enabled") and settings.get("auto_pause_disk_enabled"))
    if not enabled:
        return {"ok": True, "enabled": False, "profile_id": profile_id}
    usage = download_planner.disk_usage(profile, int(settings.get("user_id") or 0) or None) or {}
    threshold = max(1, min(100, int(settings.get("auto_pause_disk_percent") or 95)))
    percent = float(usage.get("percent") or 0)
    triggered = bool(usage.get("ok") and percent >= threshold)
    return {
        "ok": True,
        "enabled": True,
        "profile_id": profile_id,
        "triggered": triggered,
        "rules": [{"threshold": threshold, "percent": percent, "mode": usage.get("mode"), "path": usage.get("path"), "usage": usage}] if triggered else [],
    }


def assert_can_start_download(profile: dict) -> None:
    result = check(profile, force=True)
    if result.get("enabled") and result.get("triggered"):
        rule = (result.get("rules") or [{}])[0]
        raise RuntimeError(
            f"Planner disk protection blocked download start: {rule.get('percent')}% >= {rule.get('threshold')}% ({rule.get('path')})"
        )
