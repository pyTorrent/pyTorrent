from __future__ import annotations
from .client import *
from ...config import BASE_DIR

def torrent_files(profile: dict, torrent_hash: str) -> list[dict]:
    rows = client_for(profile).f.multicall(torrent_hash, "", "f.path=", "f.size_bytes=", "f.completed_chunks=", "f.size_chunks=", "f.priority=")
    files = []
    for idx, r in enumerate(rows):
        size = int(r[1] or 0)
        completed_chunks = int(r[2] or 0)
        size_chunks = int(r[3] or 0)
        progress = 100.0 if size <= 0 else round((completed_chunks / size_chunks) * 100, 2) if size_chunks else 0.0
        files.append({
            "index": idx,
            "path": r[0],
            "size": size,
            "size_h": human_size(size),
            "completed_chunks": completed_chunks,
            "size_chunks": size_chunks,
            "progress": min(100.0, max(0.0, progress)),
            "priority": int(r[4] or 0),
        })
    return files


def torrent_file_tree(profile: dict, torrent_hash: str) -> dict:
    root = {"name": "", "path": "", "type": "directory", "size": 0, "children": {}}
    for item in torrent_files(profile, torrent_hash):
        parts = [part for part in str(item.get("path") or "").split("/") if part]
        node = root
        prefix: list[str] = []
        for part in parts[:-1]:
            prefix.append(part)
            children = node.setdefault("children", {})
            node = children.setdefault(part, {"name": part, "path": "/".join(prefix), "type": "directory", "size": 0, "children": {}})
        name = parts[-1] if parts else str(item.get("path") or f"file-{item.get('index')}")
        child = dict(item)
        child.update({"name": name, "type": "file"})
        node.setdefault("children", {})[name] = child
    def finalize(node: dict) -> dict:
        if node.get("type") == "file":
            return node
        children = [finalize(v) for v in node.get("children", {}).values()]
        children.sort(key=lambda x: (x.get("type") != "directory", str(x.get("name") or "").lower()))
        node["children"] = children
        node["size"] = sum(int(c.get("size") or 0) for c in children)
        node["size_h"] = human_size(node["size"])
        return node
    return finalize(root)



def _torrent_file_remote_path(profile: dict, torrent_hash: str, index: int) -> tuple[dict, str]:
    c = client_for(profile)
    files = torrent_files(profile, torrent_hash)
    selected = next((f for f in files if int(f.get("index", -1)) == int(index)), None)
    if selected is None:
        available = ", ".join(str(f.get("index")) for f in files[:20]) or "none"
        raise ValueError(f"File index {index} not found. Available indexes: {available}")

    base = _remote_clean_path(_torrent_data_path(c, torrent_hash))
    rel = str(selected.get("path") or "").lstrip("/")

    # Note: rTorrent can report d.base_path as either the payload file or the
    # containing data directory for a one-file torrent. Keep both existing
    # layouts working and avoid treating a directory as the media file.
    if len(files) == 1 and base and rel:
        base_name = posixpath.basename(base.rstrip("/"))
        rel_name = posixpath.basename(rel.rstrip("/"))
        path = base if base_name == rel_name else _remote_join(base, rel)
    else:
        path = _remote_join(base, rel)
    return selected, path


def download_tmp_dir() -> str:
    PYTORRENT_TMP_DIR.mkdir(parents=True, exist_ok=True)
    return str(PYTORRENT_TMP_DIR)


def _remote_readability_error(c: ScgiRtorrentClient, source_path: str) -> str | None:
    script = (
        'p=$1; '
        'command -v base64 >/dev/null 2>&1 || { echo "base64 command not found on rTorrent host"; exit 0; }; '
        '[ -e "$p" ] || { echo "source file does not exist"; exit 0; }; '
        '[ -f "$p" ] || { echo "source path is not a regular file"; exit 0; }; '
        '[ -r "$p" ] || { echo "source file is not readable by rTorrent"; exit 0; }; '
        'echo OK'
    )
    output = str(_rt_execute(c, "execute.capture", "sh", "-c", script, "pytorrent-download-check", source_path) or "").strip()
    return None if output == "OK" else (output or "source file cannot be read by rTorrent")


def remote_file_readability_error(profile: dict, source_path: str) -> str | None:
    return _remote_readability_error(client_for(profile), source_path)


