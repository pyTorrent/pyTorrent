from __future__ import annotations
import hashlib
from pathlib import PurePosixPath
from typing import Any


class BencodeError(ValueError):
    pass


class BencodeReader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def parse(self) -> Any:
        value = self._read_value()
        if self.pos != len(self.data):
            raise BencodeError("Trailing data in torrent file")
        return value

    def _read_value(self) -> Any:
        if self.pos >= len(self.data):
            raise BencodeError("Unexpected end of bencoded data")
        token = self.data[self.pos:self.pos + 1]
        if token == b"i":
            return self._read_int()
        if token == b"l":
            return self._read_list()
        if token == b"d":
            return self._read_dict()
        if b"0" <= token <= b"9":
            return self._read_bytes()
        raise BencodeError(f"Invalid bencode token at offset {self.pos}")

    def _read_int(self) -> int:
        self.pos += 1
        end = self.data.find(b"e", self.pos)
        if end < 0:
            raise BencodeError("Unterminated integer")
        raw = self.data[self.pos:end]
        self.pos = end + 1
        return int(raw)

    def _read_bytes(self) -> bytes:
        colon = self.data.find(b":", self.pos)
        if colon < 0:
            raise BencodeError("Invalid byte string length")
        length = int(self.data[self.pos:colon])
        self.pos = colon + 1
        end = self.pos + length
        if end > len(self.data):
            raise BencodeError("Byte string exceeds input size")
        value = self.data[self.pos:end]
        self.pos = end
        return value

    def _read_list(self) -> list[Any]:
        self.pos += 1
        out: list[Any] = []
        while self.pos < len(self.data) and self.data[self.pos:self.pos + 1] != b"e":
            out.append(self._read_value())
        if self.pos >= len(self.data):
            raise BencodeError("Unterminated list")
        self.pos += 1
        return out

    def _read_dict(self) -> dict[bytes, Any]:
        self.pos += 1
        out: dict[bytes, Any] = {}
        while self.pos < len(self.data) and self.data[self.pos:self.pos + 1] != b"e":
            key = self._read_bytes()
            out[key] = self._read_value()
        if self.pos >= len(self.data):
            raise BencodeError("Unterminated dictionary")
        self.pos += 1
        return out


def bencode(value: Any) -> bytes:
    if isinstance(value, int):
        return b"i" + str(value).encode("ascii") + b"e"
    if isinstance(value, bytes):
        return str(len(value)).encode("ascii") + b":" + value
    if isinstance(value, str):
        raw = value.encode("utf-8")
        return str(len(raw)).encode("ascii") + b":" + raw
    if isinstance(value, list):
        return b"l" + b"".join(bencode(item) for item in value) + b"e"
    if isinstance(value, dict):
        items = sorted(value.items(), key=lambda item: item[0] if isinstance(item[0], bytes) else str(item[0]).encode("utf-8"))
        raw = []
        for key, item in items:
            raw.append(bencode(key if isinstance(key, bytes) else str(key)))
            raw.append(bencode(item))
        return b"d" + b"".join(raw) + b"e"
    raise TypeError(f"Unsupported bencode type: {type(value)!r}")


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value or "")


def parse_torrent(data: bytes) -> dict:
    # Note: The parser is dependency-free so .torrent preview works in offline installations.
    root = BencodeReader(data).parse()
    if not isinstance(root, dict) or b"info" not in root:
        raise BencodeError("Missing torrent info dictionary")
    info = root[b"info"]
    if not isinstance(info, dict):
        raise BencodeError("Invalid torrent info dictionary")
    info_hash = hashlib.sha1(bencode(info)).hexdigest().upper()
    name = _text(info.get(b"name") or "")
    piece_length = int(info.get(b"piece length") or 0)
    private = int(info.get(b"private") or 0)
    files: list[dict] = []
    total = 0
    if b"files" in info:
        for entry in info.get(b"files") or []:
            if not isinstance(entry, dict):
                continue
            length = int(entry.get(b"length") or 0)
            path_parts = [_text(part) for part in entry.get(b"path") or []]
            rel_path = str(PurePosixPath(name, *path_parts)) if path_parts else name
            total += length
            files.append({"path": rel_path, "size": length})
    else:
        length = int(info.get(b"length") or 0)
        total = length
        files.append({"path": name, "size": length})
    announce = _text(root.get(b"announce") or "")
    trackers = [announce] if announce else []
    for tier in root.get(b"announce-list") or []:
        for tracker in tier if isinstance(tier, list) else [tier]:
            value = _text(tracker)
            if value and value not in trackers:
                trackers.append(value)
    return {
        "name": name,
        "info_hash": info_hash,
        "size": total,
        "file_count": len(files),
        "files": files,
        "trackers": trackers,
        "piece_length": piece_length,
        "private": private,
    }
