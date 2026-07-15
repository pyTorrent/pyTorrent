from __future__ import annotations
import os
from pathlib import Path
import gunicorn.http.wsgi

gunicorn.http.wsgi.SERVER = "pyTorrent"

# Note: Gunicorn writes to data/logs by default; pyTorrent also writes rotated app/access/error logs there.
_log_dir = Path(os.getenv("PYTORRENT_LOG_DIR", "data/logs"))
_log_dir.mkdir(parents=True, exist_ok=True)
accesslog = os.getenv("PYTORRENT_GUNICORN_ACCESS_LOG", str(_log_dir / "gunicorn-access.log"))
errorlog = os.getenv("PYTORRENT_GUNICORN_ERROR_LOG", str(_log_dir / "gunicorn-error.log"))
loglevel = os.getenv("PYTORRENT_GUNICORN_LOG_LEVEL", "info")