def iter_remote_file_chunks(profile: dict, source_path: str, size: int | None = None, chunk_size: int | None = None):
    c = client_for(profile)
    clean = _remote_clean_path(source_path)
    err = _remote_readability_error(c, clean)
    if err:
        raise RuntimeError(err)
    block_size = max(65536, int(chunk_size or REMOTE_READ_CHUNK_BYTES or 1048576))
    offset = 0
    emitted = 0
    script = (
        'p=$1; bs=$2; skip=$3; '
        'command -v base64 >/dev/null 2>&1 || { printf "ERR\tbase64 command not found on rTorrent host"; exit 0; }; '
        '[ -r "$p" ] || { printf "ERR\tsource file is not readable by rTorrent"; exit 0; }; '
        'dd if="$p" bs="$bs" skip="$skip" count=1 2>/dev/null | base64 | tr -d "\n"'
    )
    while size is None or emitted < int(size):
        output = str(_rt_execute(c, "execute.capture", "sh", "-c", script, "pytorrent-download-read", clean, str(block_size), str(offset)) or "")
        if output.startswith("ERR\t"):
            raise RuntimeError(output.split("\t", 1)[1] or "remote read failed")
        if not output:
            break
        try:
            chunk = __import__("base64").b64decode(output, validate=False)
        except Exception as exc:
            raise RuntimeError(f"remote read returned invalid base64: {exc}") from exc
        if not chunk:
            break
        yield chunk
        emitted += len(chunk)
        offset += 1
        if size is not None and emitted >= int(size):
            break



_MEDIA_INFO_EXTENSIONS = {
    ".3g2", ".3gp", ".aac", ".aiff", ".ape", ".asf", ".avi", ".flac",
    ".flv", ".m4a", ".m4v", ".mka", ".mkv", ".mov", ".mp3", ".mp4",
    ".mpeg", ".mpg", ".ogg", ".opus", ".ts", ".wav", ".webm", ".wma", ".wmv",
}
_TEXT_PREVIEW_EXTENSIONS = {
    ".ass", ".cue", ".csv", ".ini", ".json", ".log", ".m3u", ".m3u8",
    ".md", ".nfo", ".srt", ".ssa", ".sub", ".sfv", ".txt", ".url",
    ".xml", ".yaml", ".yml",
}
_IMAGE_PREVIEW_EXTENSIONS = {".avif", ".bmp", ".gif", ".jpeg", ".jpg", ".png", ".webp"}
_PDF_PREVIEW_EXTENSIONS = {".pdf"}
_MEDIA_INFO_SAMPLE_BYTES = 32 * 1024 * 1024
_MEDIA_INFO_CHUNK_BYTES = 1024 * 1024
_TEXT_PREVIEW_BYTES = 512 * 1024
_IMAGE_PREVIEW_BYTES = 8 * 1024 * 1024
_MEDIA_INFO_TMP_DIR = BASE_DIR / "data" / "media-info-samples"


def _file_extension(path: str) -> str:
    return LocalPath(str(path or "")).suffix.lower()


def _media_info_supported(path: str) -> bool:
    # Note: Extension filtering avoids trying binary metadata parsers on every torrent payload file.
    return _file_extension(path) in _MEDIA_INFO_EXTENSIONS


def _text_preview_supported(path: str) -> bool:
    # Note: Text previews intentionally include NFO and subtitle files so the existing info button becomes useful for release notes too.
    return _file_extension(path) in _TEXT_PREVIEW_EXTENSIONS


def _image_preview_supported(path: str) -> bool:
    # Note: Image previews are limited to browser-safe raster formats and avoid SVG to prevent inline script-like payloads.
    return _file_extension(path) in _IMAGE_PREVIEW_EXTENSIONS


def _pdf_preview_supported(path: str) -> bool:
    # Note: PDF previews are rendered inline by the browser so image-heavy books keep their page layout.
    return _file_extension(path) in _PDF_PREVIEW_EXTENSIONS


def _media_info_sample_suffix(source_path: str) -> str:
    suffix = LocalPath(str(source_path or "")).suffix.lower()
    if suffix and len(suffix) <= 16 and all(ch.isalnum() or ch in ".-_" for ch in suffix):
        return suffix
    return ".bin"


