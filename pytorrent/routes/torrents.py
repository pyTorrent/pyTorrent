from __future__ import annotations
from ._shared import *
import json
import posixpath
from ..services import profile_speed_limits, speed_limit_profiles
from ..utils import human_size
from ..services import pdf_preview_links, torrent_creator
from ..services.reverse_dns import attach_reverse_dns

@bp.get("/torrents")
def torrents():
    profile = request_profile()
    if not profile:
        return ok({"torrents": [], "summary": cached_summary(0, []), "error": "No rTorrent profile"})
    rows = torrent_cache.snapshot(profile["id"])
    return ok({
        "profile_id": profile["id"],
        "torrents": rows,
        "summary": cached_summary(profile["id"], rows),
        "error": torrent_cache.error(profile["id"]),
    })



@bp.get("/trackers/summary")
def trackers_summary():
    profile = request_profile()
    if not profile:
        return ok({"summary": {"hashes": {}, "trackers": [], "errors": [], "scanned": 0, "pending": 0}, "error": "No profile"})
    try:
        # Note: Tracker summary returns cached data immediately; optional warmup scans rTorrent in the background for very large libraries.
        scan_limit = min(250, max(0, int(request.args.get("scan_limit") or 0)))
        bg_limit = min(250, max(1, int(request.args.get("bg_limit") or 80)))
        warm = str(request.args.get("warm") or "").lower() in {"1", "true", "yes"}
        hashes = [t.get("hash") for t in torrent_cache.snapshot(profile["id"]) if t.get("hash")]
        prefs = preferences.get_preferences()
        include_favicons = bool(prefs and prefs.get("tracker_favicons_enabled"))
        loader = lambda h: rtorrent.torrent_trackers(profile, h)
        summary = tracker_cache.summary(profile, hashes, loader, scan_limit=scan_limit, include_favicons=include_favicons)
        if warm and int(summary.get("pending") or 0) > 0:
            summary["warming"] = tracker_cache.warm_summary_cache(profile, hashes, loader, batch_size=bg_limit)
        return ok({"summary": summary})
    except Exception as exc:
        return ok({"summary": {"hashes": {}, "trackers": [], "errors": [{"error": str(exc)}], "scanned": 0, "pending": 0}, "error": str(exc)})



@bp.get("/trackers/favicon/<path:domain>")

@bp.get("/tracker-favicon/<path:domain>")
def tracker_favicon(domain: str):
    prefs = preferences.get_preferences()
    force = str(request.args.get("refresh") or "").lower() in {"1", "true", "yes", "force"}
    # Note: Manual refresh must work from CLI even when tracker favicons are disabled in Preferences.
    enabled = force or bool(prefs and prefs.get("tracker_favicons_enabled"))
    static_url = tracker_cache.favicon_public_url(domain, enabled=enabled, create=True, force=force)
    if static_url:
        # Note: The API only discovers/cache-warms the icon; the browser receives the file from /static/tracker_favicons/.
        return redirect(static_url, code=302)
    cached = tracker_cache.favicon_cache_row(domain)
    return jsonify({
        "ok": False,
        "error": "favicon not found",
        "domain": tracker_cache.tracker_domain(domain),
        "enabled": bool(enabled),
        "cached_error": (cached or {}).get("error") if cached else None,
    }), 404



@bp.get("/trackers/favicon")
def tracker_favicon_query():
    # Note: Query-string alias makes cache warming easier from shell scripts where path routing/proxies may differ.
    domain = str(request.args.get("domain") or "").strip()
    if not domain:
        return jsonify({"ok": False, "error": "domain is required"}), 400
    return tracker_favicon(domain)


@bp.get("/torrent-stats")
def torrent_stats_get():
    profile = request_profile()
    if not profile:
        return ok({"stats": {}, "error": "No profile"})
    force = str(request.args.get("force") or "").lower() in {"1", "true", "yes"}
    try:
        # Note: Heavy file metadata is served from a 15-minute DB cache unless the user explicitly refreshes it.
        return ok({"stats": torrent_stats.get(profile, force=force)})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500



@bp.get("/torrents/<torrent_hash>/files")
def torrent_files(torrent_hash: str):
    profile = request_profile()
    if not profile:
        return jsonify({"ok": False, "error": "No profile"}), 400
    return ok({"files": rtorrent.torrent_files(profile, torrent_hash)})



