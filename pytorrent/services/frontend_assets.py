from __future__ import annotations
from pathlib import Path
from ..config import BASE_DIR, USE_OFFLINE_LIBS

LIBS_STATIC_DIR = "libs"
LIBS_DIR = BASE_DIR / "pytorrent" / "static" / LIBS_STATIC_DIR
BOOTSTRAP_VERSION = "5.3.3"
BOOTSWATCH_VERSION = "5.3.3"
FONTAWESOME_VERSION = "6.5.2"
FLAG_ICONS_VERSION = "7.2.3"
SWAGGER_UI_VERSION = "5"
SOCKET_IO_VERSION = "4.7.5"

GOOGLE_FONT_FAMILIES = (
    "DM Sans",
    "Figtree",
    "Geist",
    "IBM Plex Sans",
    "Inter",
    "JetBrains Mono",
    "Lato",
    "Manrope",
    "Montserrat",
    "Nunito Sans",
    "Open Sans",
    "Poppins",
    "Roboto",
    "Source Sans 3",
)
GOOGLE_FONT_WEIGHTS = "400;500;600;700;800"


def google_fonts_css_url() -> str:
    families = "&".join(
        f"family={name.replace(' ', '+')}:wght@{GOOGLE_FONT_WEIGHTS}"
        for name in GOOGLE_FONT_FAMILIES
    )
    return f"https://fonts.googleapis.com/css2?{families}&display=swap"


DEVEXPRESS_BOOTSTRAP_THEMES = {
    "blazing-berry": "Blazing Berry",
    "office-white": "Office White",
    "purple": "Purple",
}

PYTORRENT_APP_THEMES = {
    "adaptive": "pyTorrent Adaptive",
    "ocean": "pyTorrent Ocean",
    "graphite": "pyTorrent Graphite",
    "forest": "pyTorrent Forest",
    "amber": "pyTorrent Amber",
    "nord": "pyTorrent Nord",
    "crimson": "pyTorrent Crimson",
    "sky": "pyTorrent Sky",
    "bootstrap22": "Bootstrap 2 Classic",
    "bootstrap22-inverse": "Bootstrap 2 Inverse",
    "bootstrap3": "Bootstrap 3 Glyph",
    "bootstrap3-inverse": "Bootstrap 3 Inverse",
}


BOOTSTRAP_THEME_DEFINITIONS = {
    "default": {
        "label": "Default Bootstrap",
        "local": f"{LIBS_STATIC_DIR}/bootstrap/{BOOTSTRAP_VERSION}/css/bootstrap.min.css",
        "cdn": f"https://cdn.jsdelivr.net/npm/bootstrap@{BOOTSTRAP_VERSION}/dist/css/bootstrap.min.css",
    },
    # Bootswatch themes.
    "flatly": {"label": "Bootswatch: Flatly", "provider": "bootswatch"},
    "litera": {"label": "Bootswatch: Litera", "provider": "bootswatch"},
    "lumen": {"label": "Bootswatch: Lumen", "provider": "bootswatch"},
    "minty": {"label": "Bootswatch: Minty", "provider": "bootswatch"},
    "sketchy": {"label": "Bootswatch: Sketchy", "provider": "bootswatch"},
    "spacelab": {"label": "Bootswatch: Spacelab", "provider": "bootswatch"},
    "united": {"label": "Bootswatch: United", "provider": "bootswatch"},
    "zephyr": {"label": "Bootswatch: Zephyr", "provider": "bootswatch"},
    # Complete DevExpress Bootstrap v5 dist.v5 set.
    **{
        f"dx-{theme}": {
            "label": f"DevExpress: {label}",
            "provider": "devexpress",
            "local": f"{LIBS_STATIC_DIR}/devexpress-bootstrap-themes/dist.v5/{theme}/bootstrap.min.css",
            "cdn": f"https://cdn.jsdelivr.net/gh/DevExpress/bootstrap-themes@master/dist.v5/{theme}/bootstrap.min.css",
        }
        for theme, label in DEVEXPRESS_BOOTSTRAP_THEMES.items()
    },
    # App-specific Bootstrap variable overrides. These sit on top of default Bootstrap.
    **{
        f"pytorrent-{theme}": {
            "label": f"Custom: {label}",
            "provider": "pytorrent",
            "local": f"{LIBS_STATIC_DIR}/pytorrent-themes/{theme}/bootstrap.min.css",
            "cdn": f"/static/{LIBS_STATIC_DIR}/pytorrent-themes/{theme}/bootstrap.min.css",
        }
        for theme, label in PYTORRENT_APP_THEMES.items()
    },
}

