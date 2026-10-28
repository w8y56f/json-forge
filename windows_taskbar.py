"""Windows taskbar identity for the Python-hosted JSON Forge window.

Window icons alone do not describe a pinned shortcut. Give Explorer an explicit
identity, icon and relaunch command instead of inheriting pythonw.exe's defaults.
The native API is loaded lazily so importing this module is safe on other OSes.
"""

from __future__ import annotations

import ctypes
import logging
import os
from pathlib import Path
import subprocess
import sys
import uuid


APP_USER_MODEL_ID = "LocalTools.JSONForge"
LAUNCHER_PATH_ENV = "JSON_FORGE_LAUNCHER_PATH"
_LOGGER = logging.getLogger(__name__)


class _GUID(ctypes.Structure):
    _fields_ = [
        ("data1", ctypes.c_uint32), ("data2", ctypes.c_uint16),
        ("data3", ctypes.c_uint16), ("data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def parse(cls, value: str):
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


class _PropertyKey(ctypes.Structure):
    _fields_ = [("fmtid", _GUID), ("pid", ctypes.c_uint32)]


class _CountedPointer(ctypes.Structure):
    _fields_ = [("count", ctypes.c_uint32), ("pointer", ctypes.c_void_p)]


class _VariantValue(ctypes.Union):
    # PROPVARIANT's union occupies 16 bytes on Win64, 8 on Win32.
    _fields_ = [("text", ctypes.c_wchar_p), ("array", _CountedPointer),
                ("integer", ctypes.c_int64)]


class _PropVariant(ctypes.Structure):
    _anonymous_ = ("value",)
    _fields_ = [("vt", ctypes.c_uint16), ("reserved", ctypes.c_uint16 * 3),
                ("value", _VariantValue)]


_APP_PROPERTIES = _GUID.parse("9f4c2855-9f79-4b39-a8d0-e1d42de1d5f3")
_PROPERTY_STORE_IID = _GUID.parse("886d8eeb-8cf2-4446-8d02-cdba1dbdcf99")
_PROPERTY_IDS = (2, 3, 4, 5)  # RelaunchCommand, IconResource, DisplayNameResource, ID


def _check_hresult(result: int) -> None:
    if result < 0:
        raise OSError(f"Windows taskbar API failed: HRESULT 0x{result & 0xffffffff:08X}")


class _WindowsShell:
    def __init__(self):
        self.shell = ctypes.WinDLL("shell32", use_last_error=True)
        self.shell.SetCurrentProcessExplicitAppUserModelID.argtypes = [ctypes.c_wchar_p]
        self.shell.SetCurrentProcessExplicitAppUserModelID.restype = ctypes.c_int32
        self.shell.SHGetPropertyStoreForWindow.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p),
        ]
        self.shell.SHGetPropertyStoreForWindow.restype = ctypes.c_int32

    def set_process_id(self):
        _check_hresult(self.shell.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID))

    def set_window_properties(self, hwnd: int, properties: dict[int, str | None]):
        store = ctypes.c_void_p()
        _check_hresult(self.shell.SHGetPropertyStoreForWindow(
            hwnd, ctypes.byref(_PROPERTY_STORE_IID), ctypes.byref(store),
        ))
        vtable = ctypes.cast(store, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        release = ctypes.WINFUNCTYPE(ctypes.c_uint32, ctypes.c_void_p)(vtable[2])
        set_value = ctypes.WINFUNCTYPE(
            ctypes.c_int32, ctypes.c_void_p,
            ctypes.POINTER(_PropertyKey), ctypes.POINTER(_PropVariant),
        )(vtable[6])
        try:
            for pid, text in properties.items():
                key = _PropertyKey(_APP_PROPERTIES, pid)
                value = _PropVariant()
                if text is not None:
                    value.vt = 31  # VT_LPWSTR; None leaves VT_EMPTY to remove a property.
                    value.text = text
                # ctypes owns this string until SetValue has copied it. It must
                # not be passed to PropVariantClear (which frees COM allocations).
                _check_hresult(set_value(store, ctypes.byref(key), ctypes.byref(value)))
        finally:
            release(store)


def relaunch_command(application_dir: Path) -> str:
    """Return a Windows command line without transient document arguments."""
    launcher = os.environ.get(LAUNCHER_PATH_ENV)
    if launcher and Path(launcher).is_file():
        return subprocess.list2cmdline([str(Path(launcher).resolve())])
    python = Path(sys.executable)
    if python.name.lower() == "python.exe":
        pythonw = python.with_name("pythonw.exe")
        if pythonw.is_file():
            python = pythonw
    return subprocess.list2cmdline([
        str(python.resolve()), str(application_dir.resolve() / "app.py"),
    ])


def configure_process_identity() -> bool:
    """Set process identity before QApplication creates any native windows."""
    if sys.platform != "win32":
        return False
    try:
        _WindowsShell().set_process_id()
        return True
    except OSError:
        _LOGGER.warning("Could not set Windows process identity", exc_info=True)
        return False


def configure_window_identity(hwnd: int, application_dir: Path, name: str) -> bool:
    """Describe the pinned shortcut before the main window is shown."""
    if sys.platform != "win32":
        return False
    properties = {
        2: relaunch_command(application_dir),
        3: str(application_dir.resolve() / "assets" / "JSON-Forge.ico") + ",0",
        4: name,
        5: APP_USER_MODEL_ID,
    }
    try:
        _WindowsShell().set_window_properties(hwnd, properties)
        return True
    except OSError:
        _LOGGER.warning("Could not set Windows taskbar properties", exc_info=True)
        # Also remove properties from a partially successful setup.
        clear_window_identity(hwnd)
        return False


def clear_window_identity(hwnd: int) -> None:
    """Release window property resources before the native window closes."""
    if sys.platform != "win32":
        return
    try:
        _WindowsShell().set_window_properties(hwnd, dict.fromkeys(_PROPERTY_IDS))
    except OSError:
        _LOGGER.warning("Could not clear Windows taskbar properties", exc_info=True)
