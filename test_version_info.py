import runpy
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import APP_VERSION
from version_info import (
    COMMIT_ID, DISPLAY_COMMIT_ID, DISPLAY_VERSION, VERSION, read_commit_id,
    read_display_commit_id, read_version, version_file_path,
)


class VersionInfoTests(unittest.TestCase):
    def test_project_version_is_loaded_from_version_file(self):
        self.assertEqual(VERSION, version_file_path().read_text(encoding="utf-8").strip())
        self.assertEqual(DISPLAY_VERSION, f"v{VERSION}")
        self.assertEqual(APP_VERSION, DISPLAY_VERSION)
        self.assertRegex(COMMIT_ID, r"^[0-9a-f]{7}$")
        self.assertIn(DISPLAY_COMMIT_ID, (COMMIT_ID, f"{COMMIT_ID}-dirty"))

    def test_display_commit_tracks_real_git_worktree_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def git(*args):
                return subprocess.run(
                    ["git", "-C", directory, *args], check=True,
                    capture_output=True, text=True,
                ).stdout.strip()

            git("init", "--quiet")
            tracked = root / "tracked.py"
            tracked.write_text("original\n", encoding="utf-8")
            (root / ".gitignore").write_text("ignored.tmp\n", encoding="utf-8")
            git("add", "tracked.py", ".gitignore")
            git("-c", "user.name=Test", "-c", "user.email=test@example.com",
                "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "initial")
            commit = git("rev-parse", "--short=7", "HEAD")
            with patch("version_info.__file__", str(root / "version_info.py")):
                self.assertEqual(read_display_commit_id(commit), commit)
                tracked.write_text("modified\n", encoding="utf-8")
                self.assertEqual(read_display_commit_id(commit), f"{commit}-dirty")
                git("add", "tracked.py")
                self.assertEqual(read_display_commit_id(commit), f"{commit}-dirty")
                git("reset", "--hard", "HEAD")
                new_file = root / "new.py"
                new_file.write_text("new\n", encoding="utf-8")
                self.assertEqual(read_display_commit_id(commit), f"{commit}-dirty")
                new_file.unlink()
                (root / "ignored.tmp").write_text("cache\n", encoding="utf-8")
                self.assertEqual(read_display_commit_id(commit), commit)

    def test_packaged_display_commit_does_not_inspect_git(self):
        with tempfile.TemporaryDirectory() as directory:
            build_commit = Path(directory) / "BUILD_COMMIT"
            build_commit.write_text("a6d8db5\n", encoding="utf-8")
            with (patch("version_info.commit_file_path", return_value=build_commit),
                  patch("version_info.subprocess.run") as run):
                self.assertEqual(read_display_commit_id("a6d8db5"), "a6d8db5")
                run.assert_not_called()
        with (patch("version_info.sys.frozen", True, create=True),
              patch("version_info.subprocess.run") as run):
            self.assertEqual(read_display_commit_id("a6d8db5"), "a6d8db5")
            run.assert_not_called()

    def test_display_commit_preserves_fallback_when_git_unavailable(self):
        with patch("version_info.subprocess.run") as run:
            self.assertEqual(read_display_commit_id("unknown"), "unknown")
            run.assert_not_called()
            for error in (FileNotFoundError("git"), subprocess.CalledProcessError(128, "git")):
                with self.subTest(error=type(error).__name__):
                    run.side_effect = error
                    self.assertEqual(read_display_commit_id("a6d8db5"), "a6d8db5")

    def test_build_commit_file_is_normalized_to_seven_characters(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "BUILD_COMMIT"
            path.write_text("A6D8DB58848BC2337A6554A2AAC709F650783B84\n", encoding="utf-8")
            self.assertEqual(read_commit_id(path), "a6d8db5")
            path.write_text("invalid\n", encoding="utf-8")
            self.assertEqual(read_commit_id(path), "unknown")

    def test_read_version_accepts_three_part_semantic_version(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "VERSION"
            path.write_text("2.3.4\n", encoding="utf-8")
            self.assertEqual(read_version(path), "2.3.4")

    def test_read_version_rejects_invalid_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "VERSION"
            for value in ("v1.0.0", "1.0", "01.0.0", "latest"):
                with self.subTest(value=value):
                    path.write_text(value, encoding="utf-8")
                    with self.assertRaises(RuntimeError):
                        read_version(path)

    def test_all_packaging_artifact_names_use_project_version(self):
        root = Path(__file__).resolve().parent
        macos = runpy.run_path(str(root / "packaging" / "build_macos_app.py"))
        portable = runpy.run_path(str(root / "packaging" / "build_release.py"))
        windows = runpy.run_path(str(root / "packaging" / "build_windows_exe.py"))

        self.assertEqual(macos["archive_name"]("arm64"), f"JSON-Forge-v{VERSION}-macos-arm64.zip")
        self.assertEqual(
            portable["archive_name"]("macos-arm64"),
            f"JSON-Forge-v{VERSION}-macos-arm64.tar.gz",
        )
        self.assertEqual(
            portable["archive_name"]("windows-x86_64"),
            f"JSON-Forge-v{VERSION}-windows-x86_64.zip",
        )
        self.assertEqual(windows["ARCHIVE_NAME"], f"JSON-Forge-v{VERSION}-windows-x86_64.zip")
        self.assertEqual(windows["OUTPUT_NAME"], f"JSON-Forge-v{VERSION}-windows-x86_64.exe")
        self.assertIn("assets/JSON-Forge.png", portable["SOURCE_FILES"])
        self.assertIn("assets/JSON-Forge.ico", portable["SOURCE_FILES"])
        self.assertIn("windows_taskbar.py", portable["SOURCE_FILES"])
        self.assertIn("Reference Book.md", portable["SOURCE_FILES"])
        self.assertRegex(portable["COMMIT_ID"], r"^[0-9a-f]{7}$")
        self.assertTrue((root / "assets" / "JSON-Forge.icns").is_file())
        self.assertTrue((root / "packaging" / "windows_launcher" / "icon_windows_amd64.syso").is_file())


if __name__ == "__main__":
    unittest.main()
