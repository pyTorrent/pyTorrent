from __future__ import annotations
import math
import re
from .client import *
from .files import set_file_priorities


_HEX_RE = re.compile(r"[0-9a-fA-F]")


def _clean_hex_bitfield(value) -> str:
    """Return only hexadecimal bitfield characters from rTorrent output."""
    return "".join(_HEX_RE.findall(str(value or ""))).lower()


def _hex_to_bits(value: str, limit: int | None = None) -> list[int]:
    """Decode an rTorrent hex bitfield into one bit per torrent piece."""
    bits: list[int] = []
    for char in _clean_hex_bitfield(value):
        nibble = int(char, 16)
        bits.extend([
            1 if nibble & 0b1000 else 0,
            1 if nibble & 0b0100 else 0,
            1 if nibble & 0b0010 else 0,
            1 if nibble & 0b0001 else 0,
        ])
    if limit is not None and limit >= 0:
        if len(bits) < limit:
            bits.extend([0] * (limit - len(bits)))
        return bits[:limit]
    return bits


def _chunk_status(completed: int, total: int, seen: bool = False) -> str:
    """Classify a visual chunk cell for CSS and filtering."""
    if total <= 0:
        return "missing"
    if completed >= total:
        return "complete"
    if completed <= 0:
        return "seen" if seen else "missing"
    return "partial"


def _group_cells(cells: list[dict], max_cells: int) -> list[dict]:
    """Reduce very large torrents to a browser-friendly number of visual cells."""
    if max_cells <= 0 or len(cells) <= max_cells:
        return cells
    grouped: list[dict] = []
    scale = len(cells) / float(max_cells)
    for out_idx in range(max_cells):
        start = int(math.floor(out_idx * scale))
        end = int(math.floor((out_idx + 1) * scale))
        part = cells[start:max(end, start + 1)]
        if not part:
            continue
        completed = sum(int(c.get("completed") or 0) for c in part)
        total = sum(int(c.get("total") or 0) for c in part)
        seen = any(bool(c.get("seen")) for c in part)
        percent = round((completed / total) * 100.0, 2) if total > 0 else 0.0
        grouped.append({
            "index": out_idx,
            "first_chunk": int(part[0].get("first_chunk", 0)),
            "last_chunk": int(part[-1].get("last_chunk", 0)),
            "completed": completed,
            "total": total,
            "percent": percent,
            "seen": seen,
            "status": _chunk_status(completed, total, seen),
            "grouped": True,
            "unit_count": len(part),
        })
    return grouped


def _build_piece_cells(total_chunks: int, have_bits: list[int], seen_bits: list[int]) -> list[dict]:
    """Create one raw cell per real torrent piece."""
    cells: list[dict] = []
    for idx in range(max(0, int(total_chunks or 0))):
        completed = 1 if idx < len(have_bits) and have_bits[idx] else 0
        seen = idx < len(seen_bits) and bool(seen_bits[idx])
        cells.append({
            "index": idx,
            "first_chunk": idx,
            "last_chunk": idx,
            "completed": completed,
            "total": 1,
            "percent": 100.0 if completed else 0.0,
            "seen": seen,
            "status": _chunk_status(completed, 1, seen),
            "grouped": False,
            "unit_count": 1,
        })
    return cells