@bp.get("/torrents/<torrent_hash>/files/<int:file_index>/mediainfo")
def torrent_file_media_info(torrent_hash: str, file_index: int):
    profile = request_profile()
    if not profile:
        return jsonify({"ok": False, "error": "No profile"}), 400
    try:
        # Note: The route is additive and keeps all existing file endpoints unchanged.
        media_info = rtorrent.torrent_file_media_info(profile, torrent_hash, file_index)
        if media_info.get("kind") == "pdf":
            link = pdf_preview_links.create_pdf_preview_link(
                torrent_hash,
                file_index,
                int(profile.get("id") or 0),
                int(default_user_id() or 0),
            )
            # Note: The frontend receives an in-app temporary URL instead of exposing the API download endpoint in the new-tab action.
            media_info["preview_url"] = url_for("main.pdf_preview", token=link["token"])
            media_info["preview_expires_in"] = link["expires_in"]
        return ok({"media_info": media_info})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@bp.post("/torrents/<torrent_hash>/files/priority")
def torrent_file_priority(torrent_hash: str):
    profile = request_profile()
    if not profile:
        return jsonify({"ok": False, "error": "No profile"}), 400
    data = request.get_json(silent=True) or {}
    files = data.get("files") or []
    if not isinstance(files, list) or not files:
        return jsonify({"ok": False, "error": "No files selected"}), 400
    result = rtorrent.set_file_priorities(profile, torrent_hash, files)
    status = 207 if result.get("errors") else 200
    return ok(result), status



@bp.get("/torrents/<torrent_hash>/files/tree")
def torrent_file_tree(torrent_hash: str):
    profile = request_profile()
    if not profile:
        return jsonify({"ok": False, "error": "No profile"}), 400
    return ok({"tree": rtorrent.torrent_file_tree(profile, torrent_hash)})



@bp.post("/torrents/<torrent_hash>/files/folder-priority")
def torrent_folder_priority(torrent_hash: str):
    profile = request_profile()
    if not profile:
        return jsonify({"ok": False, "error": "No profile"}), 400
    data = request.get_json(silent=True) or {}
    result = rtorrent.set_folder_priority(profile, torrent_hash, str(data.get("path") or ""), int(data.get("priority") or 0))
    status = 207 if result.get("errors") else 200
    return ok(result), status


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
    chunks = []
    for chunk in rtorrent.iter_remote_file_chunks(profile, path):
        if chunk:
            chunks.append(bytes(chunk))
    return b"".join(chunks)


def _send_staged_file(profile: dict, path: str, download_name: str, local: bool = False):
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




@bp.post("/torrents/<torrent_hash>/files/<int:file_index>/download-link")
def torrent_file_download_link(torrent_hash: str, file_index: int):
    profile = request_profile()
    if not profile:
        return jsonify({"ok": False, "error": "No profile"}), 400
    try:
        # Note: The API validates the file selection before returning a short-lived in-app /download URL to the UI.
        rtorrent.torrent_download_file_info(profile, torrent_hash, file_index)
        link = pdf_preview_links.create_file_download_link(torrent_hash, file_index, int(profile.get("id") or 0), int(default_user_id() or 0))
        return ok({"url": url_for("main.temporary_download", token=link["token"]), "expires_in": link["expires_in"]})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@bp.post("/torrents/<torrent_hash>/files/download-link")
def torrent_file_download_link_from_body(torrent_hash: str):
    data = request.get_json(silent=True) or {}
    try:
        file_index = int(data.get("file_index"))
    except Exception:
        return jsonify({"ok": False, "error": "file_index is required"}), 400
    return torrent_file_download_link(torrent_hash, file_index)


@bp.post("/torrents/<torrent_hash>/files/download.zip/link")
def torrent_files_download_zip_link(torrent_hash: str):
    profile = request_profile()
    if not profile:
        return jsonify({"ok": False, "error": "No profile"}), 400
    data = request.get_json(silent=True) or {}
    try:
        indexes = data.get("indexes") or None
        # Note: ZIP link creation validates the requested files through the same service used by the direct download endpoint.
        rtorrent.torrent_download_zip_items(profile, torrent_hash, indexes)
        link = pdf_preview_links.create_file_zip_download_link(torrent_hash, indexes, int(profile.get("id") or 0), int(default_user_id() or 0))
        return ok({"url": url_for("main.temporary_download", token=link["token"]), "expires_in": link["expires_in"]})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@bp.get("/torrents/<torrent_hash>/torrent-file/link")
def torrent_file_export_link(torrent_hash: str):
    profile = request_profile()
    if not profile:
        return jsonify({"ok": False, "error": "No profile"}), 400
    try:
        # Note: Create only a short-lived link here; the actual .torrent export runs once when the browser opens /download/<token>.
        link = pdf_preview_links.create_torrent_file_download_link(torrent_hash, int(profile.get("id") or 0), int(default_user_id() or 0))
        return ok({"url": url_for("main.temporary_download", token=link["token"]), "expires_in": link["expires_in"]})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@bp.post("/torrents/torrent-files.zip/link")
