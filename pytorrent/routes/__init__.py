from __future__ import annotations
from importlib import import_module

API_ROUTE_MODULES = (
    "torrents",
    "profiles",
    "rss",
    "automations",
    "smart_queue",
    "system",
    "backup",
    "operation_logs",
    "planner",
)


def load_api_route_modules() -> None:
    """Import API route modules so their shared blueprint decorators are registered."""
    for module_name in API_ROUTE_MODULES:
        import_module(f"{__name__}.{module_name}")
