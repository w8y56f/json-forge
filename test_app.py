import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QPlainTextEdit

from app import (
    JsonEditor,
    JsonWindow,
    MoreSettingsDialog,
    acquire_instance_lock,
    application_icon_path,
    create_app_settings,
    default_settings_file_path,
    default_settings_values,
    instance_lock_path,
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
        self.assertIn("Ctrl/Cmd+F2 Toggle Bookmark", hint)


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
        self.assertIn("preserving comments", self.window.format_button.toolTip())
        self.assertEqual(self.window.wrap_button.text(), "Wrap")
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
        self.assertEqual(self.window.wrap_button.text(), "换行")
        self.assertEqual(self.window.tab_bar.rename_hint, "双击标签标题可重命名")

    def test_line_wrap_button_precedes_fold_and_persists_for_all_tabs(self):
        toolbar_layout = self.window.wrap_button.parentWidget().layout()
        self.assertEqual(
            toolbar_layout.indexOf(self.window.wrap_button) + 1,
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
            toolbar_layout.indexOf(self.window.single_button) + 1,
            toolbar_layout.indexOf(self.window.key_value_double_button),
        )
        self.assertEqual(
            toolbar_layout.indexOf(self.window.key_value_double_button) + 1,
            toolbar_layout.indexOf(self.window.key_value_single_button),
        )

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

    def test_minify_confirms_before_normalizing_json5_source(self):
        source = "{\n// note\nname:'Alice',\n}"
        self.window.editor.setPlainText(source)

        with patch.object(self.window, "_confirm_json5_minify", return_value=False) as confirm:
            self.window.apply_transform(True, "double")

        confirm.assert_called_once()
        self.assertEqual(self.window.editor.toPlainText(), source)

    def test_minify_confirms_after_json5_quote_rewrite(self):
        self.window.editor.setPlainText("{\n// note\nname:'Alice',\n}")
        self.window.key_value_double_button.click()
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

        self.window.key_value_double_button.click()
        self.assertEqual(
            self.window.editor.toPlainText(),
            '{ name: "Alice", city : "Taipei", count: 2, }',
        )

        self.window.double_button.click()
        self.assertEqual(
            self.window.editor.toPlainText(),
            '{ "name": "Alice", "city" : "Taipei", "count": 2, }',
        )

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
