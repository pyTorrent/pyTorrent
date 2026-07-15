# pyTorrent stack installer

This document describes the one-command installer for installing **rTorrent + pyTorrent** from a clean server.

The installer is split into two layers:

- `scripts/install_stack.sh` - public bootstrap script intended to be downloaded directly from Git.
- `scripts/stack_installers/` - OS-specific installers and helper scripts used by the bootstrap script.

## Quick install

Run as root or through `sudo`:

```bash
curl -fsSL https://raw.githubusercontent.com/pyTorrent/pyTorrent/master/scripts/install_stack.sh | sudo bash
```

The bootstrap script downloads the current pyTorrent repository, detects the operating system family, and runs the matching installer:

- Debian / Ubuntu: `scripts/stack_installers/install_stack_debian_ubuntu.sh`
- RHEL-compatible systems: `scripts/stack_installers/install_stack_rhel.sh`
- Arch Linux: `scripts/stack_installers/install_stack_arch.sh`

Supported RHEL-compatible systems include RHEL, Rocky Linux, AlmaLinux, CentOS Stream, and Fedora-like systems where `dnf` or `yum` is available.

## What gets installed

Default installation includes:

- Debian/Ubuntu/RHEL: rTorrent `v0.16.11` and libtorrent `v0.16.11` built from source with tinyxml2 XML-RPC
- Arch Linux: current `rtorrent` package from the official repositories through `pacman`
- minimal source build without c-ares/custom curl on Debian/Ubuntu/RHEL
- rTorrent system user: `rtorrent`
- rTorrent SCGI endpoint: `scgi://127.0.0.1:5000`
- rTorrent incoming BitTorrent port: `51300`
- pyTorrent application directory: `/opt/pytorrent`
- pyTorrent HTTP port: `8090`
- pyTorrent profile configured through the HTTP API

The installer creates or updates a pyTorrent rTorrent profile through API after both services are installed.

## Recommended usage with overrides

Environment variables must be passed to the `sudo bash` process.

Example:

```bash
curl -fsSL https://raw.githubusercontent.com/pyTorrent/pyTorrent/master/scripts/install_stack.sh \
  | sudo PYTORRENT_PORT=8091 RTORRENT_SCGI_PORT=5001 bash
```

Another example with a custom profile name:

```bash
curl -fsSL https://raw.githubusercontent.com/pyTorrent/pyTorrent/master/scripts/install_stack.sh \
  | sudo PYTORRENT_PROFILE_NAME="Local rTorrent" PYTORRENT_PORT=8090 bash
```

## Bootstrap parameters

These variables are used by `scripts/install_stack.sh`.

| Variable | Default | Description |
| --- | --- | --- |
| `PYTORRENT_REPO_URL` | `https://github.com/pyTorrent/pyTorrent` | GitHub repository base URL. |
| `PYTORRENT_REPO_BRANCH` | `master` | Branch used to download the repository archive. |
| `PYTORRENT_ARCHIVE_URL` | derived from repo URL and branch | Custom repository archive URL. |
| `PYTORRENT_BOOTSTRAP_DIR` | `/tmp/pytorrent-stack-installer` | Temporary directory used by the bootstrap script. |
| `PYTORRENT_KEEP_BOOTSTRAP_DIR` | `0` | Set to `1` to keep the temporary directory after installation. |

Example using a different branch:

```bash
curl -fsSL https://raw.githubusercontent.com/pyTorrent/pyTorrent/master/scripts/install_stack.sh \
  | sudo PYTORRENT_REPO_BRANCH=develop bash
```

## rTorrent parameters

These variables are used by both stack installers.

| Variable | Default | Description |
| --- | --- | --- |
| `RTORRENT_USER` | `rtorrent` | System user used to run rTorrent. |
| `RTORRENT_HOME` | `/home/${RTORRENT_USER}` | Home directory for the rTorrent user. |
| `RTORRENT_BASE_DIR` | `/opt/rtorrent_build` | Build and install directory for libtorrent and rTorrent. On Arch this is used only when source build is requested. |
| `RTORRENT_SCGI_PORT` | `5000` | Local SCGI port for rTorrent XMLRPC/SCGI. |
| `RTORRENT_TORRENT_PORT` | `51300` | Incoming BitTorrent listen port. |
| `RTORRENT_REF` | `v0.16.11` | rTorrent Git tag, branch, or commit. Ignored by default on Arch unless source build is requested. |
| `LIBTORRENT_REF` | `v0.16.11` | libtorrent Git tag, branch, or commit. Ignored by default on Arch unless source build is requested. |
| `RTORRENT_WITH_XMLRPC_C` | `0` | Set to `1` to compile rTorrent with classic xmlrpc-c instead of the default tinyxml2 XML-RPC backend. On Arch this also switches from repo package to source build. |
| `RTORRENT_BUILD_FROM_SOURCE` | `0` on Arch, source build on Debian/Ubuntu/RHEL | On Arch, set to `1` or pass `--build-rtorrent` to compile instead of using the `pacman` package. |
| `RTORRENT_FORCE_CONFIG` | `1` | On Arch repo-package install, overwrite generated `.rtorrent.rc`; set to `0` to keep an existing config. |