def _read_file_prefix(profile: dict, source_path: str, max_bytes: int) -> bytes:
    # Note: File info must read through rTorrent, not the pyTorrent process, because torrents may live on a remote host or under rTorrent-only permissions.
    limit = max(0, int(max_bytes or 0))
    chunks: list[bytes] = []
    collected = 0
    for chunk in iter_remote_file_chunks(profile, source_path, size=limit, chunk_size=_MEDIA_INFO_CHUNK_BYTES):
        if collected >= limit:
            break
        data = bytes(chunk[: max(0, limit - collected)])
        chunks.append(data)
        collected += len(data)
    return b"".join(chunks)


def _decode_text_preview(data: bytes) -> tuple[str, str]:
    # Note: NFO files are often CP437, while normal text is usually UTF-8; the fallback keeps ASCII art readable.
    if not data:
        return "utf-8", ""
    for encoding in ("utf-8-sig", "utf-8"):
        try:
            return encoding, data.decode(encoding)
        except UnicodeDecodeError:
            pass
    for encoding in ("cp437", "cp1250", "latin-1"):
        try:
            return encoding, data.decode(encoding, errors="replace")
        except Exception:
            pass
    return "utf-8", data.decode("utf-8", errors="replace")


def _image_preview_mime(path: str) -> str:
    # Note: The MIME type is extension-based because preview input is already restricted to known image suffixes.
    ext = _file_extension(path)
    return {
        ".avif": "image/avif",
        ".bmp": "image/bmp",
        ".gif": "image/gif",
        ".jpeg": "image/jpeg",
        ".jpg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(ext, "application/octet-stream")


def _text_file_preview(profile: dict, selected: dict, remote_path: str, max_bytes: int = _TEXT_PREVIEW_BYTES) -> dict:
    # Note: Text preview returns escaped-by-frontend content and a clear truncation flag for large NFO/log/subtitle files.
    size = int(selected.get("size") or 0)
    data = _read_file_prefix(profile, remote_path, max_bytes)
    encoding, text = _decode_text_preview(data)
    return {
        **selected,
        "kind": "text",
        "parser": "text-preview",
        "supported": True,
        "sample_bytes": len(data),
        "sample_limit": int(max_bytes),
        "partial": bool(size and len(data) < size),
        "encoding": encoding,
        "text": text,
        "line_count": text.count("\n") + (1 if text else 0),
        "summary": {},
        "fields": [
            {"key": "Type", "value": "Text preview"},
            {"key": "Encoding", "value": encoding},
            {"key": "Preview bytes", "value": human_size(len(data))},
        ],
        "raw": [],
    }


def _image_file_preview(profile: dict, selected: dict, remote_path: str, max_bytes: int = _IMAGE_PREVIEW_BYTES) -> dict:
    # Note: Image preview is size capped and CSS-constrained in the modal instead of decoding/resizing images server-side.
    size = int(selected.get("size") or 0)
    result = {
        **selected,
        "kind": "image",
        "parser": "image-preview",
        "supported": True,
        "sample_bytes": 0,
        "sample_limit": int(max_bytes),
        "partial": False,
        "mime_type": _image_preview_mime(str(selected.get("path") or remote_path)),
        "summary": {},
        "fields": [
            {"key": "Type", "value": "Image preview"},
        ],
        "raw": [],
    }
    if size > max_bytes:
        result.update({
            "too_large": True,
            "error": f"Image preview is limited to {human_size(max_bytes)}. Download the file to view the full image.",
        })
        return result
    data = _read_file_prefix(profile, remote_path, max_bytes)
    import base64

    result.update({
        "sample_bytes": len(data),
        "data_url": f"data:{result['mime_type']};base64,{base64.b64encode(data).decode('ascii')}",
        "fields": result["fields"] + [
            {"key": "Image bytes", "value": human_size(len(data))},
            {"key": "MIME type", "value": result["mime_type"]},
        ],
    })
    return result


def _pdf_file_preview(
    profile: dict,
    selected: dict,
    remote_path: str,
) -> dict:
    # Note: pypdf is no longer required because PDFs are not parsed; the browser renders the original file stream.
    size = int(selected.get("size") or 0)
    return {
        **selected,
        "kind": "pdf",
        "parser": "browser-pdf-viewer",
        "supported": True,
        "sample_bytes": 0,
        "sample_limit": 0,
        "page_limit": 0,
        "partial": False,
        "summary": {
            "duration": None,
            "bit_rate": human_size(size) if size else None,
            "compression": "PDF",
            "producer": "Browser inline preview",
            "creation_date": None,
        },
        "fields": [
            {"key": "Type", "value": "PDF inline preview"},
            {"key": "PDF size", "value": human_size(size)},
            {"key": "Preview mode", "value": "Browser PDF renderer"},
        ],
        "raw": [],
        "text": "",
    }


def _media_info_temp_sample(profile: dict, source_path: str, max_bytes: int) -> tuple[str, int]:
    # Note: hachoir needs a seekable file, so this writes a bounded sample into the app data directory instead of loading whole media into RAM.
    import tempfile

    _MEDIA_INFO_TMP_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix="pytorrent-mediainfo-",
        suffix=_media_info_sample_suffix(source_path),
        dir=str(_MEDIA_INFO_TMP_DIR),
    )
    written = 0
    try:
        with os.fdopen(fd, "wb") as tmp:
            for chunk in iter_remote_file_chunks(profile, source_path, size=max_bytes, chunk_size=_MEDIA_INFO_CHUNK_BYTES):
                if written >= max_bytes:
                    break
                data = bytes(chunk[: max(0, max_bytes - written)])
                tmp.write(data)
                written += len(data)
        return tmp_path, written
    except Exception:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        raise


