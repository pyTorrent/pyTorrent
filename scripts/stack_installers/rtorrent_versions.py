from __future__ import annotations

import re
from dataclasses import dataclass

DEFAULT_LIBTORRENT_REF = "v0.16.11"
DEFAULT_RTORRENT_REF = "v0.16.11"
DEFAULT_XMLRPC_REF = "latest-stable"
DEFAULT_RPC_BACKEND = "tinyxml2"
MODERN_MATCHED_VERSION_MIN = (0, 15, 7)


class VersionSelectionError(ValueError):
    pass


@dataclass(frozen=True)
class BuildSelection:
    rtorrent_ref: str
    libtorrent_ref: str
    rpc_backend: str
    xmlrpc_ref: str
    version: str | None = None


def _semantic_version(value: str | None) -> tuple[int, int, int] | None:
    text = str(value or "").strip()
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", text)
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def _normalized_version(value: str) -> str:
    parsed = _semantic_version(value)
    if parsed is None:
        raise VersionSelectionError("--version must use MAJOR.MINOR.PATCH format, for example 0.9.8 or 0.16.11.")
    return ".".join(str(part) for part in parsed)


def _preset_for_version(version: str) -> BuildSelection:
    normalized = _normalized_version(version)
    parsed = _semantic_version(normalized)
    assert parsed is not None

    if parsed == (0, 9, 8):
        # Note: rTorrent 0.9.8 requires the legacy libtorrent 0.13.8 line and has no tinyxml2 RPC backend.
        return BuildSelection("v0.9.8", "v0.13.8", "xmlrpc-c", DEFAULT_XMLRPC_REF, normalized)

    if parsed >= MODERN_MATCHED_VERSION_MIN:
        # Note: Modern presets keep rTorrent/libtorrent on the same tag and use the tinyxml2 XML-RPC backend by default.
        ref = f"v{normalized}"
        return BuildSelection(ref, ref, "tinyxml2", DEFAULT_XMLRPC_REF, normalized)

    raise VersionSelectionError(
        f"No automatic dependency preset is defined for rTorrent {normalized}. "
        "Use --rtorrent-ref and --libtorrent-ref explicitly; versions below 0.15.7 use xmlrpc-c automatically."
    )


def resolve_build_selection(
    *,
    version: str | None,
    rtorrent_ref: str | None,
    libtorrent_ref: str | None,
    xmlrpc_ref: str | None,
    force_xmlrpc_c: bool = False,
) -> BuildSelection:
    if version:
        selection = _preset_for_version(version)
        if force_xmlrpc_c and selection.rpc_backend != "xmlrpc-c":
            selection = BuildSelection(
                selection.rtorrent_ref,
                selection.libtorrent_ref,
                "xmlrpc-c",
                xmlrpc_ref or selection.xmlrpc_ref,
                selection.version,
            )
        elif xmlrpc_ref:
            selection = BuildSelection(
                selection.rtorrent_ref,
                selection.libtorrent_ref,
                selection.rpc_backend,
                xmlrpc_ref,
                selection.version,
            )
        return selection

    resolved_rtorrent = rtorrent_ref or DEFAULT_RTORRENT_REF
    rtorrent_version = _semantic_version(resolved_rtorrent)

    if rtorrent_version == (0, 9, 8) and not libtorrent_ref:
        resolved_libtorrent = "v0.13.8"
    elif rtorrent_version and rtorrent_version >= MODERN_MATCHED_VERSION_MIN and not libtorrent_ref:
        resolved_libtorrent = f"v{'.'.join(str(part) for part in rtorrent_version)}"
    else:
        resolved_libtorrent = libtorrent_ref or DEFAULT_LIBTORRENT_REF

    if rtorrent_version and rtorrent_version < MODERN_MATCHED_VERSION_MIN:
        if rtorrent_version != (0, 9, 8) and not libtorrent_ref:
            raise VersionSelectionError(
                f"rTorrent {resolved_rtorrent} is a legacy build and needs an explicit matching --libtorrent-ref."
            )
        rpc_backend = "xmlrpc-c"
    else:
        rpc_backend = "xmlrpc-c" if force_xmlrpc_c else DEFAULT_RPC_BACKEND

    return BuildSelection(
        resolved_rtorrent,
        resolved_libtorrent,
        rpc_backend,
        xmlrpc_ref or DEFAULT_XMLRPC_REF,
        None,
    )
