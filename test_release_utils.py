import tempfile
import unittest
from pathlib import Path

from release_utils import backup_existing


class ReleaseUtilsTests(unittest.TestCase):
    def test_windows_launcher_uses_pythonw_and_returns_after_starting(self):
        launcher = (Path(__file__).resolve().parent / "start.bat").read_text(encoding="utf-8")

        self.assertIn("runtime\\python\\pythonw.exe", launcher)
        self.assertIn('start "" /b "%PYTHONW%"', launcher)
        self.assertIn("exit /b 0", launcher)

    def test_backup_existing_prefixes_zip_with_timestamp(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "JSON-Forge-v1.0.0-macos-arm64.zip"
            artifact.write_text("old", encoding="utf-8")

            backup = backup_existing(artifact, "20260830_100556")

            self.assertEqual(
                backup,
                Path(directory) / "bak_20260830_100556_JSON-Forge-v1.0.0-macos-arm64.zip",
            )
            self.assertFalse(artifact.exists())
            self.assertEqual(backup.read_text(encoding="utf-8"), "old")

    def test_backup_existing_keeps_complete_tar_gz_name(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "JSON-Forge-v1.0.0-macos-arm64.tar.gz"
            artifact.write_text("old", encoding="utf-8")

            backup = backup_existing(artifact, "20260830_100556")

            self.assertEqual(
                backup,
                Path(directory) / "bak_20260830_100556_JSON-Forge-v1.0.0-macos-arm64.tar.gz",
            )

    def test_backup_existing_avoids_same_second_collision(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "release.exe"
            first_backup = Path(directory) / "bak_20260830_100556_release.exe"
            first_backup.write_text("first", encoding="utf-8")
            artifact.write_text("second", encoding="utf-8")

            backup = backup_existing(artifact, "20260830_100556")

            self.assertEqual(backup, Path(directory) / "bak_20260830_100556_2_release.exe")
            self.assertEqual(backup.read_text(encoding="utf-8"), "second")

    def test_backup_existing_returns_none_when_artifact_is_absent(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertIsNone(backup_existing(Path(directory) / "missing.zip", "20260830_100556"))


if __name__ == "__main__":
    unittest.main()
