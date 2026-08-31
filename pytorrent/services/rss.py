from __future__ import annotations
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from typing import Iterable

from ..db import connect, default_user_id, utcnow
from . import auth, rtorrent
from .safe_http import fetch_public
from .workers import enqueue

RSS_FETCH_LIMIT = 2_000_000


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).astimezone(timezone.utc)
    except Exception:
        return None


def _item_size(item: ET.Element) -> int:
    enc = item.find("enclosure")
    if enc is not None:
        try:
            return int(enc.get("length") or 0)
        except Exception:
            return 0
    for tag in ("size", "length"):
        try:
            return int(item.findtext(tag) or 0)
        except Exception:
            pass
    return 0


def _item_category(item: ET.Element) -> str:
    values = [x.text or "" for x in item.findall("category")]
    return " ".join(values).strip()


def parse_feed(raw: bytes) -> list[dict]:
    root = ET.fromstring(raw)
    items = root.findall(".//item")
    if not items and root.tag.lower().endswith("feed"):
        items = root.findall("{http://www.w3.org/2005/Atom}entry")
    parsed: list[dict] = []
    for item in items[:200]:
        title = item.findtext("title") or item.findtext("{http://www.w3.org/2005/Atom}title") or ""
        link = item.findtext("link") or ""
        atom_link = item.find("{http://www.w3.org/2005/Atom}link")
        if atom_link is not None and atom_link.get("href"):
            link = atom_link.get("href") or link
        enc = item.find("enclosure")
        if enc is not None and enc.get("url"):
            link = enc.get("url") or link
        pub_date = item.findtext("pubDate") or item.findtext("updated") or item.findtext("{http://www.w3.org/2005/Atom}updated")
        parsed.append({
            "title": title.strip(),
            "link": str(link or "").strip(),
            "size": _item_size(item),
            "category": _item_category(item),
            "published_at": _parse_dt(pub_date).isoformat(timespec="seconds") if _parse_dt(pub_date) else None,
        })
    return parsed


def fetch_feed(url: str) -> list[dict]:
    """Fetch an RSS/Atom feed only from public HTTP(S) destinations."""
    # Note: DNS resolution and every redirect hop are revalidated to block loopback/private/link-local SSRF targets.
    raw, _content_type, _final_url = fetch_public(url, timeout=12, limit=RSS_FETCH_LIMIT, headers={"User-Agent": "pyTorrent RSS"})
    return parse_feed(raw)


def _season_episode(title: str) -> tuple[int | None, int | None]:
    match = re.search(r"S(\d{1,2})E(\d{1,3})", title or "", re.I)
    if match:
        return int(match.group(1)), int(match.group(2))
    match = re.search(r"\b(\d{1,2})x(\d{1,3})\b", title or "", re.I)
    if match:
        return int(match.group(1)), int(match.group(2))
    return None, None


def matches_rule(rule: dict, item: dict) -> tuple[bool, str]:
    title = str(item.get("title") or "")
    haystack = " ".join([title, str(item.get("category") or "")])
    pattern = str(rule.get("pattern") or ".*")
    exclude = str(rule.get("exclude_pattern") or "").strip()
    try:
        if pattern and not re.search(pattern, haystack, re.I):
            return False, "include pattern did not match"
        if exclude and re.search(exclude, haystack, re.I):
            return False, "exclude pattern matched"
    except re.error as exc:
        return False, f"invalid regex: {exc}"
    size_mb = (int(item.get("size") or 0) / 1024 / 1024) if item.get("size") else 0
    min_size = int(rule.get("min_size_mb") or 0)
    max_size = int(rule.get("max_size_mb") or 0)
    if min_size and size_mb and size_mb < min_size:
        return False, "item is below minimum size"
    if max_size and size_mb and size_mb > max_size:
        return False, "item is above maximum size"
    category = str(rule.get("category") or "").strip().lower()
    if category and category not in str(item.get("category") or "").lower() and category not in title.lower():
        return False, "category did not match"
    quality = str(rule.get("quality") or "").strip().lower()
    if quality and quality not in title.lower():
        return False, "quality did not match"
    wanted_season = rule.get("season")
    wanted_episode = rule.get("episode")
    found_season, found_episode = _season_episode(title)
    if wanted_season not in (None, "", 0) and int(wanted_season) != int(found_season or -1):
        return False, "season did not match"
    if wanted_episode not in (None, "", 0) and int(wanted_episode) != int(found_episode or -1):
        return False, "episode did not match"
    return True, "matched"


def _log(profile_id: int, feed_id: int | None, rule_id: int | None, item: dict, status: str, message: str, *, job_id: str | None = None) -> int | None:
    """Write one RSS history row and return its id when accepted."""
    # Note: Callers reserve a queued row before enqueueing so the unique index prevents duplicate jobs, not merely duplicate history.
    with connect() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO rss_history(profile_id,feed_id,rule_id,title,link,status,message,job_id,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (profile_id, feed_id, rule_id, item.get("title"), item.get("link"), status, message, job_id, utcnow()),
            )
            return int(cur.lastrowid)
        except Exception:
            return None


def _set_history_job(history_id: int, job_id: str) -> None:
    # Note: The history row is linked after enqueue succeeds without changing its pre-enqueue reservation status.
    with connect() as conn:
        conn.execute("UPDATE rss_history SET job_id=? WHERE id=? AND status='queued'", (str(job_id), int(history_id)))


def _fail_history_reservation(history_id: int, message: str) -> None:
    # Note: Failed enqueue reservations become retryable because the RSS unique index covers only queued/added rows.
    with connect() as conn:
        conn.execute("UPDATE rss_history SET status='failed', message=? WHERE id=? AND status='queued'", (str(message or "enqueue failed"), int(history_id)))


