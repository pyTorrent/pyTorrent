#!/usr/bin/env python3
"""Find and optionally remove rTorrent payloads not referenced by selected pyTorrent profiles."""

from __future__ import annotations

import argparse
import os
import posixpath
import re
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from rtorrent_cli import make_rpc_client  # noqa: E402


@dataclass(frozen=True)
class MountInfo:
    mount_point: Path
    fs_type: str
    source: str


def default_database_path() -> Path:
    # Note: Mirror pyTorrent's DB path convention without importing the Flask application package into this standalone CLI.
    raw = os.getenv("PYTORRENT_DB_PATH", "").strip()
    if not raw:
        env_file = PROJECT_ROOT / ".env"
        if env_file.is_file():
            for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
                text = line.strip()
                if not text or text.startswith("#"):
                    continue
                if text.startswith("export "):
                    text = text[7:].lstrip()
                if not text.startswith("PYTORRENT_DB_PATH="):
                    continue
                raw = text.split("=", 1)[1].strip().strip('"').strip("'")
                break
    path = Path(raw or "data/pytorrent.sqlite3").expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


RTORRENT_PATH_FIELDS = (
    "d.hash=",
    "d.name=",
    "d.directory=",
    "d.base_path=",
    "d.is_multi_file=",
)


def parse_args() -> argparse.Namespace:
    # Note: Dry-run is the default; storage roots are shared filesystem locations, while --profile optionally limits which rTorrent inventory is checked.
    parser = argparse.ArgumentParser(
        description=(
            "Compare configured pyTorrent rTorrent profiles with one local download directory "
            "and report filesystem entries that are not referenced by any checked torrent."
        )
    )
    parser.add_argument(
        "download_dir",
        help=(
            "Local directory to scan. It may directly contain rTorrent payloads or shared NFS mount roots "
            "such as node1/ and node2/."
        ),
    )
    parser.add_argument(
        "--db",
        default=str(default_database_path()),
        help=f"pyTorrent SQLite database path (default: {default_database_path()})",
    )
    parser.add_argument(
        "--storage-root",
        action="append",
        default=[],
        metavar="LOCAL_ROOT",
        help=(
            "Add a shared local payload root below download_dir. Every checked rTorrent profile may reference every storage root. "
            "May be repeated, e.g. --storage-root node1 --storage-root node2. Active top-level NFS mounts are discovered automatically."
        ),
    )
    parser.add_argument(
        "--profile",
        metavar="PROFILE",
        help=(
            "Check only one rTorrent profile, selected by exact name or numeric profile ID. "
            "Use this only when the scanned directory is exclusively owned by that profile."
        ),
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Delete reported orphaned files/directories. Refused if any profile/path/mount scan is incomplete.",
    )
    return parser.parse_args()


def open_database(path: Path) -> sqlite3.Connection:
    # Note: The cleanup CLI opens SQLite read-only so filesystem cleanup cannot mutate pyTorrent state.
    uri = f"{path.resolve().as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def load_profiles(db_path: Path) -> list[dict[str, Any]]:
    # Note: Read profiles directly from SQLite so the CLI checks every configured profile, independent of web-session permissions.
    if not db_path.is_file():
        raise FileNotFoundError(f"pyTorrent database not found: {db_path}")
    with open_database(db_path) as conn:
        rows = conn.execute(
            "SELECT id, user_id, name, scgi_url, timeout_seconds, is_remote FROM rtorrent_profiles ORDER BY id"
        ).fetchall()
    return [dict(row) for row in rows]


def remote_payload_path(row: list[Any]) -> str:
    # Note: Mirror pyTorrent's data-path rule: d.base_path is authoritative; the directory/name fallback covers older sessions.
    name = str(row[1] or "").strip()
    directory = str(row[2] or "").strip()
    base_path = str(row[3] or "").strip()
    is_multi_file = bool(int(row[4] or 0))
    if base_path:
        return posixpath.normpath(base_path)
    if is_multi_file:
        return posixpath.normpath(directory) if directory else ""
    if directory and name:
        return posixpath.normpath(posixpath.join(directory, name))
    return posixpath.normpath(directory) if directory else ""


