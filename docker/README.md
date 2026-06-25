# pyTorrent Docker and Podman

This directory contains isolated container files. Existing application files and existing Linux installers are not required to change for this setup.

## What was added

| Item | Note |
| --- | --- |
| `Dockerfile.pytorrent` | Builds pyTorrent only. Python 3.14 Alpine is the default base image; Python 3.14 Debian slim can be selected with a build argument. |
| `Dockerfile.rtorrent` | Builds rTorrent for the full stack from the selected distribution package only. Debian 13 slim is the default; Alpine can be selected with a build argument. |
| `docker-compose.pytorrent.yml` | Runs only pyTorrent and connects it to an existing rTorrent SCGI endpoint. |
| `docker-compose.stack.yml` | Runs pyTorrent and rTorrent on a private Docker network with torrent ports published to the host. |
| `.env.pytorrent.example` | Example environment for the pyTorrent-only mode. |
| `.env.stack.example` | Example environment for the full rTorrent + pyTorrent mode. |
| `scripts/docker-up.sh` | Small helper for one-command local startup. |
| `scripts/configure-pytorrent-profile.py` | Creates or updates the default rTorrent profile from environment variables after pyTorrent starts. |
| `rtorrent/rtorrent.rc.template` | Generates rTorrent config from `RTORRENT_*` variables. |


## Podman-ready image names

| Item | Note |
| --- | --- |
| `PYTORRENT_IMAGE=localhost/pytorrent:local` | Keeps the pyTorrent image local and avoids Podman short-name registry lookup. |
| `RTORRENT_IMAGE=localhost/pytorrent-rtorrent:local` | Keeps the rTorrent image local and lets copied `.env.stack.example` start without manual image edits. |

The example `.env` files are ready to copy. For the full stack, `cp .env.stack.example .env` is enough before running compose.

## Recommended paths

### 1. pyTorrent only

Use this when rTorrent already runs on the host or another server.

```bash
cd docker
cp .env.pytorrent.example .env
${EDITOR:-vi} .env

docker compose --env-file .env -f docker-compose.pytorrent.yml up -d --build
```

One-liner:

```bash
cd docker && cp -n .env.pytorrent.example .env && docker compose --env-file .env -f docker-compose.pytorrent.yml up -d --build
```

Podman:

```bash
cd docker
cp -n .env.pytorrent.example .env
podman-compose -f docker-compose.pytorrent.yml up -d --build
```

Important variables:

| Variable | Default | Note |
| --- | --- | --- |
| `PYTORRENT_BASE_IMAGE` | `python:3.14-alpine` | Use `python:3.14-slim` for Debian slim. |
| `PYTORRENT_HTTP_PORT` | `8090` | Host HTTP port. |
| `PYTORRENT_RTORRENT_SCGI_URL` | `scgi://host.docker.internal:5000` | Existing rTorrent SCGI endpoint. |
| `PYTORRENT_CONFIGURE_PROFILE` | `true` | Creates or updates the default profile automatically. |
| `PYTORRENT_AUTH_ENABLE` | `false` | Enable only after setting a real secret and auth settings. |

### 2. Full stack: pyTorrent + rTorrent

Use this when containers should run both services.

```bash
cd docker
cp .env.stack.example .env
${EDITOR:-vi} .env

docker compose --env-file .env -f docker-compose.stack.yml up -d --build
```

One-liner:

```bash
cd docker && cp -n .env.stack.example .env && docker compose --env-file .env -f docker-compose.stack.yml up -d --build
```

Podman:

```bash
cd docker
cp -n .env.stack.example .env
podman-compose -f docker-compose.stack.yml up -d --build
```

Default full-stack behavior:

| Component | Default | Note |
| --- | --- | --- |
| pyTorrent | Alpine Python image | Lightweight app image. |
| rTorrent | Debian package | Uses the selected distribution package. No source compilation. |
| SCGI | `scgi://rtorrent:5000` | Private Docker network only. |
| BitTorrent port | `51300/tcp` and `51300/udp` | Published to the host. |
| Downloads | `./data/downloads` | Host bind mount. |
| Watch dir | `./data/watch` | Drop `.torrent` files here. |

## rTorrent image variants

Only distribution packages are used. Source compilation is intentionally not included.

### Default Debian package

```env
RTORRENT_BASE_IMAGE=debian:13-slim
RTORRENT_TERM=xterm
```

Note: this is the default path. It installs the rTorrent package from the selected Debian repository.

### Alpine package

```env
RTORRENT_BASE_IMAGE=alpine:3.24
RTORRENT_TERM=xterm
```

Note: this installs the rTorrent package from the selected Alpine repository.

## Offline frontend assets

pyTorrent validates local frontend assets at startup when `PYTORRENT_USE_OFFLINE_LIBS=true`. The Docker image runs:

```bash
python scripts/download_frontend_libs.py
```

during build, matching the behavior of the Linux installers. If those files are missing in an old image, rebuild with `--no-cache`.

## Network layout

`docker-compose.stack.yml` creates one bridge network:

```text
browser -> host:8090 -> pytorrent:8090
pytorrent -> rtorrent:5000
internet peers -> host:51300/tcp+udp -> rtorrent:51300
```

SCGI is not published to the host in the full stack compose file. Keep it private unless you explicitly know why it must be exposed.

## rTorrent environment variables

| Variable | Default | Note |
| --- | --- | --- |
| `RTORRENT_SCGI_HOST` | `0.0.0.0` | Listen address inside the container. |
| `RTORRENT_SCGI_PORT` | `5000` | Internal SCGI port used by pyTorrent. |
| `RTORRENT_TORRENT_PORT` | `51300` | Incoming peer port. |
| `RTORRENT_DHT_PORT` | `6881` | DHT UDP port in rTorrent config. |
| `RTORRENT_MIN_PEERS` | `40` | Minimum peer count. |
| `RTORRENT_MAX_PEERS` | `200` | Maximum peer count. |
| `RTORRENT_MAX_UPLOADS` | `50` | Global upload slots. |
| `RTORRENT_DOWNLOAD_RATE` | `0` | Download limit in KiB/s. `0` means unlimited. |
| `RTORRENT_UPLOAD_RATE` | `0` | Upload limit in KiB/s. `0` means unlimited. |
| `RTORRENT_DHT` | `auto` | DHT mode. |
| `RTORRENT_PEER_EXCHANGE` | `yes` | Peer exchange setting. |
| `RTORRENT_FORCE_CONFIG` | `false` | Regenerate `/config/rtorrent.rc` on container start. |
| `RTORRENT_EXTRA_CONFIG` | empty | Extra raw rTorrent config appended to the generated file. |

## pyTorrent environment variables

| Variable | Default | Note |
| --- | --- | --- |
| `PYTORRENT_DB_PATH` | `/data/pytorrent.sqlite3` | Persistent SQLite database path. |
| `PYTORRENT_LOG_DIR` | `/data/logs` | Persistent log path. |
| `PYTORRENT_RTORRENT_PROFILE_NAME` | `Local rTorrent` | Profile name created at startup. |
| `PYTORRENT_RTORRENT_SCGI_URL` | `scgi://rtorrent:5000` | rTorrent SCGI URL saved in the profile. |
| `PYTORRENT_API_WAIT_SECONDS` | `90` | Startup wait for profile auto-configuration. |
| `PYTORRENT_API_TOKEN` | empty | Bearer token for profile setup when auth/API protection requires it. |

## Useful commands

```bash
# Logs
docker compose --env-file .env -f docker-compose.stack.yml logs -f

# Rebuild after changing Docker files
docker compose --env-file .env -f docker-compose.stack.yml build --no-cache

# Stop the stack
docker compose --env-file .env -f docker-compose.stack.yml down

# Keep volumes but restart services
docker compose --env-file .env -f docker-compose.stack.yml up -d
```

## Security notes

- Do not expose rTorrent SCGI directly to the internet.
- Change `PYTORRENT_SECRET_KEY` before enabling authentication.
- Bind downloads and watch directories only to trusted host paths.
- Review tracker and copyright rules before using any torrent client.


## Troubleshooting

| Symptom | Note | Fix |
| --- | --- | --- |
| `Worker failed to boot` with missing `pytorrent/static/libs/...` files | pyTorrent is running with `PYTORRENT_USE_OFFLINE_LIBS=true`, but the image was built before frontend assets were downloaded. | Rebuild the pyTorrent image with `--no-cache`, or temporarily set `PYTORRENT_USE_OFFLINE_LIBS=false`. |
| `aardvark-dns binary not found` on Podman | Podman cannot resolve service names like `rtorrent` inside the compose network. | Install `aardvark-dns` and `netavark` on the host. |

## Container DNS fallback

Note: Docker Compose normally resolves service names through the private compose DNS. Some Podman installations print `aardvark-dns binary not found`, which disables container DNS. The stack avoids that failure by default:

- `rtorrent` gets a fixed private address from `RTORRENT_IPV4`.
- pyTorrent gets `/etc/hosts` entries for `rtorrent` and `pytorrent-rtorrent`.
- The SCGI URL can stay `scgi://rtorrent:5000`.
- Port `5000` is not published to the host; it stays inside the private container network.

Default values in `.env.stack.example`:

```env
PYTORRENT_NETWORK_SUBNET=10.89.90.0/24
RTORRENT_IPV4=10.89.90.10
PYTORRENT_RTORRENT_SCGI_URL=scgi://rtorrent:5000
```

Change the subnet only if it conflicts with your local networks.