def _theme_definition(theme: str | None) -> dict[str, str]:
    theme = theme if theme in BOOTSTRAP_THEME_DEFINITIONS else "default"
    item = dict(BOOTSTRAP_THEME_DEFINITIONS[theme])
    if item.get("provider") == "bootswatch":
        item["local"] = f"{LIBS_STATIC_DIR}/bootswatch/{BOOTSWATCH_VERSION}/{theme}/bootstrap.min.css"
        item["cdn"] = f"https://cdn.jsdelivr.net/npm/bootswatch@{BOOTSWATCH_VERSION}/dist/{theme}/bootstrap.min.css"
    return item


BOOTSTRAP_THEMES = tuple(BOOTSTRAP_THEME_DEFINITIONS.keys())
BOOTSTRAP_THEME_LABELS = {key: value["label"] for key, value in BOOTSTRAP_THEME_DEFINITIONS.items()}

STATIC_ASSETS = {
    "bootstrap_js": {
        "local": f"{LIBS_STATIC_DIR}/bootstrap/{BOOTSTRAP_VERSION}/js/bootstrap.bundle.min.js",
        "cdn": f"https://cdn.jsdelivr.net/npm/bootstrap@{BOOTSTRAP_VERSION}/dist/js/bootstrap.bundle.min.js",
    },
    "fontawesome_css": {
        "local": f"{LIBS_STATIC_DIR}/fontawesome/{FONTAWESOME_VERSION}/css/all.min.css",
        "cdn": f"https://cdnjs.cloudflare.com/ajax/libs/font-awesome/{FONTAWESOME_VERSION}/css/all.min.css",
    },
    "flag_icons_css": {
        "local": f"{LIBS_STATIC_DIR}/flag-icons/{FLAG_ICONS_VERSION}/css/flag-icons.min.css",
        "cdn": f"https://cdn.jsdelivr.net/gh/lipis/flag-icons@{FLAG_ICONS_VERSION}/css/flag-icons.min.css",
    },
    "font_css": {
        "local": f"{LIBS_STATIC_DIR}/fonts/google-fonts.css",
        "cdn": google_fonts_css_url(),
    },
    "socket_io_js": {
        "local": f"{LIBS_STATIC_DIR}/socket.io/{SOCKET_IO_VERSION}/socket.io.min.js",
        "cdn": f"https://cdn.socket.io/{SOCKET_IO_VERSION}/socket.io.min.js",
    },
    "swagger_css": {
        "local": f"{LIBS_STATIC_DIR}/swagger-ui/{SWAGGER_UI_VERSION}/swagger-ui.css",
        "cdn": f"https://cdn.jsdelivr.net/npm/swagger-ui-dist@{SWAGGER_UI_VERSION}/swagger-ui.css",
    },
    "swagger_js": {
        "local": f"{LIBS_STATIC_DIR}/swagger-ui/{SWAGGER_UI_VERSION}/swagger-ui-bundle.js",
        "cdn": f"https://cdn.jsdelivr.net/npm/swagger-ui-dist@{SWAGGER_UI_VERSION}/swagger-ui-bundle.js",
    },
}


def bootstrap_css_asset(theme: str | None = None) -> dict[str, str]:
    item = _theme_definition(theme)
    return {"local": item["local"], "cdn": item["cdn"]}