def torrent_chunks(profile: dict, torrent_hash: str, max_cells: int = 2048) -> dict:
    """Return ruTorrent-like visual chunk data for one torrent."""
    c = client_for(profile)
    values = {
        "bitfield": _clean_hex_bitfield(c.call("d.bitfield", torrent_hash)),
        "seen": "",
        "chunk_size": 0,
        "size_chunks": 0,
        "completed_chunks": 0,
        "chunks_hashed": 0,
    }
    optional_calls = {
        "seen": "d.chunks_seen",
        "chunk_size": "d.chunk_size",
        "size_chunks": "d.size_chunks",
        "completed_chunks": "d.completed_chunks",
        "chunks_hashed": "d.chunks_hashed",
    }
    for key, method in optional_calls.items():
        try:
            raw = c.call(method, torrent_hash)
            values[key] = _clean_hex_bitfield(raw) if key == "seen" else int(raw or 0)
        except Exception:
            values[key] = "" if key == "seen" else 0

    total_chunks = int(values["size_chunks"] or 0)
    completed = int(values["completed_chunks"] or 0)
    if total_chunks <= 0:
        total_chunks = max(completed, len(values["bitfield"]) * 4)

    have_bits = _hex_to_bits(values["bitfield"], total_chunks)
    seen_bits = _hex_to_bits(values["seen"], total_chunks)
    cells = _build_piece_cells(total_chunks, have_bits, seen_bits)

    visual_cells = _group_cells(cells, max(64, min(10000, int(max_cells or 2048))))
    return {
        "hash": torrent_hash,
        "chunk_size": int(values["chunk_size"] or 0),
        "chunk_size_h": human_size(values["chunk_size"] or 0),
        "size_chunks": total_chunks,
        "completed_chunks": completed,
        "chunks_hashed": int(values["chunks_hashed"] or 0),
        "bitfield_units": len(have_bits),
        "visual_cells": len(visual_cells),
        "grouped": len(visual_cells) != len(cells),
        "cells": visual_cells,
        "summary": {
            "complete": sum(1 for c in visual_cells if c.get("status") == "complete"),
            "partial": sum(1 for c in visual_cells if c.get("status") == "partial"),
            "missing": sum(1 for c in visual_cells if c.get("status") == "missing"),
            "seen": sum(1 for c in visual_cells if c.get("status") == "seen"),
        },
    }


def _files_touching_chunks(c: ScgiRtorrentClient, torrent_hash: str, first_chunk: int, last_chunk: int) -> list[dict]:
    """Find files whose rTorrent chunk range overlaps the selected visual cells."""
    # Note: rTorrent exposes file chunk coverage through f.range_first and f.range_second; the second value is exclusive.
    rows = c.f.multicall(torrent_hash, "", "f.path=", "f.range_first=", "f.range_second=", "f.priority=")
    matches = []
    for idx, row in enumerate(rows):
        start = int(row[1] or 0)
        end_exclusive = int(row[2] or 0)
        end = max(start, end_exclusive - 1)
        if start <= last_chunk and end >= first_chunk:
            matches.append({
                "index": idx,
                "path": str(row[0] or ""),
                "range_first": start,
                "range_second": end_exclusive,
                "priority": int(row[3] or 0),
            })
    return matches


def torrent_chunk_action(profile: dict, torrent_hash: str, action: str, payload: dict | None = None) -> dict:
    """Run safe actions related to visual chunk selection."""
    payload = payload or {}
    action = str(action or "").strip().lower()
    c = client_for(profile)
    if action == "recheck":
        c.call("d.check_hash", torrent_hash)
        return {"action": action, "message": "Torrent hash check queued", "scope": "torrent"}
    if action == "prioritize_files":
        first_chunk = max(0, int(payload.get("first_chunk") or 0))
        last_chunk = max(first_chunk, int(payload.get("last_chunk") if payload.get("last_chunk") is not None else first_chunk))
        priority = max(0, min(3, int(payload.get("priority") or 2)))
        matches = _files_touching_chunks(c, torrent_hash, first_chunk, last_chunk)
        if not matches:
            return {"action": action, "updated": [], "errors": [{"error": "No files overlap selected chunk range"}]}
        result = set_file_priorities(profile, torrent_hash, [{"index": m["index"], "priority": priority} for m in matches])
        try:
            c.call("d.update_priorities", torrent_hash)
        except Exception:
            pass
        result.update({"action": action, "files": matches, "priority": priority, "first_chunk": first_chunk, "last_chunk": last_chunk})
        return result
    raise ValueError("Unknown chunk action")


__all__ = [
    name for name in globals()
    if not name.startswith("__") and name not in {"annotations"}
]