def torrent_files_export_zip_link():
    profile = request_profile()
    if not profile:
        return jsonify({"ok": False, "error": "No profile"}), 400
    data = request.get_json(silent=True) or {}
    hashes = [str(h) for h in (data.get("hashes") or []) if str(h).strip()]
    if not hashes:
        return jsonify({"ok": False, "error": "No torrents selected"}), 400
    try:
        # Note: Store only the selected hashes in the temporary token; exporting each .torrent now happens once during the real ZIP download.
        link = pdf_preview_links.create_torrent_files_zip_download_link(hashes, int(profile.get("id") or 0), int(default_user_id() or 0))
        return ok({"url": url_for("main.temporary_download", token=link["token"]), "expires_in": link["expires_in"]})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@bp.get("/torrents/<torrent_hash>/files/<int:file_index>/download")
def torrent_file_download(torrent_hash: str, file_index: int):
    profile = request_profile()
    if not profile:
        return jsonify({"ok": False, "error": "No profile"}), 400
    try:
        item = rtorrent.torrent_download_file_info(profile, torrent_hash, file_index)
        size = int(item.get("size") or 0)
        download_name = item.get("download_name") or "file.bin"
        inline_pdf = str(request.args.get("disposition") or "").lower() == "inline" and Path(download_name).suffix.lower() == ".pdf"
        # Note: Inline mode is limited to PDFs so the existing download behavior remains unchanged for every other file type.
        headers = _attachment_headers(download_name, "application/pdf" if inline_pdf else "application/octet-stream", "inline" if inline_pdf else "attachment")
        if size > 0:
            headers["Content-Length"] = str(size)
        def generate():
            yield from rtorrent.iter_remote_file_chunks(profile, item["remote_path"], size=size or None)
        return Response(stream_with_context(generate()), headers=headers, direct_passthrough=True)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


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


def _safe_zip_name(name: str, fallback: str) -> str:
    value = str(name or fallback).replace("\\", "/").lstrip("/")
    parts = [part for part in value.split("/") if part not in ("", ".", "..")]
    return "/".join(parts) or fallback


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

    threading.Thread(target=produce, name="pytorrent-zip-stream", daemon=True).start()
    while True:
        chunk = writer.queue.get()
        if chunk is None:
            break
        yield chunk
    if errors:
        raise errors[0]



@bp.post("/torrents/<torrent_hash>/files/download.zip")
def torrent_files_download_zip(torrent_hash: str):
    profile = request_profile()
    if not profile:
        return jsonify({"ok": False, "error": "No profile"}), 400
    data = request.get_json(silent=True) or {}
    try:
        items = rtorrent.torrent_download_zip_items(profile, torrent_hash, data.get("indexes") or None)
        headers = _attachment_headers(f"{torrent_hash[:12]}-files.zip", "application/zip")
        headers["X-PyTorrent-Download-Mode"] = "rtorrent-stream"
        return Response(stream_with_context(_stream_torrent_files_zip(profile, items)), headers=headers, direct_passthrough=True)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400



@bp.get("/torrents/<torrent_hash>/torrent-file")
def torrent_file_export(torrent_hash: str):
    profile = request_profile()
    if not profile:
        return jsonify({"ok": False, "error": "No profile"}), 400
    try:
        item = rtorrent.export_torrent_file(profile, torrent_hash)
        return _send_staged_file(profile, item["path"], item["download_name"], bool(item.get("local")))
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400