def query_profile_default_root(client: Any) -> tuple[str, str]:
    # Note: Use the same fallback order as pyTorrent so explicit/local mount mapping follows the profile's live rTorrent path namespace.
    errors: list[str] = []
    for method in ("directory.default", "system.cwd"):
        try:
            value = str(getattr(client, method)() or "").strip()
            if value and posixpath.isabs(value):
                return posixpath.normpath(value), ""
        except Exception as exc:
            errors.append(f"{method}: {exc}")
    return "", "; ".join(errors) or "rTorrent returned no absolute default directory"


def query_profile_payloads(profile: dict[str, Any]) -> tuple[list[dict[str, str]], list[str], str, str]:
    # Note: One d.multicall2 per profile avoids per-torrent RPC round trips; one small directory query also enables local NFS namespace mapping.
    client = make_rpc_client(str(profile["scgi_url"]), int(profile.get("timeout_seconds") or 5))
    rows = client.d.multicall2("", "main", *RTORRENT_PATH_FIELDS)
    payloads: list[dict[str, str]] = []
    unresolved: list[str] = []
    for raw in rows or []:
        row = list(raw)
        while len(row) < len(RTORRENT_PATH_FIELDS):
            row.append(0)
        torrent_hash = str(row[0] or "").strip()
        path = remote_payload_path(row)
        if not path or not posixpath.isabs(path):
            unresolved.append(torrent_hash or "<unknown hash>")
            continue
        payloads.append({"hash": torrent_hash, "path": path})
    default_root, default_root_error = query_profile_default_root(client)
    return payloads, unresolved, default_root, default_root_error


def normalized_absolute_path(value: str | Path) -> Path:
    # Note: Keep path comparison lexical (no symlink dereference) because mapped rTorrent and local mount namespaces are intentionally different.
    return Path(os.path.abspath(os.path.normpath(os.fspath(value))))


def is_within(path: Path, root: Path) -> bool:
    # Note: commonpath prevents prefix tricks such as /downloads-old being treated as a child of /downloads.
    try:
        return os.path.commonpath((str(path), str(root))) == str(root)
    except ValueError:
        return False


def remote_is_within(path: str, root: str) -> bool:
    # Note: POSIX component-aware comparison avoids treating /downloads-old as a child of /downloads during shared-root translation.
    clean_path = posixpath.normpath(str(path or ""))
    clean_root = posixpath.normpath(str(root or ""))
    if not posixpath.isabs(clean_path) or not posixpath.isabs(clean_root):
        return False
    try:
        return posixpath.commonpath((clean_path, clean_root)) == clean_root
    except ValueError:
        return False


def decode_mount_field(value: str) -> str:
    # Note: Linux mountinfo/fstab escape whitespace and backslashes with octal sequences; decode only the documented path escapes.
    replacements = {"\\040": " ", "\\011": "\t", "\\012": "\n", "\\134": "\\"}
    result = value
    for encoded, decoded in replacements.items():
        result = result.replace(encoded, decoded)
    return result


def read_mount_info(root: Path) -> list[MountInfo]:
    # Note: /proc/self/mountinfo identifies active nested mounts without following them, which is required to keep NFS mount roots out of orphan candidates.
    path = Path("/proc/self/mountinfo")
    if not path.is_file():
        return []
    mounts: list[MountInfo] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        fields = line.split()
        try:
            separator = fields.index("-")
            mount_point = normalized_absolute_path(decode_mount_field(fields[4]))
            fs_type = fields[separator + 1]
            source = decode_mount_field(fields[separator + 2])
        except (ValueError, IndexError):
            continue
        if mount_point != root and is_within(mount_point, root):
            mounts.append(MountInfo(mount_point=mount_point, fs_type=fs_type, source=source))
    return sorted({item.mount_point: item for item in mounts}.values(), key=lambda item: (len(item.mount_point.parts), str(item.mount_point)))


def read_expected_mount_points(root: Path) -> set[Path]:
    # Note: Expected fstab mount targets are protected even when temporarily unmounted so a failed NFS mount cannot expose its local mount directory as deletable data.
    path = Path("/etc/fstab")
    if not path.is_file():
        return set()
    expected: set[Path] = set()
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        fields = re.split(r"\s+", line)
        if len(fields) < 3:
            continue
        mount_point = normalized_absolute_path(decode_mount_field(fields[1]))
        if mount_point != root and is_within(mount_point, root):
            expected.add(mount_point)
    return expected


