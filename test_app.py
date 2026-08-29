import json
import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

from app import JsonEditor, JsonWindow, MoreSettingsDialog, platform_shortcut_hint


class PlatformHintTests(unittest.TestCase):
    def test_macos_uses_command_symbols(self):
        hint = platform_shortcut_hint("darwin")
        self.assertIn("⌘↵ 格式化", hint)
        self.assertIn("⌘⇧M 紧凑", hint)

    def test_windows_uses_ctrl_labels(self):
        hint = platform_shortcut_hint("win32")
        self.assertIn("Ctrl+Enter 格式化", hint)
        self.assertIn("Ctrl+Shift+M 紧凑", hint)

    def test_linux_uses_ctrl_labels(self):
        hint = platform_shortcut_hint("linux")
        self.assertIn("Ctrl+Enter 格式化", hint)
        self.assertIn("Ctrl+Shift+M 紧凑", hint)

    def test_english_windows_hint(self):
        hint = platform_shortcut_hint("win32", "en")
        self.assertIn("Ctrl+Enter Format", hint)
        self.assertIn("Ctrl+Shift+M Minify", hint)


class LanguageUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.settings_dir = tempfile.TemporaryDirectory()
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, cls.settings_dir.name)

    @classmethod
    def tearDownClass(cls):
        cls.settings_dir.cleanup()

    def setUp(self):
        settings = QSettings("LocalTools", "JSON Studio")
        settings.clear()
        settings.setValue("confirm_exit", False)
        self.window = JsonWindow()

    def tearDown(self):
        self.window.deleteLater()
        self.app.processEvents()

    def test_language_switch_updates_buttons_hover_and_status(self):
        self.window.apply_language("en")
        self.assertEqual(self.window.compact_button.text(), "Minify JSON")
        self.assertEqual(self.window.tab_bar.rename_hint, "Double-click the tab title to rename")
        self.assertEqual(self.window.path_label.text(), "Path  $")
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
        self.assertEqual(self.window.tab_bar.rename_hint, "双击标签标题可重命名")

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
        dialog.deleteLater()

    def test_json_errors_are_localized_in_english(self):
        self.window.apply_language("en")
        message = "没有找到完整、有效的 JSON 对象或数组（附近第 2 行、第 7 列）"
        localized_message = self.window._localized_json_error(message)
        self.assertEqual(
            localized_message,
            "No complete, valid JSON object or array was found (near line 2, column 7)",
        )


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


if __name__ == "__main__":
    unittest.main()