def _media_info_plaintext(metadata) -> list[str]:
    # Note: exportPlaintext is the most stable hachoir API across supported package versions.
    try:
        lines = metadata.exportPlaintext() or []
    except Exception:
        return []
    return [str(line).strip(" -") for line in lines if str(line).strip(" -")]


def _media_info_parse_lines(lines: list[str]) -> list[dict]:
    # Note: The frontend receives both grouped fields and raw text so unknown hachoir fields stay visible.
    fields = []
    for line in lines:
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if key and value:
            fields.append({"key": key, "value": value})
    return fields


def _media_info_field_lookup(fields: list[dict]) -> dict:
    lookup = {}
    for field in fields:
        key = str(field.get("key") or "").lower()
        if key and key not in lookup:
            lookup[key] = field.get("value")
    return lookup


def _media_info_summary(fields: list[dict]) -> dict:
    # Note: Summary keeps the modal readable while raw fields remain available below it.
    lookup = _media_info_field_lookup(fields)
    def first(*names):
        for name in names:
            value = lookup.get(name.lower())
            if value:
                return value
        return None
    return {
        "duration": first("Duration", "Play duration"),
        "bit_rate": first("Bit rate", "Overall bit rate"),
        "width": first("Image width", "Width"),
        "height": first("Image height", "Height"),
        "frame_rate": first("Frame rate"),
        "sample_rate": first("Sample rate"),
        "channels": first("Channel", "Channel(s)", "Channels"),
        "compression": first("Compression", "Compressor", "Codec", "Video codec", "Audio codec"),
        "producer": first("Producer", "Encoder", "Writing application"),
        "creation_date": first("Creation date", "Creation time"),
    }


def _media_info_hachoir_imports():
    # Note: Import is checked before reading the media sample so dependency problems fail fast and clearly.
    import sys

    try:
        from hachoir.metadata import extractMetadata
        from hachoir.parser import createParser
        return createParser, extractMetadata
    except ModuleNotFoundError as exc:
        missing = str(getattr(exc, "name", "") or "hachoir")
        if missing.split(".", 1)[0] == "hachoir":
            raise RuntimeError(
                "Python package 'hachoir' is not importable in the application runtime. "
                "Install it inside the pyTorrent virtualenv and restart the service: "
                "/opt/pyTorrent/venv/bin/pip install -r /opt/pyTorrent/requirements.txt && systemctl restart pytorrent. "
                f"Runtime: {sys.executable}."
            ) from exc
        raise RuntimeError(
            f"hachoir is installed, but one of its Python dependencies is missing: {missing}. "
            f"Runtime: {sys.executable}."
        ) from exc
    except Exception as exc:
        raise RuntimeError(
            "hachoir was found, but failed during import. "
            f"Runtime: {sys.executable}. Details: {exc}"
        ) from exc


