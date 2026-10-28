import ctypes
import os
from pathlib import Path
import runpy
import shutil
import struct
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

import windows_taskbar as taskbar


class TaskbarIdentityTests(unittest.TestCase):
    def test_non_windows_does_not_load_native_apis(self):
        with patch.object(taskbar.sys, "platform", "darwin"), patch.object(taskbar, "_WindowsShell") as native:
            self.assertFalse(taskbar.configure_process_identity())
            self.assertFalse(taskbar.configure_window_identity(1, Path("."), "JSON Forge"))
            taskbar.clear_window_identity(1)
            native.assert_not_called()

    def test_process_and_window_use_same_version_independent_identity(self):
        with tempfile.TemporaryDirectory(prefix="JSON Forge 中文 ") as directory:
            root = Path(directory).resolve()
            python = root / "pythonw.exe"
            python.touch()
            with (patch.object(taskbar.sys, "platform", "win32"),
                  patch.object(taskbar.sys, "executable", str(python)),
                  patch.dict(os.environ, {taskbar.LAUNCHER_PATH_ENV: ""}),
                  patch.object(taskbar, "_WindowsShell") as native):
                self.assertTrue(taskbar.configure_process_identity())
                self.assertTrue(taskbar.configure_window_identity(123, root, "JSON Forge"))
                native.return_value.set_process_id.assert_called_once_with()
                native.return_value.set_window_properties.assert_called_once_with(123, {
                    2: f'"{python}" "{root / "app.py"}"',
                    3: str(root / "assets" / "JSON-Forge.ico") + ",0",
                    4: "JSON Forge",
                    5: "LocalTools.JSONForge",
                })

    def test_exe_relaunch_uses_original_launcher_without_python_or_document_arguments(self):
        with tempfile.TemporaryDirectory(prefix="JSON Forge 中文 ") as directory:
            launcher = Path(directory).resolve() / "JSON Forge.exe"
            launcher.touch()
            with (patch.dict(os.environ, {taskbar.LAUNCHER_PATH_ENV: str(launcher)}),
                  patch.object(taskbar.sys, "argv", ["app.py", "document.json"])):
                self.assertEqual(taskbar.relaunch_command(Path(directory)), f'"{launcher}"')

    def test_direct_python_launch_prefers_pythonw_and_missing_launcher_falls_back(self):
        with tempfile.TemporaryDirectory(prefix="JSON Forge 中文 ") as directory:
            root = Path(directory).resolve()
            python = root / "python.exe"
            pythonw = root / "pythonw.exe"
            python.touch()
            pythonw.touch()
            with (patch.dict(os.environ, {taskbar.LAUNCHER_PATH_ENV: str(root / "missing.exe")}),
                  patch.object(taskbar.sys, "executable", str(python))):
                self.assertEqual(taskbar.relaunch_command(root), f'"{pythonw}" "{root / "app.py"}"')
                pythonw.unlink()
                self.assertEqual(taskbar.relaunch_command(root), f'"{python}" "{root / "app.py"}"')

    def test_api_failure_does_not_prevent_startup_and_clears_partial_properties(self):
        with (patch.object(taskbar.sys, "platform", "win32"),
              patch.object(taskbar, "_WindowsShell") as native,
              self.assertLogs(taskbar.__name__, level="WARNING")):
            native.return_value.set_process_id.side_effect = OSError("unavailable")
            native.return_value.set_window_properties.side_effect = [OSError("failed"), None]
            self.assertFalse(taskbar.configure_process_identity())
            self.assertFalse(taskbar.configure_window_identity(123, Path("."), "JSON Forge"))
            self.assertEqual(native.return_value.set_window_properties.call_args.args,
                             (123, {2: None, 3: None, 4: None, 5: None}))


