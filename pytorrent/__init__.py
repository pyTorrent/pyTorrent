from __future__ import annotations

from pathlib import Path
from flask import Flask, jsonify, render_template, request, url_for
from flask_socketio import SocketIO
from werkzeug.middleware.proxy_fix import ProxyFix
from .config import (
    SECRET_KEY,
    SESSION_COOKIE_SECURE,
    PROXY_FIX_ENABLE,
    PROXY_FIX_X_FOR,
    PROXY_FIX_X_PROTO,
    PROXY_FIX_X_HOST,
    PROXY_FIX_X_PORT,
    PROXY_FIX_X_PREFIX,
    SOCKETIO_CORS_ALLOWED_ORIGINS,
    STATIC_CACHE_MAX_AGE,
    STATIC_CACHE_IMMUTABLE
)
from .db import init_db
from .services.frontend_assets import asset_path, bootstrap_css_path, initialize_static_hash, static_hash, validate_offline_assets

socketio = SocketIO(cors_allowed_origins=SOCKETIO_CORS_ALLOWED_ORIGINS, ping_timeout=30, async_mode="threading")


def _wants_json_response() -> bool:
    """Return true for API/error clients that should not receive an HTML page."""
    best = request.accept_mimetypes.best_match(["application/json", "text/html"])
    return request.path.startswith("/api/") or best == "application/json"


def register_error_pages(app: Flask) -> None:
    @app.errorhandler(404)
    def not_found(error):
        if _wants_json_response():
            return jsonify({"ok": False, "error": "Not found"}), 404
        return render_template(
            "error.html",
            code=404,
            title="Page not found",
            message="The requested pyTorrent view does not exist or is not available.",
            icon="fa-compass-drafting",
        ), 404

    @app.errorhandler(500)
    def server_error(error):
        if _wants_json_response():
            return jsonify({"ok": False, "error": "Internal server error"}), 500
        return render_template(
            "error.html",
            code=500,
            title="Application error",
            message="pyTorrent hit an internal error while handling this request.",
            icon="fa-bug",
        ), 500


def create_app() -> Flask:
    validate_offline_assets()
    app = Flask(__name__)
    initialize_static_hash(Path(app.static_folder or ""))
    from .logging_config import configure_logging
    configure_logging(app)
    if PROXY_FIX_ENABLE:
        app.wsgi_app = ProxyFix(
            app.wsgi_app,
            x_for=PROXY_FIX_X_FOR,
            x_proto=PROXY_FIX_X_PROTO,
            x_host=PROXY_FIX_X_HOST,
            x_port=PROXY_FIX_X_PORT,
            x_prefix=PROXY_FIX_X_PREFIX,
        )
    app.secret_key = SECRET_KEY
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=SESSION_COOKIE_SECURE,
    )

    @app.context_processor
    def static_helpers():
        def current_static_hash() -> str:
            return static_hash(Path(app.static_folder or ""))

        def static_url(filename: str) -> str:
            path = Path(app.static_folder or "") / filename
            try:
                path.stat()
                # Note: A single JS/CSS hash keeps module imports, stylesheets and local libraries on the same cache version.
                return url_for("static", filename=filename, v=current_static_hash())
            except OSError:
                return url_for("static", filename=filename)

        def frontend_asset_url(key: str) -> str:
            path = asset_path(key)
            return path if path.startswith("http") else static_url(path)

        def bootstrap_theme_url(theme: str | None = None) -> str:
            path = bootstrap_css_path(theme)
            return path if path.startswith("http") else static_url(path)

        return {
            "static_url": static_url,
            "frontend_asset_url": frontend_asset_url,
            "bootstrap_theme_url": bootstrap_theme_url,
            "static_hash": current_static_hash,
        }

    @app.after_request
    def cache_headers(response):
        static_file = request.path.startswith("/static/")
        tracker_icon = request.path.startswith("/static/tracker_favicons/")
        favicon = request.path in ("/favicon.ico", "/favicon.svg")
        openapi_spec = request.path == "/api/openapi.json"

        if (static_file and not tracker_icon) or favicon:
            if STATIC_CACHE_MAX_AGE > 0:
                cache_control = f"public, max-age={STATIC_CACHE_MAX_AGE}"
                if STATIC_CACHE_IMMUTABLE:
                    cache_control += ", immutable"
                response.headers["Cache-Control"] = cache_control
            else:
                response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        elif openapi_spec:
            response.headers["Cache-Control"] = "private, no-cache, must-revalidate"
        else:
            response.headers["Cache-Control"] = "private, no-store"

        return response

    from .routes.main import bp as main_bp
    from .routes.api import bp as api_bp
    app.register_blueprint(main_bp)
    app.register_blueprint(api_bp)
    register_error_pages(app)
    init_db()
    from .services.speed_peaks import load_cache
    load_cache()
    from .services.auth import install_guards
    install_guards(app)

    socketio.init_app(app)
    from .services.workers import set_socketio, start_watchdog
    set_socketio(socketio)
    start_watchdog()
    from .services.websocket import register_socketio_handlers
    register_socketio_handlers(socketio)
    from .services.startup_config import schedule_startup_config_apply
    schedule_startup_config_apply(socketio)
    from .services.background_automations import start_scheduler as start_background_automation_scheduler
    start_background_automation_scheduler(socketio)
    from .services.rss import start_scheduler as start_rss_scheduler
    from .services.ratio_rules import start_scheduler as start_ratio_scheduler
    from .services.download_planner import start_scheduler as start_download_planner_scheduler
    from .services.backup import start_scheduler as start_backup_scheduler
    start_rss_scheduler(socketio)
    start_ratio_scheduler(socketio)
    start_download_planner_scheduler(socketio)
    start_backup_scheduler()
    from .services.background_cache_warmup import start_scheduler as start_cache_warmup_scheduler
    start_cache_warmup_scheduler(socketio)
    return app
