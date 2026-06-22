from __future__ import annotations

from ._shared import bp
from . import load_api_route_modules

load_api_route_modules()

__all__ = ["bp"]
