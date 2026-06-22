from __future__ import annotations
import json
import mimetypes
import re
import time
import threading
import ssl
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from ..config import BASE_DIR
from ..db import connect, utcnow

TRACKER_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
FAVICON_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
TRACKER_SCAN_LIMIT = 80
FAVICON_DIR = BASE_DIR / "data" / "tracker_favicons"
PUBLIC_FAVICON_BASE = "/static/tracker_favicons"
_TRACKER_SCAN_LOCKS: dict[int, threading.Lock] = {}
_TRACKER_SCAN_LOCKS_GUARD = threading.Lock()


class _IconParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.icons: list[str] = []

    def handle_starttag(self, tag: str, attrs):
        if tag.lower() != "link":
            return
        data = {str(k).lower(): str(v or "") for k, v in attrs}
        rel = re.sub(r"\s+", " ", data.get("rel", "").lower()).strip()
        href = data.get("href", "").strip()
        if href and "icon" in rel:
            self.icons.append(href)


def _now_epoch() -> float:
    return time.time()


def tracker_domain(url: str) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    parsed = urllib.parse.urlparse(raw if "://" in raw else f"http://{raw}")
    host = (parsed.hostname or "").lower().strip(".")
    if host.startswith("www."):
        host = host[4:]
    return host


def _root_domain(domain: str) -> str:
    parts = [p for p in str(domain or "").lower().strip(".").split(".") if p]
    if len(parts) <= 2:
        return ".".join(parts)
    # Note: Tracker favicon discovery needs the real main site first; for t.pte.nu that is pte.nu, not t.pte.nu.
    known_second_level_suffixes = {"co", "com", "net", "org", "gov", "edu", "ac"}
    if len(parts[-1]) == 2 and parts[-2] in known_second_level_suffixes and len(parts) >= 3:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _safe_filename(domain: str) -> str:
    return re.sub(r"[^a-z0-9_.-]+", "_", domain.lower()).strip("._") or "tracker"


def _read_cached(profile_id: int, hashes: list[str], ttl: int) -> tuple[dict[str, list[dict]], set[str]]:
    if not hashes:
        return {}, set()
    now = _now_epoch()
    cached: dict[str, list[dict]] = {}
    fresh: set[str] = set()
    with connect() as conn:
        for start in range(0, len(hashes), 900):
            chunk = hashes[start:start + 900]
            placeholders = ",".join("?" for _ in chunk)
            rows = conn.execute(
                f"SELECT torrent_hash, trackers_json, updated_epoch FROM tracker_summary_cache WHERE profile_id=? AND torrent_hash IN ({placeholders})",
                (profile_id, *chunk),
            ).fetchall()
            for row in rows:
                h = str(row.get("torrent_hash") or "")
                try:
                    items = json.loads(row.get("trackers_json") or "[]")
                except Exception:
                    items = []
                cached[h] = items if isinstance(items, list) else []
                if now - float(row.get("updated_epoch") or 0) < ttl:
                    fresh.add(h)
    return cached, fresh


