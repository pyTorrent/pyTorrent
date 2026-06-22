#!/usr/bin/env python3
import argparse
import itertools
import os
import pwd
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

DEFAULT_USER = "rtorrent"
DEFAULT_GROUP = "rtorrent"
DEFAULT_HOME = "/home/rtorrent"
DEFAULT_BASE_DIR = "/opt/rtorrent_build"
DEFAULT_LIBTORRENT_REF = "v0.16.11"
DEFAULT_RTORRENT_REF = "v0.16.11"
DEFAULT_XMLRPC_REF = "latest-stable"
DEFAULT_RPC_BACKEND = "tinyxml2"
DEFAULT_CARES_REF = "1.34.6"
DEFAULT_CURL_REF = "8.19.0"
DEFAULT_SERVICE_PATH = "/etc/systemd/system/rtorrent@.service"
DEFAULT_SCGI_PORT = 5000
DEFAULT_TORRENT_PORT = 51300
DOWNLOAD_RETRIES = int(os.environ.get("PYTORRENT_DOWNLOAD_RETRIES", "4"))
DOWNLOAD_RETRY_DELAY = int(os.environ.get("PYTORRENT_DOWNLOAD_RETRY_DELAY", "10"))
DOWNLOAD_CONNECT_TIMEOUT = int(os.environ.get("PYTORRENT_DOWNLOAD_CONNECT_TIMEOUT", "30"))
DOWNLOAD_MAX_TIME = int(os.environ.get("PYTORRENT_DOWNLOAD_MAX_TIME", "600"))


def retry_countdown(seconds):
    for remaining in range(seconds, 0, -1):
        print(f"Retrying in {remaining}s...", end="\r", flush=True)
        time.sleep(1)
    if seconds > 0:
        print(" " * 40, end="\r", flush=True)


def run_with_retry(cmd, *, retries=DOWNLOAD_RETRIES, retry_delay=DOWNLOAD_RETRY_DELAY, retry_label=None, **kwargs):
    last_error = None
    label = retry_label or " ".join(str(x) for x in cmd[:3])
    for attempt in range(1, retries + 1):
        try:
            return run(cmd, **kwargs)
        except InstallError as exc:
            last_error = exc
            print(f"{label} failed ({attempt}/{retries}): {exc}")
            if attempt < retries:
                retry_countdown(retry_delay)
    raise last_error


def download_url_candidates(url):
    candidates = [url]
    if url.startswith("https://github.com/c-ares/c-ares/releases/download/v") and url.endswith(".tar.gz"):
        version = url.rsplit("/c-ares-", 1)[-1].removesuffix(".tar.gz")
        candidates.append(f"https://codeload.github.com/c-ares/c-ares/tar.gz/refs/tags/v{version}")
    if url.startswith("https://curl.se/download/curl-") and url.endswith(".tar.gz"):
        version = url.rsplit("/curl-", 1)[-1].removesuffix(".tar.gz")
        tag = "curl-" + version.replace(".", "_")
        candidates.append(f"https://github.com/curl/curl/releases/download/{tag}/curl-{version}.tar.gz")
        candidates.append(f"https://codeload.github.com/curl/curl/tar.gz/refs/tags/{tag}")
    if "sourceforge.net/projects/xmlrpc-c/files/latest/download" in url:
        candidates.append("https://downloads.sourceforge.net/project/xmlrpc-c/latest/download")
    if url.startswith("https://downloads.sourceforge.net/project/xmlrpc-c/"):
        candidates.append(url.replace("https://downloads.sourceforge.net/", "https://sourceforge.net/projects/").replace("project/xmlrpc-c/", "xmlrpc-c/files/"))

    unique = []
    for candidate in candidates:
        if candidate not in unique:
            unique.append(candidate)
    return unique


class InstallError(Exception):
    pass


class Spinner:
    FRAMES = ["|", "/", "-", "\\"]

    def __init__(self, message, enabled=True):
        self.message = message
        self.enabled = enabled and sys.stdout.isatty()
        self._stop = threading.Event()
        self._thread = None
        self._start = None

    def _run(self):
        for frame in itertools.cycle(self.FRAMES):
            if self._stop.is_set():
                break
            elapsed = time.time() - self._start
            sys.stdout.write(f"\r[ {frame} ] {self.message} ({elapsed:.1f}s)")
            sys.stdout.flush()
            time.sleep(0.12)

    def __enter__(self):
        self._start = time.time()
        if self.enabled:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        elapsed = time.time() - self._start
        if self.enabled:
            self._stop.set()
            self._thread.join(timeout=0.5)
            status = "ERR" if exc else "OK "
            sys.stdout.write(f"\r[ {status} ] {self.message} ({elapsed:.1f}s)\n")
            sys.stdout.flush()


