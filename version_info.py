"""Read JSON Forge's single source of truth for the application version."""

from __future__ import annotations

import re
import sys
from pathlib import Path


VERSION_PATTERN = re.compile(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)")


def version_file_path() -> Path:
    """Return VERSION from the source tree or a PyInstaller bundle."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / "VERSION"


def read_version(path: Path | None = None) -> str:
    """Read and validate a three-part semantic version without a leading v."""
    version_path = path or version_file_path()
    try:
        value = version_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError(f"Unable to read application version from {version_path}: {exc}") from exc
    if not VERSION_PATTERN.fullmatch(value):
        raise RuntimeError(f"Invalid application version in {version_path}: {value!r}")
    return value


VERSION = read_version()
DISPLAY_VERSION = f"v{VERSION}"