def _store(profile_id: int, torrent_hash: str, trackers: list[dict]) -> None:
    now = utcnow()
    epoch = _now_epoch()
    compact = []
    seen = set()
    for item in trackers:
        domain = tracker_domain(str(item.get("url") or item.get("domain") or "")) or str(item.get("domain") or "")
        if not domain or domain in seen:
            continue
        seen.add(domain)
        compact.append({"domain": domain, "url": str(item.get("url") or "")})
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO tracker_summary_cache(profile_id, torrent_hash, trackers_json, updated_at, updated_epoch)
            VALUES(?, ?, ?, ?, ?)
            ON CONFLICT(profile_id, torrent_hash) DO UPDATE SET
              trackers_json=excluded.trackers_json,
              updated_at=excluded.updated_at,
              updated_epoch=excluded.updated_epoch
            """,
            (profile_id, torrent_hash, json.dumps(compact), now, epoch),
        )


def summary(profile: dict, hashes: list[str], loader, scan_limit: int = TRACKER_SCAN_LIMIT, include_favicons: bool = False) -> dict:
    """Build tracker sidebar data from disk cache and refresh a small batch per request."""
    # Note: Tracker data is cached per torrent hash, so huge rTorrent libraries are never scanned in one UI request.
    profile_id = int(profile.get("id") or 0)
    clean_hashes = [str(h or "").strip() for h in hashes if str(h or "").strip()]
    cached, fresh = _read_cached(profile_id, clean_hashes, TRACKER_CACHE_TTL_SECONDS)
    missing = [h for h in clean_hashes if h not in fresh]
    errors: list[dict] = []
    scanned_now = 0
    for h in missing[:max(0, int(scan_limit or 0))]:
        try:
            trackers = loader(h)
            _store(profile_id, h, trackers)
            cached[h] = [{"domain": tracker_domain(t.get("url") or t.get("domain") or ""), "url": str(t.get("url") or "")} for t in trackers]
            fresh.add(h)
            scanned_now += 1
        except Exception as exc:
            errors.append({"hash": h, "error": str(exc)})
    by_hash: dict[str, list[dict]] = {}
    counts: dict[str, dict] = {}
    for h in clean_hashes:
        items = []
        seen = set()
        for item in cached.get(h, []):
            domain = tracker_domain(str(item.get("url") or item.get("domain") or "")) or str(item.get("domain") or "")
            if not domain or domain in seen:
                continue
            seen.add(domain)
            row = {"domain": domain, "url": str(item.get("url") or "")}
            items.append(row)
            bucket = counts.setdefault(domain, {"domain": domain, "url": row["url"], "count": 0})
            bucket["count"] += 1
            if not bucket.get("url") and row["url"]:
                bucket["url"] = row["url"]
        by_hash[h] = items
    trackers = sorted(counts.values(), key=lambda x: (-int(x.get("count") or 0), str(x.get("domain") or "")))
    if include_favicons:
        # Note: Summary returns only already cached static favicon URLs; network favicon discovery stays outside the hot tracker count path.
        for item in trackers:
            item["favicon_url"] = favicon_public_url(str(item.get("domain") or ""), enabled=True, create=False)
    pending = max(0, len([h for h in clean_hashes if h not in fresh]))
    return {"hashes": by_hash, "trackers": trackers, "errors": errors[:25], "scanned": len(clean_hashes), "scanned_now": scanned_now, "pending": pending, "cached": len(clean_hashes) - pending}



def _scan_lock(profile_id: int) -> threading.Lock:
    with _TRACKER_SCAN_LOCKS_GUARD:
        if profile_id not in _TRACKER_SCAN_LOCKS:
            _TRACKER_SCAN_LOCKS[profile_id] = threading.Lock()
        return _TRACKER_SCAN_LOCKS[profile_id]


def warm_summary_cache(profile: dict, hashes: list[str], loader, batch_size: int = TRACKER_SCAN_LIMIT) -> bool:
    """Start a non-blocking tracker cache warmup for large libraries."""
    # Note: Tracker cache warming runs in one background thread per profile, so F5 returns cached data immediately instead of waiting for rTorrent scans.
    profile_id = int(profile.get("id") or 0)
    clean_hashes = [str(h or "").strip() for h in hashes if str(h or "").strip()]
    if not profile_id or not clean_hashes:
        return False
    lock = _scan_lock(profile_id)
    if lock.locked():
        return False

    def _worker():
        if not lock.acquire(blocking=False):
            return
        try:
            while True:
                result = summary(profile, clean_hashes, loader, scan_limit=max(1, int(batch_size or TRACKER_SCAN_LIMIT)), include_favicons=False)
                if int(result.get("pending") or 0) <= 0 or int(result.get("scanned_now") or 0) <= 0:
                    break
                time.sleep(0.05)
        finally:
            lock.release()

    threading.Thread(target=_worker, name=f"tracker-cache-warm-{profile_id}", daemon=True).start()
    return True


def favicon_public_url(domain: str, enabled: bool = True, create: bool = False, force: bool = False) -> str:
    """Return the static URL for a cached tracker favicon, optionally creating or refreshing it first."""
    # Note: Favicon files stay in data/tracker_favicons, but the browser loads them via the static/tracker_favicons symlink.
    clean = tracker_domain(domain)
    if not enabled or not clean:
        return ""
    if create:
        favicon_path(clean, enabled=True, force=force)
    cached = _cached_favicon(clean)
    now = _now_epoch()
    if not cached or now - float(cached.get("updated_epoch") or 0) >= FAVICON_CACHE_TTL_SECONDS:
        return ""
    path = Path(str(cached.get("file_path") or ""))
    if not path.exists() or not path.is_file():
        return ""
    try:
        rel = path.resolve().relative_to(FAVICON_DIR.resolve())
    except Exception:
        rel = Path(path.name)
    return f"{PUBLIC_FAVICON_BASE}/{urllib.parse.quote(str(rel).replace(chr(92), '/'))}"

def _fetch(url: str, limit: int = 262144) -> tuple[bytes, str, str]:
    # Note: Favicon discovery uses browser-like headers and a certificate fallback, because tracker login pages/CDNs often reject minimal Python requests.
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; pyTorrent favicon fetcher)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/png,image/svg+xml,image/*,*/*;q=0.8",
            "Connection": "close",
        },
    )

    def _read(context=None):
        with urllib.request.urlopen(req, timeout=8, context=context) as resp:
            data = resp.read(limit + 1)
            if len(data) > limit:
                data = data[:limit]
            content_type = str(resp.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
            final_url = str(resp.geturl() or url)
            return data, content_type, final_url

    try:
        return _read()
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", None)
        if isinstance(reason, ssl.SSLError) or "CERTIFICATE_VERIFY_FAILED" in str(exc):
            return _read(ssl._create_unverified_context())
        raise


def _is_icon(data: bytes, content_type: str, url: str) -> bool:
    """Validate that downloaded bytes are a browser-readable image, not only an image-like HTTP header."""
    # Note: Some trackers serve a broken /favicon.ico with image/vnd.microsoft.icon; pyTorrent now validates bytes before caching it.
    if not data or len(data) < 16:
        return False
    head = data[:32]
    lower = data[:512].lstrip().lower()
    if head.startswith(b"\x00\x00\x01\x00") or head.startswith(b"\x00\x00\x02\x00"):
        try:
            count = int.from_bytes(data[4:6], "little")
        except Exception:
            count = 0
        return 0 < count <= 256 and len(data) >= 6 + (16 * count)
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return True
    if head.startswith(b"\xff\xd8\xff"):
        return True
    if head.startswith((b"GIF87a", b"GIF89a")):
        return True
    if head.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return True
    if lower.startswith(b"<svg") or b"<svg" in lower[:256]:
        return True
    ctype = content_type.lower()
    if ctype in {"image/svg+xml"}:
        return b"<svg" in lower[:512]
    return False



def _attr_value(tag: str, name: str) -> str:
    # Note: Accept quoted and unquoted HTML attributes so favicon discovery works with compact/minified tracker pages.
    match = re.search(rf"\b{name}\s*=\s*(['\"])(.*?)\1", tag, re.I | re.S)
    if match:
        return match.group(2).strip()
    match = re.search(rf"\b{name}\s*=\s*([^\s>]+)", tag, re.I | re.S)
    return match.group(1).strip().strip("'\"") if match else ""


def _extract_icon_hrefs(html: str) -> list[str]:
    # Note: Read any <link rel=...icon... href=...> order, including shortcut icon and relative CDN paths.
    hrefs: list[str] = []
    parser = _IconParser()
    try:
        parser.feed(html)
        hrefs.extend(parser.icons)
    except Exception:
        pass
    for match in re.finditer(r"<link\b[^>]*>", html, re.I | re.S):
        tag = match.group(0)
        rel = _attr_value(tag, "rel").lower()
        href = _attr_value(tag, "href")
        if href and "icon" in rel:
            hrefs.append(href)
    clean = []
    seen = set()
    for href in hrefs:
        href = str(href or "").strip()
        if href and href not in seen:
            seen.add(href)
            clean.append(href)
    return clean


def _tracker_icon_hosts(domain: str) -> list[str]:
    host = tracker_domain(domain)
    root = _root_domain(host)
    # Note: Direct favicon fallback checks the tracker host first, then the main domain.
    return [h for h in dict.fromkeys([host, root]) if h]


def _tracker_html_hosts(domain: str) -> list[str]:
    host = tracker_domain(domain)
    root = _root_domain(host)
    # Note: HTML discovery checks the main site first, because tracker announce hosts often return text/plain.
    return [h for h in dict.fromkeys([root, host]) if h]


def _favicon_candidates(domain: str) -> list[str]:
    candidates = []
    for h in _tracker_icon_hosts(domain):
        candidates.extend([f"https://{h}/favicon.ico", f"http://{h}/favicon.ico"])
    return list(dict.fromkeys(candidates))


def _html_icon_candidates(domain: str, errors: list[str] | None = None) -> list[str]:
    urls = []
    for h in _tracker_html_hosts(domain):
        for scheme in ("https", "http"):
            base = f"{scheme}://{h}/"
            try:
                data, ctype, final_url = _fetch(base, limit=524288)
            except Exception as exc:
                if errors is not None:
                    errors.append(f"{base}: {exc}")
                continue
            lower = data[:4096].lower()
            if "html" not in ctype and b"<html" not in lower and b"<link" not in data.lower():
                if errors is not None:
                    errors.append(f"{base}: response is not html ({ctype or 'unknown content-type'})")
                continue
            html = data.decode("utf-8", errors="ignore")
            for href in _extract_icon_hrefs(html):
                urls.append(urllib.parse.urljoin(final_url, href))
    return list(dict.fromkeys(urls))


def _cached_favicon(domain: str):
    clean = tracker_domain(domain)
    if not clean:
        return None
    with connect() as conn:
        return conn.execute("SELECT * FROM tracker_favicon_cache WHERE domain=?", (clean,)).fetchone()


def favicon_cache_row(domain: str):
    """Note: Expose the favicon cache row for diagnostics without duplicating SQL in routes or CLI."""
    return _cached_favicon(domain)


def favicon_path(domain: str, enabled: bool = True, force: bool = False) -> tuple[Path | None, str | None]:
    clean = tracker_domain(domain)
    if not enabled or not clean:
        return None, None
    cached = _cached_favicon(clean)
    now = _now_epoch()
    if cached and not force and now - float(cached.get("updated_epoch") or 0) < FAVICON_CACHE_TTL_SECONDS:
        path = Path(str(cached.get("file_path") or ""))
        mime = str(cached.get("mime_type") or mimetypes.guess_type(path.name)[0] or "image/x-icon")
        if path.exists() and path.is_file():
            try:
                if _is_icon(path.read_bytes()[:524288], mime, str(cached.get("source_url") or path.name)):
                    return path, mime
            except Exception:
                pass
        if cached.get("error"):
            return None, None
    # Note: Favicon lookup checks the main-domain HTML first, then tracker HTML, then direct /favicon.ico fallbacks.
    FAVICON_DIR.mkdir(parents=True, exist_ok=True)
    errors = []
    candidates = _html_icon_candidates(clean, errors) + _favicon_candidates(clean)
    candidates = list(dict.fromkeys(candidates))
    idx = 0
    while idx < len(candidates):
        url = candidates[idx]
        idx += 1
        try:
            data, ctype, final_url = _fetch(url, limit=524288)
            if not _is_icon(data, ctype, final_url):
                errors.append(f"{url}: invalid icon ({ctype or 'unknown content-type'}, {len(data)} bytes)")
                continue
            ext = Path(urllib.parse.urlparse(final_url).path).suffix.lower() or mimetypes.guess_extension(ctype) or ".ico"
            if ext not in {".ico", ".png", ".jpg", ".jpeg", ".svg", ".webp"}:
                ext = ".ico"
            path = FAVICON_DIR / f"{_safe_filename(clean)}{ext}"
            path.write_bytes(data)
            mime = ctype if ctype.startswith("image/") else (mimetypes.guess_type(path.name)[0] or "image/x-icon")
            with connect() as conn:
                conn.execute(
                    """
                    INSERT INTO tracker_favicon_cache(domain, source_url, file_path, mime_type, updated_at, updated_epoch, error)
                    VALUES(?, ?, ?, ?, ?, ?, NULL)
                    ON CONFLICT(domain) DO UPDATE SET
                      source_url=excluded.source_url,
                      file_path=excluded.file_path,
                      mime_type=excluded.mime_type,
                      updated_at=excluded.updated_at,
                      updated_epoch=excluded.updated_epoch,
                      error=NULL
                    """,
                    (clean, final_url, str(path), mime, utcnow(), now),
                )
            return path, mime
        except Exception as exc:
            errors.append(f"{url}: {exc}")
        # HTML is checked once before direct /favicon.ico probes; do not guess cdn/static/www hosts unless HTML points there.
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO tracker_favicon_cache(domain, source_url, file_path, mime_type, updated_at, updated_epoch, error)
            VALUES(?, '', '', '', ?, ?, ?)
            ON CONFLICT(domain) DO UPDATE SET
              updated_at=excluded.updated_at,
              updated_epoch=excluded.updated_epoch,
              error=excluded.error
            """,
            (clean, utcnow(), now, "; ".join(errors[-8:]) or "favicon not found"),
        )
    return None, None


