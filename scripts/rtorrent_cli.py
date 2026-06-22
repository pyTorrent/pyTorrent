#!/usr/bin/env python3
"""
rtorrent_cli.py - simple CLI for bulk rTorrent management over XML-RPC/SCGI.

Default endpoint:
  scgi://127.0.0.1:5000

Examples:
  python3 rtorrent_cli.py ping
  python3 rtorrent_cli.py list
  python3 rtorrent_cli.py list --only-stopped --only-complete
  python3 rtorrent_cli.py show HASH
  python3 rtorrent_cli.py start HASH
  python3 rtorrent_cli.py bulk-start --only-stopped --only-complete
  python3 rtorrent_cli.py bulk-stop --name-regex "ubuntu|debian"
  python3 rtorrent_cli.py bulk-announce --only-active
  python3 rtorrent_cli.py bulk-check-hash --only-stopped --name-regex "movie"
  python3 rtorrent_cli.py dump-methods
"""

from __future__ import annotations

import argparse
import json
import re
import socket
import sys
import xmlrpc.client
from dataclasses import dataclass, asdict
from typing import Any, Iterable
from urllib.parse import urlparse


DEFAULT_URL = "scgi://127.0.0.1:5000"


# ----------------------------
# SCGI XML-RPC transport
# ----------------------------

class SCGITransport(xmlrpc.client.Transport):
    def __init__(self, host: str, port: int, timeout: int = 15):
        super().__init__()
        self.host = host
        self.port = port
        self.timeout = timeout

    def request(self, host: str, handler: str, request_body: bytes, verbose: bool = False):
        body = request_body.encode("utf-8") if isinstance(request_body, str) else request_body

        headers = {
            "CONTENT_LENGTH": str(len(body)),
            "SCGI": "1",
            "REQUEST_METHOD": "POST",
            "REQUEST_URI": handler or "/RPC2",
        }

        header_bytes = b""
        for key, value in headers.items():
            header_bytes += key.encode("utf-8") + b"\x00" + value.encode("utf-8") + b"\x00"

        packet = str(len(header_bytes)).encode("ascii") + b":" + header_bytes + b"," + body

        with socket.create_connection((self.host, self.port), timeout=self.timeout) as sock:
            sock.sendall(packet)
            response = b""
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                response += chunk

        # rTorrent over SCGI usually returns raw XML body,
        # but some proxies may prepend HTTP headers.
        if b"\r\n\r\n" in response:
            response = response.split(b"\r\n\r\n", 1)[1]

        return self.parse_response_bytes(response)

    def parse_response_bytes(self, data: bytes):
        p, u = self.getparser()
        p.feed(data)
        p.close()
        return u.close()


def make_rpc_client(url: str, timeout: int):
    parsed = urlparse(url)

    if parsed.scheme == "scgi":
        if not parsed.hostname:
            raise ValueError("SCGI URL must include a host, e.g. scgi://127.0.0.1:5000")
        transport = SCGITransport(parsed.hostname, parsed.port or 5000, timeout=timeout)
        return xmlrpc.client.ServerProxy(
            "http://rtorrent/RPC2",
            transport=transport,
            allow_none=True,
        )

    return xmlrpc.client.ServerProxy(url, allow_none=True)


# ----------------------------
# Helpers
# ----------------------------

@dataclass
class Torrent:
    hash: str
    name: str
    state: int
    active: int
    complete: int
    size_bytes: int
    completed_bytes: int
    ratio: int
    down_rate: int
    up_rate: int
    message: str

    @property
    def stopped(self) -> bool:
        return self.state == 0

    @property
    def started_or_paused(self) -> bool:
        return self.state == 1

    @property
    def is_active(self) -> bool:
        return self.active == 1

    @property
    def is_complete(self) -> bool:
        return self.complete == 1


def rpc_error(exc: Exception, context: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {
        "ok": False,
        "error_type": exc.__class__.__name__,
        "error": str(exc),
    }
    if context:
        payload["context"] = context
    return payload


def print_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False))


