---
name: json-forge-packager
description: Build, verify, and clean JSON Forge release artifacts when the user asks to package, bundle, publish, or rebuild this repository for macOS or Windows.
---

# JSON Forge Packager

Use the repository's existing packaging scripts. Do not recreate their behavior in ad-hoc commands.

Read and validate the repository-root `VERSION` before presenting output names or building. Treat its value without a leading `v` as `{version}`; use `v{version}` only for display labels, release names, and manifests. Never hardcode a release version in this skill.

When a selected final artifact already exists at the same versioned path, the build script must preserve it by renaming it to `bak_YYYYMMDD_HHMMSS_<original-name>` before writing the replacement. Preserve the complete original filename and extension, report backup files, and do not remove them during normal cleanup. If a backup name already exists in the same second, use `bak_YYYYMMDD_HHMMSS_2_<original-name>`, then `_3`, and so on.

## Select outputs

If the user has not named a target, ask them to select one or more numbered outputs before building. Accept selections such as `1,3,4` or `all`:

1. macOS Apple Silicon app ZIP: `dist/JSON-Forge-v{version}-macos-arm64.zip`
2. macOS Apple Silicon portable archive with `start.sh`: `dist/JSON-Forge-v{version}-macos-arm64.tar.gz`
3. Windows x64 portable archive with `start.bat`: `dist/JSON-Forge-v{version}-windows-x86_64.zip`
4. Windows x64 self-extracting GUI executable: `dist/JSON-Forge-v{version}-windows-x86_64.exe`

Do not offer `JSON-Forge-windows-x86_64.tar.gz`; ZIP is the supported Windows portable format. Explain that option 4 is a Go-built self-extracting launcher, not a Windows-native PyInstaller one-file build, when that distinction matters.

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

- macOS app ZIP: extract it to a temporary directory with `ditto -x -k`, confirm the main executable is Mach-O arm64, bundled `VERSION` equals the repository version, Info.plist reports `{version}`, `settings.default.ini` exists under `Contents/Resources/config`, `codesign --verify --deep --strict` succeeds, and perform a short launch smoke test with settings/session/lock paths redirected to a temporary directory. Confirm `dist/JSON Forge.app` was removed after ZIP creation.
- Portable archives: list the archive and confirm it contains `VERSION`, `version_info.py`, `app.py`, `json_tools.py`, `config/settings.default.ini`, the matching start script, and the bundled Python executable. Confirm `runtime-manifest.json` reports `v{version}`.
- Windows EXE: use `file` to confirm `PE32+ executable (GUI) x86-64` and report its SHA-256.
- Rebuilds: confirm every pre-existing selected artifact has a timestamped backup and every canonical output path contains the newly built artifact.

State clearly when a Windows artifact was structurally verified on macOS but not executed on Windows.

## Clean

Only after all requested builds and verifications succeed, run:

```bash
.venv/bin/python .agents/skills/json-forge-packager/scripts/cleanup_build_artifacts.py
```

The cleanup is deliberately allowlisted. It removes PyInstaller intermediates, any leftover macOS `.app`, expanded directory copies when their final archive exists, and obsolete legacy-named outputs. It preserves source files, `.venv`, `downloads/`, canonical release artifacts, and all `bak_YYYYMMDD_HHMMSS_...` backup artifacts.

If a build or verification fails, retain intermediates for diagnosis and report the failure instead of cleaning them.