def asset_path(key: str) -> str:
    return STATIC_ASSETS[key]["local" if USE_OFFLINE_LIBS else "cdn"]


def bootstrap_css_path(theme: str | None = None) -> str:
    return bootstrap_css_asset(theme)["local" if USE_OFFLINE_LIBS else "cdn"]


def required_offline_paths() -> list[Path]:
    paths = [LIBS_DIR.parent / item["local"] for item in STATIC_ASSETS.values()]
    paths.extend(LIBS_DIR.parent / bootstrap_css_asset(theme)["local"] for theme in BOOTSTRAP_THEMES)
    return paths


def missing_offline_paths() -> list[Path]:
    missing = [path for path in required_offline_paths() if not path.is_file() or path.stat().st_size <= 0]
    required_dirs = [
        LIBS_DIR / f"fontawesome/{FONTAWESOME_VERSION}/webfonts",
        LIBS_DIR / f"flag-icons/{FLAG_ICONS_VERSION}/flags/4x3",
        LIBS_DIR / f"flag-icons/{FLAG_ICONS_VERSION}/flags/1x1",
        LIBS_DIR / "fonts/files",
    ]
    for directory in required_dirs:
        if not directory.is_dir() or not any(directory.iterdir()):
            missing.append(directory)
    return missing


def validate_offline_assets() -> None:
    if not USE_OFFLINE_LIBS:
        return
    missing = missing_offline_paths()
    if missing:
        preview = "\n".join(f"- {path.relative_to(BASE_DIR)}" for path in missing[:20])
        extra = "" if len(missing) <= 20 else f"\n- ... and {len(missing) - 20} more"
        raise RuntimeError(
            "PYTORRENT_USE_OFFLINE_LIBS=true, but frontend libraries are missing. "
            "Run: ./scripts/download_frontend_libs.py or ./install.sh\n"
            f"Missing files:\n{preview}{extra}"
        )


_STATIC_HASH_VALUE = "dev"
_STATIC_HASH_READY = False


def _versioned_static_files(root: Path) -> list[Path]:
    """Return static files that should invalidate frontend JS/CSS caches.

    Note: Only JavaScript and CSS affect the executable frontend version. Images,
    favicons and user-provided tracker icons stay outside this lightweight hash.
    """
    return [
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix.lower() in {".js", ".css"}
        and "tracker_favicons" not in path.parts
    ]


def compute_static_hash(static_root: Path | None = None) -> str:
    """Compute one short startup hash for frontend JavaScript and CSS files.

    Note: This function reads JS/CSS files and should be called during app
    startup, not from frequent request handlers.
    """
    import hashlib

    root = static_root or (BASE_DIR / "pytorrent" / "static")
    digest = hashlib.sha256()
    files = sorted(_versioned_static_files(root), key=lambda item: item.as_posix())
    for path in files:
        rel = path.relative_to(root).as_posix()
        try:
            stat = path.stat()
            content = path.read_bytes()
        except OSError:
            continue
        digest.update(rel.encode("utf-8"))
        digest.update(str(stat.st_size).encode("ascii"))
        digest.update(content)
    value = digest.hexdigest()[:16]
    return value or "dev"


def initialize_static_hash(static_root: Path | None = None) -> str:
    """Compute and store the frontend static hash once for this process.

    Note: The API endpoint and template helpers only return this in-memory value,
    which keeps mobile version checks ultra-light.
    """
    global _STATIC_HASH_VALUE, _STATIC_HASH_READY
    _STATIC_HASH_VALUE = compute_static_hash(static_root)
    _STATIC_HASH_READY = True
    return _STATIC_HASH_VALUE


def static_hash(static_root: Path | None = None) -> str:
    """Return the startup frontend static hash without rescanning files.

    Note: The optional argument is kept for compatibility with existing callers;
    it is only used for a lazy fallback before app startup initialization.
    """
    if not _STATIC_HASH_READY:
        return initialize_static_hash(static_root)
    return _STATIC_HASH_VALUE