def _torrent_file_is_complete(selected: dict) -> bool:
    # Note: File info reads real file bytes, so incomplete payload files are blocked before any parser touches them.
    size = int(selected.get("size") or 0)
    completed_chunks = int(selected.get("completed_chunks") or 0)
    size_chunks = int(selected.get("size_chunks") or 0)
    progress = float(selected.get("progress") or 0)
    return size <= 0 or progress >= 100.0 or (size_chunks > 0 and completed_chunks >= size_chunks)


def torrent_file_media_info(profile: dict, torrent_hash: str, index: int, max_bytes: int = _MEDIA_INFO_SAMPLE_BYTES) -> dict:
    # Note: This additive endpoint now acts as a smart file preview: media metadata, text/NFO reader, or image preview depending on file type.
    selected, remote_path = _torrent_file_remote_path(profile, torrent_hash, index)
    name = str(selected.get("path") or remote_path)
    size = int(selected.get("size") or 0)

    if not _torrent_file_is_complete(selected):
        raise RuntimeError("File info is available only after this file is fully downloaded.")

    err = remote_file_readability_error(profile, remote_path)
    if err:
        raise RuntimeError(err)

    if _text_preview_supported(name):
        return _text_file_preview(profile, selected, remote_path)
    if _image_preview_supported(name):
        return _image_file_preview(profile, selected, remote_path)
    if _pdf_preview_supported(name):
        return _pdf_file_preview(profile, selected, remote_path)

    supported = _media_info_supported(name)
    result = {
        **selected,
        "kind": "media",
        "supported": supported,
        "sample_bytes": 0,
        "sample_limit": int(max_bytes),
        "partial": True,
        "summary": {},
        "fields": [],
        "raw": [],
        "parser": "hachoir",
    }
    if not supported:
        result.update({
            "kind": "unsupported",
            "error": "This file extension is not supported by the built-in preview or media info parser.",
        })
        return result

    createParser, extractMetadata = _media_info_hachoir_imports()

    tmp_path = None
    try:
        tmp_path, written = _media_info_temp_sample(profile, remote_path, max(1024 * 1024, int(max_bytes)))
        # Note: Do not pass real_filename here; some hachoir versions treat it as an input path and fail for nested torrent file names.
        parser = createParser(tmp_path)
        if parser is None:
            result.update({"sample_bytes": written, "error": "hachoir could not detect this media container."})
            return result
        with parser:
            metadata = extractMetadata(parser)
        if metadata is None:
            result.update({"sample_bytes": written, "error": "No media metadata found in the sampled part of the file."})
            return result
        raw = _media_info_plaintext(metadata)
        fields = _media_info_parse_lines(raw)
        result.update({
            "sample_bytes": written,
            "partial": bool(size and written < size),
            "summary": _media_info_summary(fields),
            "fields": fields,
            "raw": raw,
        })
        return result
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

def torrent_download_file_info(profile: dict, torrent_hash: str, index: int) -> dict:
    selected, remote_path = _torrent_file_remote_path(profile, torrent_hash, index)
    err = remote_file_readability_error(profile, remote_path)
    if err:
        raise RuntimeError(err)
    return {**selected, "remote_path": remote_path, "download_name": LocalPath(str(selected.get("path") or remote_path)).name}


def torrent_download_zip_items(profile: dict, torrent_hash: str, indexes: list[int] | None = None) -> list[dict]:
    files = torrent_files(profile, torrent_hash)
    wanted = {int(x) for x in indexes} if indexes else {int(f["index"]) for f in files}
    items = []
    for item in files:
        if int(item.get("index", -1)) not in wanted:
            continue
        _, remote_path = _torrent_file_remote_path(profile, torrent_hash, int(item["index"]))
        err = remote_file_readability_error(profile, remote_path)
        if err:
            raise RuntimeError(f"{item.get('path') or item.get('index')}: {err}")
        items.append({**item, "remote_path": remote_path})
    if not items:
        raise ValueError("No files selected")
    return items