def cached_domains_for_profile(profile_id: int, limit: int = 200) -> list[str]:
    """Return tracker domains already known for a profile from the summary cache."""
    # Note: The background favicon worker reads cached summary rows first, so it does not need the browser sidebar to discover domains.
    domains: list[str] = []
    seen: set[str] = set()
    with connect() as conn:
        rows = conn.execute(
            "SELECT trackers_json FROM tracker_summary_cache WHERE profile_id=? ORDER BY updated_epoch DESC LIMIT ?",
            (int(profile_id), max(1, int(limit or 200))),
        ).fetchall()
    for row in rows:
        try:
            items = json.loads(row.get("trackers_json") or "[]")
        except Exception:
            items = []
        for item in items if isinstance(items, list) else []:
            domain = tracker_domain(str((item or {}).get("url") or (item or {}).get("domain") or "")) or str((item or {}).get("domain") or "")
            if domain and domain not in seen:
                seen.add(domain)
                domains.append(domain)
    return domains[:max(1, int(limit or 200))]


def warm_favicon_cache(domains: list[str], enabled: bool = True, limit: int = 20, force: bool = False) -> dict:
    """Warm missing or stale tracker favicons for a bounded list of domains."""
    # Note: Favicon lookup can perform network requests, so the caller must keep the batch size small.
    clean_domains = []
    seen: set[str] = set()
    for domain in domains or []:
        clean = tracker_domain(domain)
        if clean and clean not in seen:
            seen.add(clean)
            clean_domains.append(clean)
    checked = 0
    cached = 0
    errors: list[dict] = []
    for domain in clean_domains[:max(0, int(limit or 0))]:
        checked += 1
        try:
            path, _mime = favicon_path(domain, enabled=enabled, force=force)
            if path:
                cached += 1
        except Exception as exc:
            errors.append({"domain": domain, "error": str(exc)})
    return {"checked": checked, "cached": cached, "errors": errors[:10]}