class NativePropertyStoreTests(unittest.TestCase):
    """Exercise the COM ABI with real ctypes callbacks, without Windows DLLs."""

    def setUp(self):
        self.values = []
        self.releases = []
        self.result = 0
        self.factory = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
        self.release = self.factory(ctypes.c_uint32, ctypes.c_void_p)(
            lambda store: self.releases.append(store) or 0)
        self.set_value = self.factory(
            ctypes.c_int32, ctypes.c_void_p,
            ctypes.POINTER(taskbar._PropertyKey), ctypes.POINTER(taskbar._PropVariant),
        )(self.record_value)
        self.vtable = (ctypes.c_void_p * 8)()
        self.vtable[2] = ctypes.cast(self.release, ctypes.c_void_p).value
        self.vtable[6] = ctypes.cast(self.set_value, ctypes.c_void_p).value
        self.store = ctypes.pointer(ctypes.cast(self.vtable, ctypes.POINTER(ctypes.c_void_p)))
        self.dll = Mock()
        self.dll.SetCurrentProcessExplicitAppUserModelID.return_value = 0
        self.dll.SHGetPropertyStoreForWindow.side_effect = self.get_store

    def get_store(self, hwnd, iid, output):
        self.assertEqual(hwnd, 123)
        self.assertEqual(bytes(iid._obj), bytes(taskbar._PROPERTY_STORE_IID))
        ctypes.cast(output, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.cast(self.store, ctypes.c_void_p)
        return 0

    def record_value(self, store, key, value):
        self.values.append((key.contents.pid, value.contents.vt, value.contents.text))
        self.assertEqual(bytes(key.contents.fmtid), bytes(taskbar._APP_PROPERTIES))
        return self.result

    def shell(self):
        with patch.object(ctypes, "WinDLL", return_value=self.dll, create=True):
            return taskbar._WindowsShell()

    def test_native_layout_and_string_properties_then_cleanup(self):
        self.assertEqual(ctypes.sizeof(taskbar._GUID), 16)
        self.assertEqual(ctypes.sizeof(taskbar._PropertyKey), 20)
        self.assertEqual(ctypes.sizeof(taskbar._PropVariant), 24 if struct.calcsize("P") == 8 else 16)
        shell = self.shell()
        shell.set_process_id()
        self.dll.SetCurrentProcessExplicitAppUserModelID.assert_called_once_with("LocalTools.JSONForge")
        with patch.object(ctypes, "WINFUNCTYPE", self.factory, create=True):
            shell.set_window_properties(123, {2: '"C:\\应用 路径\\JSON Forge.exe"', 5: taskbar.APP_USER_MODEL_ID})
            shell.set_window_properties(123, {2: None, 5: None})
        self.assertEqual(self.values, [
            (2, 31, '"C:\\应用 路径\\JSON Forge.exe"'), (5, 31, taskbar.APP_USER_MODEL_ID),
            (2, 0, None), (5, 0, None),
        ])
        self.assertEqual(len(self.releases), 2)

    def test_property_store_is_released_when_setvalue_fails(self):
        self.result = -2147467259  # E_FAIL
        shell = self.shell()
        with (patch.object(ctypes, "WINFUNCTYPE", self.factory, create=True),
              self.assertRaisesRegex(OSError, "80004005")):
            shell.set_window_properties(123, {2: "command"})
        self.assertEqual(len(self.releases), 1)


class WindowsLauncherBuildTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("go"), "Go is required for PE icon verification")
    def test_real_build_embeds_icon_resources(self):
        root = Path(__file__).resolve().parent
        build = runpy.run_path(str(root / "packaging" / "build_windows_exe.py"))["main"]
        with tempfile.TemporaryDirectory(prefix="json-forge-icon-test-") as directory:
            fixture = Path(directory)
            source = fixture / "packaging" / "windows_launcher"
            source.mkdir(parents=True)
            for name in ("main.go", "icon_windows_amd64.syso"):
                shutil.copy2(root / "packaging" / "windows_launcher" / name, source / name)
            (fixture / "dist").mkdir()
            archive = fixture / "fixture.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("JSON-Forge-windows-x86_64/app.py", "# test payload\n")
            with (patch.dict(build.__globals__, {"ROOT": fixture}),
                  patch.object(sys, "argv", ["build_windows_exe.py", "--archive", str(archive)])):
                self.assertEqual(build(), 0)
            binary = next((fixture / "dist").glob("*.exe")).read_bytes()
            pe = struct.unpack_from("<I", binary, 60)[0]
            section_count = struct.unpack_from("<H", binary, pe + 6)[0]
            optional = pe + 24
            optional_size = struct.unpack_from("<H", binary, pe + 20)[0]
            # The launcher targets PE32+ (Windows amd64).
            self.assertEqual(struct.unpack_from("<H", binary, optional)[0], 0x20b)
            rva, size = struct.unpack_from("<II", binary, optional + 112 + 16)
            self.assertGreater(size, 0, "EXE has no resource directory")
            resource_offset = None
            for index in range(section_count):
                offset = optional + optional_size + index * 40
                virtual_size, address, raw_size, raw = struct.unpack_from("<IIII", binary, offset + 8)
                if address <= rva < address + max(virtual_size, raw_size):
                    resource_offset = raw + rva - address
                    break
            self.assertIsNotNone(resource_offset)
            named, numbered = struct.unpack_from("<HH", binary, resource_offset + 12)
            kinds = {struct.unpack_from("<I", binary, resource_offset + 16 + index * 8)[0]
                     for index in range(named + numbered)}
            self.assertTrue({3, 14}.issubset(kinds), "EXE is missing ICON or GROUP_ICON")


if __name__ == "__main__":
    unittest.main()
