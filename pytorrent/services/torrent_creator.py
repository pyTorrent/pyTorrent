from __future__ import annotations
import hashlib
import os
import time
from pathlib import Path
from typing import Any

DEFAULT_PIECE_KIB = 256
MIN_PIECE_KIB = 16
MAX_PIECE_KIB = 16384


def _bencode(value: Any) -> bytes:
    if isinstance(value, bool):
        value = int(value)
    if isinstance(value, int):
        return b"i" + str(value).encode("ascii") + b"e"
    if isinstance(value, bytes):
        return str(len(value)).encode("ascii") + b":" + value
    if isinstance(value, str):
        raw = value.encode("utf-8")
        return str(len(raw)).encode("ascii") + b":" + raw
    if isinstance(value, (list, tuple)):
        return b"l" + b"".join(_bencode(item) for item in value) + b"e"
    if isinstance(value, dict):
        items = []
        for key in sorted(value.keys(), key=lambda k: k.encode("utf-8") if isinstance(k, str) else bytes(k)):
            bkey = key.encode("utf-8") if isinstance(key, str) else bytes(key)
            items.append(_bencode(bkey) + _bencode(value[key]))
        return b"d" + b"".join(items) + b"e"
    raise TypeError(f"Unsupported bencode value: {type(value)!r}")


def _clean_tracker_lines(raw: str) -> list[str]:
    lines = []
    seen = set()
    for item in str(raw or "").replace("\r", "\n").split("\n"):
        url = item.strip()
        if not url or url in seen:
            continue
        seen.add(url)
        lines.append(url)
    return lines


def _normalize_piece_size(piece_size_kib: int | str | None) -> int:
    try:
        kib = int(piece_size_kib or DEFAULT_PIECE_KIB)
    except Exception:
        kib = DEFAULT_PIECE_KIB
    kib = max(MIN_PIECE_KIB, min(MAX_PIECE_KIB, kib))
    return kib * 1024


def _safe_path_parts(path: Path) -> list[str]:
    parts = [part for part in path.parts if part not in {"", ".", ".."}]
    if not parts:
        raise ValueError("File path inside torrent is empty")
    return parts


def _iter_files(source: Path) -> list[tuple[Path, list[str], int]]:
    if source.is_file():
        return [(source, [source.name], source.stat().st_size)]
    if not source.is_dir():
        raise ValueError("Source must be an existing file or directory")
    rows: list[tuple[Path, list[str], int]] = []
    for root, dirs, files in os.walk(source):
        dirs[:] = sorted(d for d in dirs if not (Path(root) / d).is_symlink())
        for filename in sorted(files):
            full = Path(root) / filename
            if full.is_symlink() or not full.is_file():
                continue
            rel = full.relative_to(source)
            rows.append((full, _safe_path_parts(rel), full.stat().st_size))
    if not rows:
        raise ValueError("Source directory does not contain regular files")
    return rows


def _piece_hashes(files: list[tuple[Path, list[str], int]], piece_size: int) -> bytes:
    pieces = bytearray()
    buffer = bytearray()
    for full, _parts, _size in files:
        with full.open("rb") as handle:
            while True:
                chunk = handle.read(max(64 * 1024, min(piece_size, 1024 * 1024)))
                if not chunk:
                    break
                buffer.extend(chunk)
                while len(buffer) >= piece_size:
                    piece = bytes(buffer[:piece_size])
                    del buffer[:piece_size]
                    pieces.extend(hashlib.sha1(piece).digest())
    if buffer:
        pieces.extend(hashlib.sha1(bytes(buffer)).digest())
    return bytes(pieces)


def build_torrent(
    source_path: str,
    trackers: str = "",
    comment: str = "",
    source: str = "",
    piece_size_kib: int | str | None = DEFAULT_PIECE_KIB,
    private: bool = False,
    created_by: str = "pyTorrent",
) -> dict[str, Any]:
    source_path = str(source_path or "").strip()
    if not source_path:
        raise ValueError("Source path is required")
    path = Path(source_path).expanduser().resolve()
    files = _iter_files(path)
    piece_size = _normalize_piece_size(piece_size_kib)

    info: dict[str, Any] = {
        "name": path.name,
        "piece length": piece_size,
        "pieces": _piece_hashes(files, piece_size),
    }
    if private:
        info["private"] = 1
    if source:
        info["source"] = str(source).strip()
    if path.is_file():
        info["length"] = files[0][2]
    else:
        info["files"] = [{"length": size, "path": parts} for _full, parts, size in files]

    tracker_lines = _clean_tracker_lines(trackers)
    meta: dict[str, Any] = {
        "created by": created_by,
        "creation date": int(time.time()),
        "info": info,
    }
    if tracker_lines:
        meta["announce"] = tracker_lines[0]
        meta["announce-list"] = [[url] for url in tracker_lines]
    if comment:
        meta["comment"] = str(comment).strip()

    data = _bencode(meta)
    info_hash = hashlib.sha1(_bencode(info)).hexdigest().upper()
    return {
        "data": data,
        "filename": f"{path.name}.torrent",
        "info_hash": info_hash,
        "source_parent": str(path.parent),
        "file_count": len(files),
        "total_size": sum(size for _full, _parts, size in files),
        "piece_size": piece_size,
        "private": bool(private),
        "trackers": tracker_lines,
    }