def top_level_mounts(mounts: list[MountInfo], root: Path) -> list[MountInfo]:
    # Note: Only nearest nested mounts are considered profile storage roots; deeper mounts are still protected as filesystem boundaries.
    selected: list[MountInfo] = []
    for mount in mounts:
        if any(mount.mount_point != parent.mount_point and is_within(mount.mount_point, parent.mount_point) for parent in selected):
            continue
        if is_within(mount.mount_point, root):
            selected.append(mount)
    return selected


def resolve_profile_selector(selector: str, profiles: list[dict[str, Any]]) -> dict[str, Any]:
    # Note: Profile scope accepts either the exact profile name or its numeric database ID so cleanup can be safely limited to a known owner.
    raw = str(selector or "").strip()
    if raw.isdigit():
        matches = [profile for profile in profiles if int(profile.get("id") or 0) == int(raw)]
    else:
        matches = [profile for profile in profiles if str(profile.get("name") or "").casefold() == raw.casefold()]
    if len(matches) != 1:
        raise ValueError(f"profile selector {selector!r} matched {len(matches)} profiles")
    return matches[0]


def resolve_storage_root(value: str, root: Path) -> Path:
    # Note: Shared storage roots may be given relative to download_dir, but they can never escape the selected cleanup tree.
    raw = Path(str(value or "").strip()).expanduser()
    local_root = normalized_absolute_path(raw if raw.is_absolute() else root / raw)
    if local_root == root or not is_within(local_root, root):
        raise ValueError(f"storage root must be a child of {root}: {value}")
    if not local_root.is_dir():
        raise ValueError(f"storage root is not an accessible directory: {local_root}")
    return local_root


def collect_storage_roots(
    root: Path,
    active_mounts: list[MountInfo],
    explicit_values: list[str],
) -> tuple[set[Path], dict[Path, str], list[str]]:
    # Note: Storage roots are global filesystem locations, never profile assignments; all top-level NFS mounts are shared candidates.
    roots: set[Path] = set()
    sources: dict[Path, str] = {}
    errors: list[str] = []

    for mount in top_level_mounts(active_mounts, root):
        if mount.fs_type.lower().startswith("nfs"):
            roots.add(mount.mount_point)
            sources[mount.mount_point] = f"auto {mount.fs_type} {mount.source}"

    for value in explicit_values:
        try:
            local_root = resolve_storage_root(value, root)
            roots.add(local_root)
            sources[local_root] = "explicit --storage-root"
        except Exception as exc:
            errors.append(str(exc))

    return roots, sources, errors


def payload_local_candidates(remote_path: str, default_root: str, root: Path, storage_roots: set[Path]) -> list[Path]:
    # Note: Resolve each torrent independently across all shared roots; actual path existence decides the node instead of the profile identity.
    clean = posixpath.normpath(str(remote_path or ""))
    candidates: list[Path] = []

    identity = normalized_absolute_path(clean)
    if is_within(identity, root):
        candidates.append(identity)

    remote_root = posixpath.normpath(str(default_root or ""))
    if remote_root and remote_is_within(clean, remote_root):
        relative = posixpath.relpath(clean, remote_root)
        if relative != ".":
            relative_parts = PurePosixPath(relative).parts
            for storage_root in sorted(storage_roots):
                local = normalized_absolute_path(storage_root.joinpath(*relative_parts))
                if is_within(local, storage_root):
                    candidates.append(local)

    unique: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        if candidate not in seen:
            seen.add(candidate)
            unique.append(candidate)
    return unique


def resolve_payload_paths(remote_path: str, default_root: str, root: Path, storage_roots: set[Path]) -> tuple[list[Path], list[Path], bool]:
    # Note: Existing candidates are protected; duplicate matches on multiple nodes are deliberately marked ambiguous and block destructive cleanup.
    candidates = payload_local_candidates(remote_path, default_root, root, storage_roots)
    existing = [path for path in candidates if os.path.lexists(path)]
    if existing:
        return existing, [], len(existing) > 1
    return [], candidates, False


def add_ancestor_chain(ancestors: set[Path], path: Path, root: Path) -> None:
    # Note: Protect every parent that leads to a referenced payload or mount boundary so the scanner descends to the exact safe decision point.
    current = path
    while is_within(current, root):
        ancestors.add(current)
        if current == root:
            break
        current = current.parent