def _remote_file_exists(c: ScgiRtorrentClient, source_path: str) -> bool:
    # Note: Export fallback checks candidate .torrent files on the rTorrent host before staging, avoiding stale tied-file paths.
    clean = _remote_clean_path(source_path)
    if not clean:
        return False
    script = 'p=$1; [ -f "$p" ] && [ -r "$p" ] && printf OK || true'
    try:
        return str(_rt_execute(c, "execute.capture", "sh", "-c", script, "pytorrent-file-exists", clean) or "").strip() == "OK"
    except Exception:
        return False


def _remote_stage_path(c: ScgiRtorrentClient, source_path: str, suffix: str = "") -> str:
    token = uuid.uuid4().hex
    safe_suffix = ''.join(ch if ch.isalnum() or ch in '.-_' else '_' for ch in str(suffix or ''))[:80]
    target = f"{download_tmp_dir().rstrip('/')}/pytorrent-download-{token}{safe_suffix}"
    script = (
        'src=$1; dst=$2; '
        'if [ ! -f "$src" ]; then printf "ERR\tmissing source: %s\n" "$src"; exit 0; fi; '
        'if [ ! -r "$src" ]; then printf "ERR\tsource is not readable: %s\n" "$src"; exit 0; fi; '
        'cp -- "$src" "$dst" 2>/tmp/pytorrent-cp-err-$$ || { rc=$?; err=$(cat /tmp/pytorrent-cp-err-$$ 2>/dev/null); rm -f /tmp/pytorrent-cp-err-$$; printf "ERR\t%s\t%s\n" "$rc" "$err"; exit 0; }; '
        'rm -f /tmp/pytorrent-cp-err-$$; chmod 0644 "$dst" 2>/dev/null || true; printf "OK\t%s\n" "$dst"'
    )
    clean_source = _remote_clean_path(source_path)
    output = str(_rt_execute(c, "execute.capture", "sh", "-c", script, "pytorrent-stage-file", clean_source, target) or "").strip()
    parts = (output.splitlines()[0] if output else "").split("\t", 2)
    if len(parts) >= 2 and parts[0] == "OK":
        return parts[1]
    detail = parts[2] if len(parts) > 2 else (parts[1] if len(parts) > 1 else output)
    raise RuntimeError(detail or "Cannot stage file through rTorrent")


def _remote_stage_zip(c: ScgiRtorrentClient, files: list[dict], suffix: str = ".zip") -> str:
    if not files:
        raise ValueError("No files selected")
    token = uuid.uuid4().hex
    tmp_base = download_tmp_dir().rstrip("/")
    list_path = f"{tmp_base}/pytorrent-zip-list-{token}.txt"
    zip_path = f"{tmp_base}/pytorrent-download-{token}{suffix}"
    lines = []
    for item in files:
        src = str(item.get("remote_path") or "")
        arc = str(item.get("path") or LocalPath(src).name).lstrip("/") or LocalPath(src).name
        lines.append(src.replace("\t", " ") + "\t" + arc.replace("\t", " "))
    list_data = "\n".join(lines)
    script = (
        'list=$1; zip=$2; data=$3; umask 022; printf "%s\n" "$data" > "$list"; '
        'rm -f "$zip"; tmpdir=$(mktemp -d /tmp/pytorrent-zip-XXXXXX) || exit 3; '
        'rc=0; while IFS=$(printf "\\t") read -r src arc; do '
        '[ -n "$src" ] || continue; '
        'if [ ! -f "$src" ]; then echo "missing source: $src" >&2; rc=4; break; fi; '
        'case "$arc" in /*|../*|*/../*) echo "unsafe zip path: $arc" >&2; rc=5; break;; esac; '
        'dir=${arc%/*}; if [ "$dir" != "$arc" ]; then mkdir -p "$tmpdir/$dir" || { rc=$?; break; }; fi; cp -- "$src" "$tmpdir/$arc" || { rc=$?; break; }; '
        'done; if [ $rc -eq 0 ]; then (cd "$tmpdir" && zip -qr "$zip" .) || rc=$?; fi; '
        'rm -rf "$tmpdir" "$list"; '
        'if [ $rc -eq 0 ] && [ -f "$zip" ]; then chmod 0644 "$zip" 2>/dev/null || true; printf "OK\t%s\n" "$zip"; else printf "ERR\t%s\n" "$rc"; fi'
    )
    output = str(_rt_execute(c, "execute.capture", "sh", "-c", script, "pytorrent-stage-zip", list_path, zip_path, list_data) or "").strip()
    parts = (output.splitlines()[0] if output else "").split("\t", 1)
    if len(parts) == 2 and parts[0] == "OK":
        return parts[1]
    raise RuntimeError(output or "Cannot create ZIP through rTorrent")


