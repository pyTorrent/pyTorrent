from __future__ import annotations
from functools import lru_cache
from pathlib import Path
from ..config import GEOIP_DB

try:
    import geoip2.database
except Exception:
    geoip2 = None

_reader = None


def _get_reader():
    global _reader
    if _reader is not None:
        return _reader
    if not GEOIP_DB.exists() or geoip2 is None:
        return None
    _reader = geoip2.database.Reader(str(GEOIP_DB))
    return _reader


@lru_cache(maxsize=50000)
def lookup_ip(ip: str) -> dict:
    reader = _get_reader()
    if not reader:
        return {"country_iso": "", "country": "", "city": ""}
    try:
        hit = reader.city(ip)
        return {
            "country_iso": (hit.country.iso_code or "").lower(),
            "country": hit.country.name or "",
            "city": hit.city.name or "",
        }
    except Exception:
        return {"country_iso": "", "country": "", "city": ""}
