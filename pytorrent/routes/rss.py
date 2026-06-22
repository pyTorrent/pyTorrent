from __future__ import annotations
from ._shared import *

def _active_profile_or_400():
    profile = request_profile()
    if not profile:
        return None
    return profile


@bp.get("/rss")
def rss_list():
    profile = _active_profile_or_400()
    if not profile:
        return ok({"feeds": [], "rules": [], "history": []})
    pid = int(profile["id"])
    with connect() as conn:
        feeds = conn.execute("SELECT * FROM rss_feeds WHERE profile_id=? ORDER BY name", (pid,)).fetchall()
        rules = conn.execute("SELECT * FROM rss_rules WHERE profile_id=? ORDER BY name", (pid,)).fetchall()
        history = conn.execute("SELECT * FROM rss_history WHERE profile_id=? ORDER BY id DESC LIMIT 80", (pid,)).fetchall()
    return ok({"feeds": feeds, "rules": rules, "history": history})


@bp.post("/rss/feeds")
def rss_feed_save():
    profile = _active_profile_or_400()
    if not profile:
        return jsonify({"ok": False, "error": "No profile"}), 400
    data = request.get_json(silent=True) or {}
    now = utcnow()
    feed_id = data.get("id")
    pid = int(profile["id"])
    with connect() as conn:
        if feed_id:
            conn.execute(
                "UPDATE rss_feeds SET name=?,url=?,enabled=?,interval_minutes=?,updated_at=? WHERE id=? AND profile_id=?",
                (data.get("name") or "RSS", data.get("url") or "", 1 if data.get("enabled", True) else 0, int(data.get("interval_minutes") or 30), now, feed_id, pid),
            )
        else:
            conn.execute(
                "INSERT INTO rss_feeds(profile_id,name,url,enabled,interval_minutes,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (pid, data.get("name") or "RSS", data.get("url") or "", 1 if data.get("enabled", True) else 0, int(data.get("interval_minutes") or 30), now, now),
            )
    return rss_list()


@bp.delete("/rss/feeds/<int:feed_id>")
def rss_feed_delete(feed_id: int):
    profile = _active_profile_or_400()
    if not profile:
        return jsonify({"ok": False, "error": "No profile"}), 400
    with connect() as conn:
        conn.execute("DELETE FROM rss_feeds WHERE id=? AND profile_id=?", (feed_id, int(profile["id"])))
    return rss_list()


@bp.post("/rss/rules")
def rss_rule_save():
    profile = _active_profile_or_400()
    if not profile:
        return jsonify({"ok": False, "error": "No profile"}), 400
    data = request.get_json(silent=True) or {}
    now = utcnow()
    rule_id = data.get("id")
    pid = int(profile["id"])
    values = (
        data.get("name") or "Rule",
        data.get("pattern") or ".*",
        data.get("exclude_pattern") or "",
        int(data.get("min_size_mb") or 0),
        int(data.get("max_size_mb") or 0),
        data.get("category") or "",
        data.get("quality") or "",
        data.get("season") or None,
        data.get("episode") or None,
        data.get("save_path") or active_default_download_path(profile),
        data.get("label") or "",
        1 if data.get("start", True) else 0,
        1 if data.get("enabled", True) else 0,
        now,
    )
    with connect() as conn:
        if rule_id:
            conn.execute(
                "UPDATE rss_rules SET name=?,pattern=?,exclude_pattern=?,min_size_mb=?,max_size_mb=?,category=?,quality=?,season=?,episode=?,save_path=?,label=?,start=?,enabled=?,updated_at=? WHERE id=? AND profile_id=?",
                (*values, rule_id, pid),
            )
        else:
            conn.execute(
                "INSERT INTO rss_rules(profile_id,name,pattern,exclude_pattern,min_size_mb,max_size_mb,category,quality,season,episode,save_path,label,start,enabled,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (pid, *values, now),
            )
    return rss_list()


@bp.delete("/rss/rules/<int:rule_id>")
def rss_rule_delete(rule_id: int):
    profile = _active_profile_or_400()
    if not profile:
        return jsonify({"ok": False, "error": "No profile"}), 400
    with connect() as conn:
        conn.execute("DELETE FROM rss_rules WHERE id=? AND profile_id=?", (rule_id, int(profile["id"])))
    return rss_list()


@bp.post("/rss/rules/test")
def rss_rule_test():
    data = request.get_json(silent=True) or {}
    try:
        result = rss_service.test_rule(str(data.get("feed_url") or ""), data.get("rule") or data)
        return ok({"result": result})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@bp.post("/rss/check")
def rss_check():
    profile = request_profile()
    if not profile:
        return jsonify({"ok": False, "error": "No profile"}), 400
    return ok(rss_service.check(profile, only_due=False))
