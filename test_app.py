import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtGui import QCloseEvent, QTextCursor
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QMessageBox, QPlainTextEdit, QTabBar

from app import (
    APP_NAME,
    APP_VERSION,
    COMMIT_ID,
    DISPLAY_COMMIT_ID,
    JsonEditor,
    JsonWindow,
    MoreSettingsDialog,
    acquire_instance_lock,
    application_icon_path,
    create_app_settings,
    default_settings_file_path,
    default_settings_values,
    instance_lock_path,
    migrate_legacy_session_file,
    platform_shortcut_hint,
    reset_settings_to_defaults,
    session_file_path,
    settings_file_path,
)


class InstanceLockTests(unittest.TestCase):
    def test_second_lock_is_rejected_until_first_is_released(self):
        with tempfile.TemporaryDirectory() as directory:
            previous = os.environ.get("JSON_STUDIO_INSTANCE_LOCK_PATH")
            os.environ["JSON_STUDIO_INSTANCE_LOCK_PATH"] = os.path.join(directory, "json-forge.lock")
            first = second = None
            try:
                first, already_running = acquire_instance_lock()
                self.assertIsNotNone(first)
                self.assertFalse(already_running)
                self.assertEqual(instance_lock_path(), Path(os.environ["JSON_STUDIO_INSTANCE_LOCK_PATH"]).resolve())

                second, already_running = acquire_instance_lock()
                self.assertIsNone(second)
                self.assertTrue(already_running)
            finally:
                if first is not None:
                    first.unlock()
                if second is not None:
                    second.unlock()
                if previous is None:
                    os.environ.pop("JSON_STUDIO_INSTANCE_LOCK_PATH", None)
                else:
                    os.environ["JSON_STUDIO_INSTANCE_LOCK_PATH"] = previous

    def test_release_lock_uses_platform_user_data_directory(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "APPDATA": directory,
                "LOCALAPPDATA": "",
                "JSON_STUDIO_INSTANCE_LOCK_PATH": "",
            },
            clear=False,
        ), patch("app.sys.platform", "win32"), patch("app.is_release_package", return_value=True):
            self.assertEqual(
                instance_lock_path(),
                Path(directory) / APP_NAME / "cache" / "json-forge.lock",
            )


class SessionMigrationTests(unittest.TestCase):
    def test_release_package_session_path_migrates_from_bundle_directory(self):
        import app

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / "bundle"
            (bundle / "runtime").mkdir(parents=True)
            legacy = bundle / "cache" / "session.json"
            legacy.parent.mkdir()
            legacy.write_text("legacy-session", encoding="utf-8")
            data_root = root / "roaming"
            with patch.dict(
                os.environ,
                {
                    "APPDATA": str(data_root),
                    "JSON_STUDIO_SESSION_PATH": "",
                },
                clear=False,
            ), patch("app.sys.platform", "win32"), patch(
                "app.__file__", str(bundle / "app.py")
            ):
                self.assertEqual(
                    session_file_path(),
                    data_root / APP_NAME / "cache" / "session.json",
                )
            self.assertEqual(
                (data_root / APP_NAME / "cache" / "session.json").read_text(
                    encoding="utf-8"
                ),
                "legacy-session",
            )
            self.assertFalse(legacy.exists())

    def test_legacy_session_is_copied_then_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "old" / "cache" / "session.json"
            destination = root / "user" / "cache" / "session.json"
            legacy.parent.mkdir(parents=True)
            legacy.write_text('{"tabs": []}', encoding="utf-8")

            self.assertTrue(migrate_legacy_session_file(legacy, destination))
            self.assertEqual(destination.read_text(encoding="utf-8"), '{"tabs": []}')
            self.assertFalse(legacy.exists())

    def test_existing_user_session_wins_and_legacy_is_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "old-session.json"
            destination = root / "user-session.json"
            legacy.write_text("old", encoding="utf-8")
            destination.write_text("new", encoding="utf-8")

            self.assertTrue(migrate_legacy_session_file(legacy, destination))
            self.assertEqual(destination.read_text(encoding="utf-8"), "new")
            self.assertFalse(legacy.exists())

    def test_failed_migration_keeps_legacy_session(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "old-session.json"
            destination = root / "not-a-directory" / "session.json"
            legacy.write_text("old", encoding="utf-8")
            destination.parent.write_text("blocker", encoding="utf-8")

            self.assertFalse(migrate_legacy_session_file(legacy, destination))
            self.assertEqual(legacy.read_text(encoding="utf-8"), "old")


class SettingsStorageTests(unittest.TestCase):
    def test_application_icon_resource_exists(self):
        icon = application_icon_path()
        self.assertTrue(icon.is_file())
        self.assertEqual(icon.name, "JSON-Forge.png")

    def test_default_settings_file_contains_all_application_defaults(self):
        defaults = default_settings_values(force_reload=True)
        self.assertTrue(default_settings_file_path().is_file())
        self.assertEqual(defaults.get("theme"), "light")
        self.assertEqual(defaults.get("language"), "zh_CN")
        self.assertEqual(defaults.get("tab_style"), "practical")
        self.assertEqual(defaults.get("show_line_numbers"), "true")
        self.assertEqual(defaults.get("confirm_exit"), "true")
        self.assertEqual(defaults.get("single_instance"), "true")
        self.assertEqual(defaults.get("brace_guides"), "true")
        self.assertEqual(defaults.get("line_wrap"), "true")
        self.assertEqual(defaults.get("editor_font_size"), "13")

    def test_portable_ini_settings_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "config", "settings.ini")
            previous = os.environ.get("JSON_STUDIO_SETTINGS_PATH")
            os.environ["JSON_STUDIO_SETTINGS_PATH"] = path
            try:
                settings = create_app_settings()
                settings.setValue("theme", "dark")
                settings.setValue("language", "en")
                settings.sync()

                self.assertEqual(settings_file_path(), Path(path).resolve())
                self.assertTrue(os.path.exists(path))
                reopened = create_app_settings()
                self.assertEqual(reopened.value("theme"), "dark")
                self.assertEqual(reopened.value("language"), "en")
            finally:
                if previous is None:
                    os.environ.pop("JSON_STUDIO_SETTINGS_PATH", None)
                else:
                    os.environ["JSON_STUDIO_SETTINGS_PATH"] = previous

    def test_reset_settings_overwrites_current_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "settings.ini")
            settings = QSettings(path, QSettings.Format.IniFormat)
            settings.setValue("theme", "dark")
            settings.setValue("language", "en")
            settings.setValue("single_instance", False)
            settings.sync()

            self.assertTrue(reset_settings_to_defaults(settings))
            self.assertEqual(settings.value("theme"), "light")
            self.assertEqual(settings.value("language"), "zh_CN")
            self.assertEqual(settings.value("single_instance"), "true")


class SessionPersistenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.settings_path = os.path.join(self.directory.name, "settings.ini")
        self.session_path = os.path.join(self.directory.name, "session.json")
        os.environ["JSON_STUDIO_SETTINGS_PATH"] = self.settings_path
        os.environ["JSON_STUDIO_SESSION_PATH"] = self.session_path
        settings = QSettings(self.settings_path, QSettings.Format.IniFormat)
        settings.setValue("confirm_exit", False)
        settings.sync()

    def tearDown(self):
        os.environ.pop("JSON_STUDIO_SETTINGS_PATH", None)
        os.environ.pop("JSON_STUDIO_SESSION_PATH", None)
        self.directory.cleanup()

    def test_tabs_and_editor_state_are_restored(self):
        window = JsonWindow()
        window.editor.setPlainText('{\n  "name": "Alice",\n  "items": [1, 2]\n}')
        window.editor.set_bookmark(1)
        window.editor.toggle_fold(0)
        window.tab_bar.setTabText(0, "payload")
        window._mark_session_dirty()

        window.add_tab()
        window.editor.setPlainText('{"second": true}')
        window.tab_bar.setTabText(1, "notes")
        window._mark_session_dirty()
        window._save_session(force=True)
        self.assertTrue(os.path.exists(session_file_path()))
        window.deleteLater()
        self.app.processEvents()

        restored = JsonWindow()
        self.assertEqual(restored.tab_bar.count(), 2)
        self.assertEqual(restored.tab_bar.tabText(0), "payload")
        self.assertEqual(restored.tab_bar.tabText(1), "notes")
        self.assertEqual(restored.editor.toPlainText(), '{"second": true}')
        self.assertEqual(restored.tab_bar.currentIndex(), 1)
        first_editor = restored.editor_stack.widget(0)
        self.assertEqual(first_editor.bookmark_block_numbers(), [1])
        self.assertEqual(first_editor.collapsed_blocks, {0})
        restored.deleteLater()
        self.app.processEvents()


class PlatformHintTests(unittest.TestCase):
    def test_macos_uses_command_symbols(self):
        hint = platform_shortcut_hint("darwin")
        self.assertIn("⌘↵ 格式化", hint)
        self.assertIn("⌘⇧M 紧凑", hint)
        self.assertIn("⌘滚轮 / ⌘+−0 缩放", hint)
        self.assertIn("选中字段时自动显示JSONPath", hint)
        self.assertIn("⌘F2 添加/取消书签 · F2/⇧F2 跳转", hint)

    def test_windows_uses_ctrl_labels(self):
        hint = platform_shortcut_hint("win32")
        self.assertIn("Ctrl+Enter 格式化", hint)
        self.assertIn("Ctrl+Shift+M 紧凑", hint)
        self.assertIn("Ctrl+滚轮 / Ctrl++−0 缩放", hint)
        self.assertIn("Ctrl+F2 添加/取消书签 · F2/Shift+F2 跳转", hint)

    def test_linux_uses_ctrl_labels(self):
        hint = platform_shortcut_hint("linux")
        self.assertIn("Ctrl+Enter 格式化", hint)
        self.assertIn("Ctrl+Shift+M 紧凑", hint)

    def test_english_windows_hint(self):
        hint = platform_shortcut_hint("win32", "en")
        self.assertIn("Ctrl+Enter Format", hint)
        self.assertIn("Ctrl+Shift+M Minify", hint)
        self.assertIn("Select a field to show JSONPath", hint)
        self.assertIn("Click left of a line number or Ctrl/Cmd+F2 to Toggle Bookmark", hint)


class LanguageUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.settings_dir = tempfile.TemporaryDirectory()
        cls.settings_path = os.path.join(cls.settings_dir.name, "settings.ini")
        cls.session_path = os.path.join(cls.settings_dir.name, "session.json")
        os.environ["JSON_STUDIO_SETTINGS_PATH"] = cls.settings_path
        os.environ["JSON_STUDIO_SESSION_PATH"] = cls.session_path

    @classmethod
    def tearDownClass(cls):
        os.environ.pop("JSON_STUDIO_SETTINGS_PATH", None)
        os.environ.pop("JSON_STUDIO_SESSION_PATH", None)
        cls.settings_dir.cleanup()

    def setUp(self):
        settings = QSettings(self.settings_path, QSettings.Format.IniFormat)
        settings.clear()
        settings.setValue("confirm_exit", False)
        settings.sync()
        self.window = JsonWindow()

    def tearDown(self):
        self.window.deleteLater()
        self.app.processEvents()

    def test_taskbar_properties_are_cleared_only_when_close_is_accepted(self):
        self.window._taskbar_hwnd = 123
        self.window.settings.setValue("confirm_exit", True)
        with (patch("app.clear_window_identity") as clear,
              patch.object(self.window, "_confirm_exit", return_value=False) as confirm):
            event = QCloseEvent()
            self.window.closeEvent(event)
            self.assertFalse(event.isAccepted())
            clear.assert_not_called()
            self.assertEqual(self.window._taskbar_hwnd, 123)

            confirm.return_value = True
            event = QCloseEvent()
            self.window.closeEvent(event)
            self.assertTrue(event.isAccepted())
            clear.assert_called_once_with(123)
            self.assertIsNone(self.window._taskbar_hwnd)

    def test_language_switch_updates_buttons_hover_and_status(self):
        self.assertEqual(self.window.theme, "light")
        self.assertTrue(self.window.editor.brace_guides_visible)
        self.window.apply_language("en")
        self.assertEqual(self.window.compact_button.text(), "Minify JSON")
        self.assertEqual(self.window.copy_postman_button.text(), "Copy Postman JSON")
        self.assertEqual(self.window.remove_null_button.text(), "Remove Null Fields")
        self.assertEqual(self.window.sort_menu_button.text(), "Sort Actions")
        self.assertEqual(self.window.focus_mode_button.text(), "Focus Editor")
        tab_layout = self.window.tab_row_widget.layout()
        self.assertEqual(tab_layout.indexOf(self.window.focus_mode_button), tab_layout.count() - 1)
        self.assertIn("removing JSON5 comments", self.window.copy_postman_button.toolTip())
        self.assertIn("preserving comments", self.window.format_button.toolTip())
        self.assertEqual(self.window.wrap_button.text(), "Wrap")
        self.assertEqual(self.window.tab_bar.rename_hint, "Double-click the tab title to rename")
        self.assertEqual(self.window.path_label.text(), "Path  $")
        self.assertEqual(self.window.copy_path_label.text(), "Copy JSON Path ->")
        self.assertIn("Waiting for input", self.window.stats_label.text())
        self.assertEqual(self.window.settings.value("language"), "en")

        self.window.resize(1080, 720)
        self.window.show()
        self.app.processEvents()
        self.window.tab_bar.setTabText(0, "short")
        self.assertEqual(
            self.window.tab_bar.tooltip_text(0),
            "Double-click the tab title to rename",
        )
        long_title = "a-very-long-tab-title-that-must-be-elided-in-the-tab-bar"
        self.window.tab_bar.setTabText(0, long_title)
        self.window.tab_bar.setMaximumWidth(180)
        self.app.processEvents()
        self.assertEqual(self.window.tab_bar.tooltip_text(0), long_title)

        self.window.apply_language("zh_CN")
        self.assertEqual(self.window.compact_button.text(), "压缩JSON")
        self.assertEqual(self.window.copy_postman_button.text(), "拷贝Postman JSON")
        self.assertIn("去掉json5", self.window.copy_postman_button.toolTip())
        self.assertEqual(self.window.wrap_button.text(), "换行")
        self.assertEqual(self.window.tab_bar.rename_hint, "双击标签标题可重命名")

    def test_about_displays_short_commit_beside_version(self):
        with patch("app.QMessageBox") as dialog_type:
            self.window.show_about()

        dialog = dialog_type.return_value
        dialog.setText.assert_called_once_with(f"{APP_NAME} {APP_VERSION} ({DISPLAY_COMMIT_ID})")
        dialog.exec.assert_called_once()

    def test_line_wrap_button_precedes_fold_and_persists_for_all_tabs(self):
        toolbar_layout = self.window.wrap_button.parentWidget().layout()
        self.assertEqual(
            toolbar_layout.indexOf(self.window.wrap_button) + 1,
            toolbar_layout.indexOf(self.window.selection_highlight_button),
        )
        self.assertEqual(
            toolbar_layout.indexOf(self.window.selection_highlight_button) + 1,
            toolbar_layout.indexOf(self.window.fold_button),
        )
        self.assertTrue(self.window.wrap_button.isChecked())
        self.assertTrue(self.window.settings.contains("line_wrap"))
        self.assertTrue(self.window.settings.value("line_wrap", False, type=bool))
        self.assertEqual(
            self.window.editor.lineWrapMode(),
            QPlainTextEdit.LineWrapMode.WidgetWidth,
        )

        self.assertEqual(
            toolbar_layout.indexOf(self.window.copy_postman_button) + 1,
            toolbar_layout.indexOf(self.window.remove_null_button),
        )
        self.assertEqual(
            toolbar_layout.indexOf(self.window.remove_null_button) + 1,
            toolbar_layout.indexOf(self.window.sort_menu_button),
        )
        self.assertEqual(
            toolbar_layout.indexOf(self.window.sort_menu_button) + 1,
            toolbar_layout.indexOf(self.window.key_value_menu_button),
        )
        self.assertEqual(self.window.sort_menu.actions()[0], self.window.sort_keys_ascending_action)
        self.assertEqual(self.window.sort_menu.actions()[1], self.window.sort_keys_descending_action)
        self.assertEqual(self.window.key_value_menu.actions()[4], self.window.key_value_double_action)
        self.assertTrue(self.window.key_value_menu.actions()[6].isSeparator())
        self.assertEqual(self.window.key_value_menu.actions()[7], self.window.key_initial_upper_action)
        self.assertEqual(self.window.key_value_menu.actions()[8], self.window.key_initial_lower_action)
        self.assertTrue(self.window.key_value_menu.actions()[9].isSeparator())
        self.assertEqual(self.window.key_value_menu.actions()[10], self.window.remove_quote_escapes_action)
        self.assertEqual(self.window.key_value_menu.actions()[11], self.window.escape_double_quotes_action)
        self.assertEqual(self.window.key_value_menu.actions()[12], self.window.escape_single_quotes_action)

        self.window.wrap_button.click()
        self.assertFalse(self.window.settings.value("line_wrap", True, type=bool))
        self.assertEqual(
            self.window.editor.lineWrapMode(),
            QPlainTextEdit.LineWrapMode.NoWrap,
        )
        self.window.add_tab()
        self.assertEqual(
            self.window.editor.lineWrapMode(),
            QPlainTextEdit.LineWrapMode.NoWrap,
        )
        first_editor = self.window.editor_stack.widget(0)
        self.window.wrap_button.click()
        self.assertTrue(self.window.settings.value("line_wrap", False, type=bool))
        self.assertEqual(first_editor.lineWrapMode(), QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.assertEqual(
            self.window.editor.lineWrapMode(),
            QPlainTextEdit.LineWrapMode.WidgetWidth,
        )

    def test_focus_mode_keeps_tabs_and_hides_non_editor_chrome(self):
        self.assertFalse(self.window.focus_mode_enabled)
        self.window.focus_mode_button.click()

        self.assertTrue(self.window.focus_mode_enabled)
        self.assertEqual(self.window.focus_mode_button.text(), "退出专注")
        self.assertTrue(self.window.drag_bar.isHidden())
        self.assertFalse(self.window.tab_row_widget.isHidden())
        self.assertTrue(self.window.toolbar_container.isHidden())
        self.assertTrue(self.window.hint_container.isHidden())
        self.assertTrue(self.window.status_bar.isHidden())

        self.window.toggle_focus_mode(False)
        self.assertFalse(self.window.focus_mode_enabled)
        self.assertEqual(self.window.focus_mode_button.text(), "专注编辑")
        self.assertFalse(self.window.drag_bar.isHidden())
        self.assertFalse(self.window.toolbar_container.isHidden())
        self.assertFalse(self.window.hint_container.isHidden())
        self.assertFalse(self.window.status_bar.isHidden())

    def test_editor_font_zoom_updates_all_tabs_and_persists(self):
        self.assertEqual(self.window.editor_font_size, 13)
        self.assertEqual(self.window.editor.font().pointSize(), 13)

        self.window.add_tab()
        first_editor = self.window.editor_stack.widget(0)
        self.window.adjust_editor_font_size(2)
        self.assertEqual(self.window.editor_font_size, 15)
        self.assertEqual(self.window.settings.value("editor_font_size", type=int), 15)
        self.assertEqual(first_editor.font().pointSize(), 15)
        self.assertEqual(self.window.editor.font().pointSize(), 15)

        self.window.adjust_editor_font_size(-100)
        self.assertEqual(self.window.editor_font_size, 8)
        self.window.adjust_editor_font_size(0)
        self.assertEqual(self.window.editor_font_size, 13)

    def test_format_button_preserves_json5_comments_and_literal_spelling(self):
        self.window.editor.setPlainText("{\n// note\nname:'Alice',\nratio:.5,\ntags:[1,2,],\n}")

        self.window.apply_transform(False, "double", preserve_source=True)

        output = self.window.editor.toPlainText()
        self.assertIn("// note", output)
        self.assertIn("name: 'Alice'", output)
        self.assertIn("ratio: .5", output)
        self.assertIn("2,\n\t]", output)

    def test_copy_postman_normalizes_silently_without_changing_editor(self):
        source = "{\n// note\nname:'Alice', ratio:.5, tags:[1,2,],\n}"
        self.window.editor.setPlainText(source)
        layout = self.window.copy_postman_button.parentWidget().layout()
        self.assertEqual(
            layout.indexOf(self.window.copy_postman_button) + 1,
            layout.indexOf(self.window.remove_null_button),
        )
        with patch.object(self.window, "_confirm_json5_minify") as confirm, \
                patch("app.QMessageBox.warning") as warning, \
                patch("app.QToolTip.showText") as toast:
            self.window.copy_postman_button.click()
        confirm.assert_not_called()
        warning.assert_not_called()
        toast.assert_called_once()
        self.assertEqual(QApplication.clipboard().text(), '{"name":"Alice","ratio":0.5,"tags":[1,2]}')
        self.assertEqual(self.window.editor.toPlainText(), source)
        self.assertEqual(self.window.hint.text(), "已放进剪切板")

    def test_remove_null_fields_removes_object_fields_and_reports_count(self):
        self.window.editor.setPlainText(
            '{"remove":null,"keep":1,"nested":{"remove":null,"keep":2},'
            '"items":[null,{"remove":null,"keep":3}]}'
        )

        with patch("app.QMessageBox.information") as information:
            self.window.remove_null_button.click()

        self.assertEqual(
            json.loads(self.window.editor.toPlainText()),
            {"keep": 1, "nested": {"keep": 2}, "items": [None, {"keep": 3}]},
        )
        information.assert_called_once()
        self.assertEqual(information.call_args.args[2], "已移除 3 个 null 值字段")

    def test_remove_null_fields_reports_zero_without_changing_content(self):
        source = '{"keep": [null, {"name": "Alice"}]}'
        self.window.editor.setPlainText(source)
        self.window.apply_language("en")

        with patch("app.QMessageBox.information") as information:
            self.window.remove_null_button.click()

        self.assertEqual(self.window.editor.toPlainText(), source)
        information.assert_called_once()
        self.assertEqual(information.call_args.args[2], "Removed 0 null-valued field(s)")

    def test_sort_actions_sort_all_objects_by_unicode_key_order(self):
        self.window.editor.setPlainText(
            '{"z":1,"中文":2,"a":3,"A":4,"nested":{"z":1,"a":2},'
            '"items":[{"b":1,"a":2}]}'
        )

        self.window.sort_keys_ascending_action.trigger()
        ascending = json.loads(self.window.editor.toPlainText())
        self.assertEqual(list(ascending), ["A", "a", "items", "nested", "z", "中文"])
        self.assertEqual(list(ascending["nested"]), ["a", "z"])
        self.assertEqual(list(ascending["items"][0]), ["a", "b"])

        self.window.sort_keys_descending_action.trigger()
        descending = json.loads(self.window.editor.toPlainText())
        self.assertEqual(list(descending), ["中文", "z", "nested", "items", "a", "A"])
        self.assertEqual(list(descending["nested"]), ["z", "a"])

    def test_copy_postman_after_quote_rewrite_and_in_english(self):
        self.window.apply_language("en")
        self.window.editor.setPlainText('{"name":"Alice"}')
        self.window.key_value_single_action.trigger()
        with patch("app.QToolTip.showText") as toast:
            self.window.copy_postman_button.click()
        self.assertEqual(QApplication.clipboard().text(), '{"name":"Alice"}')
        self.assertEqual(toast.call_args.args[1], "Copied to clipboard")

    def test_copy_postman_invalid_or_empty_input_shows_error_and_preserves_clipboard(self):
        for language in ("zh_CN", "en"):
            self.window.apply_language(language)
            for source in ('{"name": }', '   '):
                with self.subTest(language=language, source=source):
                    self.window.editor.setPlainText(source)
                    QApplication.clipboard().setText("previous clipboard")
                    with patch("app.QMessageBox.warning") as warning, \
                            patch("app.QToolTip.showText") as toast:
                        self.window.copy_postman_button.click()
                    warning.assert_called_once()
                    toast.assert_not_called()
                    self.assertEqual(QApplication.clipboard().text(), "previous clipboard")
                    self.assertEqual(self.window.editor.toPlainText(), source)
                    if language == "en":
                        self.assertTrue(warning.call_args.args[2].isascii())

    def test_minify_confirms_before_normalizing_json5_source(self):
        source = "{\n// note\nname:'Alice',\n}"
        self.window.editor.setPlainText(source)

        with patch.object(self.window, "_confirm_json5_minify", return_value=False) as confirm:
            self.window.apply_transform(True, "double")

        confirm.assert_called_once()
        self.assertEqual(self.window.editor.toPlainText(), source)

    def test_minify_confirms_after_json5_quote_rewrite(self):
        self.window.editor.setPlainText("{\n// note\nname:'Alice',\n}")
        self.window.key_value_double_action.trigger()
        rewritten = self.window.editor.toPlainText()
        self.assertIn("// note", rewritten)
        self.assertIn('name:"Alice"', rewritten)

        with patch.object(self.window, "_confirm_json5_minify", return_value=False) as confirm:
            self.window.apply_transform(True, "double")

        confirm.assert_called_once()
        self.assertEqual(self.window.editor.toPlainText(), rewritten)

    def test_minify_does_not_confirm_for_pretty_standard_json(self):
        self.window.editor.setPlainText('{\n  "name": "Alice"\n}')

        with patch.object(self.window, "_confirm_json5_minify") as confirm:
            self.window.apply_transform(True, "double")

        confirm.assert_not_called()
        self.assertEqual(self.window.editor.toPlainText(), '{"name":"Alice"}')

    def test_quote_buttons_rewrite_only_requested_tokens_without_reformatting(self):
        self.window.editor.setPlainText("{ name: 'Alice', city : 'Taipei', count: 2, }")

        self.window.key_value_double_action.trigger()
        self.assertEqual(
            self.window.editor.toPlainText(),
            '{ name: "Alice", city : "Taipei", count: 2, }',
        )

        self.window.double_action.trigger()
        self.assertEqual(
            self.window.editor.toPlainText(),
            '{ "name": "Alice", "city" : "Taipei", "count": 2, }',
        )

    def test_quote_escape_menu_actions_round_trip_without_reformatting(self):
        source = "{ name: 'stone', count: 2 }"
        self.window.editor.setPlainText(source)

        self.window.escape_double_quotes_action.trigger()
        self.assertEqual(self.window.editor.toPlainText(), r'{ \"name\": \"stone\", \"count\": 2 }')
        self.assertIsNone(self.window.current_value)

        self.window.remove_quote_escapes_action.trigger()
        self.assertEqual(self.window.editor.toPlainText(), '{ "name": "stone", "count": 2 }')
        self.assertEqual(self.window.current_value, {"name": "stone", "count": 2})

        self.window.escape_single_quotes_action.trigger()
        self.assertEqual(self.window.editor.toPlainText(), r"{ \'name\': \'stone\', \'count\': 2 }")
        self.window.escape_single_quotes_action.trigger()
        self.assertEqual(self.window.editor.toPlainText(), r"{ \'name\': \'stone\', \'count\': 2 }")

    def test_key_initial_actions_preserve_non_english_keys(self):
        self.window.editor.setPlainText("{ firstName: 1, 'LastName': 2, 中文: 3 }")

        self.window.key_initial_upper_action.trigger()
        self.assertEqual(self.window.editor.toPlainText(), "{ FirstName: 1, 'LastName': 2, 中文: 3 }")

        self.window.key_initial_lower_action.trigger()
        self.assertEqual(self.window.editor.toPlainText(), "{ firstName: 1, 'lastName': 2, 中文: 3 }")

    def test_transform_preserves_editor_scroll_position(self):
        editor = self.window.editor
        editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        editor.setPlainText("{\n" + ",\n".join(
            f"item{index}: '" + "x" * 160 + "'" for index in range(80)
        ) + "\n}")
        self.app.processEvents()
        vertical = editor.verticalScrollBar()
        horizontal = editor.horizontalScrollBar()
        self.assertGreater(vertical.maximum(), 0)
        self.assertGreater(horizontal.maximum(), 0)
        vertical.setValue(vertical.maximum() // 2)
        horizontal.setValue(horizontal.maximum() // 2)
        expected = (vertical.value(), horizontal.value())

        self.window.apply_transform(
            False, "double", preserve_presentation=True, value_quote="double",
        )
        self.app.processEvents()

        self.assertEqual((vertical.value(), horizontal.value()), expected)

    def test_format_resets_horizontal_scroll_to_left_edge(self):
        editor = self.window.editor
        editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        editor.setPlainText("{\n" + ",\n".join(
            f'  "item{index}": "' + "x" * 160 + '"' for index in range(80)
        ) + "\n}")
        self.app.processEvents()
        vertical = editor.verticalScrollBar()
        horizontal = editor.horizontalScrollBar()
        self.assertGreater(horizontal.maximum(), 0)
        vertical.setValue(vertical.maximum() // 2)
        horizontal.setValue(horizontal.maximum() // 2)
        expected_vertical = vertical.value()

        self.window.format_button.click()
        self.app.processEvents()

        self.assertEqual(vertical.value(), expected_vertical)
        self.assertEqual(horizontal.value(), 0)

    def test_tab_context_menu_contains_localized_rename_action(self):
        self.assertEqual(self.window.tab_bar.contextMenuPolicy(), Qt.ContextMenuPolicy.CustomContextMenu)
        self.window.apply_language("en")
        menu = self.window._create_tab_context_menu(0)
        self.assertEqual([action.text() for action in menu.actions()], ["Rename"])

    def test_general_settings_contains_persisted_language(self):
        self.window.apply_language("en")
        dialog = MoreSettingsDialog(self.window.settings, "en", self.window)
        self.assertEqual(dialog.language_combo.currentData(), "en")
        self.assertEqual(dialog.windowTitle(), "More Settings")
        self.assertEqual(dialog.brace_guides_checkbox.text(), "Closing Brace Guide")
        self.assertEqual(dialog.restore_defaults_button.text(), "Restore Default Settings")
        self.assertTrue(dialog.single_instance_checkbox.isChecked())
        dialog.single_instance_checkbox.setChecked(False)
        dialog.brace_guides_checkbox.setChecked(False)
        dialog.accept()
        self.assertFalse(self.window.settings.value("single_instance", True, type=bool))
        self.assertFalse(self.window.settings.value("brace_guides", True, type=bool))
        self.window.editor.set_brace_guides_visible(False)
        self.assertEqual(self.window.editor._brace_guide_segments(), [])
        dialog.deleteLater()

    def test_json_errors_are_localized_in_english(self):
        self.window.apply_language("en")
        message = "没有找到完整、有效的 JSON 对象或数组（附近第 2 行、第 7 列）"
        localized_message = self.window._localized_json_error(message)
        self.assertEqual(
            localized_message,
            "No complete, valid JSON object or array was found (near line 2, column 7)",
        )

    def test_format_error_is_shown_in_a_confirmable_dialog(self):
        self.window.editor.setPlainText('{"name": }')

        with patch.object(self.window, "_show_json_error") as show_error:
            self.window.format_button.click()

        show_error.assert_called_once_with(
            "第 10 个字符附近不是有效的 JSON 值", '{"name": }',
        )

    def test_json_error_location_includes_character_line_and_column(self):
        location = self.window._json_error_location(
            "第 12 个字符附近不是有效的属性名", "{\n  first: 1,\n  !bad: 2\n}",
        )

        self.assertEqual(location, (12, 2, 10))

    def test_json_error_dialog_has_confirm_button(self):
        with patch("app.QMessageBox") as dialog_type:
            self.window._show_json_error(
                "第 632 个字符附近不是有效的属性名", "\n" * 24 + "x" * 700,
            )

        dialog = dialog_type.return_value
        dialog.setWindowTitle.assert_called_once_with("JSON 格式错误")
        dialog.setText.assert_called_once_with(
            "第 632 个字符附近不是有效的属性名\n\n位置：第 632 个字符（第 25 行，第 608 列）"
        )
        dialog.addButton.assert_called_once()
        self.assertEqual(dialog.addButton.call_args.args[0], "确认")
        dialog.exec.assert_called_once()


class BraceMatchingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.editor = JsonEditor(theme="light", show_line_numbers=True)

    def tearDown(self):
        self.editor.deleteLater()
        self.app.processEvents()

    def test_pair_near_end_of_large_document_is_selected_as_characters(self):
        text = json.dumps(
            {"rows": [{"id": index, "values": [index, index + 1]} for index in range(1500)]},
            indent=2,
        )
        self.editor.setPlainText(text)
        self.app.processEvents()
        opening = text.rfind("[")
        closing = text.find("]", opening)
        text_changed = QSignalSpy(self.editor.textChanged)

        cursor = self.editor.textCursor()
        cursor.setPosition(opening + 1)
        self.editor.setTextCursor(cursor)
        self.app.processEvents()

        self.assertEqual(self.editor.highlighted_brace_positions, {opening, closing})
        self.assertEqual(
            [selection.cursor.selectedText() for selection in self.editor.brace_extra_selections],
            ["[", "]"],
        )
        self.assertEqual(text_changed.count(), 0)

    def test_malformed_section_does_not_prevent_later_pairs(self):
        text = '{"broken": [1}\n{"after": [2, 3]}'
        self.editor.setPlainText(text)
        self.app.processEvents()
        opening = text.rfind("[")
        closing = text.rfind("]")

        self.assertEqual(self.editor.brace_pairs[opening], closing)
        self.assertEqual(self.editor.brace_pairs[closing], opening)

    def test_astral_unicode_before_braces_keeps_qt_positions_aligned(self):
        text = '{"primitiveValues": {}, "unicode": "😀", "specialCharacters": {"x": 1}}'
        self.editor.setPlainText(text)
        self.app.processEvents()
        python_opening = text.index("{", text.index('"specialCharacters"'))
        python_closing = text.rindex("}") - 1
        # One emoji before this object occupies two UTF-16 code units.
        qt_opening = python_opening + 1
        qt_closing = python_closing + 1

        cursor = self.editor.textCursor()
        cursor.setPosition(qt_opening + 1)
        self.editor.setTextCursor(cursor)
        self.app.processEvents()

        self.assertEqual(self.editor.highlighted_brace_positions, {qt_opening, qt_closing})
        self.assertEqual(
            [selection.cursor.selectedText() for selection in self.editor.brace_extra_selections],
            ["{", "}"],
        )

    def test_double_click_after_container_start_selects_inner_content(self):
        text = '{"items": [1, {"name": "Alice"}]}'
        self.editor.setPlainText(text)
        self.app.processEvents()
        opening = text.index("[")

        selected = self.editor._select_container_contents_at(opening + 1)

        self.assertTrue(selected)
        self.assertEqual(self.editor.textCursor().selectedText(), '1, {"name": "Alice"}')

    def test_container_selection_uses_utf16_positions_after_emoji(self):
        text = '{"emoji": "😀", "items": [1, 2]}'
        self.editor.setPlainText(text)
        self.app.processEvents()
        python_opening = text.index("[")
        qt_opening = python_opening + 1

        self.assertTrue(self.editor._select_container_contents_at(qt_opening + 1))
        self.assertEqual(self.editor.textCursor().selectedText(), "1, 2")

    def test_double_click_selection_ignores_parentheses(self):
        text = '(value)'
        self.editor.setPlainText(text)
        self.app.processEvents()

        self.assertFalse(self.editor._select_container_contents_at(1))
        self.assertFalse(self.editor.textCursor().hasSelection())

    def test_on_or_after_container_end_selects_inner_content(self):
        for text, closing_character in (
            ('{"name": "Alice"}', "}"),
            ('["Java", "SQL"]', "]"),
        ):
            with self.subTest(closing=closing_character):
                self.editor.setPlainText(text)
                self.app.processEvents()
                closing = text.rindex(closing_character)
                for position in (closing, closing + 1):
                    self.assertTrue(self.editor._select_container_contents_at(position))
                    self.assertEqual(self.editor.textCursor().selectedText(), text[1:-1])

    def test_current_outer_closer_wins_over_adjacent_inner_closer(self):
        text = '{"outer": {"inner": 1}}'
        self.editor.setPlainText(text)
        self.app.processEvents()
        outer_closing = text.rindex("}")

        self.assertTrue(self.editor._select_container_contents_at(outer_closing))
        self.assertEqual(self.editor.textCursor().selectedText(), text[1:-1])

    def test_real_mouse_double_click_selects_array_contents(self):
        text = '{"items": [1, 2, 3]}'
        self.editor.resize(600, 240)
        self.editor.show()
        self.editor.setPlainText(text)
        opening = text.index("[")
        cursor = self.editor.textCursor()
        cursor.setPosition(opening + 1)
        self.editor.setTextCursor(cursor)
        self.app.processEvents()

        QTest.mouseDClick(
            self.editor.viewport(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            self.editor.cursorRect(cursor).center(),
        )
        self.app.processEvents()

        self.assertEqual(self.editor.textCursor().selectedText(), "1, 2, 3")

    def test_brace_guides_are_gray_by_default_and_red_when_active(self):
        text = '{\n  "items": [\n    1\n  ]\n}'
        self.editor.resize(600, 300)
        self.editor.show()
        self.editor.setPlainText(text)
        self.app.processEvents()
        opening = text.index("{")
        guides = self.editor._brace_guide_segments()
        self.assertTrue(any(color == "#64748B" for _, _, _, _, color in guides))

        cursor = self.editor.textCursor()
        cursor.setPosition(opening + 1)
        self.editor.setTextCursor(cursor)
        self.app.processEvents()
        active_guides = self.editor._brace_guide_segments()
        self.assertTrue(any(color == "#EF4444" for _, _, _, _, color in active_guides))

    def test_dark_brace_guides_use_cyan_for_inactive_pairs(self):
        self.editor.set_editor_theme("dark")
        self.editor.setPlainText('{\n  "items": [1]\n}')
        cursor = self.editor.textCursor()
        cursor.setPosition(2)
        self.editor.setTextCursor(cursor)
        self.app.processEvents()
        self.assertTrue(any(color == "#38BDF8" for _, _, _, _, color in self.editor._brace_guide_segments()))


class FindSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        os.environ["JSON_STUDIO_SETTINGS_PATH"] = os.path.join(self.directory.name, "settings.ini")
        os.environ["JSON_STUDIO_SESSION_PATH"] = os.path.join(self.directory.name, "session.json")
        settings = QSettings(os.environ["JSON_STUDIO_SETTINGS_PATH"], QSettings.Format.IniFormat)
        settings.setValue("confirm_exit", False)
        settings.sync()
        self.window = JsonWindow()
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.deleteLater()
        self.app.processEvents()
        os.environ.pop("JSON_STUDIO_SETTINGS_PATH", None)
        os.environ.pop("JSON_STUDIO_SESSION_PATH", None)
        self.directory.cleanup()

    def test_middle_click_closes_inactive_tab_without_confirmation(self):
        window = self.window
        window.editor.setPlainText("background content")
        window.add_tab()
        active_editor = window.editor
        active_editor.setPlainText("active content")
        self.app.processEvents()

        with patch.object(window, "_confirm_close_tab", return_value=False) as confirm:
            QTest.mouseClick(
                window.tab_bar, Qt.MouseButton.MiddleButton,
                pos=window.tab_bar.tabRect(0).center(),
            )
        confirm.assert_not_called()
        self.assertEqual(window.tab_bar.count(), 1)
        self.assertIs(window.editor, active_editor)
        self.assertEqual(window.editor.toPlainText(), "active content")

    def test_middle_click_clears_last_tab_without_confirmation(self):
        window = self.window
        window.editor.setPlainText("last tab content")
        with patch.object(window, "_confirm_close_tab", return_value=False) as confirm:
            QTest.mouseClick(
                window.tab_bar, Qt.MouseButton.MiddleButton,
                pos=window.tab_bar.tabRect(0).center(),
            )
        confirm.assert_not_called()
        self.assertEqual(window.tab_bar.count(), 1)
        self.assertEqual(window.editor.toPlainText(), "")

    def test_close_button_requires_confirmation_for_nonempty_tab(self):
        window = self.window
        window.editor.setPlainText("content to close")
        window.add_tab()
        self.app.processEvents()
        button = next(
            button for side in (QTabBar.ButtonPosition.LeftSide, QTabBar.ButtonPosition.RightSide)
            if (button := window.tab_bar.tabButton(0, side)) is not None
        )
        with patch.object(window, "_confirm_close_tab", return_value=False) as confirm:
            QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        confirm.assert_called_once_with(window.tab_bar.tabText(0))
        self.assertEqual(window.tab_bar.count(), 2)
        self.assertEqual(window.editor_stack.widget(0).toPlainText(), "content to close")

        with patch.object(window, "_confirm_close_tab", return_value=True) as confirm:
            QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        confirm.assert_called_once()
        self.assertEqual(window.tab_bar.count(), 1)

    def test_tabs_restore_search_without_moving_cursor_or_view(self):
        window = self.window

        def view_state(editor):
            cursor = editor.textCursor()
            return (cursor.position(), cursor.anchor(),
                    editor.verticalScrollBar().value(), editor.horizontalScrollBar().value())

        editors = []
        expected = []
        for index, query in enumerate(("alpha", "beta")):
            if index:
                window.add_tab()
            editor = window.editor
            editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
            editor.setPlainText("\n".join(f"{query} {line} " + "x" * 300 for line in range(120)))
            window.open_search()
            window.search_input.setText(query)
            window.navigate_search(1)
            cursor = editor.textCursor()
            cursor.setPosition(500)
            cursor.setPosition(490, QTextCursor.MoveMode.KeepAnchor)
            editor.setTextCursor(cursor)
            editor.verticalScrollBar().setValue(45 + index * 10)
            editor.horizontalScrollBar().setValue(100 + index * 10)
            self.app.processEvents()
            editors.append(editor)
            expected.append((view_state(editor), window.match_label.text(), list(window.search_matches)))

        with patch.object(window, "perform_search", wraps=window.perform_search) as search:
            for index in (0, 1, 0, 1):
                window.tab_bar.setCurrentIndex(index)
                self.app.processEvents()
                self.assertEqual(window.search_input.text(), ("alpha", "beta")[index])
                self.assertEqual(view_state(editors[index]), expected[index][0])
                self.assertEqual(window.match_label.text(), expected[index][1])
                self.assertEqual(window.search_matches, expected[index][2])
            search.assert_not_called()

        window.search_input.setFocus()
        QTest.keyClick(window.search_input, Qt.Key.Key_Return)
        self.assertEqual(window.search_index, 2)
        self.assertEqual(window.editor.textCursor().selectionStart(), window.search_matches[2][0])
        QTest.keyClick(window.search_input, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(window.search_index, 1)

    def test_search_visibility_and_options_belong_to_each_tab(self):
        window = self.window
        editor = window.editor
        editor.setPlainText('"Alpha"\n"Alpha"\n"alpha"')
        cursor = editor.textCursor()
        cursor.setPosition(0)
        cursor.setPosition(15, QTextCursor.MoveMode.KeepAnchor)
        editor.setTextCursor(cursor)
        window.open_search()
        window.search_input.setText("Alpha")
        window.case_button.setChecked(True)
        window.word_button.setChecked(True)
        window.search_scope.setCurrentIndex(2)
        window.selection_button.setChecked(True)
        matches = list(window.search_matches)
        candidate = window.search_candidate_selection

        window.add_tab()
        self.assertFalse(window.search_bar.isVisible())
        self.assertEqual(window.search_input.text(), "")
        self.assertFalse(window.case_button.isChecked())
        self.assertFalse(window.word_button.isChecked())
        self.assertFalse(window.selection_button.isChecked())
        self.assertEqual(window.search_scope.currentData(), "all")
        self.assertIsNone(window.search_selection_range)
        window.editor.setPlainText("beta beta")
        window.open_search()
        window.search_input.setText("beta")
        window.close_search()

        with patch("app.QMessageBox.warning") as warning:
            window.tab_bar.setCurrentIndex(0)
            warning.assert_not_called()
        self.assertTrue(window.search_bar.isVisible())
        self.assertEqual(window.search_input.text(), "Alpha")
        self.assertTrue(window.case_button.isChecked())
        self.assertTrue(window.word_button.isChecked())
        self.assertTrue(window.selection_button.isChecked())
        self.assertEqual(window.search_scope.currentData(), "values")
        self.assertEqual(window.search_selection_range, (0, 15))
        self.assertEqual(window.initial_search_selection, (0, 15))
        self.assertEqual(window.search_candidate_selection, candidate)
        self.assertEqual(window.search_matches, matches)

        window.close_search()
        window.tab_bar.setCurrentIndex(1)
        self.assertFalse(window.search_bar.isVisible())
        self.assertEqual(window.search_input.text(), "beta")
        window.open_search()
        self.assertTrue(window.search_bar.isVisible())
        self.assertEqual(window.search_matches, [(0, 4), (5, 9)])
        window.tab_bar.setCurrentIndex(0)
        self.assertFalse(window.search_bar.isVisible())

    def test_reordering_and_closing_tabs_keeps_search_with_document(self):
        window = self.window
        editors = []
        for index, query in enumerate(("alpha", "beta", "gamma")):
            if index:
                window.add_tab()
            window.editor.setPlainText(query + " " + query)
            window.open_search()
            window.search_input.setText(query)
            window.navigate_search(1)
            editors.append(window.editor)
        window.tab_bar.moveTab(2, 0)
        self.assertIs(window.editor, editors[2])
        self.assertEqual(window.search_input.text(), "gamma")
        window.tab_bar.setCurrentIndex(1)
        self.assertIs(window.editor, editors[0])
        self.assertEqual(window.search_input.text(), "alpha")
        with patch.object(window, "_confirm_close_tab", return_value=True):
            window.close_tab(0)
            self.assertIs(window.editor, editors[0])
            self.assertEqual(window.search_input.text(), "alpha")
            window.close_tab(0)
            self.assertIs(window.editor, editors[1])
            self.assertEqual(window.search_input.text(), "beta")
            self.assertEqual(window.search_index, 1)
            window.close_tab(0)
        self.assertFalse(window.search_bar.isVisible())
        self.assertEqual(window.search_input.text(), "")
        self.assertEqual(window.search_matches, [])

    def test_inactive_document_changes_refresh_matches_without_navigation(self):
        window = self.window
        editor = window.editor
        editor.setPlainText("alpha alpha")
        window.open_search()
        window.search_input.setText("alpha")
        window.add_tab()
        editor.setPlainText("prefix alpha")
        before = editor.textCursor().position()
        window.tab_bar.setCurrentIndex(0)
        self.assertEqual(window.search_matches, [(7, 12)])
        self.assertEqual(editor.textCursor().position(), before)

    def test_reenabling_selection_search_does_not_reuse_old_range(self):
        editor = self.window.editor
        editor.setPlainText("alpha\nbeta\nalpha")
        self.window.search_input.setText("alpha")
        cursor = editor.textCursor()
        cursor.setPosition(0)
        cursor.setPosition(10, QTextCursor.MoveMode.KeepAnchor)
        editor.setTextCursor(cursor)
        self.app.processEvents()

        self.window.selection_button.setChecked(True)
        self.assertEqual(self.window.search_selection_range, (0, 10))
        self.window.selection_button.setChecked(False)
        self.assertIsNone(self.window.search_selection_range)
        self.assertIsNone(self.window.search_candidate_selection)
        self.assertIsNone(self.window.initial_search_selection)
        self.assertFalse(editor.textCursor().hasSelection())

        with patch("app.QMessageBox.warning") as warning:
            self.window.selection_button.setChecked(True)
            warning.assert_called_once()
        self.assertFalse(self.window.selection_button.isChecked())

    def test_selection_highlight_matches_selection_with_configured_rules(self):
        editor = self.window.editor
        editor.setPlainText("Name name names")
        cursor = editor.textCursor()
        cursor.setPosition(0)
        cursor.setPosition(4, QTextCursor.MoveMode.KeepAnchor)
        editor.setTextCursor(cursor)

        self.window.selection_highlight_button.setChecked(True)
        self.assertEqual(len(editor.selection_highlight_extra_selections), 3)

        self.window.selection_highlight_word_action.setChecked(True)
        self.assertEqual(len(editor.selection_highlight_extra_selections), 2)

        self.window.selection_highlight_case_action.setChecked(True)
        self.assertEqual(len(editor.selection_highlight_extra_selections), 1)

        self.window.selection_highlight_button.setChecked(False)
        self.assertEqual(editor.selection_highlight_extra_selections, [])


class BookmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.settings_dir = tempfile.TemporaryDirectory()
        cls.settings_path = os.path.join(cls.settings_dir.name, "settings.ini")
        cls.session_path = os.path.join(cls.settings_dir.name, "session.json")
        os.environ["JSON_STUDIO_SETTINGS_PATH"] = cls.settings_path
        os.environ["JSON_STUDIO_SESSION_PATH"] = cls.session_path

    @classmethod
    def tearDownClass(cls):
        os.environ.pop("JSON_STUDIO_SETTINGS_PATH", None)
        os.environ.pop("JSON_STUDIO_SESSION_PATH", None)
        cls.settings_dir.cleanup()

    def setUp(self):
        self.editor = JsonEditor(theme="light", show_line_numbers=True)

    def tearDown(self):
        self.editor.deleteLater()
        self.app.processEvents()

    def _show_document(self, text, *, wrap=False, height=240):
        self.editor.resize(600, height)
        self.editor.setLineWrapMode(
            QPlainTextEdit.LineWrapMode.WidgetWidth if wrap
            else QPlainTextEdit.LineWrapMode.NoWrap
        )
        self.editor.setPlainText(text)
        self.editor.show()
        self.app.processEvents()

    def test_visible_bookmark_preserves_scroll_position(self):
        self._show_document("\n".join(f"line {i}" for i in range(200)))
        scrollbar = self.editor.verticalScrollBar()
        scrollbar.setValue(50)
        self.app.processEvents()
        for target in (52, 55, 51):
            self.editor.go_to_bookmark(target)
            self.app.processEvents()
            self.assertEqual(self.editor.textCursor().blockNumber(), target)
            self.assertEqual(scrollbar.value(), 50)

    def test_offscreen_bookmarks_align_at_top_including_document_end(self):
        self._show_document("\n".join(f"line {i}" for i in range(200)))
        original = self.editor.toPlainText()
        for target in (100, 20, 199, 0):
            self.editor.go_to_bookmark(target)
            self.app.processEvents()
            self.assertEqual(self.editor.firstVisibleBlock().blockNumber(), target)
            self.assertLessEqual(self.editor.cursorRect().top(), self.editor.document().documentMargin())
            self.assertGreaterEqual(self.editor.cursorRect().top(), 0)
        self.assertEqual(self.editor.toPlainText(), original)
        self.assertFalse(self.editor.centerOnScroll())
        # Retain the trailing space when Qt recomputes the scrollbar range.
        self.editor.go_to_bookmark(199)
        self.editor.resize(600, 320)
        self.app.processEvents()
        self.assertEqual(self.editor.firstVisibleBlock().blockNumber(), 199)
        self.assertEqual(self.editor.cursorRect().top(), 0)
        self.editor.insertPlainText(" edited")
        self.app.processEvents()
        self.assertEqual(self.editor.firstVisibleBlock().blockNumber(), 199)
        self.assertEqual(self.editor.cursorRect().top(), 0)

    def test_partially_visible_bookmark_moves_to_top(self):
        self._show_document("\n".join(f"line {i}" for i in range(200)), height=247)
        viewport = self.editor.viewport().rect()
        cursor = self.editor.cursorForPosition(QPoint(5, viewport.bottom()))
        cursor.setPosition(cursor.block().position())
        self.assertGreater(self.editor.cursorRect(cursor).bottom(), viewport.bottom())
        self.editor.go_to_bookmark(cursor.blockNumber())
        self.app.processEvents()
        self.assertEqual(self.editor.firstVisibleBlock().blockNumber(), cursor.blockNumber())
        self.assertEqual(self.editor.cursorRect().top(), 0)

    def test_wrapped_bookmarks_use_display_lines_for_top_alignment(self):
        self._show_document("\n".join(f"line {i} " + "word " * 60 for i in range(100)), wrap=True)
        for target in (50, 99, 5):
            self.editor.go_to_bookmark(target)
            self.app.processEvents()
            self.assertEqual(self.editor.firstVisibleBlock().blockNumber(), target)
            self.assertEqual(self.editor.cursorRect().top(), 0)

    def test_visible_start_of_long_wrapped_bookmark_does_not_scroll(self):
        self._show_document("first\n" + "word " * 2000, wrap=True)
        before = self.editor.verticalScrollBar().value()
        self.editor.go_to_bookmark(1)
        self.app.processEvents()
        self.assertEqual(self.editor.verticalScrollBar().value(), before)

    def test_folded_bookmarks_reveal_and_align_at_top(self):
        self._show_document("{\n" + "\n".join(f'"key{i}": {i},' for i in range(80))
                            + "\n}\n" + "\n".join(f"tail {i}" for i in range(100)))
        self.editor.toggle_fold(0)
        self.app.processEvents()
        self.assertFalse(self.editor.document().findBlockByNumber(40).isVisible())
        # Hidden lines above the target do not count as scrollbar steps.
        self.editor.go_to_bookmark(130)
        self.app.processEvents()
        self.assertEqual(self.editor.firstVisibleBlock().blockNumber(), 130)
        self.editor.go_to_bookmark(40)
        self.app.processEvents()
        self.assertTrue(self.editor.document().findBlockByNumber(40).isVisible())
        self.assertEqual(self.editor.firstVisibleBlock().blockNumber(), 40)
        self.assertEqual(self.editor.cursorRect().top(), 0)

    def test_bookmarks_toggle_and_click_in_gutter(self):
        self.editor.resize(600, 240)
        self.editor.show()
        self.editor.setPlainText("one\ntwo\nthree")
        self.app.processEvents()
        block = self.editor.document().findBlockByNumber(1)
        y = self.editor.blockBoundingGeometry(block).translated(self.editor.contentOffset()).center().y()

        QTest.mouseClick(self.editor.gutter, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(6, int(y)))
        self.assertEqual(self.editor.bookmark_block_numbers(), [1])
        self.editor.set_bookmark(1)
        self.assertEqual(self.editor.bookmark_block_numbers(), [])

        self.assertEqual(self.editor.bookmark_column_width, 18)
        marker_start = self.editor.gutter_width() - self.editor.fold_column_width
        number_left = marker_start - 4 - self.editor.fontMetrics().horizontalAdvance("2")
        self.assertGreater(number_left - 1, self.editor.bookmark_column_width)
        QTest.mouseClick(self.editor.gutter, Qt.MouseButton.LeftButton, pos=QPoint(number_left - 1, int(y)))
        self.assertEqual(self.editor.bookmark_block_numbers(), [1])
        QTest.mouseClick(self.editor.gutter, Qt.MouseButton.LeftButton, pos=QPoint(number_left + 1, int(y)))
        self.assertEqual(self.editor.bookmark_block_numbers(), [1])
        QTest.mouseClick(self.editor.gutter, Qt.MouseButton.LeftButton, pos=QPoint(number_left - 1, int(y)))
        self.assertEqual(self.editor.bookmark_block_numbers(), [])

    def test_window_shortcuts_cycle_and_update_status(self):
        settings = QSettings(self.settings_path, QSettings.Format.IniFormat)
        settings.clear()
        settings.setValue("confirm_exit", False)
        settings.sync()
        window = JsonWindow()
        window.resize(700, 400)
        window.show()
        editor = window.editor
        editor.setPlainText("one\ntwo\nthree\nfour")
        editor.setFocus()
        cursor = editor.textCursor()
        cursor.setPosition(editor.document().findBlockByNumber(1).position())
        editor.setTextCursor(cursor)
        self.app.processEvents()

        QTest.keyClick(editor, Qt.Key.Key_F2, Qt.KeyboardModifier.ControlModifier)
        cursor.setPosition(editor.document().findBlockByNumber(3).position())
        editor.setTextCursor(cursor)
        QTest.keyClick(editor, Qt.Key.Key_F2, Qt.KeyboardModifier.ControlModifier)
        self.app.processEvents()
        self.assertEqual(editor.bookmark_block_numbers(), [1, 3])
        QTest.keyClick(editor, Qt.Key.Key_F2)
        self.app.processEvents()
        self.assertEqual(editor.textCursor().blockNumber(), 1)
        self.assertIn("1 / 2", window.bookmark_label.text())
        QTest.keyClick(editor, Qt.Key.Key_F2, Qt.KeyboardModifier.ShiftModifier)
        self.app.processEvents()
        self.assertEqual(editor.textCursor().blockNumber(), 3)
        editor.setPlainText("\n".join(f"line {i}" for i in range(200)))
        for line in (50, 55, 199):
            editor.set_bookmark(line, True)
        self.app.processEvents()
        for target, expected_top, modifiers in (
            (50, 50, Qt.KeyboardModifier.NoModifier),
            (55, 50, Qt.KeyboardModifier.NoModifier),
            (199, 199, Qt.KeyboardModifier.NoModifier),
            (50, 50, Qt.KeyboardModifier.NoModifier),
            (199, 199, Qt.KeyboardModifier.ShiftModifier),
        ):
            QTest.keyClick(editor, Qt.Key.Key_F2, modifiers)
            self.app.processEvents()
            self.assertEqual(editor.textCursor().blockNumber(), target)
            self.assertEqual(editor.firstVisibleBlock().blockNumber(), expected_top)
        window.deleteLater()
        self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