def _remote_remove_staged(profile: dict, path: str) -> None:
    clean = str(path or "")
    tmp_prefix = download_tmp_dir().rstrip("/") + "/pytorrent-download-"
    if not clean.startswith(tmp_prefix):
        return
    try:
        _rt_execute(client_for(profile), "execute.throw", "rm", "-f", clean)
    except Exception:
        pass


def torrent_staged_file_path(profile: dict, torrent_hash: str, index: int) -> dict:
    c = client_for(profile)
    selected, remote_path = _torrent_file_remote_path(profile, torrent_hash, index)
    suffix = LocalPath(str(selected.get("path") or "file")).suffix
    staged = _remote_stage_path(c, remote_path, suffix)
    return {**selected, "remote_path": remote_path, "staged_path": staged, "download_name": LocalPath(str(selected.get("path") or staged)).name}


def torrent_staged_zip_path(profile: dict, torrent_hash: str, indexes: list[int] | None = None) -> dict:
    c = client_for(profile)
    files = torrent_files(profile, torrent_hash)
    wanted = {int(x) for x in indexes} if indexes else {int(f["index"]) for f in files}
    items = []
    for item in files:
        if int(item.get("index", -1)) not in wanted:
            continue
        _, remote_path = _torrent_file_remote_path(profile, torrent_hash, int(item["index"]))
        items.append({**item, "remote_path": remote_path})
    staged = _remote_stage_zip(c, items)
    return {"staged_path": staged, "count": len(items)}


def _torrent_raw_from_method(c: ScgiRtorrentClient, torrent_hash: str) -> bytes | None:
    for method in ("d.get_metafile", "d.metafile"):
        try:
            value = c.call(method, torrent_hash)
        except Exception:
            continue
        if hasattr(value, "data"):
            data = value.data
        elif isinstance(value, bytes):
            data = value
        elif isinstance(value, str):
            data = value.encode("latin-1", "ignore")
        else:
            data = None
        if data:
            return bytes(data)
    return None


def _rtorrent_session_path(c: ScgiRtorrentClient) -> str:
    for method in ("session.path", "get_session"):
        try:
            value = str(c.call(method) or "").strip()
        except Exception:
            continue
        if value:
            return _remote_clean_path(value)
    return ""


def _torrent_source_file_candidates(c: ScgiRtorrentClient, torrent_hash: str) -> list[str]:
    # Note: rTorrent may keep stale watch/tied paths; session candidates preserve .torrent export when the original source was moved.
    candidates: list[str] = []
    for method in ("d.tied_to_file", "d.get_tied_to_file", "d.loaded_file", "d.get_loaded_file", "d.session_file", "d.get_session_file"):
        try:
            value = str(c.call(method, torrent_hash) or "").strip()
        except Exception:
            continue
        if value:
            candidates.append(value)
    session_path = _rtorrent_session_path(c)
    hash_values = []
    clean_hash = str(torrent_hash or "").strip()
    if clean_hash:
        hash_values.extend([clean_hash, clean_hash.upper(), clean_hash.lower()])
    for h in dict.fromkeys(hash_values):
        if session_path:
            candidates.append(_remote_join(session_path, f"{h}.torrent"))
            candidates.append(_remote_join(session_path, h))
        candidates.append(f"/tmp/{h}.torrent")
    result = []
    for item in candidates:
        clean = _remote_clean_path(item)
        if clean and clean not in result:
            result.append(clean)
    return result


def _torrent_source_file(c: ScgiRtorrentClient, torrent_hash: str) -> str:
    for source in _torrent_source_file_candidates(c, torrent_hash):
        if _remote_file_exists(c, source):
            return source
    return ""


def _save_torrent_session_source(c: ScgiRtorrentClient, torrent_hash: str) -> list[str]:
    saved_methods: list[str] = []
    for method in ("d.save_full_session", "d.save_resume"):
        try:
            c.call(method, torrent_hash)
            saved_methods.append(method)
        except Exception:
            continue
    return saved_methods


