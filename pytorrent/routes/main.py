from __future__ import annotations

from pathlib import Path
import json
from urllib.parse import quote
import queue
import tempfile
import threading
import zipfile

from flask import Blueprint, render_template, Response, request, redirect, url_for, abort, send_file, stream_with_context
from ..services.preferences import get_preferences, list_profiles, active_profile, get_profile, BOOTSTRAP_THEMES, PYTORRENT_THEMES, UI_FRAMEWORKS, FONT_FAMILIES
from ..services import auth, pdf_preview_links, rtorrent, prometheus_metrics
from ..config import PYTORRENT_TMP_DIR, SMART_QUEUE_LABEL, SMART_QUEUE_STALLED_LABEL, METRICS_PATH
from ..services.frontend_assets import asset_path, bootstrap_css_path, static_hash
from flask import current_app, send_from_directory

bp = Blueprint("main", __name__)


def _asset_url(key: str) -> str:
    path = asset_path(key)
    return path if path.startswith("http") else url_for("static", filename=path)


def _bootstrap_json_value(value, fallback, expected_type):
    # Note: Stored JSON preferences are normalized before they reach JavaScript so one malformed legacy value cannot break the whole page bootstrap.
    parsed = value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return fallback
    return parsed if isinstance(parsed, expected_type) else fallback


def _bootstrap_static_url(filename: str) -> str:
    path = Path(current_app.static_folder or "") / filename
    try:
        path.stat()
        return url_for("static", filename=filename, v=static_hash(Path(current_app.static_folder or "")))
    except OSError:
        return url_for("static", filename=filename)


def _bootstrap_theme_urls() -> dict[str, str]:
    # Note: Theme URLs are serialized inside the same bootstrap payload as the remaining frontend configuration.
    urls: dict[str, str] = {}
    for key in BOOTSTRAP_THEMES.keys():
        path = bootstrap_css_path(key)
        urls[key] = path if path.startswith("http") else _bootstrap_static_url(path)
    return urls


def _frontend_bootstrap_config(prefs: dict, profile: dict | None, current_user: dict | None) -> dict:
    # Note: Build one typed Python payload and serialize it once in Jinja instead of concatenating executable JavaScript fragments.
    def flag(key: str, default: bool = False) -> int:
        value = prefs.get(key)
        if value is None:
            value = default
        return 1 if bool(value) else 0

    def number(key: str, default: int) -> int:
        try:
            value = prefs.get(key)
            return int(value) if value is not None else default
        except (TypeError, ValueError):
            return default

    toast_mode = str(prefs.get("toast_notification_mode") or "all").strip().lower()
    if toast_mode not in {"important", "all"}:
        toast_mode = "all"
    history_mode = str(prefs.get("notification_history_mode") or "important").strip().lower()
    if history_mode not in {"important", "all"}:
        history_mode = "important"

    return {
        "authEnabled": 1 if auth.enabled() else 0,
        "authProvider": auth.provider(),
        "externalAuth": 1 if auth.uses_external_provider() else 0,
        "currentUser": current_user,
        "activeProfile": profile.get("id") if profile else None,
        "tableColumns": _bootstrap_json_value(prefs.get("table_columns_json"), {}, dict),
        "torrentSort": _bootstrap_json_value(prefs.get("torrent_sort_json"), {}, dict),
        "activeFilter": str(prefs.get("active_filter") or "all"),
        "detailPanelHeight": number("detail_panel_height", 255),
        "peersRefreshSeconds": number("peers_refresh_seconds", 0),
        "portCheckEnabled": flag("port_check_enabled"),
        "freeSpaceCheckEnabled": flag("free_space_check_enabled", True),
        "interfaceScale": number("interface_scale", 80),
        "torrentListFontSize": number("torrent_list_font_size", 12),
        "compactTorrentListEnabled": flag("compact_torrent_list_enabled", True),
        "titleSpeedEnabled": flag("title_speed_enabled", True),
        "trackerFaviconsEnabled": flag("tracker_favicons_enabled", True),
        "reverseDnsEnabled": flag("reverse_dns_enabled", True),
        "automationToastsEnabled": flag("automation_toasts_enabled", True),
        "smartQueueToastsEnabled": flag("smart_queue_toasts_enabled", True),
        "toastNotificationMode": toast_mode,
        "notificationHistoryEnabled": flag("notification_history_enabled"),
        "notificationHistoryMode": history_mode,
        "diskMonitorPaths": _bootstrap_json_value(prefs.get("disk_monitor_paths_json"), [], list),
        "diskMonitorMode": str(prefs.get("disk_monitor_mode") or "default"),
        "diskMonitorSelectedPath": str(prefs.get("disk_monitor_selected_path") or ""),
        "diskMonitorOwnerLabel": str(prefs.get("disk_monitor_owner_label") or ""),
        "bootstrapTheme": str(prefs.get("bootstrap_theme") or "default"),
        "pytorrentTheme": str(prefs.get("pytorrent_theme") or "default-beta"),
        "pytorrentAnimationsEnabled": flag("pytorrent_animations_enabled", True),
        "uiFramework": str(prefs.get("ui_framework") or "bootstrap"),
        "fontFamily": str(prefs.get("font_family") or "default"),
        "footerItems": _bootstrap_json_value(prefs.get("footer_items_json"), {}, dict),
        "footerOrder": _bootstrap_json_value(prefs.get("footer_order_json"), [], list),
        "easterEggEnabled": flag("easter_egg_enabled"),
        "easterEggLoadingImageUrl": str(prefs.get("easter_egg_loading_image_url") or ""),
        "easterEggClickImageUrl": str(prefs.get("easter_egg_click_image_url") or ""),
        "bootstrapThemes": BOOTSTRAP_THEMES,
        "pytorrentThemes": PYTORRENT_THEMES,
        "bootstrapThemeUrls": _bootstrap_theme_urls(),
        "fontFamilies": FONT_FAMILIES,
        "staticHash": static_hash(Path(current_app.static_folder or "")),
        "smartQueueTechnicalLabel": SMART_QUEUE_LABEL,
        "smartQueueStalledLabel": SMART_QUEUE_STALLED_LABEL,
        "sidebarLabelsExpanded": flag("sidebar_labels_expanded"),
        "sidebarShortcutsExpanded": flag("sidebar_shortcuts_expanded"),
        "systemUsageChartMode": str(prefs.get("system_usage_chart_mode") or "combined"),
        "systemUsageChartExpanded": flag("system_usage_chart_expanded"),
    }