Example:

```bash
curl -fsSL https://raw.githubusercontent.com/pyTorrent/pyTorrent/master/scripts/install_stack.sh \
  | sudo RTORRENT_USER=rtorrent RTORRENT_SCGI_PORT=5001 RTORRENT_TORRENT_PORT=51400 bash
```

Classic xmlrpc-c backend instead of default tinyxml2. On Arch this forces source build:

```bash
curl -fsSL https://raw.githubusercontent.com/pyTorrent/pyTorrent/master/scripts/install_stack.sh \
  | sudo RTORRENT_WITH_XMLRPC_C=1 bash
```

## pyTorrent parameters

| Variable | Default | Description |
| --- | --- | --- |
| `PYTORRENT_APP_DIR` | `/opt/pytorrent` | pyTorrent installation directory. |
| `PYTORRENT_PORT` | `8090` | HTTP port used by the pyTorrent service. |
| `PYTORRENT_BASE_URL` | `http://127.0.0.1:${PYTORRENT_PORT}` | Base URL used by the API configurator. |
| `PYTORRENT_PROFILE_NAME` | `Local rTorrent` | Name of the rTorrent profile created in pyTorrent. |
| `PYTORRENT_API_TOKEN` | empty | Bearer token used when pyTorrent API authentication is enabled. |
| `PYTORRENT_SERVICE_NAME` | `pytorrent` | systemd service name for pyTorrent. |
| `PYTORRENT_RTORRENT_SCGI_URL` | `scgi://127.0.0.1:${RTORRENT_SCGI_PORT}` | SCGI URL saved in the pyTorrent rTorrent profile. |

Example with API token:

```bash
curl -fsSL https://raw.githubusercontent.com/pyTorrent/pyTorrent/master/scripts/install_stack.sh \
  | sudo PYTORRENT_API_TOKEN="pt_xxx" bash
```

## API configurator parameters

The API configurator can be run manually:

```bash
/opt/pytorrent/.venv/bin/python /opt/pytorrent/scripts/stack_installers/configure_pytorrent_api.py \
  --base-url http://127.0.0.1:8090 \
  --profile-name "Local rTorrent" \
  --scgi-url scgi://127.0.0.1:5000
```

CLI options:

| Option | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `--base-url` | `PYTORRENT_BASE_URL` | `http://127.0.0.1:8090` | pyTorrent API base URL. |
| `--api-token` | `PYTORRENT_API_TOKEN` | empty | Bearer token for authenticated API calls. |
| `--profile-name` | `PYTORRENT_RTORRENT_PROFILE_NAME` | `Local rTorrent` | Profile name to create or update. |
| `--scgi-url` | `PYTORRENT_RTORRENT_SCGI_URL` | `scgi://127.0.0.1:5000` | rTorrent SCGI URL. |
| `--timeout` | `PYTORRENT_RTORRENT_TIMEOUT` | `10` | rTorrent request timeout in seconds. |
| `--wait` | `PYTORRENT_API_WAIT_SECONDS` | `90` | Time to wait for the pyTorrent API to become available. |
| `--remote` | `PYTORRENT_RTORRENT_REMOTE` | `0` | Mark profile as remote. Accepts `1`, `true`, `yes`, `on`. |

## Local installation without bootstrap

If the repository is already cloned:

Debian / Ubuntu:

```bash
sudo bash scripts/stack_installers/install_stack_debian_ubuntu.sh
```

RHEL-compatible systems:

```bash
sudo bash scripts/stack_installers/install_stack_rhel.sh
```

Arch Linux, using the repository rTorrent package by default:

```bash
sudo bash scripts/stack_installers/install_stack_arch.sh
```

Arch Linux, forcing source build:

```bash
sudo bash scripts/stack_installers/install_stack_arch.sh --build-rtorrent
```

## Installed service hints

Check services:

```bash
systemctl status pytorrent
systemctl status rtorrent@rtorrent.service
```

Check logs:

```bash
tail -f /data/logs/app.log /data/logs/error.log
journalctl -u pytorrent -f
journalctl -u rtorrent@rtorrent.service -f
```

## Notes

- Debian/Ubuntu/RHEL source builds are intentionally minimal by default.
- Arch Linux uses the current repository `rtorrent` package by default and does not compile rTorrent unless `--build-rtorrent`, `RTORRENT_BUILD_FROM_SOURCE=1`, or `--with-xmlrpc-c` is used.
- c-ares and custom curl are not enabled by the stack installer defaults.
- The rTorrent installer overwrites the generated `.rtorrent.rc` by default.
- pyTorrent is configured through the HTTP API after the service starts.
- If API authentication is enabled before profile configuration, pass `PYTORRENT_API_TOKEN`.


## Build logs and troubleshooting

Source-build installers write quiet build output to `/var/log/pytorrent-installer` by default.
Override it with:

```bash
PYTORRENT_STACK_LOG_DIR=/tmp/pytorrent-build-logs
```

For full command output during rTorrent/libtorrent compilation, run with:

```bash
PYTORRENT_DEBUG_INSTALL=1
```

