---
name: json-forge-github-release
description: Publish JSON Forge versions to the GitHub repository associated with the project, including a version tag, release notes, and all four release artifacts. Use when the user asks to publish or upload a JSON Forge GitHub Release; packaging alone does not request publishing.
---

# JSON Forge GitHub Release

Run from the JSON Forge repository root. This skill publishes releases; creating, editing, reviewing, or installing this skill does not authorize running its publication workflow. Honor requests to prepare only or create a draft only.

A user request to publish a version authorizes creating and pushing its tag, creating the Release, and uploading its four packages. Once that authorization exists, finish without asking again for routine steps. Do not include unrelated commits or push unrelated branches.

## Establish the release inputs

- Read applicable repository instructions. Read and validate root `VERSION` using `version_info.read_version`; derive `version` and `tag = v{version}` without hardcoding a version.
- Resolve the project root with Git and inspect the push URL of the selected remote (normally `origin`). At creation time the project used `git@github.com:w8y56f/json-forge.git`; treat this only as context, never as a substitute for checking the current remote. Use the verified host and owner/repository explicitly for GitHub commands. Resolve ambiguous or conflicting destinations before mutation.
- Check GitHub CLI availability, `gh auth status`, repository accessibility, and write permission without printing tokens. If CLI authentication or permission is unavailable, finish local preparation and report the specific blocker; do not attempt publication repeatedly.
- Record the exact release commit SHA. Inspect `git status`, staged changes, and `git show <sha>:VERSION`. The commit must contain the intended version and release code. Do not silently commit a dirty tree. Resolve uncommitted release changes with the user unless committing them was already authorized. Unrelated local changes can remain if building from an isolated checkout of the selected commit.
- Inspect local and remote tags and the matching GitHub Release before writing. Compare peeled commit SHAs for annotated tags. Distinguish a confirmed missing release from an authentication or network failure.
- Find the preceding published version that is an ancestor of the release commit, then inspect the commits and diff since it. For a first release, describe the actual features at the selected commit.

## Prepare packages and notes before remote mutation

The exact default asset list is:

1. `dist/JSON-Forge-v{version}-macos-arm64.zip`
2. `dist/JSON-Forge-v{version}-macos-arm64.tar.gz`
3. `dist/JSON-Forge-v{version}-windows-x86_64.zip`
4. `dist/JSON-Forge-v{version}-windows-x86_64.exe`

Use explicit paths. Never upload a broad `dist/*` glob, backups, other versions, expanded directories, or personal settings/session files.

Use the project's `.agents/skills/json-forge-packager/SKILL.md` for building and artifact verification. Reuse packages when the current session establishes that they were built and verified from the selected unchanged release source. Filenames, modification times, and matching version strings alone do not establish that correspondence. If provenance is uncertain or an artifact is missing, rebuild all four from the selected commit using that skill; the EXE must embed that build's Windows ZIP. If its instructions are unavailable, report that prerequisite rather than inventing a replacement packaging workflow.

Record each artifact's filename, byte size, and SHA-256 locally. Confirm all four packages passed the packager's checks before touching remote tags or releases. Keep any required build backups.

Write release notes to a temporary UTF-8 file outside the working tree. Default title: `JSON Forge v{version}`. Default notes language: Chinese, following the user's language unless requested otherwise. Include:

- Concrete changes derived from the release diff, grouped into new behavior and fixes only when useful. Describe user-visible outcomes; do not invent changes from the version number.
- A short download table explaining macOS Apple Silicon app ZIP, macOS portable TAR.GZ with `start.sh`, Windows x64 portable ZIP with `start.bat`, and Windows x64 self-extracting GUI EXE.
- The tests and artifact checks actually completed, including that Windows artifacts were structurally verified on macOS but not run on Windows when applicable.
- SHA-256 values for the four attached packages.

Use `--notes-file` for `gh release create` or `gh release edit`; preserve real newlines. Use structured arguments or proper shell quoting for titles and paths. Do not interpolate release prose into shell code.

## Publish or resume

1. Immediately before remote mutation, confirm the session requests publication and the recorded commit/artifact inputs remain unchanged. For prepare-only requests, return the prepared notes and artifact inventory without creating tags or releases. For explicit draft-only requests, retain the draft at the end.
2. If the version tag is absent, create an annotated local tag at the recorded commit, then push only `refs/tags/<tag>` to the verified remote. If the local or remote tag exists at the same commit, reuse it and push only if missing remotely. If either tag points elsewhere, stop and report both SHAs; never move, delete, or force-push a tag automatically.
3. Verify the remote tag resolves to the recorded commit before creating the Release. Use `gh release create <tag> --repo <repo> --verify-tag --draft --title <title> --notes-file <file>` for a new Release, so GitHub cannot select the default branch implicitly. Upload the four explicit files while the Release is still a draft.
4. For an existing Release, inspect its draft/published state, notes, and assets. Preserve existing descriptions unless this session requests updating them. For each matching asset name, compare size and SHA-256 with the local artifact using GitHub's asset digest, or download it to a temporary directory and hash it if no digest is available. Skip identical files and upload missing files. If the same name has different content, report the conflict instead of using `--clobber` or deleting anything. Never delete and recreate an existing published Release to work around immutability.
5. Fetch the Release asset inventory and verify all four exact names, byte sizes, and hashes against the local manifest. Only after successful verification, publish a newly created or resumed draft using `gh release edit <tag> --repo <repo> --draft=false`, unless the user requested a draft. Keep prerelease/latest behavior consistent with the request; do not promote an older version to Latest automatically.
6. Re-read the Release and remote tag. Confirm the requested publication state, tag commit, asset integrity, and download URLs. Return the Release URL, version tag, and the four uploaded or reused assets, noting any Windows execution limitation.

If a network operation fails or times out, inspect remote state before retrying; a timeout may have followed a successful write. Resume missing steps rather than duplicating a Release or replacing completed assets. After two failed retries of the same operation, retain the tag/draft/uploaded assets, report precisely what succeeded and what remains, and stop. Do not publish an incomplete draft or automatically roll back remote data.
