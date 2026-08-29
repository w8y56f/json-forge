#!/usr/bin/env python3
"""Wrap the portable Windows bundle in a single self-extracting GUI executable."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


APP_VERSION = "v1.0.0"
ARCHIVE_NAME = "json-forge-windows-x86_64.zip"
OUTPUT_NAME = "JSON Forge-Windows-x86_64.exe"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, help="portable Windows ZIP to embed")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    archive = (args.archive or root / "dist" / ARCHIVE_NAME).resolve()
    launcher_source = root / "packaging" / "windows_launcher" / "main.go"
    go = shutil.which("go")
    if not go:
        parser.error("Go is required to cross-compile the Windows launcher")
    if not archive.is_file():
        parser.error(f"portable Windows archive does not exist: {archive}")

    output = root / "dist" / OUTPUT_NAME
    environment = os.environ.copy()
    environment.update({"GOOS": "windows", "GOARCH": "amd64", "CGO_ENABLED": "0", "GO111MODULE": "off"})
    try:
        with tempfile.TemporaryDirectory(prefix="json-forge-windows-launcher-") as temporary_name:
            temporary = Path(temporary_name)
            shutil.copy2(launcher_source, temporary / "main.go")
            shutil.copy2(archive, temporary / "payload.zip")
            command = [
                go,
                "build",
                "-trimpath",
                "-ldflags",
                f"-s -w -H=windowsgui -X main.payloadVersion={APP_VERSION}",
                "-o",
                str(output),
                "main.go",
            ]
            print("+", " ".join(command), flush=True)
            subprocess.run(command, cwd=temporary, env=environment, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"Build failed: {exc}", file=sys.stderr)
        return 1

    print(f"Built executable: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
