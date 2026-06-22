from __future__ import annotations
import logging
import time
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any
from flask import Flask, g, request
from .config import LOG_DIR, LOG_ENABLE, LOG_RETENTION_HOURS

_CONFIGURED = False


def _make_handler(path: Path, level: int) -> TimedRotatingFileHandler:
    """Create an hourly rotating log handler with retention configured in hours."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = TimedRotatingFileHandler(
        path,
        when="H",
        interval=1,
        backupCount=max(1, int(LOG_RETENTION_HOURS)),
        encoding="utf-8",
        utc=False,
    )
    handler.setLevel(level)
    handler.suffix = "%Y%m%d%H"
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
    return handler


def configure_logging(app: Flask | None = None) -> None:
    """Route pyTorrent app, error and access logs to the configured data log directory."""
    global _CONFIGURED
    if not LOG_ENABLE:
        # Note: Installation can disable file logging while keeping normal service stdout/stderr available.
        return
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    if not _CONFIGURED:
        app_handler = _make_handler(LOG_DIR / "app.log", logging.INFO)
        error_handler = _make_handler(LOG_DIR / "error.log", logging.WARNING)

        root = logging.getLogger()
        root.setLevel(logging.INFO)
        root.addHandler(app_handler)
        root.addHandler(error_handler)

        for name in ("pytorrent", "werkzeug", "gunicorn.error"):
            logger = logging.getLogger(name)
            logger.setLevel(logging.INFO)
            logger.propagate = True

        _CONFIGURED = True

    if app is not None:
        app.logger.setLevel(logging.INFO)
        if not getattr(app, "_pytorrent_access_logging", False):
            access_logger = logging.getLogger("pytorrent.access")
            access_logger.setLevel(logging.INFO)
            access_logger.propagate = False
            access_logger.addHandler(_make_handler(LOG_DIR / "access.log", logging.INFO))

            @app.before_request
            def _mark_access_start() -> None:
                g._access_started_at = time.perf_counter()

            @app.after_request
            def _write_access_log(response):
                duration_ms = int((time.perf_counter() - getattr(g, "_access_started_at", time.perf_counter())) * 1000)
                # Note: Application access logging is rotated hourly, unlike raw gunicorn stdout logs.
                access_logger.info(
                    '%s "%s %s" %s %s %sms "%s"',
                    request.headers.get("X-Forwarded-For", request.remote_addr or "-"),
                    request.method,
                    request.full_path.rstrip("?"),
                    response.status_code,
                    response.calculate_content_length() or 0,
                    duration_ms,
                    request.headers.get("User-Agent", "-"),
                )
                return response

            @app.teardown_request
            def _log_unhandled_error(error: BaseException | None) -> None:
                if error is not None:
                    app.logger.error("Unhandled request error", exc_info=(type(error), error, error.__traceback__))

            app._pytorrent_access_logging = True  # type: ignore[attr-defined]