def check(profile: dict, user_id: int | None = None, only_due: bool = False, system_execution: bool = False) -> dict:
    """Check profile RSS rules and queue matches under the requested execution scope."""
    # Note: Scheduler jobs retain trusted profile authority while manual requests remain bound to their authenticated user.
    profile_id = int(profile["id"])
    executor_user_id = int(user_id or auth.current_user_id() or profile.get("user_id") or default_user_id())
    if not system_execution and not auth.can_write_profile(profile_id, executor_user_id):
        raise PermissionError("No write access to profile")
    now = utcnow()
    with connect() as conn:
        if only_due:
            feeds = conn.execute("SELECT * FROM rss_feeds WHERE profile_id=? AND enabled=1 AND (next_check_at IS NULL OR next_check_at<=?)", (profile_id, now)).fetchall()
        else:
            feeds = conn.execute("SELECT * FROM rss_feeds WHERE profile_id=? AND enabled=1", (profile_id,)).fetchall()
        rules = conn.execute("SELECT * FROM rss_rules WHERE profile_id=? AND enabled=1", (profile_id,)).fetchall()
    queued = 0
    tested = 0
    errors: list[dict] = []
    for feed in feeds:
        interval = max(5, int(feed.get("interval_minutes") or 30))
        next_check = (datetime.now(timezone.utc) + timedelta(minutes=interval)).isoformat(timespec="seconds")
        try:
            items = fetch_feed(feed["url"])
            for item in items:
                for rule in rules:
                    matched, reason = matches_rule(rule, item)
                    tested += 1
                    if not matched:
                        continue
                    link = item.get("link") or ""
                    if not link:
                        _log(profile_id, feed["id"], rule["id"], item, "skipped", "missing link")
                        continue
                    history_id = _log(profile_id, feed["id"], rule["id"], item, "queued", reason)
                    if not history_id:
                        continue
                    try:
                        payload = {
                            "uri": link, "start": bool(rule["start"]),
                            "directory": rule.get("save_path") or rtorrent.default_download_path(profile),
                            "label": rule.get("label") or "", "source": "rss",
                            "job_context": {"source": "rss", "rss_history_id": history_id, "feed_id": feed.get("id"), "rule_id": rule.get("id"), "title": item.get("title"), "hash_count": 0},
                        }
                        job_id = enqueue("add_magnet", profile_id, payload, user_id=executor_user_id, system_managed=system_execution)
                        _set_history_job(history_id, job_id)
                        queued += 1
                    except Exception as exc:
                        _fail_history_reservation(history_id, str(exc))
                        raise
            with connect() as conn:
                conn.execute("UPDATE rss_feeds SET last_error=NULL,last_checked_at=?,next_check_at=?,updated_at=? WHERE id=?", (now, next_check, now, feed["id"]))
        except Exception as exc:
            errors.append({"feed_id": feed.get("id"), "error": str(exc)})
            with connect() as conn:
                conn.execute("UPDATE rss_feeds SET last_error=?,last_checked_at=?,next_check_at=?,updated_at=? WHERE id=?", (str(exc), now, next_check, now, feed["id"]))
    return {"queued": queued, "tested": tested, "feeds_checked": len(feeds), "errors": errors}


def record_job_result(job_id: str, job: dict, payload: dict, success: bool, *, result: dict | None = None, error: str = "") -> None:
    """Finalize the RSS history reservation linked to a terminal add job."""
    # Note: Worker completion is the single authority that changes queued RSS history to added or failed.
    ctx = (payload or {}).get("job_context") or {}
    if str(ctx.get("source") or "") != "rss":
        return
    history_id = int(ctx.get("rss_history_id") or 0)
    if not history_id:
        return
    with connect() as conn:
        conn.execute(
            "UPDATE rss_history SET status=?, message=?, job_id=? WHERE id=? AND (job_id=? OR job_id IS NULL)",
            ("added" if success else "failed", "job completed" if success else (error or "job failed"), str(job_id), history_id, str(job_id)),
        )


def test_rule(feed_url: str, rule: dict) -> dict:
    items = fetch_feed(feed_url)
    matches = []
    rejected = []
    for item in items[:100]:
        matched, reason = matches_rule(rule, item)
        target = matches if matched else rejected
        target.append({**item, "reason": reason})
    return {"matches": matches[:50], "rejected": rejected[:50], "total": len(items)}


_scheduler_started = False


def start_scheduler(socketio=None) -> None:
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True

    def loop() -> None:
        # Note: The lightweight RSS scheduler uses persisted next_check_at values, so restarts do not reset cadence.
        while True:
            try:
                from .preferences import get_profile_for_system
                with connect() as conn:
                    profiles = conn.execute("SELECT DISTINCT profile_id FROM rss_feeds WHERE enabled=1 AND profile_id IS NOT NULL").fetchall()
                for row in profiles:
                    profile_id = int(row["profile_id"])
                    profile = get_profile_for_system(profile_id)
                    if profile:
                        # Note: The profile owner labels durable job history but does not authorize background RSS execution.
                        audit_user_id = int(profile.get("user_id") or default_user_id())
                        result = check(profile, user_id=audit_user_id, only_due=True, system_execution=True)
                        if socketio and result.get("queued"):
                            socketio.emit("rss_checked", {"profile_id": profile["id"], **result}, to=f"profile:{profile['id']}")
            except Exception:
                pass
            time.sleep(60)

    if socketio:
        socketio.start_background_task(loop)
    else:
        import threading
        threading.Thread(target=loop, daemon=True, name="pytorrent-rss-scheduler").start()