def build_protected_paths(
    root: Path,
    payload_paths: list[Path],
    storage_roots: set[Path],
    active_mounts: list[MountInfo],
    opaque_mounts: set[Path],
    inactive_expected_mounts: set[Path],
) -> tuple[set[Path], set[Path]]:
    # Note: Torrent payloads are exact protected roots, shared storage roots are scanned, and unknown/inactive mounts are opaque boundaries that are never orphan candidates.
    exact: set[Path] = set(payload_paths)
    ancestors: set[Path] = {root}
    for path in payload_paths:
        add_ancestor_chain(ancestors, path.parent, root)
    for local_root in storage_roots:
        add_ancestor_chain(ancestors, local_root, root)
    for mount in active_mounts:
        add_ancestor_chain(ancestors, mount.mount_point, root)
    for mount_point in opaque_mounts | inactive_expected_mounts:
        if os.path.lexists(mount_point):
            exact.add(mount_point)
        add_ancestor_chain(ancestors, mount_point.parent, root)
    return exact, ancestors


def find_orphans(root: Path, exact: set[Path], ancestors: set[Path]) -> list[Path]:
    # Note: Stop descending at a referenced payload or opaque mount root; torrent-owned internals and unknown filesystems must never be separate orphan candidates.
    if root in exact:
        return []
    orphans: list[Path] = []

    def walk(directory: Path) -> None:
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name.casefold())
        except OSError as exc:
            raise RuntimeError(f"Cannot scan {directory}: {exc}") from exc
        for entry in entries:
            child = normalized_absolute_path(entry.path)
            if child in exact:
                continue
            if child in ancestors:
                if entry.is_dir(follow_symlinks=False):
                    walk(child)
                continue
            orphans.append(child)

    walk(root)
    return orphans


def orphan_kind(path: Path) -> str:
    # Note: Symlinks are reported separately and are never followed during scan or deletion.
    if path.is_symlink():
        return "SYMLINK"
    if path.is_dir():
        return "DIR"
    return "FILE"


def delete_orphan(path: Path, root: Path, root_real: Path) -> None:
    # Note: Destructive operations stay below the selected root and never follow a symlink outside it.
    if path == root or not is_within(path, root):
        raise RuntimeError(f"Refusing unsafe path: {path}")
    if not os.path.lexists(path):
        return
    if path.is_symlink() or not path.is_dir():
        path.unlink()
        return
    resolved = path.resolve(strict=True)
    if not is_within(resolved, root_real):
        raise RuntimeError(f"Refusing directory that resolves outside the download root: {path} -> {resolved}")
    shutil.rmtree(path)


def relative_display(path: Path, root: Path) -> str:
    # Note: Human output stays relative to the selected download root so long paths remain readable.
    return os.path.relpath(path, root)


