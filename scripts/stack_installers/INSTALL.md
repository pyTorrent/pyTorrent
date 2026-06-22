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
/opt/pytorrent/venv/bin/python /opt/pytorrent/scripts/stack_installers/configure_pytorrent_api.py \
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
