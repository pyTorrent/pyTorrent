#!/usr/bin/env python3
"""Read-only pyTorrent API performance probe for /api/torrents.

The script measures cache behaviour, response times, payload size and basic
response integrity without changing torrents or application state. It can also
run one optional force-refresh request when the server allows it.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any

TRUE_VALUES = {"1", "true", "yes", "on", "force"}


class ApiClient:
    def __init__(self, base_url: str, api_key: str = "", bearer: str = "", timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = float(timeout)
        self.cookie_jar = CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookie_jar))
        self.default_headers = {"Accept": "application/json", "User-Agent": "pyTorrent-api-probe/1.0"}
        if api_key:
            self.default_headers["X-API-Key"] = api_key
        if bearer:
            self.default_headers["Authorization"] = f"Bearer {bearer}"

    def login(self, username: str, password: str) -> dict[str, Any]:
        payload = json.dumps({"username": username, "password": password}).encode("utf-8")
        return self.request("POST", "/api/auth/login", body=payload, extra_headers={"Content-Type": "application/json"})["json"]

    def request(self, method: str, path: str, params: dict[str, Any] | None = None, body: bytes | None = None, extra_headers: dict[str, str] | None = None) -> dict[str, Any]:
        query = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None})
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{query}"
        headers = dict(self.default_headers)
        headers.update(extra_headers or {})
        request = urllib.request.Request(url, data=body, headers=headers, method=method.upper())
        started = time.perf_counter()
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read()
                elapsed_ms = (time.perf_counter() - started) * 1000.0
                text = raw.decode("utf-8", errors="replace")
                try:
                    data = json.loads(text) if text else None
                except json.JSONDecodeError:
                    data = None
                return {
                    "status": response.status,
                    "elapsed_ms": elapsed_ms,
                    "bytes": len(raw),
                    "headers": dict(response.headers),
                    "json": data,
                    "text": text[:1000],
                    "url": url,
                }
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            text = raw.decode("utf-8", errors="replace")
            try:
                data = json.loads(text) if text else None
            except json.JSONDecodeError:
                data = None
            return {
                "status": exc.code,
                "elapsed_ms": elapsed_ms,
                "bytes": len(raw),
                "headers": dict(exc.headers),
                "json": data,
                "text": text[:1000],
                "url": url,
                "error": str(exc),
            }


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * pct
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return ordered[low]
    return ordered[low] * (high - rank) + ordered[high] * (rank - low)


def torrent_digest(torrents: list[dict[str, Any]]) -> dict[str, Any]:
    hashes = [str(item.get("hash") or "") for item in torrents if item.get("hash")]
    names = [str(item.get("name") or "") for item in torrents if item.get("name")]
    up_rate = sum(int(item.get("up_rate") or 0) for item in torrents)
    down_rate = sum(int(item.get("down_rate") or 0) for item in torrents)
    active = sum(1 for item in torrents if item.get("active"))
    complete = sum(1 for item in torrents if item.get("complete"))
    return {
        "count": len(torrents),
        "hash_count": len(hashes),
        "unique_hash_count": len(set(hashes)),
        "duplicate_hash_count": max(0, len(hashes) - len(set(hashes))),
        "named_count": len(names),
        "active_count": active,
        "complete_count": complete,
        "up_rate": up_rate,
        "down_rate": down_rate,
        "hashes_sample": hashes[:5],
    }


def validate_torrents_payload(data: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["response JSON is not an object"]
    if data.get("ok") is not True:
        errors.append("ok is not true")
    torrents = data.get("torrents")
    if not isinstance(torrents, list):
        errors.append("torrents is not a list")
        torrents = []
    if "summary" not in data or not isinstance(data.get("summary"), dict):
        errors.append("summary is missing or is not an object")
    if "cache_age_seconds" not in data:
        errors.append("cache_age_seconds is missing")
    if "refresh" not in data:
        errors.append("refresh metadata is missing")
    required_torrent_keys = {"hash", "name"}
    for index, item in enumerate(torrents[:50]):
        if not isinstance(item, dict):
            errors.append(f"torrent[{index}] is not an object")
            continue
        missing = sorted(key for key in required_torrent_keys if key not in item)
        if missing:
            errors.append(f"torrent[{index}] missing keys: {', '.join(missing)}")
    digest = torrent_digest(torrents)
    if digest["duplicate_hash_count"]:
        errors.append(f"duplicate torrent hashes detected: {digest['duplicate_hash_count']}")
    return errors


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    times = [float(row["elapsed_ms"]) for row in results]
    bytes_values = [int(row["bytes"]) for row in results]
    skipped = [row for row in results if row.get("refresh_skipped") is True]
    refreshed = [row for row in results if row.get("refresh_ok") is True and row.get("refresh_skipped") is False]
    return {
        "requests": len(results),
        "http_statuses": sorted(set(row["status"] for row in results)),
        "ok_responses": sum(1 for row in results if row.get("ok") is True),
        "errors": sum(len(row.get("validation_errors") or []) for row in results),
        "avg_ms": statistics.mean(times) if times else None,
        "median_ms": statistics.median(times) if times else None,
        "p95_ms": percentile(times, 0.95),
        "min_ms": min(times) if times else None,
        "max_ms": max(times) if times else None,
        "avg_payload_bytes": statistics.mean(bytes_values) if bytes_values else None,
        "max_payload_bytes": max(bytes_values) if bytes_values else None,
        "cache_skipped_responses": len(skipped),
        "refresh_responses": len(refreshed),
        "last_cache_age_seconds": results[-1].get("cache_age_seconds") if results else None,
        "last_torrent_count": results[-1].get("torrent_count") if results else None,
        "last_unique_hash_count": results[-1].get("unique_hash_count") if results else None,
    }


def write_outputs(output_dir: Path, rows: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "api_torrents_probe.json"
    csv_path = output_dir / "api_torrents_probe.csv"
    json_path.write_text(json.dumps({"summary": summary, "requests": rows}, indent=2, ensure_ascii=False), encoding="utf-8")
    fieldnames = [
        "index", "mode", "status", "ok", "elapsed_ms", "bytes", "torrent_count", "unique_hash_count",
        "duplicate_hash_count", "active_count", "complete_count", "up_rate", "down_rate", "cache_age_seconds",
        "refresh_ok", "refresh_skipped", "refresh_age_seconds", "refresh_added", "refresh_updated", "refresh_removed",
        "validation_error_count", "url",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fieldnames})
    print(f"Wrote JSON report: {json_path}")
    print(f"Wrote CSV report:  {csv_path}")


def print_summary(summary: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    print("\n=== /api/torrents read-only probe ===")
    print(f"Requests:           {summary['requests']}")
    print(f"HTTP statuses:      {summary['http_statuses']}")
    print(f"OK responses:       {summary['ok_responses']}")
    print(f"Validation errors:  {summary['errors']}")
    print(f"Avg / median / p95: {summary['avg_ms']:.1f} ms / {summary['median_ms']:.1f} ms / {summary['p95_ms']:.1f} ms")
    print(f"Min / max:          {summary['min_ms']:.1f} ms / {summary['max_ms']:.1f} ms")
    print(f"Avg payload:        {summary['avg_payload_bytes']:.0f} bytes")
    print(f"Cache skipped:      {summary['cache_skipped_responses']}")
    print(f"Refresh responses:  {summary['refresh_responses']}")
    print(f"Last cache age:     {summary['last_cache_age_seconds']}")
    print(f"Last torrents:      {summary['last_torrent_count']} total, {summary['last_unique_hash_count']} unique hashes")
    problem_rows = [row for row in rows if row.get("validation_errors")]
    if problem_rows:
        print("\nValidation problems:")
        for row in problem_rows[:10]:
            print(f"- request #{row['index']} ({row['mode']}): {'; '.join(row['validation_errors'])}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only performance and cache probe for pyTorrent /api/torrents.")
    parser.add_argument("--base-url", default=os.getenv("PYTORRENT_BASE_URL", "http://127.0.0.1:5000"), help="pyTorrent base URL")
    parser.add_argument("--api-key", default=os.getenv("PYTORRENT_API_KEY", ""), help="API token for X-API-Key")
    parser.add_argument("--bearer", default=os.getenv("PYTORRENT_BEARER_TOKEN", ""), help="API token for Authorization: Bearer")
    parser.add_argument("--username", default=os.getenv("PYTORRENT_USERNAME", ""), help="Optional session login username")
    parser.add_argument("--password", default=os.getenv("PYTORRENT_PASSWORD", ""), help="Optional session login password")
    parser.add_argument("--profile-id", type=int, default=int(os.getenv("PYTORRENT_PROFILE_ID", "0") or 0), help="Optional profile_id")
    parser.add_argument("--profile-name", default=os.getenv("PYTORRENT_PROFILE_NAME", ""), help="Optional profile_name")
    parser.add_argument("--requests", type=int, default=10, help="Number of cache-first requests")
    parser.add_argument("--delay", type=float, default=1.0, help="Delay between requests in seconds")
    parser.add_argument("--timeout", type=float, default=30.0, help="HTTP timeout in seconds")
    parser.add_argument("--force-refresh", action="store_true", help="Run one extra /api/torrents?refresh=1 request")
    parser.add_argument("--output-dir", default="api-tests/results", help="Directory for JSON and CSV reports")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    client = ApiClient(args.base_url, api_key=args.api_key, bearer=args.bearer, timeout=args.timeout)
    if args.username or args.password:
        if not (args.username and args.password):
            print("Both --username and --password are required for session login.", file=sys.stderr)
            return 2
        login = client.login(args.username, args.password)
        if not isinstance(login, dict) or not login.get("ok"):
            print(f"Login failed: {login}", file=sys.stderr)
            return 2

    base_params: dict[str, Any] = {}
    if args.profile_id:
        base_params["profile_id"] = args.profile_id
    if args.profile_name:
        base_params["profile_name"] = args.profile_name

    rows: list[dict[str, Any]] = []
    modes = ["cache-first"] * max(0, int(args.requests))
    if args.force_refresh:
        modes.append("force-refresh")

    for index, mode in enumerate(modes, start=1):
        params = dict(base_params)
        if mode == "force-refresh":
            params["refresh"] = 1
        response = client.request("GET", "/api/torrents", params=params)
        data = response.get("json")
        errors = validate_torrents_payload(data)
        torrents = data.get("torrents") if isinstance(data, dict) else []
        digest = torrent_digest(torrents if isinstance(torrents, list) else [])
        refresh = data.get("refresh") if isinstance(data, dict) and isinstance(data.get("refresh"), dict) else {}
        row = {
            "index": index,
            "mode": mode,
            "url": response.get("url"),
            "status": response.get("status"),
            "ok": data.get("ok") if isinstance(data, dict) else False,
            "elapsed_ms": round(float(response.get("elapsed_ms") or 0), 3),
            "bytes": response.get("bytes"),
            "cache_age_seconds": data.get("cache_age_seconds") if isinstance(data, dict) else None,
            "refresh_ok": refresh.get("ok"),
            "refresh_skipped": refresh.get("skipped"),
            "refresh_age_seconds": refresh.get("age_seconds"),
            "refresh_added": refresh.get("added"),
            "refresh_updated": refresh.get("updated"),
            "refresh_removed": refresh.get("removed"),
            "validation_errors": errors,
            "validation_error_count": len(errors),
            **digest,
        }
        rows.append(row)
        print(
            f"#{index:02d} {mode:13s} {row['status']} {row['elapsed_ms']:8.1f} ms "
            f"{row['bytes']:9} B torrents={row['torrent_count']} cache_age={row['cache_age_seconds']} "
            f"refresh_skipped={row['refresh_skipped']} errors={len(errors)}"
        )
        if index < len(modes) and args.delay > 0:
            time.sleep(args.delay)

    summary = summarize(rows)
    print_summary(summary, rows)
    write_outputs(Path(args.output_dir), rows, summary)
    return 1 if summary["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