@bp.post("/torrents/torrent-files.zip")
def torrent_files_export_zip():
    profile = request_profile()
    if not profile:
        return jsonify({"ok": False, "error": "No profile"}), 400
    data = request.get_json(silent=True) or {}
    hashes = [str(h) for h in (data.get("hashes") or []) if str(h).strip()]
    if not hashes:
        return jsonify({"ok": False, "error": "No torrents selected"}), 400
    staged_paths = []
    PYTORRENT_TMP_DIR.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(prefix="pytorrent-torrents-", suffix=".zip", delete=False, dir=str(PYTORRENT_TMP_DIR))
    tmp.close()
    try:
        with zipfile.ZipFile(tmp.name, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            used_names = set()
            for h in hashes:
                item = rtorrent.export_torrent_file(profile, h)
                staged_paths.append((item["path"], bool(item.get("local"))))
                name = Path(item["download_name"]).name or f"{h}.torrent"
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
    except Exception as exc:
        for path, is_local in staged_paths:
            _cleanup_staged_file(profile, path, is_local)
        try:
            Path(tmp.name).unlink()
        except Exception:
            pass
        return jsonify({"ok": False, "error": str(exc)}), 400



@bp.get("/torrents/<torrent_hash>/chunks")
def torrent_chunks(torrent_hash: str):
    profile = request_profile()
    if not profile:
        return jsonify({"ok": False, "error": "No profile"}), 400
    try:
        max_cells = min(10000, max(64, int(request.args.get("max_cells") or 2048)))
        return ok({"chunks": rtorrent.torrent_chunks(profile, torrent_hash, max_cells=max_cells)})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@bp.post("/torrents/<torrent_hash>/chunks/<action_name>")
def torrent_chunk_action(torrent_hash: str, action_name: str):
    profile = request_profile()
    if not profile:
        return jsonify({"ok": False, "error": "No profile"}), 400
    try:
        # Note: Chunk actions are intentionally limited to rTorrent-safe operations; XML-RPC has no supported single-piece redownload call.
        result = rtorrent.torrent_chunk_action(profile, torrent_hash, action_name, request.get_json(silent=True) or {})
        return ok({"result": result, "message": result.get("message") or f"Chunk action {action_name} done"})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@bp.get("/torrents/<torrent_hash>/peers")
def torrent_peers(torrent_hash: str):
    profile = request_profile()
    if not profile:
        return jsonify({"ok": False, "error": "No profile"}), 400
    peers = rtorrent.torrent_peers(profile, torrent_hash)
    for peer in peers:
        peer.update(lookup_ip(peer.get("ip", "")))
    prefs = preferences.get_preferences(profile_id=profile.get("id"))
    if int(prefs.get("reverse_dns_enabled") or 0):
        # Note: PTR hostnames are attached only when the user enables the lightweight cached resolver.
        attach_reverse_dns(peers)
    return ok({"peers": peers})



@bp.get("/torrents/<torrent_hash>/trackers")
def torrent_trackers(torrent_hash: str):
    profile = request_profile()
    if not profile:
        return jsonify({"ok": False, "error": "No profile"}), 400
    return ok({"trackers": rtorrent.torrent_trackers(profile, torrent_hash)})



@bp.post("/torrents/<torrent_hash>/trackers/<action_name>")
def torrent_tracker_action(torrent_hash: str, action_name: str):
    profile = request_profile()
    if not profile:
        return jsonify({"ok": False, "error": "No profile"}), 400
    try:
        result = rtorrent.tracker_action(profile, torrent_hash, action_name, request.get_json(silent=True) or {})
        return ok({"result": result, "message": f"Tracker {action_name} via {result.get('method', 'XMLRPC')}"})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400




def _clean_remote_transfer_path(path: str) -> str:
    clean = posixpath.normpath(str(path or "").strip())
    if not clean or clean in {".", "/"} or not clean.startswith("/") or "\x00" in clean:
        raise ValueError("Unsafe target path")
    return clean


def _path_inside_root(path: str, root: str) -> bool:
    path = _clean_remote_transfer_path(path)
    root = _clean_remote_transfer_path(root)
    return path == root or path.startswith(root.rstrip("/") + "/")


def _target_profile_allowed_roots(target_profile: dict, user_id: int) -> list[str]:
    roots = []
    try:
        roots.append(_clean_remote_transfer_path(rtorrent.default_download_path(target_profile)))
    except Exception:
        pass
    try:
        prefs = preferences.get_disk_monitor_preferences(int(target_profile.get("id") or 0), user_id=user_id)
        for item in json.loads((prefs or {}).get("disk_monitor_paths_json") or "[]"):
            try:
                roots.append(_clean_remote_transfer_path(str(item or "")))
            except Exception:
                continue
        selected = str((prefs or {}).get("disk_monitor_selected_path") or "").strip()
        if selected:
            roots.append(_clean_remote_transfer_path(selected))
    except Exception:
        pass
    seen = []
    for root in roots:
        if root not in seen:
            seen.append(root)
    return seen


def _profile_transfer_payload(source_profile: dict, data: dict, *, require_hashes: bool = True) -> dict:
    user_id = auth.current_user_id() or default_user_id()
    source_id = int(source_profile.get("id") or 0)
    if not auth.can_write_profile(source_id, user_id):
        raise PermissionError("No write access to source profile")
    hashes = [str(h).strip() for h in (data.get("hashes") or []) if str(h).strip()]
    if require_hashes and not hashes:
        raise ValueError("No torrents selected")
    target_id = int(data.get("target_profile_id") or 0)
    if not target_id or target_id == source_id:
        raise ValueError("Choose a different target profile")
    if not auth.can_write_profile(target_id, user_id):
        raise PermissionError("No write access to target profile")
    target_profile = preferences.get_profile(target_id, user_id)
    if not target_profile:
        raise ValueError("Target profile does not exist")

    roots = _target_profile_allowed_roots(target_profile, user_id)
    default_target_path = roots[0] if roots else _clean_remote_transfer_path(rtorrent.default_download_path(target_profile))
    requested_target_path = str(data.get("target_path") or data.get("path") or "").strip()
    target_path = _clean_remote_transfer_path(requested_target_path or default_target_path)
    inside_allowed_root = bool(roots and any(_path_inside_root(target_path, root) for root in roots))
    if not inside_allowed_root:
        # Note: A chosen target path must stay inside the target profile roots even for metadata-only transfers.
        if requested_target_path:
            raise ValueError("Target path is outside the target profile download roots")
        target_path = default_target_path
        inside_allowed_root = bool(roots and any(_path_inside_root(target_path, root) for root in roots))

    requested_move_data = bool(data.get("move_data"))
    move_data = requested_move_data
    write_check = {"ok": False, "message": "not requested"}
    downgrade_reason = ""
    if requested_move_data:
        if not inside_allowed_root:
            move_data = False
            downgrade_reason = "Target path is outside the target profile download roots"
            write_check = {"ok": False, "message": downgrade_reason, "path": target_path}
        else:
            # Note: Data moves are allowed only when the source rTorrent OS user can write to the target profile path.
            write_check = rtorrent.remote_can_write_directory(source_profile, target_path)
            move_data = bool(write_check.get("ok"))
            if not move_data:
                downgrade_reason = str(write_check.get("message") or write_check.get("error") or "Target path is not writable by the source rTorrent user")

    return {
        "hashes": hashes,
        "target_profile_id": target_id,
        "target_path": target_path,
        "path": target_path,
        "move_data": move_data,
        "move_data_requested": requested_move_data,
        "move_data_downgraded": bool(requested_move_data and not move_data),
        "move_data_downgrade_reason": downgrade_reason,
        "target_allowed_roots": roots,
        "target_write_check": write_check,
        "label_mode": str(data.get("label_mode") or "none").strip(),
        "label_value": str(data.get("label_value") or "").strip(),
        "post_action": str(data.get("post_action") or "current").strip(),
    }


def _validated_profile_transfer_payload(source_profile: dict, data: dict) -> dict:
    return _profile_transfer_payload(source_profile, data, require_hashes=True)


@bp.post("/torrents/profile_transfer/validate")
def profile_transfer_validate():
    profile = request_profile()
    if not profile:
        return jsonify({"ok": False, "error": "No profile"}), 400
    try:
        payload = _profile_transfer_payload(profile, request.get_json(silent=True) or {}, require_hashes=False)
        target_profile = preferences.get_profile(int(payload["target_profile_id"]), auth.current_user_id() or default_user_id())
        return ok({
            "target_profile_id": payload["target_profile_id"],
            "target_path": payload["target_path"],
            "move_data_requested": payload["move_data_requested"],
            "move_data_allowed": bool(payload["move_data"]),
            "move_data_downgraded": bool(payload["move_data_downgraded"]),
            "move_data_downgrade_reason": payload.get("move_data_downgrade_reason") or "",
            "target_write_check": payload.get("target_write_check") or {},
            "disk": rtorrent.disk_usage_for_paths(target_profile, [payload["target_path"]], mode="selected", selected_path=payload["target_path"]),
            "target_allowed_roots": payload.get("target_allowed_roots") or [],
        })
    except PermissionError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 403
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

@bp.post("/torrents/<action_name>")
def torrent_action(action_name: str):
    profile = request_profile()
    if not profile:
        return jsonify({"ok": False, "error": "No profile"}), 400
    data = request.get_json(silent=True) or {}
    allowed = {"start", "pause", "unpause", "stop", "resume", "recheck", "reannounce", "remove", "move", "profile_transfer", "set_label", "set_ratio_group"}
    if action_name not in allowed:
        return jsonify({"ok": False, "error": "Unknown action"}), 400
    if action_name == "profile_transfer":
        try:
            data = _validated_profile_transfer_payload(profile, data)
        except PermissionError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 403
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
    if action_name in {"move", "remove", "profile_transfer"}:
        # Note: Large move/remove/profile-transfer requests are split into ordered bulk parts; smaller requests keep the old single-job response shape.
        jobs = enqueue_bulk_parts(profile, action_name, data)
        first_job_id = jobs[0]["job_id"] if jobs else None
        total_hashes = sum(int(job.get("hash_count") or 0) for job in jobs)
        return ok({
            "job_id": first_job_id,
            "job_ids": [job["job_id"] for job in jobs],
            "jobs": jobs,
            "hash_count": total_hashes,
            "bulk": total_hashes > 1,
            "bulk_parts": len(jobs),
            "chunk_size": MOVE_BULK_MAX_HASHES,
            "transfer_move_data_downgraded": bool(data.get("move_data_downgraded")),
            "transfer_move_data_downgrade_reason": str(data.get("move_data_downgrade_reason") or ""),
        })
    payload = enrich_bulk_payload(profile, action_name, data)
    job_id = enqueue(action_name, profile["id"], payload)
    return ok({"job_id": job_id, "hash_count": len(payload.get("hashes") or []), "bulk": len(payload.get("hashes") or []) > 1})



@bp.post("/torrents/create")
def torrent_create():
    profile = request_profile()
    if not profile:
        return jsonify({"ok": False, "error": "No profile"}), 400
    form = request.form if request.content_type and request.content_type.startswith("multipart/form-data") else (request.get_json(silent=True) or {})
    try:
        created = torrent_creator.build_torrent(
            source_path=form.get("source_path", ""),
            trackers=form.get("trackers", ""),
            comment=form.get("comment", ""),
            source=form.get("source", ""),
            piece_size_kib=form.get("piece_size_kib", 256),
            private=str(form.get("private", "0")).lower() in {"1", "true", "on", "yes"},
        )
        share = str(form.get("share", "0")).lower() in {"1", "true", "on", "yes"}
        if share:
            size_check = rtorrent.validate_torrent_upload_size(profile, created["data"], True, created["source_parent"], form.get("label", ""))
            if not size_check.get("ok"):
                return jsonify({"ok": False, "error": f"Created torrent is too large for the current rTorrent XML-RPC limit: request {size_check['request_h']} > limit {size_check['limit_h']}. Change {size_check['setting']}.set to e.g. {size_check['suggested_value']} in rTorrent settings.", "xmlrpc_limit": size_check}), 413
            rtorrent.add_torrent_raw(profile, created["data"], True, created["source_parent"], form.get("label", ""))
        headers = _attachment_headers(created["filename"], "application/x-bittorrent")
        headers["Content-Length"] = str(len(created["data"]))
        headers["X-PyTorrent-Info-Hash"] = created["info_hash"]
        headers["X-PyTorrent-Create-Message"] = f"Created {created['filename']} ({created['file_count']} file(s))"
        return Response(created["data"], headers=headers)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400




def _selected_torrent_size(meta: dict, file_priorities: list | None = None) -> int:
    """Return bytes selected for download; unchecked files from the Add preview do not count."""
    priority_by_index = {}
    if isinstance(file_priorities, list):
        for item in file_priorities:
            try:
                priority_by_index[int(item.get("index"))] = int(item.get("priority") or 0)
            except Exception:
                continue
    files = meta.get("files") or []
    if not priority_by_index:
        return int(meta.get("size") or 0)
    total = 0
    for index, item in enumerate(files):
        if int(priority_by_index.get(index, 1) or 0) > 0:
            total += int(item.get("size") or 0)
    return total


def _space_check_payload(profile: dict, directory: str, items: list[dict]) -> dict:
    directory = str(directory or "").strip() or active_default_download_path(profile)
    required = sum(max(0, int(item.get("required_bytes") or 0)) for item in items)
    usage = rtorrent.disk_usage_for_paths(profile, [directory], "selected", directory)
    free = int(usage.get("free") or 0) if usage.get("ok") else 0
    ok_space = bool(usage.get("ok")) and free >= required
    return {
        "ok": ok_space,
        "directory": directory,
        "required_bytes": required,
        "required_h": human_size(required),
        "free_bytes": free,
        "free_h": human_size(free),
        "shortfall_bytes": max(0, required - free),
        "shortfall_h": human_size(max(0, required - free)),
        "disk": usage,
        "items": items,
        "message": "Enough free space." if ok_space else f"Not enough free space in {directory}. Required {human_size(required)}, available {human_size(free)}.",
    }


def _parse_priority_payload(value) -> dict | list:
    try:
        return json.loads(value or "{}")
    except Exception:
        return {}


def _priorities_for(priority_payload: dict | list, filename: str, info_hash: str) -> list:
    if isinstance(priority_payload, dict):
        return priority_payload.get(filename) or priority_payload.get(info_hash) or []
    if isinstance(priority_payload, list):
        return priority_payload
    return []


def _should_check_free_space(profile: dict) -> bool:
    prefs = preferences.get_preferences(profile_id=int(profile.get("id") or 0))
    return bool(int((prefs or {}).get("free_space_check_enabled") or 0))


@bp.post("/torrents/add")
def torrent_add():
    profile = request_profile()
    if not profile:
        return jsonify({"ok": False, "error": "No profile"}), 400
    job_ids = []
    if request.content_type and request.content_type.startswith("multipart/form-data"):
        start = request.form.get("start", "1") in {"1", "true", "on", "yes"}
        directory = request.form.get("directory", "") or active_default_download_path(profile)
        label = request.form.get("label", "")
        uris = [x.strip() for x in request.form.get("uris", "").splitlines() if x.strip()]
        for uri in uris:
            job_ids.append(enqueue("add_magnet", profile["id"], {"uri": uri, "start": start, "directory": directory, "label": label}))
        existing_hashes = {str(t.get("hash") or "").upper() for t in torrent_cache.snapshot(profile["id"])}
        priority_payload = _parse_priority_payload(request.form.get("file_priorities"))
        allow_duplicates = request.form.get("allow_duplicates", "0") in {"1", "true", "on", "yes"}
        skipped_duplicates = []
        uploaded_files = [(uploaded.filename, uploaded.read()) for uploaded in request.files.getlist("files")]
        if _should_check_free_space(profile) and uploaded_files:
            space_items = []
            for filename_hint, raw_data in uploaded_files:
                meta = parse_torrent(raw_data)
                info_hash = str(meta.get("info_hash") or "").upper()
                filename_hint = filename_hint or meta.get("name") or info_hash
                file_priorities = _priorities_for(priority_payload, filename_hint, info_hash)
                space_items.append({"filename": filename_hint, "info_hash": info_hash, "required_bytes": _selected_torrent_size(meta, file_priorities)})
            space = _space_check_payload(profile, directory, space_items)
            if not space.get("ok"):
                return jsonify({"ok": False, "error": space.get("message"), "space_check": space}), 409
        for filename_hint, raw in uploaded_files:
            meta = parse_torrent(raw)
            info_hash = str(meta.get("info_hash") or "").upper()
            filename = filename_hint or meta.get("name") or info_hash
            if info_hash and info_hash in existing_hashes and not allow_duplicates:
                skipped_duplicates.append({"filename": filename, "info_hash": info_hash})
                continue
            file_priorities = []
            if isinstance(priority_payload, dict):
                file_priorities = _priorities_for(priority_payload, filename, info_hash)
            elif isinstance(priority_payload, list):
                file_priorities = priority_payload

            size_check = rtorrent.validate_torrent_upload_size(profile, raw, start, directory, label, file_priorities or None)
            if not size_check.get("ok"):
                return jsonify({
                    "ok": False,
                    "error": (
                        f"Torrent file is too large for the current rTorrent XML-RPC limit: "
                        f"request {size_check['request_h']} > limit {size_check['limit_h']}. "
                        f"Change {size_check['setting']}.set to e.g. {size_check['suggested_value']} in rTorrent settings."
                    ),
                    "xmlrpc_limit": size_check,
                }), 413
            data_b64 = base64.b64encode(raw).decode("ascii")
            job_ids.append(enqueue("add_torrent_raw", profile["id"], {"filename": filename, "data_b64": data_b64, "start": start, "directory": directory, "label": label, "file_priorities": file_priorities or None}))
        return ok({"job_ids": job_ids, "skipped_duplicates": skipped_duplicates})
    data = request.get_json(silent=True) or {}
    uris = data.get("uris") or []
    if isinstance(uris, str):
        uris = [x.strip() for x in uris.splitlines() if x.strip()]
    for uri in uris:
        job_ids.append(enqueue("add_magnet", profile["id"], {"uri": uri, "start": data.get("start", True), "directory": data.get("directory", "") or active_default_download_path(profile), "label": data.get("label", "")}))
    return ok({"job_ids": job_ids})




@bp.post("/torrents/space-check")
def torrent_space_check():
    profile = request_profile()
    if not profile:
        return jsonify({"ok": False, "error": "No profile"}), 400
    if not (request.content_type and request.content_type.startswith("multipart/form-data")):
        return jsonify({"ok": False, "error": "multipart/form-data is required"}), 400
    directory = request.form.get("directory", "") or active_default_download_path(profile)
    priority_payload = _parse_priority_payload(request.form.get("file_priorities"))
    try:
        items = []
        for uploaded in request.files.getlist("files"):
            raw = uploaded.read()
            meta = parse_torrent(raw)
            info_hash = str(meta.get("info_hash") or "").upper()
            filename = uploaded.filename or meta.get("name") or info_hash
            file_priorities = _priorities_for(priority_payload, filename, info_hash)
            items.append({"filename": filename, "info_hash": info_hash, "required_bytes": _selected_torrent_size(meta, file_priorities)})
        return ok({"space_check": _space_check_payload(profile, directory, items)})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@bp.get("/download-location/preferences")
def download_location_preferences_get():
    profile = request_profile()
    prefs = preferences.get_preferences(profile_id=profile.get("id") if profile else None)
    profile_default = active_default_download_path(profile) if profile else ""
    return ok({"preferences": prefs, "default_path": profile_default})


@bp.post("/download-location/preferences")
def download_location_preferences_save():
    profile_id = request_profile_id(require_write=True)
    prefs = preferences.save_preferences(request.get_json(silent=True) or {}, profile_id=profile_id)
    profile = preferences.get_profile(profile_id, auth.current_user_id() or default_user_id()) if profile_id else request_profile()
    return ok({"preferences": prefs, "default_path": active_default_download_path(profile) if profile else ""})


@bp.post("/torrents/preview")
def torrent_preview():
    profile = request_profile()
    existing_hashes = set()
    if profile:
        try:
            existing_hashes = {str(t.get("hash") or "").upper() for t in torrent_cache.snapshot(profile["id"])}
        except Exception:
            existing_hashes = set()
    previews = []
    xmlrpc_limit = rtorrent.xmlrpc_size_limit(profile) if profile else None
    try:
        uploads = request.files.getlist("files") if request.content_type and request.content_type.startswith("multipart/form-data") else []
        for uploaded in uploads:
            raw = uploaded.read()
            meta = parse_torrent(raw)
            meta["filename"] = uploaded.filename
            meta["duplicate"] = bool(meta.get("info_hash") and meta["info_hash"].upper() in existing_hashes)
            if profile:
                size_check = rtorrent.validate_torrent_upload_size(profile, raw)
                meta["xmlrpc_request_bytes"] = size_check["request_bytes"]
                meta["xmlrpc_request_h"] = size_check["request_h"]
                meta["xmlrpc_too_large"] = not size_check.get("ok")
            previews.append(meta)
        return ok({"previews": previews, "xmlrpc_limit": xmlrpc_limit})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400



@bp.post("/speed/limits")
def speed_limits():
    profile = request_profile()
    if not profile:
        return jsonify({"ok": False, "error": "No profile"}), 400
    data = request.get_json(silent=True) or {}
    limits = profile_speed_limits.save_limits(profile["id"], data.get("down"), data.get("up"))
    # Note: Manual speed limits are stored once per rTorrent profile, so every user opening this profile sees and applies the same values.
    job_id = enqueue("set_limits", profile["id"], {"down": limits["down"], "up": limits["up"]})
    return ok({"job_id": job_id, "limits": limits})


@bp.get("/speed/profiles")
def speed_profiles_list():
    # Note: User-defined speed presets are stored per user and only populate the modal selector; applying them still uses the existing limits job.
    return ok({"profiles": speed_limit_profiles.list_profiles()})


@bp.post("/speed/profiles")
def speed_profiles_create():
    data = request.get_json(silent=True) or {}
    try:
        profile = speed_limit_profiles.save_profile(data.get("name"), data.get("down"), data.get("up"))
        return ok({"profile": profile})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@bp.put("/speed/profiles/<int:profile_id>")
def speed_profiles_update(profile_id: int):
    data = request.get_json(silent=True) or {}
    try:
        profile = speed_limit_profiles.save_profile(data.get("name"), data.get("down"), data.get("up"), profile_id=profile_id)
        return ok({"profile": profile})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404 if "not found" in str(exc).lower() else 400


@bp.delete("/speed/profiles/<int:profile_id>")
def speed_profiles_delete(profile_id: int):
    speed_limit_profiles.delete_profile(profile_id)
    return ok({"deleted": True})


def _user_disk_status(profile: dict) -> dict:
    # Note: Disk usage is user-preference aware, so it is read separately from the shared Socket.IO poller.
    prefs = preferences.get_disk_monitor_preferences(profile.get("id") if profile else None)
    try:
        paths = json.loads((prefs or {}).get("disk_monitor_paths_json") or "[]") if prefs else []
    except Exception:
        paths = []
    return rtorrent.disk_usage_for_paths(
        profile,
        paths,
        (prefs or {}).get("disk_monitor_mode") or "default",
        (prefs or {}).get("disk_monitor_selected_path") or "",
    )