On RHEL-compatible systems the installer also tries to enable CRB/PowerTools and installs `libcurl-devel`, `redhat-rpm-config`, `patch`, `diffutils`, `findutils`, `file`, and `libstdc++-devel`, because minimal Alma/Rocky images often do not include enough build tooling.

## pyTorrent-only installer

Use this installer when rTorrent is already configured and pyTorrent only needs a web UI service and one rTorrent profile.

Interactive local run:

```bash
sudo bash scripts/install_pytorrent_only.sh
```

Bootstrap run from repository:

```bash
curl -fsSL https://raw.githubusercontent.com/pyTorrent/pyTorrent/master/scripts/install_pytorrent.sh | sudo bash
```

Non-interactive example for an existing TCP SCGI backend:

```bash
sudo bash scripts/install_pytorrent_only.sh \
  --yes \
  --user pytorrent \
  --port 8090 \
  --scgi-url scgi://127.0.0.1:5000 \
  --auth enable \
  --auth-provider local \
  --auth-user pytorrent \
  --auth-password 'change-this-password' \
  --logs enable \
  --libs offline
```

Reverse proxy example:

```bash
sudo bash scripts/install_pytorrent_only.sh \
  --yes \
  --port 8090 \
  --reverse-proxy yes \
  --proxy-domains torrent.example.com,pythong.example.com \
  --local-origins http://10.10.10.22:8890
```

Unix socket rTorrent backend via rtorrent-scgi-proxy:

```bash
sudo bash scripts/install_pytorrent_only.sh \
  --yes \
  --rtorrent-socket /run/rtorrent/rtorrent.sock \
  --install-scgi-proxy yes \
  --proxy-listen 127.0.0.1:5050 \
  --proxy-control-listen 127.0.0.1:5051 \
  --proxy-allow-net 127.0.0.1
```

The installer now writes the proxy configuration as YAML to `/etc/rtorrent-scgi-proxy/config.yaml`, creates `/var/log/rtorrent-scgi-proxy`, installs `/etc/logrotate.d/rtorrent-scgi-proxy`, and starts `rtorrent-scgi-proxy.service` with `--check-config --config`. pyTorrent receives a generated SCGI URL in the form `scgi://127.0.0.1:5050/proxy/<token>`. By default, the proxy binary is downloaded from the raw GitHub symlink `https://raw.githubusercontent.com/pyTorrent/rtorrent-scgi-proxy/refs/heads/master/dist/rtorrent-scgi-proxy-linux-amd64`. The installer reads the symlink target, recognizes the version from a name like `rtorrent-scgi-proxy-1.3.5-linux-amd64`, logs it, and downloads the real ELF binary from the same `dist` directory.

Useful proxy variables/options:

| Option / variable | Default | Description |
| --- | --- | --- |
| `--proxy-config-dir` / `RTORRENT_SCGI_PROXY_CONFIG_DIR` | `/etc/rtorrent-scgi-proxy` | Proxy config directory. |
| `--proxy-config-file` / `RTORRENT_SCGI_PROXY_CONFIG_FILE` | `/etc/rtorrent-scgi-proxy/config.yaml` | YAML config path. |
| `--proxy-log-dir` / `RTORRENT_SCGI_PROXY_LOG_DIR` | `/var/log/rtorrent-scgi-proxy` | Access/RPC log directory. |
| `--proxy-control-listen` / `RTORRENT_SCGI_PROXY_CONTROL_LISTEN` | `127.0.0.1:5051` | Health, metrics and API listener. |
| `--proxy-binary-url` / `RTORRENT_SCGI_PROXY_BINARY_URL` | `https://raw.githubusercontent.com/pyTorrent/rtorrent-scgi-proxy/refs/heads/master/dist/rtorrent-scgi-proxy-linux-amd64` | Raw GitHub dist symlink for the current Linux amd64 binary. The installer recognizes the version from the symlink target and downloads the real binary from the same `dist` directory. |
| `RTORRENT_SCGI_PROXY_BINARY_PATH` | empty | Optional local proxy binary. If set, it has priority over downloading. If downloading fails, the bundled `scripts/vendor/rtorrent-scgi-proxy-linux-amd64` is used as fallback when present. |
| `RTORRENT_SCGI_PROXY_SYSTEM_PROXY` | `true` | Enables local `proxy.system.*` XML-RPC methods. |
| `RTORRENT_SCGI_PROXY_DEBUG_RPC` | `true` | Enables `/var/log/rtorrent-scgi-proxy/rpc.log`. |
| `RTORRENT_SCGI_PROXY_ACCESS_LOG` | `true` | Enables `/var/log/rtorrent-scgi-proxy/access.log`. |

Notes:

- The default application port is `8090`; high ports are recommended because ports below `1024` usually require extra privileges or may be blocked by the system.
- Offline frontend libraries are the default. Use `--libs online` only when CDN loading is preferred.
- Local auth is configured directly by the installer. External auth providers require a trusted reverse proxy setup; see `auth.md`.
- Reverse proxy mode enables `PYTORRENT_PROXY_FIX_ENABLE`, secure cookies and CORS/API origins for the HTTPS domains plus localhost/local IP origins.