def _attachment_headers(download_name: str, content_type: str = "application/octet-stream", disposition: str = "attachment") -> dict:
    safe = Path(download_name or "download.bin").name or "download.bin"
    safe_disposition = "inline" if disposition == "inline" else "attachment"
    return {
        "Content-Type": content_type,
        "Content-Disposition": f"{safe_disposition}; filename*=UTF-8''{quote(safe)}",
        "X-Content-Type-Options": "nosniff",
    }


def _cleanup_staged_file(profile: dict, path: str, local: bool = False) -> None:
    if local:
        try:
            Path(path).unlink()
        except Exception:
            pass
        return
    rtorrent._remote_remove_staged(profile, path)
    try:
        tmp_prefix = str(PYTORRENT_TMP_DIR).rstrip("/") + "/pytorrent-download-"
        if str(path).startswith(tmp_prefix) and Path(path).exists():
            Path(path).unlink()
    except Exception:
        pass


def _read_staged_file(profile: dict, path: str, local: bool = False) -> bytes:
    if local:
        return Path(path).read_bytes()
    return b"".join(bytes(chunk) for chunk in rtorrent.iter_remote_file_chunks(profile, path) if chunk)


def _safe_zip_name(name: str, fallback: str) -> str:
    value = str(name or fallback).replace("\\", "/").lstrip("/")
    parts = [part for part in value.split("/") if part not in ("", ".", "..")]
    return "/".join(parts) or fallback


class _ZipStream:
    def __init__(self):
        self.queue: queue.Queue[bytes | None] = queue.Queue(maxsize=16)
        self.closed = False

    def write(self, data):
        if not data:
            return 0
        payload = bytes(data)
        self.queue.put(payload)
        return len(payload)

    def flush(self):
        return None

    def close(self):
        if not self.closed:
            self.closed = True
            self.queue.put(None)

    def writable(self):
        return True


