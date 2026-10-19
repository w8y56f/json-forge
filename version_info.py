"""Read JSON Forge's single source of truth for the application version."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


VERSION_PATTERN = re.compile(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)")
COMMIT_PATTERN = re.compile(r"[0-9a-fA-F]{7,40}")


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


def commit_file_path() -> Path:
    """Return BUILD_COMMIT from the source tree or a packaged application."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / "BUILD_COMMIT"


def read_commit_id(path: Path | None = None) -> str:
    """Return the seven-character build commit, or ``unknown`` when unavailable."""
    commit_path = path or commit_file_path()
    try:
        value = commit_path.read_text(encoding="utf-8").strip()
    except OSError:
        value = ""
    if COMMIT_PATTERN.fullmatch(value):
        return value[:7].lower()
    if path is not None or getattr(sys, "frozen", False):
        return "unknown"
    try:
        result = subprocess.run(
            ["git", "-C", str(Path(__file__).resolve().parent), "rev-parse", "--short=7", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    value = result.stdout.strip()
    return value.lower() if re.fullmatch(r"[0-9a-fA-F]{7}", value) else "unknown"


def read_display_commit_id(commit_id: str) -> str:
    """Mark uncommitted source changes without altering the build commit ID."""
    if (commit_id == "unknown" or getattr(sys, "frozen", False)
            or commit_file_path().is_file()):
        return commit_id
    try:
        result = subprocess.run(
            ["git", "-C", str(Path(__file__).resolve().parent),
             "status", "--porcelain", "--untracked-files=normal"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return commit_id
    return f"{commit_id}-dirty" if result.stdout.strip() else commit_id


VERSION = read_version()
DISPLAY_VERSION = f"v{VERSION}"
COMMIT_ID = read_commit_id()
DISPLAY_COMMIT_ID = read_display_commit_id(COMMIT_ID)