def call_method(rpc, method: str, *args):
    return getattr(rpc, method)(*args)


def safe_call(rpc, method: str, *args, context: dict[str, Any] | None = None):
    try:
        return True, call_method(rpc, method, *args)
    except Exception as exc:
        return False, rpc_error(exc, context=context or {"method": method, "args": args})


def human_bytes(num: int) -> str:
    value = float(num)
    for unit in ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]:
        if abs(value) < 1024:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} EiB"


# ----------------------------
# rTorrent API
# ----------------------------

def get_torrent_hashes(rpc, view: str = "main") -> list[str]:
    ok, result = safe_call(rpc, "d.multicall2", "", view, "d.hash=")
    if not ok:
        raise RuntimeError(json.dumps(result, ensure_ascii=False))

    hashes: list[str] = []
    for row in result:
        if isinstance(row, list) and row:
            hashes.append(str(row[0]))
        elif isinstance(row, str):
            hashes.append(row)
    return hashes


def list_torrents(rpc, view: str = "main") -> list[Torrent]:
    methods = [
        "d.hash=",
        "d.name=",
        "d.state=",
        "d.is_active=",
        "d.complete=",
        "d.size_bytes=",
        "d.completed_bytes=",
        "d.ratio=",
        "d.down.rate=",
        "d.up.rate=",
        "d.message=",
    ]

    ok, result = safe_call(rpc, "d.multicall2", "", view, *methods)
    if not ok:
        raise RuntimeError(json.dumps(result, ensure_ascii=False))

    torrents: list[Torrent] = []
    for row in result:
        torrents.append(Torrent(
            hash=str(row[0]),
            name=str(row[1]),
            state=int(row[2]),
            active=int(row[3]),
            complete=int(row[4]),
            size_bytes=int(row[5]),
            completed_bytes=int(row[6]),
            ratio=int(row[7]),
            down_rate=int(row[8]),
            up_rate=int(row[9]),
            message=str(row[10]),
        ))
    return torrents


def get_torrent(rpc, hash_: str) -> Torrent:
    torrents = list_torrents(rpc)
    for torrent in torrents:
        if torrent.hash.lower() == hash_.lower():
            return torrent
    raise KeyError(f"Torrent not found: {hash_}")


def filter_torrents(torrents: Iterable[Torrent], args) -> list[Torrent]:
    result = list(torrents)

    if getattr(args, "only_stopped", False):
        result = [t for t in result if t.stopped]

    if getattr(args, "only_started", False):
        result = [t for t in result if t.started_or_paused]

    if getattr(args, "only_active", False):
        result = [t for t in result if t.is_active]

    if getattr(args, "only_complete", False):
        result = [t for t in result if t.is_complete]

    if getattr(args, "only_incomplete", False):
        result = [t for t in result if not t.is_complete]

    if getattr(args, "name_regex", None):
        pattern = re.compile(args.name_regex, re.IGNORECASE)
        result = [t for t in result if pattern.search(t.name)]

    if getattr(args, "hash_regex", None):
        pattern = re.compile(args.hash_regex, re.IGNORECASE)
        result = [t for t in result if pattern.search(t.hash)]

    return result


def torrent_to_dict(t: Torrent) -> dict[str, Any]:
    data = asdict(t)
    data["size"] = human_bytes(t.size_bytes)
    data["completed"] = human_bytes(t.completed_bytes)
    data["ratio_float"] = round(t.ratio / 1000, 3)
    return data


# ----------------------------
# Commands
# ----------------------------

def cmd_ping(rpc, args) -> int:
    ok, result = safe_call(rpc, "system.client_version")
    if ok:
        print_json({"ok": True, "client_version": result})
        return 0

    # fallback for older builds
    ok, result = safe_call(rpc, "system.listMethods")
    print_json({"ok": ok, "result": result})
    return 0 if ok else 1