def export_torrent_file(profile: dict, torrent_hash: str) -> dict:
    c = client_for(profile)
    name = str(c.call("d.name", torrent_hash) or torrent_hash).strip() or torrent_hash
    filename = f"{name}.torrent" if not name.lower().endswith(".torrent") else name
    source = _torrent_source_file(c, torrent_hash)
    if source:
        # Note: Stream the existing .torrent source directly instead of copying it to a temporary staged file first.
        return {"path": source, "download_name": filename, "local": False}
    saved_methods = _save_torrent_session_source(c, torrent_hash)
    if saved_methods:
        # Note: Metadata-only profile transfers can recover missing tied .torrent files by asking rTorrent to persist its in-memory session first.
        source = _torrent_source_file(c, torrent_hash)
        if source:
            return {"path": source, "download_name": filename, "local": False, "session_saved_by": saved_methods}
    raw = _torrent_raw_from_method(c, torrent_hash)
    if raw:
        target = LocalPath(download_tmp_dir()) / f"pytorrent-download-{uuid.uuid4().hex}.torrent"
        target.write_bytes(raw)
        return {"path": str(target), "download_name": filename, "local": True}
    raise RuntimeError("Cannot find torrent source file in rTorrent")


def set_file_priorities(profile: dict, torrent_hash: str, files: list[dict]) -> dict:
    """Set rTorrent file priorities for one torrent.

    Note: Keeps the existing /files/priority API behavior and returns per-file errors
    instead of failing the whole batch on one invalid item.
    """
    c = client_for(profile)
    updated = []
    errors = []
    for item in files or []:
        try:
            index = int(item.get("index"))
            priority = int(item.get("priority"))
            if priority < 0 or priority > 3:
                raise ValueError("Priority must be between 0 and 3")
            target = f"{torrent_hash}:f{index}"
            c.call("f.priority.set", target, priority)
            updated.append({"index": index, "priority": priority})
        except Exception as exc:
            errors.append({"item": item, "error": str(exc)})
    return {"updated": updated, "errors": errors}

def set_folder_priority(profile: dict, torrent_hash: str, folder_path: str, priority: int) -> dict:
    # Note: Folder priority applies the same rTorrent file priority to every descendant path.
    folder = str(folder_path or "").strip().strip("/")
    updates = []
    for item in torrent_files(profile, torrent_hash):
        path = str(item.get("path") or "").strip("/")
        if not folder or path == folder or path.startswith(folder + "/"):
            updates.append({"index": item["index"], "priority": int(priority)})
    if not updates:
        return {"updated": [], "errors": [{"folder": folder_path, "error": "No files matched folder"}]}
    return set_file_priorities(profile, torrent_hash, updates)


def torrent_local_file_path(profile: dict, torrent_hash: str, index: int) -> str:
    c = client_for(profile)
    files = torrent_files(profile, torrent_hash)
    selected = next((f for f in files if int(f.get("index", -1)) == int(index)), None)
    if not selected:
        raise ValueError("File index not found")
    base = _remote_clean_path(_torrent_data_path(c, torrent_hash))
    rel = str(selected.get("path") or "").lstrip("/")
    if len(files) == 1 and base and not base.endswith("/"):
        path = base
    else:
        path = _remote_join(base, rel)
    # Note: HTTP file serving is enabled only for local profiles to avoid pretending remote files exist locally.
    if int(profile.get("is_remote") or 0):
        raise ValueError("HTTP file download is available only for local rTorrent profiles")
    local = LocalPath(path).resolve()
    if not local.exists() or not local.is_file():
        raise FileNotFoundError(f"Local file is not available: {local}")
    return str(local)


def torrent_local_file_paths(profile: dict, torrent_hash: str, indexes: list[int] | None = None) -> list[dict]:
    files = torrent_files(profile, torrent_hash)
    wanted = {int(x) for x in indexes} if indexes else {int(f["index"]) for f in files}
    out = []
    for item in files:
        if int(item.get("index", -1)) not in wanted:
            continue
        out.append({**item, "local_path": torrent_local_file_path(profile, torrent_hash, int(item["index"]))})
    return out




# Note: Keep split module exports compatible with the previous single rtorrent.py module.
__all__ = [
    name for name in globals()
    if not name.startswith("__") and name not in {"annotations"}
]
