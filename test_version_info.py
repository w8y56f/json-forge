import runpy
import tempfile
import unittest
from pathlib import Path

from app import APP_VERSION
from version_info import DISPLAY_VERSION, VERSION, read_version, version_file_path


class VersionInfoTests(unittest.TestCase):
    def test_project_version_is_loaded_from_version_file(self):
        self.assertEqual(VERSION, version_file_path().read_text(encoding="utf-8").strip())
        self.assertEqual(DISPLAY_VERSION, f"v{VERSION}")
        self.assertEqual(APP_VERSION, DISPLAY_VERSION)

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
        self.assertTrue((root / "assets" / "JSON-Forge.icns").is_file())
        self.assertTrue((root / "packaging" / "windows_launcher" / "icon_windows_amd64.syso").is_file())


if __name__ == "__main__":
    unittest.main()
