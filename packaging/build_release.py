#!/usr/bin/env python3
"""Build a portable JSON Forge directory and archive.

The resulting directory contains a python-build-standalone runtime, PySide6,
the application sources, and start.sh/start.bat. It does not require Python
to be installed on the computer where the bundle is used.

Examples:
    python packaging/build_release.py --target macos-arm64
    python packaging/build_release.py --target windows-x86_64

The Windows bundle can be built on Windows directly. Cross-building it from
macOS/Linux is also supported when the uv command is available; uv downloads
the matching Windows wheels without executing them on the build machine.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path


APP_NAME = "json-forge"
PYTHON_LINE = "3.12"
PBA_API = "https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest"

TARGETS = {
    "macos-arm64": {
        "triple": "aarch64-apple-darwin",
        "platform": "aarch64-apple-darwin",
        "python_executable": "bin/python3",
        "site_packages": "lib/python3.12/site-packages",
    },
    "macos-x86_64": {
        "triple": "x86_64-apple-darwin",
        "platform": "x86_64-apple-darwin",
        "python_executable": "bin/python3",
        "site_packages": "lib/python3.12/site-packages",
    },
    "windows-x86_64": {
        "triple": "x86_64-pc-windows-msvc",
        "platform": "x86_64-pc-windows-msvc",
        "python_executable": "python.exe",
        "site_packages": "Lib/site-packages",
    },
}

SOURCE_FILES = (
    "app.py",
    "json_tools.py",
    "requirements.txt",
    "README.md",
    "start.sh",
    "start.bat",
)


def _run(command: list[str], *, cwd: Path | None = None) -> None:
    print("+", " ".join(command))
    subprocess.run(command, cwd=cwd, check=True)


def _fetch_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": APP_NAME})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)
    except Exception as exc:
        # Some managed Python installations do not have the system CA bundle.
        # curl can use the platform trust store, so use it as a fallback.
        curl = shutil.which("curl")
        if not curl:
            raise RuntimeError(f"Unable to query {url}: {exc}") from exc
        result = subprocess.run(
            [curl, "--fail", "--location", "--silent", "--show-error", url],
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(result.stdout)


def _download(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    curl = shutil.which("curl")
    if curl:
        _run([curl, "--fail", "--location", "--progress-bar", url, "-o", str(destination)])
        return
    request = urllib.request.Request(url, headers={"User-Agent": APP_NAME})
    with urllib.request.urlopen(request, timeout=300) as response, destination.open("wb") as output:
        shutil.copyfileobj(response, output)


def _select_runtime_asset(target: str) -> tuple[dict, dict]:
    info = TARGETS[target]
    release = _fetch_json(PBA_API)
    pattern = re.compile(
        rf"^cpython-{re.escape(PYTHON_LINE)}\.\d+\+[^-]+-{re.escape(info['triple'])}-install_only(?:_stripped)?\.tar\.gz$"
    )
    candidates = [asset for asset in release.get("assets", []) if pattern.match(asset.get("name", ""))]
    if not candidates:
        raise RuntimeError(f"No Python {PYTHON_LINE} runtime found for {target} in release {release.get('tag_name')}")
    candidates.sort(key=lambda asset: ("_stripped" not in asset["name"], asset["name"]))
    return release, candidates[0]


def _safe_extract(archive: Path, destination: Path) -> None:
    destination_resolved = destination.resolve()
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        for member in members:
            member_path = (destination / member.name).resolve()
            if member_path != destination_resolved and destination_resolved not in member_path.parents:
                raise RuntimeError(f"Unsafe path in runtime archive: {member.name}")
        try:
            tar.extractall(destination, filter="data")
        except TypeError:  # Python < 3.12 compatibility for the build tool.
            tar.extractall(destination)


def _install_dependencies(root: Path, bundle: Path, target: str, info: dict) -> None:
    site_packages = bundle / "runtime" / "python" / info["site_packages"]
    site_packages.mkdir(parents=True, exist_ok=True)
    uv = shutil.which("uv")
    if uv:
        _run(
            [
                uv,
                "pip",
                "install",
                "--target",
                str(site_packages),
                "--python-platform",
                info["platform"],
                "--python-version",
                PYTHON_LINE,
                "--only-binary=:all:",
                "--no-cache",
                "-r",
                str(root / "requirements.txt"),
            ],
            cwd=root,
        )
        return

    if target.startswith("windows") and sys.platform != "win32":
        raise RuntimeError("Cross-building Windows requires uv; install it from https://docs.astral.sh/uv/")
    python = bundle / "runtime" / "python" / info["python_executable"]
    _run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--no-cache-dir",
            "--only-binary=:all:",
            "-r",
            str(root / "requirements.txt"),
        ],
        cwd=root,
    )


def build(target: str, output_root: Path, runtime_url: str | None = None) -> Path:
    root = Path(__file__).resolve().parents[1]
    info = TARGETS[target]
    output_root.mkdir(parents=True, exist_ok=True)
    bundle = output_root / f"{APP_NAME}-{target}"
    if bundle.exists():
        shutil.rmtree(bundle)
    bundle.mkdir(parents=True)

    if runtime_url:
        release = {"tag_name": "custom", "published_at": None}
        asset = {"name": Path(runtime_url).name, "browser_download_url": runtime_url, "digest": None}
    else:
        release, asset = _select_runtime_asset(target)

    downloads = root / "downloads"
    archive = downloads / asset["name"].replace("%2B", "+")
    if not archive.exists():
        print(f"Downloading Python runtime: {asset['name']}")
        _download(asset["browser_download_url"], archive)
    expected_digest = asset.get("digest")
    if expected_digest and expected_digest.startswith("sha256:"):
        actual_digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        if actual_digest != expected_digest.removeprefix("sha256:"):
            raise RuntimeError(f"Runtime checksum mismatch for {archive.name}")

    with tempfile.TemporaryDirectory(prefix="json-forge-runtime-") as temporary:
        extracted = Path(temporary)
        _safe_extract(archive, extracted)
        extracted_python = extracted / "python"
        if not extracted_python.is_dir():
            raise RuntimeError("Unexpected Python runtime archive layout: missing python/")
        shutil.copytree(extracted_python, bundle / "runtime" / "python", symlinks=True)

    for relative in SOURCE_FILES:
        shutil.copy2(root / relative, bundle / relative)
    (bundle / "config").mkdir()
    shutil.copy2(root / "config" / "settings.default.ini", bundle / "config" / "settings.default.ini")
    (bundle / "cache").mkdir()
    (bundle / "start.sh").chmod(0o755)

    _install_dependencies(root, bundle, target, info)
    manifest = {
        "application": APP_NAME,
        "version": "v1.0.0",
        "target": target,
        "python": PYTHON_LINE,
        "runtime_release": release.get("tag_name"),
        "runtime_asset": asset.get("name"),
        "runtime_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
    }
    (bundle / "runtime-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    archive_suffix = ".zip" if target.startswith("windows") else ".tar.gz"
    archive_path = output_root / f"{bundle.name}{archive_suffix}"
    if archive_path.exists():
        archive_path.unlink()
    if target.startswith("windows"):
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as output_archive:
            for path in bundle.rglob("*"):
                output_archive.write(path, path.relative_to(output_root))
    else:
        with tarfile.open(archive_path, "w:gz") as output_archive:
            output_archive.add(bundle, arcname=bundle.name)
    print(f"Built directory: {bundle}")
    print(f"Built archive:   {archive_path}")
    return bundle


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=sorted(TARGETS), required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("dist"))
    parser.add_argument("--runtime-url", help="Use a specific python-build-standalone archive URL")
    args = parser.parse_args()
    try:
        build(args.target, args.output_dir.resolve(), args.runtime_url)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Build failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
