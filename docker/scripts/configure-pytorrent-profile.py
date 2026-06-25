#!/usr/bin/env python3
"""Create or update the default rTorrent profile from container environment variables."""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request


def request_json(base_url: str, method: str, path: str, payload: dict | None = None, token: str | None = None, timeout: int = 10) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Accept": "application/json"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(base_url.rstrip("/") + path, data=data, headers=headers, method=method.upper())
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read().decode("utf-8", "replace")
    return json.loads(raw or "{}")


def wait_for_api(base_url: str, token: str | None, seconds: int) -> None:
    deadline = time.time() + seconds
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            request_json(base_url, "GET", "/api/profiles", token=token, timeout=5)
            return
        except Exception as exc:  # noqa: BLE001 - startup helper should retry until the app is ready.
            last_error = exc
            time.sleep(2)
    raise RuntimeError(f"pyTorrent API is not ready at {base_url}: {last_error}")


def enabled(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def main() -> int:
    if not enabled(os.getenv("PYTORRENT_CONFIGURE_PROFILE", "true")):
        return 0

    base_url = os.getenv("PYTORRENT_BASE_URL", "http://127.0.0.1:8090")
    token = os.getenv("PYTORRENT_API_TOKEN", "").strip() or None
    profile_name = os.getenv("PYTORRENT_RTORRENT_PROFILE_NAME", "Local rTorrent")
    scgi_url = os.getenv("PYTORRENT_RTORRENT_SCGI_URL", "scgi://rtorrent:5000")
    timeout_seconds = int(os.getenv("PYTORRENT_RTORRENT_TIMEOUT", "10"))
    wait_seconds = int(os.getenv("PYTORRENT_API_WAIT_SECONDS", "90"))
    remote = enabled(os.getenv("PYTORRENT_RTORRENT_REMOTE", "0"))

    wait_for_api(base_url, token, wait_seconds)
    current = request_json(base_url, "GET", "/api/profiles", token=token)
    profiles = current.get("profiles") or []
    existing = next((item for item in profiles if item.get("name") == profile_name), None)
    if existing is None:
        existing = next((item for item in profiles if item.get("scgi_url") == scgi_url), None)

    payload = {
        "name": profile_name,
        "scgi_url": scgi_url,
        "is_default": True,
        "timeout_seconds": timeout_seconds,
        "max_parallel_jobs": int(os.getenv("PYTORRENT_PROFILE_MAX_PARALLEL_JOBS", "5")),
        "light_parallel_jobs": int(os.getenv("PYTORRENT_PROFILE_LIGHT_PARALLEL_JOBS", "4")),
        "light_job_timeout_seconds": int(os.getenv("PYTORRENT_PROFILE_LIGHT_JOB_TIMEOUT_SECONDS", "300")),
        "heavy_job_timeout_seconds": int(os.getenv("PYTORRENT_PROFILE_HEAVY_JOB_TIMEOUT_SECONDS", "7200")),
        "pending_job_timeout_seconds": int(os.getenv("PYTORRENT_PROFILE_PENDING_JOB_TIMEOUT_SECONDS", "900")),
        "is_remote": remote,
    }

    if existing:
        profile_id = int(existing["id"])
        request_json(base_url, "PUT", f"/api/profiles/{profile_id}", payload, token=token)
        action = "updated"
    else:
        created = request_json(base_url, "POST", "/api/profiles", payload, token=token)
        profile_id = int((created.get("profile") or {}).get("id") or 0)
        action = "created"

    if profile_id:
        request_json(base_url, "POST", f"/api/profiles/{profile_id}/activate", token=token)
        print(f"pyTorrent profile {action}: {profile_name} -> {scgi_url}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (urllib.error.HTTPError, urllib.error.URLError, RuntimeError, ValueError) as exc:
        print(f"Profile configuration skipped: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(0)
