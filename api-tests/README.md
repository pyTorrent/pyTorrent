# API tests

Read-only helper scripts for checking pyTorrent API cache behaviour and response performance.

## `/api/torrents` probe

Run from the project root while pyTorrent is running:

```bash
python3 api-tests/test_torrents_api.py --base-url http://127.0.0.1:5000 --requests 20 --delay 1
```

With an API token:

```bash
PYTORRENT_API_KEY="your-token" python3 api-tests/test_torrents_api.py --base-url https://your-host --requests 20
```

With session login:

```bash
python3 api-tests/test_torrents_api.py --base-url https://your-host --username admin --password 'secret'
```

For a selected rTorrent profile:

```bash
python3 api-tests/test_torrents_api.py --profile-id 1
python3 api-tests/test_torrents_api.py --profile-name main
```

To verify the explicit refresh path without writing to rTorrent:

```bash
python3 api-tests/test_torrents_api.py --requests 5 --force-refresh
```

The script writes:

- `api-tests/results/api_torrents_probe.json`
- `api-tests/results/api_torrents_probe.csv`

The probe checks:

- HTTP status and JSON shape.
- Torrent count and duplicate hash count.
- Response time: average, median, p95, min and max.
- Response payload size.
- `cache_age_seconds` returned by the API.
- `refresh` metadata: `ok`, `skipped`, `age_seconds`, `added`, `updated`, `removed`.
- Whether normal calls are served cache-first and whether `?refresh=1` is respected when enabled in Poller settings.

All requests are read-only. The script only calls `GET /api/torrents` and optionally `POST /api/auth/login` for a session cookie.