def _stream_torrent_files_zip(profile: dict, items: list[dict]):
    writer = _ZipStream()
    errors: list[BaseException] = []

    def produce():
        try:
            with zipfile.ZipFile(writer, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
                used = set()
                for item in items:
                    arcname = _safe_zip_name(str(item.get("path") or ""), f"file-{item.get('index', 0)}")
                    base = arcname
                    counter = 2
                    while arcname in used:
                        stem = Path(base).stem or "file"
                        suffix = Path(base).suffix
                        parent = str(Path(base).parent).replace(".", "", 1).strip("/")
                        candidate = f"{stem}-{counter}{suffix}"
                        arcname = f"{parent}/{candidate}" if parent else candidate
                        counter += 1
                    used.add(arcname)
                    info = zipfile.ZipInfo(arcname)
                    info.compress_type = zipfile.ZIP_STORED
                    info.file_size = int(item.get("size") or 0)
                    with archive.open(info, "w", force_zip64=True) as dest:
                        for chunk in rtorrent.iter_remote_file_chunks(profile, item["remote_path"], size=int(item.get("size") or 0) or None):
                            dest.write(chunk)
        except BaseException as exc:
            errors.append(exc)
        finally:
            writer.close()

    threading.Thread(target=produce, name="pytorrent-token-zip-stream", daemon=True).start()
    while True:
        chunk = writer.queue.get()
        if chunk is None:
            break
        yield chunk
    if errors:
        raise errors[0]


def _send_staged_torrent_file(profile: dict, path: str, download_name: str, local: bool = False):
    headers = _attachment_headers(download_name, "application/x-bittorrent")
    if local:
        data = Path(path).read_bytes()
        _cleanup_staged_file(profile, path, local=True)
        headers["Content-Length"] = str(len(data))
        return Response(data, headers=headers)

    def generate():
        try:
            yield from rtorrent.iter_remote_file_chunks(profile, path)
        finally:
            _cleanup_staged_file(profile, path, local=False)

    return Response(stream_with_context(generate()), headers=headers, direct_passthrough=True)


def _profile_for_temporary_target(target: dict):
    profile_id = int(target.get("profile_id") or 0)
    owner_user_id = int(target.get("user_id") or 0)
    if auth.enabled() and owner_user_id != auth.current_user_id():
        abort(403)
    if not auth.can_access_profile(profile_id):
        abort(403)
    profile = active_profile() if not profile_id else get_profile(profile_id)
    if not profile:
        abort(404)
    return profile


@bp.get(METRICS_PATH)
def metrics():
    # Note: Prometheus scrapes cached/in-memory runtime data only; this endpoint never triggers rTorrent work.
    return prometheus_metrics.response()


@bp.get("/favicon.ico")
def favicon_ico():
    response = send_from_directory(
        current_app.static_folder,
        "favicon.svg",
        mimetype="image/svg+xml",
    )
    return response


@bp.route("/login", methods=["GET", "POST"])
def login():
    # Note: When optional authentication is disabled, /login is intentionally unavailable.
    if not auth.enabled():
        abort(404)
    next_url = request.args.get("next") or url_for("main.index")
    if auth.uses_external_provider():
        user = auth.authenticate_external_user()
        if user:
            return redirect(next_url)
        return render_template(
            "login.html",
            error="External authentication headers were not accepted by pyTorrent.",
            external_provider=auth.provider(),
        ), 401
    error = ""
    if request.method == "POST":
        user = auth.login_user(request.form.get("username", ""), request.form.get("password", ""))
        if user:
            return redirect(next_url)
        error = "Invalid username or password"
    return render_template("login.html", error=error, external_provider=None)


@bp.get("/logout")
def logout():
    # Note: External providers such as Tinyauth own the login session, so pyTorrent must not pretend to log the user out locally.
    if auth.uses_external_provider():
        return redirect(url_for("main.index"))
    auth.logout_user()
    if not auth.enabled():
        return redirect(url_for("main.index"))
    return redirect(url_for("main.login"))


@bp.get("/")
def index():
    prefs = get_preferences()
    profile = active_profile()
    current_user = auth.current_user()
    bootstrap_config = _frontend_bootstrap_config(prefs, profile, current_user)
    return render_template(
        "index.html",
        prefs=prefs,
        profiles=list_profiles(),
        active_profile=profile,
        bootstrap_themes=BOOTSTRAP_THEMES,
        pytorrent_themes=PYTORRENT_THEMES,
        ui_frameworks=UI_FRAMEWORKS,
        font_families=FONT_FAMILIES,
        auth_enabled=auth.enabled(),
        auth_provider=auth.provider(),
        external_auth=auth.uses_external_provider(),
        current_user=current_user,
        smart_queue_label=SMART_QUEUE_LABEL,
        smart_queue_stalled_label=SMART_QUEUE_STALLED_LABEL,
        bootstrap_config=bootstrap_config,
    )




@bp.get("/preview/pdf/<token>")
def pdf_preview(token: str):
    # Note: This route keeps browser-visible PDF links inside the app and delegates streaming to the existing rTorrent file reader.
    target = pdf_preview_links.get_pdf_preview_link(token)
    if not target:
        abort(404)
    profile_id = int(target.get("profile_id") or 0)
    owner_user_id = int(target.get("user_id") or 0)
    if auth.enabled() and owner_user_id != auth.current_user_id():
        abort(403)
    if not auth.can_access_profile(profile_id):
        abort(403)
    profile = active_profile() if not profile_id else get_profile(profile_id)
    if not profile:
        abort(404)
    item = rtorrent.torrent_download_file_info(profile, target["torrent_hash"], int(target["file_index"]))
    filename = Path(item.get("download_name") or "preview.pdf").name or "preview.pdf"
    if Path(filename).suffix.lower() != ".pdf":
        abort(404)
    size = int(item.get("size") or 0)
    headers = {
        "Content-Disposition": f"inline; filename*=UTF-8''{quote(filename)}",
        "Content-Type": "application/pdf",
        "X-Content-Type-Options": "nosniff",
    }
    if size > 0:
        headers["Content-Length"] = str(size)

    def generate():
        yield from rtorrent.iter_remote_file_chunks(profile, item["remote_path"], size=size or None)

    return Response(stream_with_context(generate()), headers=headers, direct_passthrough=True)


@bp.get("/download/<token>")
def temporary_download(token: str):
    # Note: UI download actions resolve API-created temporary tokens here, keeping browser-visible URLs outside /api/.
    target = pdf_preview_links.get_temporary_link(token)
    if not target:
        abort(404)
    profile = _profile_for_temporary_target(target)
    kind = str(target.get("kind") or "")

    if kind == "file_download":
        item = rtorrent.torrent_download_file_info(profile, target["torrent_hash"], int(target["file_index"]))
        size = int(item.get("size") or 0)
        headers = _attachment_headers(item.get("download_name") or "file.bin")
        if size > 0:
            headers["Content-Length"] = str(size)

        def generate_file():
            yield from rtorrent.iter_remote_file_chunks(profile, item["remote_path"], size=size or None)

        return Response(stream_with_context(generate_file()), headers=headers, direct_passthrough=True)

    if kind == "file_zip_download":
        items = rtorrent.torrent_download_zip_items(profile, target["torrent_hash"], target.get("indexes"))
        headers = _attachment_headers(f"{str(target['torrent_hash'])[:12]}-files.zip", "application/zip")
        headers["X-PyTorrent-Download-Mode"] = "temporary-token"
        return Response(stream_with_context(_stream_torrent_files_zip(profile, items)), headers=headers, direct_passthrough=True)

    if kind == "torrent_file_download":
        item = rtorrent.export_torrent_file(profile, target["torrent_hash"])
        return _send_staged_torrent_file(profile, item["path"], item["download_name"], bool(item.get("local")))

    if kind == "torrent_files_zip_download":
        hashes = [str(item) for item in (target.get("hashes") or []) if str(item).strip()]
        if not hashes:
            abort(404)
        staged_paths = []
        PYTORRENT_TMP_DIR.mkdir(parents=True, exist_ok=True)
        tmp = tempfile.NamedTemporaryFile(prefix="pytorrent-torrents-", suffix=".zip", delete=False, dir=str(PYTORRENT_TMP_DIR))
        tmp.close()
        try:
            with zipfile.ZipFile(tmp.name, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
                used_names = set()
                for torrent_hash in hashes:
                    item = rtorrent.export_torrent_file(profile, torrent_hash)
                    staged_paths.append((item["path"], bool(item.get("local"))))
                    name = Path(item["download_name"]).name or f"{torrent_hash}.torrent"
                    base_name = name
                    counter = 2
                    while name in used_names:
                        stem = Path(base_name).stem
                        name = f"{stem}-{counter}.torrent"
                        counter += 1
                    used_names.add(name)
                    archive.writestr(name, _read_staged_file(profile, item["path"], bool(item.get("local"))))
            response = send_file(tmp.name, as_attachment=True, download_name="pytorrent-torrents.zip")

            def cleanup():
                for path, is_local in staged_paths:
                    _cleanup_staged_file(profile, path, is_local)
                try:
                    Path(tmp.name).unlink()
                except Exception:
                    pass

            response.call_on_close(cleanup)
            return response
        except Exception:
            for path, is_local in staged_paths:
                _cleanup_staged_file(profile, path, is_local)
            try:
                Path(tmp.name).unlink()
            except Exception:
                pass
            raise

    abort(404)


@bp.get("/docs")
def docs():
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>pyTorrent API Docs</title><link rel="stylesheet" href="{_asset_url('swagger_css')}"></head><body><div id="swagger-ui"></div><script src="{_asset_url('swagger_js')}"></script><script>window.onload=()=>SwaggerUIBundle({{url:'/api/openapi.json',dom_id:'#swagger-ui',deepLinking:true,persistAuthorization:true}});</script></body></html>"""
    return Response(html, mimetype="text/html")


@bp.get("/api/openapi.json")
def openapi():
    spec_path = Path(current_app.root_path) / "openapi" / "openapi.json"
    response = send_file(spec_path, mimetype="application/json", conditional=False, max_age=0)
    response.headers["Cache-Control"] = "no-store, no-cache, private"
    return response
