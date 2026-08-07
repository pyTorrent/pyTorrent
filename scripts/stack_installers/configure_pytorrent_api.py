#!/usr/bin/env python3
"""Configure pyTorrent through its HTTP API after rTorrent is installed."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request


def _request(base_url: str, method: str, path: str, payload: dict | None = None, token: str | None = None, timeout: int = 10) -> dict:
    url = base_url.rstrip("/") + path
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Accept": "application/json"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, method=method.upper(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return json.loads(raw or "{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"API {method} {path} failed with HTTP {exc.code}: {raw}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"API {method} {path} failed: {exc.reason}") from exc


def _wait_for_api(base_url: str, token: str | None, seconds: int) -> None:
    deadline = time.time() + seconds
    last_error = None
    while time.time() < deadline:
        try:
            _request(base_url, "GET", "/api/profiles", token=token, timeout=5)
            return
        except Exception as exc:  # noqa: BLE001 - installation helper should keep retrying.
            last_error = exc
            time.sleep(2)
    raise RuntimeError(f"pyTorrent API is not ready after {seconds}s at {base_url}: {last_error}. Check PYTORRENT_PORT in .env and systemctl status pytorrent.")


def _find_profile(profiles: list[dict], name: str, scgi_url: str) -> dict | None:
    for profile in profiles:
        if str(profile.get("name") or "") == name:
            return profile
    for profile in profiles:
        if str(profile.get("scgi_url") or "") == scgi_url:
            return profile
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Create/update and activate a pyTorrent rTorrent profile through the HTTP API.")
    parser.add_argument("--base-url", default=os.getenv("PYTORRENT_BASE_URL", "http://127.0.0.1:8090"))
    parser.add_argument("--api-token", default=os.getenv("PYTORRENT_API_TOKEN", ""), help="Bearer token when pyTorrent auth is enabled.")
    parser.add_argument("--profile-name", default=os.getenv("PYTORRENT_RTORRENT_PROFILE_NAME", "Local rTorrent"))
    parser.add_argument("--scgi-url", default=os.getenv("PYTORRENT_RTORRENT_SCGI_URL", "scgi://127.0.0.1:5000"))
    parser.add_argument("--timeout", type=int, default=int(os.getenv("PYTORRENT_RTORRENT_TIMEOUT", "10")))
    parser.add_argument("--wait", type=int, default=int(os.getenv("PYTORRENT_API_WAIT_SECONDS", "90")))
    parser.add_argument("--remote", action="store_true", default=os.getenv("PYTORRENT_RTORRENT_REMOTE", "0").lower() in {"1", "true", "yes", "on"})
    args = parser.parse_args()

    token = args.api_token.strip() or None
    _wait_for_api(args.base_url, token, args.wait)
    current = _request(args.base_url, "GET", "/api/profiles", token=token)
    profiles = current.get("profiles") or []
    payload = {
        "name": args.profile_name,
        "scgi_url": args.scgi_url,
        "is_default": True,
        "timeout_seconds": args.timeout,
        "max_parallel_jobs": 5,
        "light_parallel_jobs": 4,
        "light_job_timeout_seconds": 300,
        "heavy_job_timeout_seconds": 7200,
        "pending_job_timeout_seconds": 900,
        "is_remote": bool(args.remote),
    }
    existing = _find_profile(profiles, args.profile_name, args.scgi_url)
    if existing:
        profile_id = int(existing["id"])
        result = _request(args.base_url, "PUT", f"/api/profiles/{profile_id}", payload, token=token)
        action = "updated"
    else:
        result = _request(args.base_url, "POST", "/api/profiles", payload, token=token)
        profile_id = int((result.get("profile") or {}).get("id") or 0)
        action = "created"
    if not profile_id:
        raise RuntimeError(f"Profile {action}, but API response did not include an id: {result}")
    _request(args.base_url, "POST", f"/api/profiles/{profile_id}/activate", token=token)
    test = _request(args.base_url, "GET", f"/api/profiles/{profile_id}/diagnostics", token=token)
    print(json.dumps({"ok": True, "action": action, "profile_id": profile_id, "diagnostics": test.get("diagnostics")}, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 - user-facing installer output.
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