def main() -> int:
    # Note: Destructive cleanup requires a complete profile inventory and unambiguous per-payload resolution across all shared storage roots.
    args = parse_args()
    db_path = Path(args.db).expanduser()
    root = normalized_absolute_path(Path(args.download_dir).expanduser())
    if not root.is_dir():
        print(f"ERROR: download directory not found or not a directory: {root}", file=sys.stderr)
        return 2
    root_real = root.resolve(strict=True)
    if root_real == Path(root_real.anchor):
        print("ERROR: refusing to scan the filesystem root.", file=sys.stderr)
        return 2

    try:
        all_profiles = load_profiles(db_path)
    except Exception as exc:
        print(f"ERROR: cannot read pyTorrent database: {exc}", file=sys.stderr)
        return 2
    if not all_profiles:
        print("ERROR: no rTorrent profiles found in the pyTorrent database.", file=sys.stderr)
        return 2

    if args.profile:
        try:
            profiles = [resolve_profile_selector(args.profile, all_profiles)]
        except Exception as exc:
            print(f"ERROR: invalid --profile selector: {exc}", file=sys.stderr)
            return 2
    else:
        profiles = all_profiles

    print(f"Database: {db_path}")
    print(f"Download directory: {root}")
    if args.profile:
        selected = profiles[0]
        print(f"Profiles checked: 1 of {len(all_profiles)}")
        print(f"Profile scope: #{selected['id']} {selected['name']} (only this profile is considered)")
    else:
        print(f"Profiles checked: {len(profiles)}")

    profile_results: dict[int, dict[str, Any]] = {}
    profile_errors: list[str] = []
    unresolved_total: list[str] = []
    total_torrents = 0
    for profile in profiles:
        profile_id = int(profile["id"])
        label = f"#{profile_id} {profile['name']}"
        try:
            payloads, unresolved, default_root, default_root_error = query_profile_payloads(profile)
            total_torrents += len(payloads) + len(unresolved)
            unresolved_total.extend(f"{label}: {torrent_hash}" for torrent_hash in unresolved)
            profile_results[profile_id] = {
                "query_ok": True,
                "payloads": payloads,
                "unresolved": unresolved,
                "default_root": default_root,
                "default_root_error": default_root_error,
            }
            suffix = f", unresolved paths: {len(unresolved)}" if unresolved else ""
            root_suffix = f", root: {default_root}" if default_root else ", root: unavailable"
            remote = " [remote]" if int(profile.get("is_remote") or 0) else ""
            print(f"[OK] {label}{remote}: {len(payloads) + len(unresolved)} torrents{suffix}{root_suffix}")
        except Exception as exc:
            message = f"{label}: {exc}"
            profile_errors.append(message)
            profile_results[profile_id] = {"query_ok": False, "payloads": [], "unresolved": [], "default_root": "", "default_root_error": str(exc)}
            print(f"[ERROR] {message}")

    active_mounts = read_mount_info(root)
    expected_mounts = read_expected_mount_points(root)
    active_mount_points = {mount.mount_point for mount in active_mounts}
    inactive_expected_mounts = {path for path in expected_mounts if path not in active_mount_points}

    storage_roots, storage_root_sources, storage_root_errors = collect_storage_roots(
        root, active_mounts, args.storage_root
    )
    # Note: The selected download directory is itself a path-translation candidate, while nested shared roots define filesystem boundaries to scan.
    translation_roots = set(storage_roots) | {root}
    scanned_mount_points = {path for path in storage_roots if path in active_mount_points}
    opaque_mounts = active_mount_points - scanned_mount_points

    if storage_roots:
        print("\nShared storage roots (available to every profile):")
        for local_root in sorted(storage_roots):
            print(f"  {relative_display(local_root, root)} [{storage_root_sources.get(local_root, 'shared')}]" )
    else:
        print("\nShared storage roots: direct download directory only")

    if storage_root_errors:
        print("\nStorage root errors:")
        for error in storage_root_errors:
            print(f"  {error}")
    if opaque_mounts:
        print("\nMounted roots outside the shared storage set (protected, not scanned):")
        for path in sorted(opaque_mounts):
            mount = next((item for item in active_mounts if item.mount_point == path), None)
            details = f" ({mount.fs_type} {mount.source})" if mount else ""
            print(f"  {relative_display(path, root)}{details}")
    if inactive_expected_mounts:
        print("\nExpected mount roots are not mounted (protected, not scanned):")
        for path in sorted(inactive_expected_mounts):
            print(f"  {relative_display(path, root)}")

    translated_payloads: list[Path] = []
    outside: list[str] = []
    missing_references: list[str] = []
    ambiguous_references: list[str] = []
    unresolved_mappings: list[str] = []
    for profile in profiles:
        profile_id = int(profile["id"])
        result = profile_results.get(profile_id) or {}
        if not result.get("query_ok"):
            continue
        label = f"#{profile_id} {profile['name']}"
        default_root = str(result.get("default_root") or "")
        for payload in result.get("payloads") or []:
            remote_path = str(payload.get("path") or "")
            existing, missing_candidates, ambiguous = resolve_payload_paths(
                remote_path, default_root, root, translation_roots
            )
            if existing:
                translated_payloads.extend(existing)
                if ambiguous:
                    locations = ", ".join(relative_display(path, root) for path in existing)
                    ambiguous_references.append(f"{label}: {remote_path} -> {locations}")
                continue
            if missing_candidates:
                locations = ", ".join(relative_display(path, root) for path in missing_candidates)
                missing_references.append(f"{label}: {remote_path} -> [{locations}]")
                continue
            if not default_root:
                unresolved_mappings.append(f"{label}: {remote_path} (rTorrent default directory unavailable)")
                continue
            outside.append(f"{label}: {remote_path}")

    payload_exact, ancestors = build_protected_paths(
        root,
        translated_payloads,
        storage_roots,
        active_mounts,
        opaque_mounts,
        inactive_expected_mounts,
    )
    try:
        orphans = find_orphans(root, payload_exact, ancestors)
    except Exception as exc:
        print(f"ERROR: filesystem scan failed: {exc}", file=sys.stderr)
        return 2

    print()
    print(f"Torrents checked: {total_torrents}")
    print(f"Referenced payloads in selected directory: {len(set(translated_payloads))}")
    print(f"Referenced payloads outside selected directory: {len(outside)}")
    print(f"Referenced payloads missing on disk: {len(missing_references)}")
    print(f"Ambiguous payload locations: {len(ambiguous_references)}")
    print(f"Unresolved storage mappings: {len(unresolved_mappings)}")
    print(f"Unresolved torrent paths: {len(unresolved_total)}")
    print(f"Profile query errors: {len(profile_errors)}")
    print(f"Protected mounted roots not scanned: {len(opaque_mounts)}")
    print(f"Inactive expected mounts: {len(inactive_expected_mounts)}")
    print(f"Orphaned filesystem entries: {len(orphans)}")

    if missing_references:
        print("\nMissing referenced payloads (checked across all shared roots):")
        for item in missing_references:
            print(f"  MISSING  {item}")
    if ambiguous_references:
        print("\nAmbiguous referenced payloads (all matches protected):")
        for item in ambiguous_references:
            print(f"  {item}")
    if unresolved_mappings:
        print("\nUnresolved storage mappings:")
        for item in unresolved_mappings:
            print(f"  {item}")
    if unresolved_total:
        print("\nUnresolved torrent paths:")
        for item in unresolved_total:
            print(f"  {item}")
    if outside:
        print("\nReferenced outside selected directory (not scanned):")
        for item in sorted(set(outside)):
            print(f"  {item}")

    if orphans:
        print("\nOrphaned data:")
        for path in orphans:
            print(f"  {orphan_kind(path):7} {relative_display(path, root)}")
    else:
        print("\nNo orphaned data found.")

    incomplete = bool(
        profile_errors
        or unresolved_total
        or storage_root_errors
        or ambiguous_references
        or unresolved_mappings
        or opaque_mounts
        or inactive_expected_mounts
    )
    if not args.delete:
        print("\nDry run only. Re-run with --delete to remove the listed orphaned data.")
        if incomplete:
            print("WARNING: scan is incomplete or ambiguous; deletion would be refused until every profile/path/mount can be checked safely.")
        return 1 if incomplete else 0

    if incomplete:
        print("\nERROR: --delete refused because the profile/path/mount scan is incomplete or ambiguous.", file=sys.stderr)
        return 3

    if not orphans:
        print("\nNothing to delete.")
        return 0

    # Note: Re-check every shared mounted root immediately before deletion so a disappearing NFS mount cannot expose its mountpoint as local data.
    current_mount_points = {mount.mount_point for mount in read_mount_info(root)}
    required_storage_mounts = storage_roots & active_mount_points
    if not required_storage_mounts.issubset(current_mount_points):
        missing_mounts = sorted(required_storage_mounts - current_mount_points)
        print("\nERROR: --delete refused because a shared storage mount disappeared during the scan:", file=sys.stderr)
        for path in missing_mounts:
            print(f"  {relative_display(path, root)}", file=sys.stderr)
        return 3

    deleted = 0
    failures = 0
    print("\nDeleting orphaned data:")
    for path in orphans:
        try:
            live_mount_points = {mount.mount_point for mount in read_mount_info(root)}
            if any(mount == path or is_within(mount, path) for mount in live_mount_points):
                raise RuntimeError("refusing path that is or now contains an active mount")
            delete_orphan(path, root, root_real)
            deleted += 1
            print(f"  DELETED {relative_display(path, root)}")
        except Exception as exc:
            failures += 1
            print(f"  FAILED  {relative_display(path, root)}: {exc}", file=sys.stderr)
    print(f"Deleted: {deleted}; failed: {failures}")
    return 4 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
