from __future__ import annotations
import hashlib
from pathlib import Path


def human_size(num: int | float | None, suffix: str = "B") -> str:
    value = float(num or 0)
    for unit in ["", "K", "M", "G", "T", "P"]:
        if abs(value) < 1024.0:
            return f"{value:3.1f} {unit}{suffix}" if unit else f"{int(value)} {suffix}"
        value /= 1024.0
    return f"{value:.1f} E{suffix}"


def human_rate(num: int | float | None) -> str:
    return f"{human_size(num)}/s"


def file_md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()[:12]
