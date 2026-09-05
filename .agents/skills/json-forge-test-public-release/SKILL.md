---
name: json-forge-test-public-release
description: Copy a selected JSON Forge Windows ZIP into a same-named folder, encrypt it with 7zz, rename the archive jf.sa, and replace the existing asset on w8y56f/test-public Release v2. Use when the user asks to distribute a Windows package to test-public; ordinary JSON Forge releases use a different skill.
---

# JSON Forge test-public Release

This workflow targets only `github.com/w8y56f/test-public`, existing Release tag `v2`, and asset `jf.sa`. The application version and the fixed release tag are different: publishing application `v1.2.1` still uses Release `v2`.

Creating, editing, or reviewing this skill does not execute it. When the user subsequently requests this publishing workflow, that request authorizes deleting the old distribution asset and uploading its replacement. Do not request that permission again for the normal replacement. Honor prepare-only instructions.

## Select the source version

Work from the JSON Forge repository root and read applicable repository instructions. Use the version explicitly selected by the user or unambiguously established for this publishing request. Accept `1.2.1` or `v1.2.1`, normalize to a validated three-part version without a leading `v`, and derive the source path:

`dist/JSON-Forge-v{version}-windows-x86_64.zip`

If the intended version is unclear, list available matching Windows ZIP versions and ask which version to publish. Do not infer the choice solely from root `VERSION`, newest modification time, or the last version mentioned during an unrelated task. A request for the current project version explicitly permits reading root `VERSION`.

Require the selected source file to exist. Do not silently choose another version or rebuild it. If absent, report the missing path and ask whether the user wants it built using the project's `json-forge-packager` skill.

## Prepare the encrypted artifact locally

1. Locate `7zz` using `command -v 7zz`; on this Mac the Homebrew executable is normally `/opt/homebrew/bin/7zz`. Require it to work. Do not switch to Bandizip or silently install dependencies.
2. Check ZIP integrity, and check its bundled `VERSION` and `runtime-manifest.json` agree with the selected version. Expected bundle root: `JSON-Forge-windows-x86_64/`; the manifest version includes the `v` prefix. Record the source ZIP's SHA-256 and byte size.
3. Create a fresh temporary staging directory outside the source tree and `dist`. Inside it, create a folder with exactly the source ZIP's stem, for example `JSON-Forge-v1.2.1-windows-x86_64`. **Copy, never move or extract,** the original ZIP into this folder with its original filename. Verify the copied ZIP has the source checksum.
4. From the staging directory, create `payload.7z` with `7zz`, password exactly `Abc*123`, 7z format, and header encryption. Pass arguments as an array with Python `subprocess.run(..., check=True, cwd=staging)` or correctly quote shell arguments. The `*` in the password must remain literal. The equivalent command is:

   ```sh
   7zz a -t7z -mhe=on '-pAbc*123' payload.7z JSON-Forge-v1.2.1-windows-x86_64
   ```

   The version above is an example; substitute the selected folder name. A fresh staging directory avoids accidentally appending to an existing archive. This ZIP is already compressed, so do not promise a substantial size reduction.
5. Test the encrypted archive with the correct password, and confirm testing with a deliberately incorrect password fails. Extract into a separate temporary verification directory using the correct password; verify the exact folder/ZIP structure and that the extracted ZIP's SHA-256 matches the original. Require exactly the selected folder and ZIP, without extra files.
6. Rename `payload.7z` to `jf.sa`. It remains a 7z archive internally. Test the final file explicitly with `7zz t -t7z '-pAbc*123' <absolute-path-to-jf.sa>`. Record its byte size and SHA-256. Recheck that the original ZIP still exists unchanged.

Expected encrypted contents for version `v1.2.1`:

```text
JSON-Forge-v1.2.1-windows-x86_64/
└── JSON-Forge-v1.2.1-windows-x86_64.zip
```

## Replace the existing Release asset

Complete local preparation and verification before deleting any remote asset. A prepare-only request ends with the validated local `jf.sa` path.

1. Check `gh` authentication and access to `w8y56f/test-public`. Inspect the existing `v2` Release and its assets using explicit `--repo w8y56f/test-public` arguments. Record its release ID, title, body, publication state, and asset inventory. Require the existing published Release; if it is missing, inaccessible, a draft, or immutable, report the condition rather than creating another release or changing its state.
2. Preserve the existing `v2` Git tag and release title. Do not create, push, move, or delete a tag; do not change the project Git remote or push JSON Forge source to test-public. Do not change latest/prerelease settings.
3. Identify the old distribution asset: prefer exact `jf.sa`. If absent, a single unmistakable legacy distribution archive such as `jf.7z` or `JSON-Forge-v<version>-windows-x86_64.zip` may be the replacement target. If several candidates exist or the target is unclear, ask the user which asset to remove. Preserve unrelated assets. If no old distribution asset exists, upload the new `jf.sa` directly.
4. Before deleting the selected old asset, download it to a separate local backup directory and verify its byte size and available GitHub digest. Keep the old Release body with the backup. If backup fails, retain the remote file and report the failure. This backup makes recovery possible if the subsequent upload fails.
5. Delete only the selected asset using `gh release delete-asset v2 <exact-asset-name> --repo w8y56f/test-public --yes`, then upload the prepared explicit file path using `gh release upload v2 <absolute-path-to-jf.sa> --repo w8y56f/test-public`. Do not delete the whole Release or use wildcard deletion. Do not use `--clobber` as an uninspected shortcut.
6. Re-fetch the Release assets. Require `jf.sa` in uploaded state with the expected size and SHA-256. Compare GitHub's asset digest, or download the asset to a separate directory and hash it if no digest is returned. Do not trust size alone.
7. After successful asset verification, update Release Notes to **only** `v{version}` (for example `v1.2.1`), without headings, password, changelog, checksum table, or generated notes. Write that text to a UTF-8 temporary file and use `gh release edit v2 --repo w8y56f/test-public --notes-file <file>`.
8. Re-read the Release and verify the notes, unchanged `v2` tag/title/publication state, and new asset. After successful upload and verification, delete this run's same-named staging folder (`JSON-Forge-v{version}-windows-x86_64`) including its copied ZIP, and remove the remaining disposable staging/verification files, including the local `jf.sa`. Verify that the staging folder no longer exists. Cleanup must target only paths created by this run; never delete or modify the original `dist/JSON-Forge-v{version}-windows-x86_64.zip` or another pre-existing folder. Preserve the separate local backup of the previous remote asset. Report the selected application version, Release URL, `jf.sa` download URL, successful staging-folder cleanup, and where the backup remains. If cleanup fails, report the remaining path instead of claiming it was removed.

## Failure and resume

After an uncertain network result, inspect remote state before retrying. A completed matching `jf.sa` upload can be reused on resume; avoid deleting it again merely because a command timed out. Retry a failed network operation at most twice. If deletion succeeded but upload failed, retain the verified new artifact and old backup, state clearly that the Release currently lacks its distribution asset, and report the paths needed to resume. Do not update the notes to the new version until its asset is verified. Do not automatically restore stale files or delete a differently hashed asset encountered on resume without resolving the conflict.