def build_log_dir():
    path = Path(os.environ.get("PYTORRENT_STACK_LOG_DIR", "/var/log/pytorrent-installer"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def tail_file(path, lines=80):
    try:
        data = Path(path).read_text(errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(data[-lines:])


def run(cmd, *, cwd=None, env=None, check=True, debug=False, capture_output=False, log_name=None):
    if debug:
        print(f"\n>>> {' '.join(cmd)}")
    log_path = None
    log_handle = None
    if log_name and not capture_output and not debug:
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", log_name).strip("_") or "command"
        log_path = build_log_dir() / f"{safe_name}.log"
        log_handle = open(log_path, "a", encoding="utf-8")
        log_handle.write(f"\n>>> {' '.join(cmd)}\n")
        log_handle.flush()
    try:
        stdout = subprocess.PIPE if capture_output else (None if debug else (log_handle or subprocess.DEVNULL))
        stderr = subprocess.PIPE if capture_output else (None if debug else (subprocess.STDOUT if log_handle else subprocess.DEVNULL))
        result = subprocess.run(cmd, cwd=cwd, env=env, check=False, text=True, stdout=stdout, stderr=stderr)
    finally:
        if log_handle:
            log_handle.close()
    if check and result.returncode != 0:
        stderr_text = ""
        if capture_output and result.stderr:
            stderr_text = f"\n{result.stderr.strip()}"
        if log_path:
            stderr_text += f"\nBuild log: {log_path}\n--- last log lines ---\n{tail_file(log_path)}"
        raise InstallError(f"Command failed with exit code {result.returncode}: {' '.join(cmd)}{stderr_text}")
    return result


def capture(cmd, **kwargs):
    result = run(cmd, capture_output=True, **kwargs)
    out = (result.stdout or "").strip()
    err = (result.stderr or "").strip()
    return out if out else err


def require_root():
    if os.geteuid() != 0:
        raise InstallError("This script must be run as root (use sudo).")


def read_os_release():
    os_release = Path("/etc/os-release")
    if not os_release.exists():
        raise InstallError("Cannot detect operating system: /etc/os-release is missing.")

    data = {}
    for line in os_release.read_text().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            data[k] = v.strip().strip('"')
    return data


def is_ubuntu_2604():
    data = read_os_release()
    return data.get("ID", "").lower() == "ubuntu" and data.get("VERSION_ID", "") == "26.04"


def detect_os_family():
    data = read_os_release()
    distro_id = data.get("ID", "").lower()
    distro_like = data.get("ID_LIKE", "").lower()
    if distro_id == "debian" or "debian" in distro_like or distro_id == "ubuntu":
        family = "debian"
    elif distro_id == "arch" or "arch" in distro_like:
        family = "arch"
    else:
        raise InstallError(
            f"Unsupported distribution: ID={data.get('ID', 'unknown')}, "
            f"ID_LIKE={data.get('ID_LIKE', 'unknown')}. This installer supports Debian/Ubuntu and Arch Linux."
        )
    print(f"Detected {family}-compatible system: {data.get('PRETTY_NAME', distro_id)}")
    return family


def prompt_yes_no(question, default=True, assume_yes=False):
    if assume_yes:
        print(f"{question} [{'Y/n' if default else 'y/N'}] -> auto-yes")
        return True

    suffix = "[Y/n]" if default else "[y/N]"
    while True:
        reply = input(f"{question} {suffix} ").strip().lower()
        if not reply:
            return default
        if reply in {"y", "yes"}:
            return True
        if reply in {"n", "no"}:
            return False
        print("Please answer yes or no.")


def parse_version(version):
    parts = [int(x) for x in re.findall(r"\d+", version)]
    return tuple(parts[:3]) if parts else (0,)


def ensure_packages(packages, *, family="debian", debug=False):
    if family == "arch":
        print("Installing build and runtime dependencies with pacman...")
        run(["pacman", "-Sy", "--noconfirm", "--needed", *packages], debug=debug, log_name="pacman_install_rtorrent_deps")
        return
    print("Updating APT metadata...")
    run(["apt-get", "update"], debug=debug)
    print("Installing build and runtime dependencies...")
    run(["apt-get", "install", "-y", *packages], debug=debug, log_name="apt_install_rtorrent_deps")


def ensure_dir(path, owner=None, group=None, mode=None):
    Path(path).mkdir(parents=True, exist_ok=True)
    if owner is not None or group is not None:
        shutil.chown(path, user=owner, group=group)
    if mode is not None:
        os.chmod(path, mode)


def create_system_user(user, group, home, assume_yes=False, debug=False):
    try:
        pwd.getpwnam(user)
        print(f"User '{user}' already exists.")
    except KeyError:
        if not prompt_yes_no(f"Create system user '{user}' with home '{home}'?", default=True, assume_yes=assume_yes):
            raise InstallError("User creation declined.")
        run(["groupadd", "--system", group], check=False, debug=debug)
        shell_path = next((p for p in ["/usr/sbin/nologin", "/sbin/nologin", "/usr/bin/nologin"] if Path(p).exists()), "/usr/bin/false")
        run([
            "useradd",
            "--system",
            "--home-dir", home,
            "--create-home",
            "--shell", shell_path,
            "--gid", group,
            user,
        ], debug=debug)


def clone_or_update_repo(repo_url, repo_dir, ref, *, debug=False):
    repo_dir = Path(repo_dir)
    if not repo_dir.exists():
        with Spinner(f"Cloning {repo_dir.name}", enabled=not debug):
            run_with_retry(["git", "clone", repo_url, str(repo_dir)], debug=debug, retry_label=f"git clone {repo_url}")
    else:
        print(f"Repository already exists: {repo_dir}")
    with Spinner(f"Checking out {repo_dir.name} -> {ref}", enabled=not debug):
        run_with_retry(["git", "fetch", "--all", "--tags"], cwd=str(repo_dir), debug=debug, retry_label=f"git fetch {repo_dir.name}")
        run(["git", "checkout", ref], cwd=str(repo_dir), debug=debug)
        run_with_retry(["git", "pull", "--ff-only"], cwd=str(repo_dir), check=False, debug=debug, retry_label=f"git pull {repo_dir.name}")


def download_file(url, destination, *, debug=False):
    last_error = None
    for candidate in download_url_candidates(url):
        for attempt in range(1, DOWNLOAD_RETRIES + 1):
            try:
                return run([
                    "curl",
                    "-fL",
                    "--connect-timeout", str(DOWNLOAD_CONNECT_TIMEOUT),
                    "--max-time", str(DOWNLOAD_MAX_TIME),
                    candidate,
                    "-o", str(destination),
                ], debug=debug)
            except InstallError as exc:
                last_error = exc
                print(f"Download failed ({attempt}/{DOWNLOAD_RETRIES}) from {candidate}: {exc}")
                if attempt < DOWNLOAD_RETRIES:
                    retry_countdown(DOWNLOAD_RETRY_DELAY)
        print(f"Trying alternative source if available after: {candidate}")
    raise last_error or InstallError(f"Download failed: {url}")


def extract_tarball(tarball, destination, *, debug=False):
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)
    run(["tar", "-xzf", str(tarball), "-C", str(destination), "--strip-components=1"], debug=debug)


def find_xmlrpc_config(base_dir, preferred_install=None):
    candidates = []

    if preferred_install is not None:
        preferred = Path(preferred_install) / "bin" / "xmlrpc-c-config"
        if preferred.exists():
            candidates.append(preferred.resolve())

    root = Path(base_dir)
    if root.exists():
        for match in root.rglob("xmlrpc-c-config"):
            if match.is_file():
                candidates.append(match.resolve())

    unique = []
    seen = set()
    for candidate in candidates:
        if candidate not in seen:
            seen.add(candidate)
            unique.append(candidate)

    if preferred_install is not None:
        preferred_prefix = str(Path(preferred_install).resolve())
        for candidate in unique:
            if str(candidate).startswith(preferred_prefix):
                return candidate

    return unique[0] if unique else None


def verify_xmlrpc_environment(xmlrpc_config_path, *, debug=False):
    tool = Path(xmlrpc_config_path)
    if not tool.exists():
        raise InstallError(f"xmlrpc-c-config was not found: {tool}")
    version = capture([str(tool), "--version"], check=True, debug=debug)
    if parse_version(version) < (1, 11):
        raise InstallError(f"xmlrpc-c version is too old: {version}. Version 1.11 or newer is required.")
    print(f"Detected xmlrpc-c version: {version} ({tool})")
    return version


def build_env(*prefixes, extra_env=None):
    env = os.environ.copy()
    include_dirs = []
    lib_dirs = []
    pkg_dirs = []
    bin_dirs = []

    for prefix in prefixes:
        if not prefix:
            continue
        prefix = str(prefix)
        include_dirs.append(f"-I{prefix}/include")
        lib_dirs.append(f"-L{prefix}/lib")
        pkg_dirs.append(f"{prefix}/lib/pkgconfig")
        bin_dirs.append(f"{prefix}/bin")

    if include_dirs:
        env["CPPFLAGS"] = " ".join(include_dirs + [env.get("CPPFLAGS", "")]).strip()
        env["CFLAGS"] = " ".join(include_dirs + [env.get("CFLAGS", "")]).strip()

    if lib_dirs:
        rpaths = [f"-Wl,-rpath,{d[2:]}" for d in lib_dirs]
        env["LDFLAGS"] = " ".join(lib_dirs + rpaths + [env.get("LDFLAGS", "")]).strip()

    if pkg_dirs:
        env["PKG_CONFIG_PATH"] = ":".join(pkg_dirs + ([env.get("PKG_CONFIG_PATH")] if env.get("PKG_CONFIG_PATH") else []))

    if bin_dirs:
        env["PATH"] = ":".join(bin_dirs + [env.get("PATH", "")])

    if extra_env:
        env.update(extra_env)

    return env


def build_xmlrpc_c(base_dir, xmlrpc_ref, *, debug=False):
    source_root = Path(base_dir) / "xmlrpc-c-src"
    install_dir = Path(base_dir) / "xmlrpc-c_install"
    build_root = Path(base_dir) / "_sources"
    tarball = build_root / "xmlrpc-c.tar.gz"

    existing_config = find_xmlrpc_config(base_dir, install_dir)
    if existing_config and str(existing_config).startswith(str(install_dir.resolve())):
        print(f"Reusing existing xmlrpc-c installation: {existing_config}")
        version = verify_xmlrpc_environment(existing_config, debug=debug)
        return install_dir, version

    ensure_dir(build_root)

    if xmlrpc_ref == "latest-stable":
        url = "https://sourceforge.net/projects/xmlrpc-c/files/latest/download"
    elif re.match(r"^\d+\.\d+\.\d+$", xmlrpc_ref):
        url = (
            "https://downloads.sourceforge.net/project/xmlrpc-c/Xmlrpc-c%20Super%20Stable/"
            f"{xmlrpc_ref}/xmlrpc-c-{xmlrpc_ref}.tgz"
        )
    else:
        url = xmlrpc_ref

    with Spinner("Downloading xmlrpc-c", enabled=not debug):
        download_file(url, tarball, debug=debug)
        extract_tarball(tarball, source_root, debug=debug)

    xmlrpc_env = os.environ.copy()
    # Ubuntu 26.04 defaults to a newer C standard where bool/true/false are keywords.
    # Older xmlrpc-c releases still define them manually, so pin only this build to GNU17.
    if is_ubuntu_2604():
        xmlrpc_env["CFLAGS"] = f"-std=gnu17 {xmlrpc_env.get('CFLAGS', '')}".strip()
        print("Detected Ubuntu 26.04; using CFLAGS=-std=gnu17 for xmlrpc-c only.")

    with Spinner("Configuring xmlrpc-c", enabled=not debug):
        run(["./configure", f"--prefix={install_dir}"], cwd=str(source_root), env=xmlrpc_env, debug=debug)
    with Spinner("Building xmlrpc-c", enabled=not debug):
        run(["make", "-j", str(os.cpu_count() or 1)], cwd=str(source_root), env=xmlrpc_env, debug=debug, log_name=f"make_{Path(source_root).name}")
    with Spinner("Installing xmlrpc-c", enabled=not debug):
        run(["make", "install"], cwd=str(source_root), env=xmlrpc_env, debug=debug, log_name=f"make_install_{Path(source_root).name}")

    xmlrpc_config = find_xmlrpc_config(base_dir, install_dir)
    if not xmlrpc_config or not str(xmlrpc_config).startswith(str(install_dir.resolve())):
        raise InstallError(f"Custom xmlrpc-c build finished, but xmlrpc-c-config was not found under {install_dir}.")
    version = verify_xmlrpc_environment(xmlrpc_config, debug=debug)
    return install_dir, version


def build_cares(base_dir, cares_version, *, debug=False):
    source_root = Path(base_dir) / "c-ares-src"
    install_dir = Path(base_dir) / "c-ares_install"
    build_root = Path(base_dir) / "_sources"
    tarball = build_root / f"c-ares-{cares_version}.tar.gz"
    url = f"https://github.com/c-ares/c-ares/releases/download/v{cares_version}/c-ares-{cares_version}.tar.gz"

    ensure_dir(build_root)
    with Spinner("Downloading c-ares", enabled=not debug):
        download_file(url, tarball, debug=debug)
        extract_tarball(tarball, source_root, debug=debug)
    with Spinner("Configuring c-ares", enabled=not debug):
        run([
            "cmake",
            "-S", str(source_root),
            "-B", str(source_root / "build"),
            f"-DCMAKE_INSTALL_PREFIX={install_dir}",
            "-DCARES_SHARED=ON",
            "-DCARES_STATIC=OFF",
            "-DCMAKE_BUILD_TYPE=Release",
        ], debug=debug)
    with Spinner("Building c-ares", enabled=not debug):
        run(["cmake", "--build", str(source_root / "build"), "--parallel", str(os.cpu_count() or 1)], debug=debug)
    with Spinner("Installing c-ares", enabled=not debug):
        run(["cmake", "--install", str(source_root / "build")], debug=debug)
    return install_dir, cares_version


def build_curl(base_dir, curl_version, cares_install, *, debug=False):
    source_root = Path(base_dir) / "curl-src"
    install_dir = Path(base_dir) / "curl_install"
    build_root = Path(base_dir) / "_sources"
    tarball = build_root / f"curl-{curl_version}.tar.gz"
    url = f"https://curl.se/download/curl-{curl_version}.tar.gz"

    ensure_dir(build_root)
    with Spinner("Downloading curl", enabled=not debug):
        download_file(url, tarball, debug=debug)
        extract_tarball(tarball, source_root, debug=debug)

    env = build_env(cares_install)
    buildconf_script = source_root / "buildconf"
    with Spinner("Preparing curl build system", enabled=not debug):
        if buildconf_script.exists():
            run(["./buildconf"], cwd=str(source_root), env=env, debug=debug)
        run(["make", "distclean"], cwd=str(source_root), env=env, check=False, debug=debug)
    with Spinner("Configuring curl with c-ares", enabled=not debug):
        run([
            "./configure",
            f"--prefix={install_dir}",
            "--with-openssl",
            f"--enable-ares={cares_install}",
            "--disable-static",
            "--enable-shared",
        ], cwd=str(source_root), env=env, debug=debug)
    with Spinner("Building curl", enabled=not debug):
        run(["make", "-j", str(os.cpu_count() or 1)], cwd=str(source_root), env=env, debug=debug, log_name=f"make_{Path(source_root).name}")
    with Spinner("Installing curl", enabled=not debug):
        run(["make", "install"], cwd=str(source_root), env=env, debug=debug, log_name=f"make_install_{Path(source_root).name}")

    version = capture([str(install_dir / "bin" / "curl"), "--version"], env=build_env(install_dir, cares_install), debug=debug)
    return install_dir, version


def build_libtorrent(base_dir, libtorrent_ref, curl_install=None, cares_install=None, *, debug=False):
    source_dir = Path(base_dir) / "libtorrent"
    install_dir = Path(base_dir) / "libtorrent_install"
    clone_or_update_repo("https://github.com/rakshasa/libtorrent.git", source_dir, libtorrent_ref, debug=debug)

    prefixes = []
    if curl_install:
        prefixes.append(curl_install)
    if cares_install:
        prefixes.append(cares_install)
    env = build_env(*prefixes)
    configure_cmd = ["./configure", f"--prefix={install_dir}"]

    if curl_install:
        curl_config = str(Path(curl_install) / "bin" / "curl-config")
        env["CURL_CONFIG"] = curl_config
        if Path(curl_config).exists():
            configure_cmd.append(f"--with-curl={curl_config}")
        env["LIBS"] = f"-L{Path(curl_install) / 'lib'} -lcurl " + env.get("LIBS", "")
        if cares_install:
            env["LIBS"] = f"-L{Path(cares_install) / 'lib'} -lcares " + env.get("LIBS", "")

    with Spinner("Preparing libtorrent build system", enabled=not debug):
        run(["autoreconf", "-i"], cwd=str(source_dir), env=env, debug=debug)
        run(["make", "distclean"], cwd=str(source_dir), env=env, check=False, debug=debug)
    with Spinner("Configuring libtorrent", enabled=not debug):
        run(configure_cmd, cwd=str(source_dir), env=env, debug=debug)
    with Spinner("Building libtorrent", enabled=not debug):
        run(["make", "-j", str(os.cpu_count() or 1)], cwd=str(source_dir), env=env, debug=debug, log_name=f"make_{Path(source_dir).name}")
    with Spinner("Installing libtorrent", enabled=not debug):
        run(["make", "install"], cwd=str(source_dir), env=env, debug=debug, log_name=f"make_install_{Path(source_dir).name}")

    version = capture(["git", "describe", "--tags", "--always"], cwd=str(source_dir), debug=debug)
    return install_dir, version


def build_rtorrent(base_dir, rtorrent_ref, libtorrent_install, rpc_backend, xmlrpc_install=None, curl_install=None, cares_install=None, *, debug=False):
    source_dir = Path(base_dir) / "rtorrent"
    install_dir = Path(base_dir) / "rtorrent_install"

    clone_or_update_repo("https://github.com/rakshasa/rtorrent.git", source_dir, rtorrent_ref, debug=debug)

    prefixes = [libtorrent_install]
    xmlrpc_config = None

    if rpc_backend == "xmlrpc-c":
        xmlrpc_config = find_xmlrpc_config(base_dir, xmlrpc_install)
        if not xmlrpc_config:
            raise InstallError(f"Could not find custom xmlrpc-c-config under {base_dir}.")
        if not str(xmlrpc_config).startswith(str(Path(xmlrpc_install).resolve())):
            raise InstallError(f"Wrong xmlrpc-c-config selected: {xmlrpc_config}. Expected one under: {xmlrpc_install}")

        verify_xmlrpc_environment(xmlrpc_config, debug=debug)
        prefixes.append(xmlrpc_install)

    elif rpc_backend == "tinyxml2":
        # Debian 13 / newer toolchains may not automatically propagate tinyxml2
        # linker flags from configure into the final rTorrent binary.
        # Force pkg-config flags into the build environment.
        pkg_config = shutil.which("pkg-config")
        if not pkg_config:
            raise InstallError("pkg-config is required for tinyxml2 backend. Install it with: apt-get install -y pkg-config")

        tinyxml2_exists = run(
            ["pkg-config", "--exists", "tinyxml2"],
            check=False,
            debug=debug,
            capture_output=True,
        )
        if tinyxml2_exists.returncode != 0:
            raise InstallError("tinyxml2 development files not found. Install them with: apt-get install -y libtinyxml2-dev")

    else:
        raise InstallError(f"Unsupported RPC backend: {rpc_backend}")

    if curl_install:
        prefixes.append(curl_install)
    if cares_install:
        prefixes.append(cares_install)

    env = build_env(*prefixes)

    if xmlrpc_config:
        env["PATH"] = f"{xmlrpc_config.parent}:" + env.get("PATH", "")
        env["XMLRPC_C_CONFIG"] = str(xmlrpc_config)

    if rpc_backend == "tinyxml2":
        tinyxml2_cflags = capture(
            ["pkg-config", "--cflags", "tinyxml2"],
            check=False,
            debug=debug,
        )
        tinyxml2_libs = capture(
            ["pkg-config", "--libs", "tinyxml2"],
            check=False,
            debug=debug,
        )

        if tinyxml2_cflags:
            env["CPPFLAGS"] = f"{tinyxml2_cflags} " + env.get("CPPFLAGS", "")
            env["CXXFLAGS"] = f"{tinyxml2_cflags} " + env.get("CXXFLAGS", "")
            env["CFLAGS"] = f"{tinyxml2_cflags} " + env.get("CFLAGS", "")

        if tinyxml2_libs:
            env["LIBS"] = f"-Wl,--no-as-needed {tinyxml2_libs} -Wl,--as-needed " + env.get("LIBS", "")

    with Spinner("Preparing rTorrent build system", enabled=not debug):
        run(["autoreconf", "-i"], cwd=str(source_dir), env=env, debug=debug)
        run(["make", "distclean"], cwd=str(source_dir), env=env, check=False, debug=debug)

    rpc_flag = "--with-xmlrpc-c" if rpc_backend == "xmlrpc-c" else "--with-xmlrpc-tinyxml2"
    configure_cmd = ["./configure", f"--prefix={install_dir}", rpc_flag]

    with Spinner("Configuring rTorrent", enabled=not debug):
        run(configure_cmd, cwd=str(source_dir), env=env, debug=debug)

    with Spinner("Building rTorrent", enabled=not debug):
        run(
            ["make", "-j", str(os.cpu_count() or 1)],
            cwd=str(source_dir),
            env=env,
            debug=debug,
            log_name=f"make_{Path(source_dir).name}",
        )

    with Spinner("Installing rTorrent", enabled=not debug):
        run(
            ["make", "install"],
            cwd=str(source_dir),
            env=env,
            debug=debug,
            log_name=f"make_install_{Path(source_dir).name}",
        )

    runtime_prefixes = [libtorrent_install]

    if rpc_backend == "xmlrpc-c" and xmlrpc_install:
        runtime_prefixes.append(xmlrpc_install)

    if curl_install:
        runtime_prefixes.append(curl_install)

    if cares_install:
        runtime_prefixes.append(cares_install)

    runtime_env = build_env(*runtime_prefixes)
    runtime_env["LD_LIBRARY_PATH"] = ":".join([f"{p}/lib" for p in runtime_prefixes])

    version = capture(
        [str(install_dir / "bin" / "rtorrent"), "-h"],
        env=runtime_env,
        check=False,
        debug=debug,
    )

    return install_dir, version

def install_symlinks(rtorrent_install, libtorrent_install, xmlrpc_install=None, curl_install=None, cares_install=None, *, debug=False):
    rtorrent_bin = Path(rtorrent_install) / "bin" / "rtorrent"
    if not rtorrent_bin.exists():
        raise InstallError(f"Compiled rtorrent binary not found: {rtorrent_bin}")

    usr_local_bin = Path("/usr/local/bin/rtorrent")
    if usr_local_bin.exists() or usr_local_bin.is_symlink():
        usr_local_bin.unlink()
    usr_local_bin.symlink_to(rtorrent_bin)
    print(f"Symlinked {usr_local_bin} -> {rtorrent_bin}")

    lib_dirs = [f"{libtorrent_install}/lib"]
    if xmlrpc_install:
        lib_dirs.append(f"{xmlrpc_install}/lib")
    if curl_install:
        lib_dirs.append(f"{curl_install}/lib")
    if cares_install:
        lib_dirs.append(f"{cares_install}/lib")
    ld_conf = Path("/etc/ld.so.conf.d/rtorrent-custom-libs.conf")
    ld_conf.write_text("\n".join(lib_dirs) + "\n")
    run(["ldconfig"], debug=debug)


def write_service(service_path, binary_path, runtime_lib_dirs):
    service_content = f"""[Unit]
Description=rTorrent for %I | https://github.com/pyTorrent/pyTorrent
After=network.target

[Service]
Type=simple
User=%I
Group=%I
KillMode=process
RuntimeDirectory=%I
RuntimeDirectoryMode=0750
WorkingDirectory=/home/%I
ExecStartPre=-/bin/rm -f /home/%I/.session/rtorrent.lock
ExecStart={binary_path} -o system.daemon.set=true -n -o import=/home/%I/.rtorrent.rc
KillSignal=SIGTERM
TimeoutStopSec=300
Restart=always
RestartSec=3
LimitNOFILE=1048576
Environment=LD_LIBRARY_PATH={runtime_lib_dirs}

[Install]
WantedBy=multi-user.target
"""
    Path(service_path).write_text(service_content)
    print(f"Wrote systemd unit: {service_path}")
    run(["systemctl", "daemon-reload"])


def extract_version_tuple(text):
    if not text:
        return None
    match = re.search(r"(?:^|[^0-9])(\d+)\.(\d+)\.(\d+)(?:[^0-9]|$)", str(text))
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def rtorrent_bind_address_directive(rtorrent_ref, rtorrent_version=None):
    version = extract_version_tuple(rtorrent_ref) or extract_version_tuple(rtorrent_version)
    if version and version < (0, 16, 0):
        return "network.bind_address.set"
    return "network.bind_address.ipv4.set"

def build_rtorrent_config_content(username, scgi_port, torrent_port, bind_address_directive, scgi_unix_socket=None):
    if scgi_unix_socket:
        scgi_line = f"network.scgi.open_local = {scgi_unix_socket}"
    else:
        scgi_line = f"network.scgi.open_port = 127.0.0.1:{scgi_port}"

    return f"""
## https://github.com/pyTorrent/pyTorrent

directory.default.set = /home/{username}/downloads
session.path.set = /home/{username}/.session
encoding.add = UTF-8

{scgi_line}
network.port_range.set = {torrent_port}-{torrent_port}
network.port_random.set = no
{bind_address_directive} = 0.0.0.0

system.file.allocate.set = 0
system.umask.set = 0022

dht.mode.set = disable
protocol.pex.set = no
trackers.use_udp.set = no
protocol.encryption.set = allow_incoming,enable_retry,prefer_plaintext

#schedule2 = tied_directory,6,5,start_tied=
#schedule2 = untied_directory,7,5,stop_untied=
schedule2 = session_save,300,300,((session.save))
schedule2 = watch_directory,60,60,load.normal=/home/{username}/watch/*.torrent

ratio.max.set = -1
network.xmlrpc.size_limit.set = 33554432

network.http.max_open.set = 64
network.max_open_sockets.set = 1024
network.max_open_files.set = 8192
network.http.dns_cache_timeout.set = 25
network.http.ssl_verify_peer.set = 0

network.send_buffer.size.set = 4M
network.receive_buffer.size.set = 4M

throttle.min_peers.normal.set = 30
throttle.max_peers.normal.set = 150
throttle.min_peers.seed.set = -1
throttle.max_peers.seed.set = -1
throttle.max_downloads.global.set = 300
throttle.max_uploads.global.set = 300
throttle.max_downloads.set = 20
throttle.max_uploads.set = 20

trackers.numwant.set = 80
pieces.hash.on_completion.set = 0
#pieces.memory.max.set = 1024M
""".lstrip()

def write_rtorrent_config(user_home, username, scgi_port, torrent_port, bind_address_directive, *, force_config=False, scgi_unix_socket=None):
    config_path = Path(user_home) / ".rtorrent.rc"
    config_content = build_rtorrent_config_content(username, scgi_port, torrent_port, bind_address_directive, scgi_unix_socket)

    if config_path.exists() and not force_config:
        print(f"Config already exists: {config_path}")
        print("Not overwriting existing config. Proposed generated config would be:")
        print("--- BEGIN PROPOSED .rtorrent.rc ---")
        print(config_content, end="")
        print("--- END PROPOSED .rtorrent.rc ---")
        print("Use --force-config to overwrite the existing config.")
        return False

    config_path.write_text(config_content)
    shutil.chown(config_path, user=username, group=username)
    print(f"Wrote config: {config_path}")
    return True


def prepare_user_dirs(user_home, username):
    for d in [Path(user_home) / "downloads", Path(user_home) / ".session", Path(user_home) / "watch"]:
        ensure_dir(d, owner=username, group=username, mode=0o755)
    shutil.chown(Path(user_home), user=username, group=username)


def enable_service(user, *, debug=False):
    unit_name = f"rtorrent@{user}.service"
    run(["systemctl", "enable", "--now", unit_name], debug=debug)
    print(f"Enabled and started {unit_name}")


def print_link_lines(title, lines):
    print(title)
    for line in lines:
        print(line)


def print_optional_libs_explanation():
    print("Optional libraries:")
    print("  - c-ares: asynchronous DNS resolver. It helps avoid blocking DNS lookups and can improve tracker/DHT-heavy workloads when curl is built with AsynchDNS support.")
    print("  - curl: HTTP/HTTPS transfer library used by libtorrent for tracker/web requests. Building a fresh curl can provide newer TLS/HTTP fixes and c-ares based async DNS.")
    print("  - minimal build: builds only libtorrent and rTorrent; it uses the system libraries already available on Debian/RHEL.")


def resolve_optional_build_mode(args):
    requested = [name for name, enabled in [
        ("--minimal", args.minimal),
        ("--with-cares", args.with_cares),
        ("--with-curl", args.with_curl),
        ("--no-cares", args.no_cares),
        ("--no-curl", args.no_curl),
    ] if enabled]

    if args.minimal and (args.with_cares or args.with_curl):
        raise InstallError("Conflicting options: --minimal cannot be used with --with-cares or --with-curl.")
    if args.no_curl and args.with_curl:
        raise InstallError("Conflicting options: --no-curl cannot be used with --with-curl.")
    if args.no_cares and (args.with_cares or args.with_curl):
        raise InstallError("Conflicting options: --no-cares cannot be used with --with-cares or --with-curl.")

    if args.minimal or args.no_curl:
        return False
    if args.with_curl or args.with_cares:
        return True
    if args.no_cares:
        return False

    if args.yes:
        return False

    print_optional_libs_explanation()
    return prompt_yes_no(
        "Build additional c-ares and newest custom curl?",
        default=False,
        assume_yes=False,
    )


def verify_libtorrent_curl_integration(base_dir, libtorrent_install, curl_install, cares_install, *, debug=False):
    libtorrent_so = next((p for p in sorted((Path(libtorrent_install) / "lib").glob("libtorrent.so*")) if p.is_file() and not p.is_symlink()), None)
    if not libtorrent_so:
        raise InstallError("Could not find compiled libtorrent shared object for verification.")

    libtorrent_linked = capture(["ldd", str(libtorrent_so)], check=True, debug=debug)
    curl_lines = [line for line in libtorrent_linked.splitlines() if "libcurl" in line.lower()]
    print_link_lines("Linked libcurl lines (from libtorrent):", curl_lines)

    expected_curl = str(Path(curl_install) / "lib")
    if curl_lines:
        if not any(expected_curl in line for line in curl_lines):
            raise InstallError(f"libtorrent does not appear to be linked against the compiled libcurl from {expected_curl}.")
    else:
        config_log = Path(base_dir) / "libtorrent" / "config.log"
        config_text = config_log.read_text(errors="ignore") if config_log.exists() else ""
        curl_config = str(Path(curl_install) / "bin" / "curl-config")
        if curl_config not in config_text and expected_curl not in config_text:
            raise InstallError(
                "libtorrent does not expose libcurl in ldd, and config.log does not show the custom curl path either. "
                "The build likely used the system curl or no curl integration."
            )
        print("libtorrent does not show libcurl in ldd; accepting config.log evidence of custom curl usage.")

    custom_curl = Path(curl_install) / "bin" / "curl"
    curl_version = capture([str(custom_curl), "--version"], env=build_env(curl_install, cares_install), check=True, debug=debug)
    print("Custom curl version:")
    print(curl_version.splitlines()[0])
    lower = curl_version.lower()
    if "asynchdns" not in lower:
        raise InstallError("Custom curl does not report AsynchDNS support.")
    if "c-ares" not in lower and "ares" not in lower:
        print("Warning: curl --version does not explicitly show c-ares. Continuing because AsynchDNS is present.")

    if cares_install:
        cares_lines = [line for line in libtorrent_linked.splitlines() if "cares" in line.lower()]
        print_link_lines("Linked c-ares lines (from libtorrent):", cares_lines)
        if not cares_lines:
            print("c-ares is not visible in libtorrent ldd; this can still be valid when libcurl is resolved differently.")


def verify_install(base_dir, rtorrent_install, libtorrent_install, rpc_backend, xmlrpc_install=None, curl_install=None, cares_install=None, *, debug=False):
    rtorrent_bin = Path(rtorrent_install) / "bin" / "rtorrent"
    which_rtorrent = capture(["which", "rtorrent"], check=False, debug=debug) or "not found in PATH"
    print(f"Resolved rtorrent from PATH: {which_rtorrent}")

    linked = capture(["ldd", str(rtorrent_bin)], check=True, debug=debug)
    checks = [("libtorrent", str(Path(libtorrent_install) / "lib"))]

    if rpc_backend == "xmlrpc-c":
        checks.append(("xmlrpc", str(Path(xmlrpc_install) / "lib")))

    for libname, expected in checks:
        lines = [line for line in linked.splitlines() if libname in line]
        print_link_lines(f"Linked {libname} lines:", lines)
        if not any(expected in line for line in lines):
            raise InstallError(f"rtorrent does not appear to be linked against the compiled {libname} from {expected}.")

    if rpc_backend == "tinyxml2":
        tinyxml_lines = [line for line in linked.splitlines() if "tinyxml2" in line.lower()]
        print_link_lines("Linked tinyxml2 lines:", tinyxml_lines)

        if not tinyxml_lines:
            config_log = Path(base_dir) / "rtorrent" / "config.log"
            config_text = config_log.read_text(errors="ignore").lower() if config_log.exists() else ""

            tinyxml2_evidence = (
                "with-xmlrpc-tinyxml2" in config_text
                or "xmlrpc-tinyxml2" in config_text
                or "tinyxml2" in config_text
            )

            if tinyxml2_evidence:
                print("tinyxml2 is not visible in ldd; accepting config.log evidence of tinyxml2/XML-RPC build.")
            else:
                raise InstallError(
                    "rTorrent does not expose tinyxml2 in ldd, and config.log does not show tinyxml2/XML-RPC support."
                )

    if curl_install:
        verify_libtorrent_curl_integration(base_dir, libtorrent_install, curl_install, cares_install, debug=debug)

    env = build_env(libtorrent_install, xmlrpc_install if rpc_backend == "xmlrpc-c" else None, curl_install, cares_install)
    env["LANG"] = "C"
    env["LC_ALL"] = "C"
    env["TERM"] = env.get("TERM", "xterm")

    ld_paths = [str(Path(libtorrent_install) / "lib")]

    if rpc_backend == "xmlrpc-c" and xmlrpc_install:
        ld_paths.append(str(Path(xmlrpc_install) / "lib"))

    if curl_install:
        ld_paths.append(str(Path(curl_install) / "lib"))

    if cares_install:
        ld_paths.append(str(Path(cares_install) / "lib"))

    env["LD_LIBRARY_PATH"] = ":".join(ld_paths)

    probe = subprocess.run(
        [str(rtorrent_bin), "-h"],
        env=env,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    help_output = ((probe.stdout or "") + "\n" + (probe.stderr or "")).lower()

    if probe.returncode != 0:
        raise InstallError("Compiled rTorrent binary exists but cannot run with the generated runtime environment.")

    if "xmlrpc-c" in help_output and "i8" in help_output:
        raise InstallError(
            "rTorrent was built against an xmlrpc-c library without i8 support. "
            "Make sure the custom xmlrpc-c build is used and that no older local installation shadows it."
        )

def build_parser():
    parser = argparse.ArgumentParser(description="Installer for libtorrent + rTorrent under /opt. RPC defaults to tinyxml2; xmlrpc-c is optional.")
    parser.add_argument("--base-dir", default=DEFAULT_BASE_DIR, help=f"Base build/install directory (default: {DEFAULT_BASE_DIR})")
    parser.add_argument("--libtorrent-ref", default=DEFAULT_LIBTORRENT_REF, help=f"Git branch, tag or commit for libtorrent (default: {DEFAULT_LIBTORRENT_REF})")
    parser.add_argument("--rtorrent-ref", default=DEFAULT_RTORRENT_REF, help=f"Git branch, tag or commit for rtorrent (default: {DEFAULT_RTORRENT_REF})")
    parser.add_argument("--xmlrpc-ref", default=DEFAULT_XMLRPC_REF, help="xmlrpc-c source version or URL. Used only with --with-xmlrpc-c (default: latest-stable)")
    parser.add_argument("--with-xmlrpc-c", action="store_true", help="Build rTorrent with classic xmlrpc-c instead of the default tinyxml2 XML-RPC backend.")
    parser.add_argument("--cares-ref", default=DEFAULT_CARES_REF, help=f"c-ares release version (default: {DEFAULT_CARES_REF})")
    parser.add_argument("--curl-ref", default=DEFAULT_CURL_REF, help=f"curl release version (default: {DEFAULT_CURL_REF})")
    parser.add_argument("--user", default=DEFAULT_USER, help=f"System user for the service (default: {DEFAULT_USER})")
    parser.add_argument("--group", default=DEFAULT_GROUP, help=f"System group for the service (default: {DEFAULT_GROUP})")
    parser.add_argument("--home", default=DEFAULT_HOME, help=f"Home directory for the service user (default: {DEFAULT_HOME})")
    parser.add_argument("--scgi-port", type=int, default=DEFAULT_SCGI_PORT, help=f"SCGI listen port for rTorrent XMLRPC/SCGI (default: {DEFAULT_SCGI_PORT})")
    parser.add_argument("--scgi-unix-socket", default="", help="Use Unix socket SCGI listener instead of TCP, for example /run/rtorrent/rtorrent.sock")
    parser.add_argument("--torrent-port", type=int, default=DEFAULT_TORRENT_PORT, help=f"Incoming BitTorrent listen port (default: {DEFAULT_TORRENT_PORT})")
    parser.add_argument("--force-config", action="store_true", help="Overwrite existing ~/.rtorrent.rc. By default, existing config is left unchanged and the proposed changes are printed.")
    parser.add_argument("--only-build", action="store_true", help="Only build and install libtorrent/rTorrent under /opt. Skip user, config and systemd.")
    parser.add_argument("--yes", action="store_true", help="Assume yes for interactive prompts; optional c-ares/curl remain disabled unless --with-curl or --with-cares is used.")
    parser.add_argument("--debug", action="store_true", help="Show full command output during build steps.")
    parser.add_argument("--minimal", "--core-only", action="store_true", help="Build only libtorrent and rTorrent. Do not build c-ares or custom curl.")
    parser.add_argument("--no-cares", "--without-cares", dest="no_cares", action="store_true", help="Do not build c-ares. This also disables custom curl integration.")
    parser.add_argument("--no-curl", "--without-curl", dest="no_curl", action="store_true", help="Do not build custom curl. Implies no c-ares integration for libtorrent.")
    parser.add_argument("--with-cares", action="store_true", help="Build c-ares and custom curl with asynchronous DNS support.")
    parser.add_argument("--with-curl", action="store_true", help="Build newest custom curl; c-ares is enabled unless --no-cares is used.")
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    args.use_cares = resolve_optional_build_mode(args)

    require_root()
    os_family = detect_os_family()

    if os_family == "arch":
        packages = [
            "base-devel", "pkgconf", "libtool", "autoconf", "automake", "git", "ca-certificates",
            "openssl", "ncurses", "expat", "curl", "libxml2", "tinyxml2", "readline", "tar", "gzip", "xz",
            "zlib", "bison", "flex", "m4", "gettext", "texinfo", "patch", "diffutils", "file", "procps-ng"
        ]
        if args.use_cares:
            packages.extend(["cmake", "libpsl", "brotli", "zstd"])
    else:
        packages = [
            "build-essential", "pkg-config", "libtool", "autoconf", "automake", "git", "ca-certificates",
            "libssl-dev", "libncurses-dev", "libncurses5-dev", "libncursesw5-dev", "libexpat1-dev",
            "libcurl4-openssl-dev", "libxml2-dev", "libtinyxml2-dev", "libreadline-dev", "curl", "tar", "gzip", "xz-utils",
            "zlib1g-dev", "bison", "flex", "m4", "gettext", "texinfo", "patch", "diffutils", "file", "procps"
        ]
        if args.use_cares:
            packages.extend(["cmake", "libpsl-dev", "libbrotli-dev", "libzstd-dev"])

    print("This script will:")
    args.rpc_backend = "xmlrpc-c" if args.with_xmlrpc_c else DEFAULT_RPC_BACKEND

    print(f"  - use rTorrent RPC backend: {args.rpc_backend}")
    if args.rpc_backend == "xmlrpc-c":
        print(f"  - build xmlrpc-c from '{args.xmlrpc_ref}'")
    else:
        print("  - use system tinyxml2 for XML-RPC")
    print(f"  - build libtorrent from '{args.libtorrent_ref}'")
    print(f"  - build rtorrent from '{args.rtorrent_ref}'")
    if args.use_cares:
        print(f"  - build c-ares from '{args.cares_ref}'")
        print(f"  - build curl from '{args.curl_ref}' with c-ares")
        print("  - benefit: async DNS via c-ares and newer curl for HTTP/HTTPS tracker requests")
    else:
        print("  - minimal build: skip c-ares/custom curl")
        print("  - build only libtorrent and rTorrent; use Debian system libraries")
    print(f"  - install everything under '{args.base_dir}'")
    if args.only_build:
        print("  - skip service user, config and systemd setup")
    else:
        print(f"  - configure systemd service for user '{args.user}'")
        print(f"  - use SCGI socket {args.scgi_unix_socket} and torrent port {args.torrent_port}" if args.scgi_unix_socket else f"  - use SCGI port {args.scgi_port} and torrent port {args.torrent_port}")

    if not prompt_yes_no("Continue?", default=True, assume_yes=args.yes):
        print("Aborted by user.")
        return 1

    ensure_packages(packages, family=os_family, debug=args.debug)
    ensure_dir(args.base_dir)

    xmlrpc_install = None
    xmlrpc_version = None
    if args.rpc_backend == "xmlrpc-c":
        xmlrpc_install, xmlrpc_version = build_xmlrpc_c(args.base_dir, args.xmlrpc_ref, debug=args.debug)

    cares_install = None
    cares_version = None
    curl_install = None
    curl_version = None

    if args.use_cares:
        cares_install, cares_version = build_cares(args.base_dir, args.cares_ref, debug=args.debug)
        curl_install, curl_version = build_curl(args.base_dir, args.curl_ref, cares_install, debug=args.debug)

    libtorrent_install, libtorrent_version = build_libtorrent(
        args.base_dir, args.libtorrent_ref, curl_install=curl_install, cares_install=cares_install, debug=args.debug
    )
    rtorrent_install, rtorrent_version = build_rtorrent(
        args.base_dir, args.rtorrent_ref, libtorrent_install, args.rpc_backend, xmlrpc_install=xmlrpc_install,
        curl_install=curl_install, cares_install=cares_install, debug=args.debug
    )

    install_symlinks(rtorrent_install, libtorrent_install, xmlrpc_install=xmlrpc_install, curl_install=curl_install, cares_install=cares_install, debug=args.debug)
    verify_install(args.base_dir, rtorrent_install, libtorrent_install, args.rpc_backend, xmlrpc_install=xmlrpc_install, curl_install=curl_install, cares_install=cares_install, debug=args.debug)

    if not args.only_build:
        create_system_user(args.user, args.group, args.home, assume_yes=args.yes, debug=args.debug)
        prepare_user_dirs(args.home, args.user)
        bind_address_directive = rtorrent_bind_address_directive(args.rtorrent_ref, rtorrent_version)
        print(f"Using rTorrent bind address directive: {bind_address_directive}")
        write_rtorrent_config(args.home, args.user, args.scgi_port, args.torrent_port, bind_address_directive, force_config=args.force_config, scgi_unix_socket=args.scgi_unix_socket or None)
        runtime_lib_dirs = [f"{libtorrent_install}/lib"]
        if args.rpc_backend == "xmlrpc-c" and xmlrpc_install:
            runtime_lib_dirs.append(f"{xmlrpc_install}/lib")
        if curl_install:
            runtime_lib_dirs.append(f"{curl_install}/lib")
        if cares_install:
            runtime_lib_dirs.append(f"{cares_install}/lib")
        write_service(DEFAULT_SERVICE_PATH, "/usr/local/bin/rtorrent", ":".join(runtime_lib_dirs))
        enable_service(args.user, debug=args.debug)
        print(f"\nService status hint: systemctl status rtorrent@{args.user}.service")

    print("\nBuild summary")
    print("-------------")
    print(f"rpc:        {args.rpc_backend}")
    if args.rpc_backend == "xmlrpc-c":
        print(f"xmlrpc-c:   {xmlrpc_version}")
    print(f"libtorrent: {libtorrent_version}")
    print(f"rtorrent:   {rtorrent_version.splitlines()[0] if rtorrent_version else args.rtorrent_ref}")
    if args.use_cares:
        print(f"c-ares:     {cares_version}")
        print(f"curl:       {curl_version.splitlines()[0] if curl_version else args.curl_ref}")
    else:
        print("c-ares:     disabled")
        print("curl:       system")
    print("binary:     /usr/local/bin/rtorrent")
    print(f"base dir:   {args.base_dir}")
    print("\nDone.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(130)
    except InstallError as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        sys.exit(1)
