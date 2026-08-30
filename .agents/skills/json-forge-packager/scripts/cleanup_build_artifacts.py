#!/usr/bin/env python3
"""Remove only reproducible JSON Forge packaging intermediates."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
DIST = ROOT / "dist"


def remove(path: Path, dry_run: bool) -> bool:
    if not path.exists() and not path.is_symlink():
        return False
    relative = path.relative_to(ROOT)
    print(f"{'Would remove' if dry_run else 'Removing'}: {relative}")
    if dry_run:
        return True
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    targets = [
        ROOT / "build",
        ROOT / "JSON Forge.spec",
        ROOT / "__pycache__",
        ROOT / "packaging" / "__pycache__",
        Path(__file__).resolve().parent / "__pycache__",
    ]
    mac_app_archive_exists = any(DIST.glob("JSON-Forge-v*-macos-*.zip"))
    mac_arm_portable_exists = any(DIST.glob("JSON-Forge-v*-macos-arm64.tar.gz"))
    mac_intel_portable_exists = any(DIST.glob("JSON-Forge-v*-macos-x86_64.tar.gz"))
    windows_portable_exists = any(DIST.glob("JSON-Forge-v*-windows-x86_64.zip"))
    windows_executable_exists = any(DIST.glob("JSON-Forge-v*-windows-x86_64.exe"))
    conditional_targets = [
        (mac_arm_portable_exists, DIST / "JSON-Forge-macos-arm64"),
        (mac_arm_portable_exists, DIST / "json-forge-macos-arm64"),
        (mac_arm_portable_exists, DIST / "json-forge-macos-arm64.tar.gz"),
        (mac_intel_portable_exists, DIST / "JSON-Forge-macos-x86_64"),
        (mac_intel_portable_exists, DIST / "json-forge-macos-x86_64"),
        (mac_intel_portable_exists, DIST / "json-forge-macos-x86_64.tar.gz"),
        (windows_portable_exists, DIST / "JSON-Forge-windows-x86_64"),
        (windows_portable_exists, DIST / "json-forge-windows-x86_64"),
        (windows_portable_exists, DIST / "json-forge-windows-x86_64.zip"),
        (windows_portable_exists, DIST / "JSON-Forge-windows-x86_64.tar.gz"),
        (windows_portable_exists, DIST / "json-forge-windows-x86_64.tar.gz"),
        (windows_executable_exists, DIST / "JSON Forge-Windows-x86_64.exe"),
    ]

    removed = sum(remove(path, args.dry_run) for path in targets)
    if mac_app_archive_exists:
        removed += remove(DIST / "JSON Forge", args.dry_run)
        removed += remove(DIST / "JSON Forge.app", args.dry_run)
    for final_artifact_exists, intermediate in conditional_targets:
        if final_artifact_exists:
            removed += remove(intermediate, args.dry_run)
    if not removed:
        print("No disposable packaging artifacts found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
