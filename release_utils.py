"""Shared helpers for creating JSON Forge release artifacts."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path


def backup_existing(path: Path, timestamp: str | None = None) -> Path | None:
    """Rename an existing release file to a collision-safe timestamped backup."""
    if not path.exists():
        return None
    if not path.is_file():
        raise RuntimeError(f"Release artifact is not a file: {path}")

    stamp = timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = path.with_name(f"bak_{stamp}_{path.name}")
    counter = 2
    while backup.exists():
        backup = path.with_name(f"bak_{stamp}_{counter}_{path.name}")
        counter += 1
    path.replace(backup)
    print(f"Backed up existing artifact: {backup}")
    return backup
