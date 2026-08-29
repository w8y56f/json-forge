---
name: json-forge-packager
description: Build, verify, and clean JSON Forge release artifacts when the user asks to package, bundle, publish, or rebuild this repository for macOS or Windows.
---

# JSON Forge Packager

Use the repository's existing packaging scripts. Do not recreate their behavior in ad-hoc commands.

## Select outputs

If the user has not named a target, ask them to select one or more numbered outputs before building. Accept selections such as `1,3,4` or `all`:

1. macOS Apple Silicon app: `dist/JSON Forge.app`
2. macOS Apple Silicon portable archive with `start.sh`: `dist/json-forge-macos-arm64.tar.gz`
3. Windows x64 portable archive with `start.bat`: `dist/json-forge-windows-x86_64.zip`
4. Windows x64 self-extracting GUI executable: `dist/JSON Forge-Windows-x86_64.exe`

Do not offer `json-forge-windows-x86_64.tar.gz`; ZIP is the supported Windows portable format. Explain that option 4 is a Go-built self-extracting launcher, not a Windows-native PyInstaller one-file build, when that distinction matters.

If the user already selected targets, start without asking again. Treat “package everything” as all four outputs.

## Build

Run from the repository root. Prefer `.venv/bin/python`; if it is unavailable, use a compatible Python with the project dependencies installed.

Run the unit tests once before building:

```bash
.venv/bin/python -m unittest -v
```

Build selected outputs in this order:

```bash
# 1
.venv/bin/python packaging/build_macos_app.py --clean

# 2
.venv/bin/python packaging/build_release.py --target macos-arm64

# 3
.venv/bin/python packaging/build_release.py --target windows-x86_64

# 4
.venv/bin/python packaging/build_windows_exe.py
```

Option 4 embeds the Windows ZIP. When option 4 is requested without option 3, rebuild option 3 first unless the user explicitly asks to reuse the existing ZIP. Build macOS apps only on macOS. The app script produces the architecture of its Python environment; verify it is arm64 before reporting option 1 as Apple Silicon compatible.

## Verify

Verify every selected output before cleanup:

- macOS app: confirm the main executable is Mach-O arm64, `settings.default.ini` exists under `Contents/Resources/config`, `codesign --verify --deep --strict` succeeds, and perform a short launch smoke test with settings/session/lock paths redirected to a temporary directory.
- Portable archives: list the archive and confirm it contains `app.py`, `json_tools.py`, `config/settings.default.ini`, the matching start script, and the bundled Python executable.
- Windows EXE: use `file` to confirm `PE32+ executable (GUI) x86-64` and report its SHA-256.

State clearly when a Windows artifact was structurally verified on macOS but not executed on Windows.

## Clean

Only after all requested builds and verifications succeed, run:

```bash
.venv/bin/python .agents/skills/json-forge-packager/scripts/cleanup_build_artifacts.py
```

The cleanup is deliberately allowlisted. It removes PyInstaller intermediates, expanded directory copies when their final archive/app exists, and the obsolete Windows `.tar.gz`. It preserves source files, `.venv`, `downloads/`, and final `.app`, `.zip`, macOS `.tar.gz`, and `.exe` artifacts.

If a build or verification fails, retain intermediates for diagnosis and report the failure instead of cleaning them.
