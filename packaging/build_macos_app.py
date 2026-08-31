#!/usr/bin/env python3
"""Build JSON Forge as a native macOS .app bundle with PyInstaller."""

from __future__ import annotations

import argparse
import platform
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from release_utils import backup_existing  # noqa: E402
from version_info import VERSION  # noqa: E402


APP_NAME = "JSON Forge"
BUNDLE_ID = "com.localtools.jsonforge"


def run(command: list[str], *, cwd: Path) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=cwd, check=True)


def archive_name(architecture: str) -> str:
    return f"JSON-Forge-v{VERSION}-macos-{architecture}.zip"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clean", action="store_true", help="discard cached PyInstaller build files")
    parser.add_argument("--no-sign", action="store_true", help="skip ad-hoc code signing")
    args = parser.parse_args()

    if sys.platform != "darwin":
        parser.error("the macOS .app must be built on macOS")

    root = ROOT
    architecture = platform.machine()
    if architecture not in {"arm64", "x86_64"}:
        parser.error(f"unsupported Mac architecture: {architecture}")

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print(
            "PyInstaller is missing. Install it with pip, or with: "
            f"uv pip install --python {sys.executable} pyinstaller",
            file=sys.stderr,
        )
        return 1

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--windowed",
        "--name",
        APP_NAME,
        "--osx-bundle-identifier",
        BUNDLE_ID,
        "--target-architecture",
        architecture,
        "--icon",
        "assets/JSON-Forge.icns",
        "--add-data",
        "config/settings.default.ini:config",
        "--add-data",
        "VERSION:.",
        "--add-data",
        "assets/JSON-Forge.png:assets",
        "app.py",
    ]
    if args.clean:
        command.insert(4, "--clean")

    try:
        run(command, cwd=root)
        app_path = root / "dist" / f"{APP_NAME}.app"
        if not app_path.is_dir():
            raise RuntimeError(f"PyInstaller did not create {app_path}")
        plist_path = app_path / "Contents" / "Info.plist"
        with plist_path.open("rb") as source:
            plist = plistlib.load(source)
        plist["CFBundleShortVersionString"] = VERSION
        plist["CFBundleVersion"] = VERSION
        with plist_path.open("wb") as destination:
            plistlib.dump(plist, destination)
        if not args.no_sign:
            codesign = shutil.which("codesign")
            if codesign:
                run([codesign, "--force", "--deep", "--sign", "-", str(app_path)], cwd=root)
        archive_path = root / "dist" / archive_name(architecture)
        backup_existing(archive_path)
        ditto = shutil.which("ditto")
        if not ditto:
            raise RuntimeError("macOS ditto command was not found")
        run(
            [ditto, "-c", "-k", "--sequesterRsrc", "--keepParent", str(app_path), str(archive_path)],
            cwd=root,
        )
        unzip = shutil.which("unzip")
        if unzip:
            run([unzip, "-tq", str(archive_path)], cwd=root)
        shutil.rmtree(app_path)
        print(f"Built archive: {archive_path}")
        print(f"Removed intermediate app: {app_path}")
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Build failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
