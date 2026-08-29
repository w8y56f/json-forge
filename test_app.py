import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication

from app import (
    JsonEditor,
    JsonWindow,
    MoreSettingsDialog,
    create_app_settings,
    platform_shortcut_hint,
    session_file_path,
    settings_file_path,
)


class SettingsStorageTests(unittest.TestCase):
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

    def test_language_switch_updates_buttons_hover_and_status(self):
        self.assertEqual(self.window.theme, "light")
        self.assertTrue(self.window.editor.brace_guides_visible)
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
        self.assertEqual(dialog.brace_guides_checkbox.text(), "Closing Brace Guide")
        dialog.brace_guides_checkbox.setChecked(False)
        dialog.accept()
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
        window.deleteLater()
        self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
