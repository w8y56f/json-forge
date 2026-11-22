import os
import tempfile
import time
import unittest
from threading import Event
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt, QThreadPool, QTimer
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app import JsonWindow
from text_diff import compare_text, utf16_offset, IgnoreMode


class TextDiffTests(unittest.TestCase):
    def test_source_can_be_reconstructed_from_aligned_rows(self):
        cases = [
            ("", ""), ("", "a\n"), ("a\n", ""),
            ("a\nb\nc\n", "a\ninsert\nb\nc\n"),
            ("a\nb\nc", "a\nc"), ("a\nb", "x\ny\nz"),
            ("same\n" * 350 + "old", "same\n" * 350 + "new"),
            ('{"中文": "😀a"}', '{"中文": "😀b"}'),
            ("a", "a\n"), ("a\r\nb", "a\nb"),
        ]
        for left, right in cases:
            with self.subTest(left=left[:30], right=right[:30]):
                result = compare_text(left, right)
                for side, original in (("left", left), ("right", right)):
                    lines = getattr(result, side + "_lines")
                    mapped = [getattr(row, side) for row in result.rows if getattr(row, side) is not None]
                    self.assertEqual(mapped, list(range(len(lines))))
                    self.assertEqual("".join(lines[n] for n in mapped), original)
                self.assertEqual(bool(result.hunks), left != right)

    def test_character_ranges_and_utf16_offsets(self):
        result = compare_text("😀甲old", "😀甲new")
        self.assertEqual(result.rows[0].left_spans, ((2, 5),))
        self.assertEqual(utf16_offset("😀甲old", 2), 3)
        self.assertEqual(utf16_offset("😀甲old", 5), 6)

    def test_long_minified_lines_use_line_highlight(self):
        result = compare_text("a" * 5000, "b" * 5000)
        self.assertTrue(result.rows[0].changed)
        self.assertEqual(result.rows[0].left_spans, ())

    def test_ignore_modes_preserve_internal_spaces_and_newlines(self):
        left, right = '  {"a": "x y"}  \n', '\t{"a": "x y"}\t\n'
        self.assertTrue(compare_text(left, right).hunks)
        self.assertFalse(compare_text(left, right, mode=IgnoreMode.EDGES).hunks)
        self.assertTrue(compare_text('"x y"', '"xy"', mode=IgnoreMode.EDGES).hunks)
        self.assertTrue(compare_text('a\n', 'a', mode=IgnoreMode.EDGES).hunks)
        self.assertFalse(compare_text(' \n  a\n\t\n', 'a', mode=IgnoreMode.EDGES_AND_EMPTY).hunks)

    def test_ignored_blank_lines_preserve_mapping_and_original_spans(self):
        for mode in IgnoreMode:
            left, right = '  😀old \n\nend\n', '\t😀new\n \n\nend'
            result = compare_text(left, right, mode=mode)
            for side, source in (("left", left), ("right", right)):
                lines = getattr(result, side + "_lines")
                mapped = [getattr(row, side) for row in result.rows if getattr(row, side) is not None]
                self.assertEqual(mapped, list(range(len(lines))))
                self.assertEqual("".join(lines[i] for i in mapped), source)
            if mode != IgnoreMode.NONE:
                self.assertEqual(result.rows[0].left_spans, ((3, 6),))
                self.assertEqual(result.rows[0].right_spans, ((2, 5),))
        for left, right in (("", "\n \t\n"), ("\n", "")):
            self.assertFalse(compare_text(left, right, mode=IgnoreMode.EDGES_AND_EMPTY).hunks)


class CompareTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.environment = patch.dict(os.environ, {
            "JSON_STUDIO_SETTINGS_PATH": os.path.join(self.directory.name, "settings.ini"),
            "JSON_STUDIO_SESSION_PATH": os.path.join(self.directory.name, "session.json"),
        })
        self.environment.start()
        self.window = JsonWindow()
        self.window.settings.setValue("confirm_exit", False)
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        QThreadPool.globalInstance().waitForDone(10000)
        self.app.processEvents()
        self.window.deleteLater()
        self.app.processEvents()
        self.environment.stop()
        self.directory.cleanup()

    def wait_result(self):
        deadline = time.monotonic() + 8
        while self.window.compare_view.busy and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertFalse(self.window.compare_view.busy)
        self.assertEqual(self.window.compare_view.error, "")

    def open_pair(self, left="a\nb\nc", right="a\nnew\nb\nc"):
        self.window.editor.setPlainText(left)
        left_editor = self.window.editor
        self.window.add_tab()
        self.window.editor.setPlainText(right)
        right_editor = self.window.editor
        for i in range(2):
            self.window.document_list.item(i).setSelected(True)
        self.window._compare_selected_documents()
        self.wait_result()
        return left_editor, right_editor

    def test_ignore_width_is_stable_and_only_title_text_activates_source(self):
        self.open_pair()
        view = self.window.compare_view
        combo = view.ignore_combo
        width = combo.width()
        for index in (1, 2, 0):
            combo.setCurrentIndex(index)
            self.wait_result()
            self.assertEqual(combo.width(), width)
        title = view.titles[0]
        QTest.mouseClick(title, Qt.MouseButton.LeftButton, pos=title.rect().center())
        self.assertTrue(self.window._compare_visible)
        QTest.mouseClick(title, Qt.MouseButton.LeftButton, pos=title.link_rect().center())
        self.assertFalse(self.window._compare_visible)

    def test_source_titles_elide_only_when_pane_is_too_narrow(self):
        self.open_pair()
        view = self.window.compare_view
        left, right = view.titles
        for name in ("c1", "c2", "被对比123"):
            right.setText(name)
            self.app.processEvents()
            self.assertEqual(right.display_text(), name)
        name = "被对比2很长很长很长很长的title还没完还没完"
        left.setText(name)
        for width, complete in ((750, True), (110, False), (750, True)):
            view.splitter.setSizes([width, 900 - width])
            self.app.processEvents()
            self.assertEqual(left.width(), view.splitter.widget(0).width())
            self.assertEqual(left.display_text() == name, complete)
            if not complete:
                self.assertTrue(left.display_text().endswith("…"))

    def test_compare_close_confirmation_is_only_on_close_button(self):
        self.open_pair()
        window = self.window
        index = window.tab_bar.currentIndex()
        with patch.object(window, "_confirm_close_tab", side_effect=(False, True)) as confirm:
            window.tab_bar.tabCloseRequested.emit(index)
            self.assertTrue(window._compare_visible)
            window.tab_bar.tabCloseRequested.emit(index)
            self.assertFalse(window._compare_visible)
            self.assertEqual(confirm.call_count, 2)

        for close_from_menu in (False, True):
            window._compare_selected_documents()
            index = window.tab_bar.currentIndex()
            with patch.object(window, "_confirm_close_tab") as confirm:
                if close_from_menu:
                    menu = window._create_tab_context_menu(index)
                    menu.actions()[0].trigger()
                else:
                    window.tab_bar.middleClickCloseRequested.emit(index)
                confirm.assert_not_called()
                self.assertEqual(window.tab_bar.count(), 2)

    def test_editable_navigation_copy_and_return_to_edit(self):
        left, right = self.open_pair()
        view = self.window.compare_view
        self.assertTrue(self.window._compare_visible)
        self.assertFalse(view.left.isReadOnly())
        self.assertFalse(view.right.isReadOnly())
        self.assertFalse(self.window.toolbar_container.isVisible())
        self.assertEqual(view.left.toPlainText(), left.toPlainText())
        self.assertEqual(view.left.changed_rows, set())
        self.assertEqual(view.right.changed_rows, {1})
        self.assertIn(1, view.left.boundaries)
        view.left.selectAll()
        view.left.copy()
        self.assertEqual(QApplication.clipboard().text(), left.toPlainText())
        cursor = QTextCursor(view.left.document().findBlockByNumber(0))
        cursor.setPosition(view.left.document().findBlockByNumber(2).position(), QTextCursor.MoveMode.KeepAnchor)
        view.left.setTextCursor(cursor)
        self.assertEqual(view.left.createMimeDataFromSelection().text(), "a\nb\n")
        before = right.toPlainText()
        self.window.apply_transform(True, "double")
        self.assertEqual(right.toPlainText(), before)
        comparison_index = self.window.tab_bar.currentIndex()
        self.window.tab_bar.setCurrentIndex(1)
        self.assertFalse(self.window._compare_visible)
        self.assertTrue(self.window.toolbar_container.isVisible())
        self.window.tab_bar.setCurrentIndex(comparison_index)
        self.assertTrue(self.window._compare_visible)
        self.window._close_current_view()
        self.assertEqual(self.window.tab_bar.count(), 2)

    def test_compare_save_targets_focused_source_and_links_follow_swap(self):
        self.window.settings.setValue("diff_sync", False)
        left, right = self.open_pair()
        view = self.window.compare_view
        self.assertFalse(view.sync_check.isChecked())
        self.assertGreater(view.left.gutter.x(), view.left.viewport().x())
        self.assertLess(view.right.gutter.x(), view.right.viewport().x())
        for pane, source in ((view.right, right), (view.left, left)):
            pane.setFocus()
            self.app.processEvents()
            with patch.object(self.window, "_save_file") as save:
                self.window.file_actions[1].trigger()
                save.assert_called_once_with(source, save_as=False)
        view.swap_button.click()
        self.wait_result()
        view.titles[0].click()
        self.assertIs(self.window.editor, right)
        self.assertFalse(self.window._compare_visible)

    def test_live_edits_keep_cursor_and_write_back_on_both_sides(self):
        left, right = self.open_pair("甲😀", "乙")
        view = self.window.compare_view
        for pane, source in ((view.left, left), (view.right, right)):
            pane.setFocus()
            pane.moveCursor(QTextCursor.MoveOperation.End)
            pane.insertPlainText("\n新行")
            position = pane.textCursor().position()
            self.assertEqual(source.toPlainText(), pane.toPlainText())
            self.wait_result()
            self.assertEqual(pane.textCursor().position(), position)
        self.assertEqual(view.result.left_lines[-1], "新行")
        self.assertEqual(view.result.right_lines[-1], "新行")

    def test_snapshot_refresh_swap_reorder_rename_and_close(self):
        left, right = self.open_pair()
        view = self.window.compare_view
        right.setPlainText("changed 😀")
        self.assertEqual(view.right.toPlainText(), "changed 😀")
        self.window.tab_bar.moveTab(0, 1)
        with patch("app.QInputDialog.getText", return_value=("Renamed", True)):
            self.window.rename_tab(self.window._document_tab_index(left))
        self.assertEqual(view.titles[0].text(), "Renamed")
        self.window._refresh_compare()
        self.wait_result()
        self.assertEqual(view.right.source, "changed 😀")
        self.window._swap_compare()
        self.wait_result()
        self.assertEqual(view.left.source, "changed 😀")
        snapshot = view.left.source
        self.window.close_tab(self.window._document_tab_index(right), confirm=False)
        self.assertFalse(view.available)
        self.assertTrue(view.left.isReadOnly())
        self.assertFalse(view.right.isReadOnly())
        self.assertEqual(view.left.source, snapshot)
        self.window._show_compare(True)
        self.assertTrue(self.window._compare_visible)

    def test_selection_context_menu_and_document_activation(self):
        self.window.add_tab()
        self.window.add_tab()
        listing = self.window.document_list
        for count in (0, 1, 2, 3):
            listing.clearSelection()
            for i in range(count):
                listing.item(i).setSelected(True)
            menu = self.window._create_document_menu()
            self.assertEqual(len(menu.actions()), {1: 2, 2: 1}.get(count, 0))
            menu.deleteLater()
        listing.clearSelection()
        for i in (0, 1):
            listing.item(i).setSelected(True)
        point = listing.visualItemRect(listing.item(0)).center()
        # Test the press itself; a full right-click would enter QMenu.exec().
        QTest.mousePress(listing.viewport(), Qt.MouseButton.RightButton, pos=point)
        self.assertEqual(len(listing.selectedItems()), 2)
        QTest.mouseRelease(listing.viewport(), Qt.MouseButton.RightButton, pos=point)
        QTest.mouseClick(listing.viewport(), Qt.MouseButton.LeftButton, pos=point)
        self.assertEqual(self.window.tab_bar.currentIndex(), 0)
        self.assertEqual(len(listing.selectedItems()), 1)
        second = listing.visualItemRect(listing.item(1)).center()
        QTest.mouseClick(listing.viewport(), Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.ControlModifier, pos=second)
        self.assertEqual(len(listing.selectedItems()), 2)
        self.assertEqual(self.window.tab_bar.currentIndex(), 0)
        third = listing.visualItemRect(listing.item(2)).center()
        QTest.mousePress(listing.viewport(), Qt.MouseButton.RightButton, pos=third)
        self.assertEqual(len(listing.selectedItems()), 1)
        self.assertEqual(listing.row(listing.selectedItems()[0]), 2)
        QTest.mouseRelease(listing.viewport(), Qt.MouseButton.RightButton, pos=third)

    def test_async_results_stale_content_and_old_request(self):
        left, right = self.open_pair()
        view = self.window.compare_view
        old_request = view.request
        self.window._refresh_compare()
        left.setPlainText("edited during calculation")
        view.receive_result(old_request, compare_text("wrong", "wrong"), "")
        self.assertTrue(view.busy)
        self.wait_result()
        self.assertNotEqual(view.left.source, "wrong")
        self.window._refresh_compare()
        self.wait_result()
        self.assertEqual(view.left.source, left.toPlainText())

    def test_unified_edit_and_alignment_padding(self):
        left, right = self.open_pair("keep\noldName\nend", "keep\nnewName\nextra\nend")
        view = self.window.compare_view
        view.view_combo.setCurrentIndex(1)
        pane = view.right
        self.assertEqual(pane.toPlainText(), "keep\noldName\nnewName\nextra\nend")
        cursor = QTextCursor(pane.document().findBlockByNumber(0))
        cursor.setPosition(pane.document().findBlockByNumber(2).position(), QTextCursor.MoveMode.KeepAnchor)
        pane.setTextCursor(cursor)
        pane.insertPlainText("forbidden")
        self.assertEqual(right.toPlainText(), "keep\nnewName\nextra\nend")
        self.assertEqual(pane.toPlainText(), "keep\noldName\nnewName\nextra\nend")
        pane.setTextCursor(QTextCursor(pane.document().findBlockByNumber(2)))
        pane.insertPlainText("😀")
        self.assertEqual(right.toPlainText(), "keep\n😀newName\nextra\nend")
        self.wait_result()
        view.view_combo.setCurrentIndex(0)
        view.align_check.setChecked(True)
        self.assertIn(None, view.left.mapping)
        view.left.selectAll()
        self.assertEqual(view.left.createMimeDataFromSelection().text(), left.toPlainText())
        view.left.setTextCursor(QTextCursor(view.left.document().findBlockByNumber(view.left.mapping.index(None))))
        view.left.insertPlainText("forbidden")
        self.assertEqual(left.toPlainText(), "keep\noldName\nend")

    def test_horizontal_scroll_sync_in_both_directions(self):
        self.open_pair("a" * 2000, "b" * 1000)
        view = self.window.compare_view
        left, right = (editor.horizontalScrollBar() for editor in (view.left, view.right))
        self.assertGreater(left.maximum(), right.maximum())
        self.assertGreater(right.maximum(), 100)
        left.setValue(100)
        self.assertEqual(right.value(), 100)
        right.setValue(80)
        self.assertEqual(left.value(), 80)
        right.setValue(right.maximum())
        self.assertEqual(left.value(), left.maximum())
        left.setValue(100)
        left.setValue(left.maximum())
        self.assertEqual(right.value(), right.maximum())
        self.assertEqual(left.value(), left.maximum())
        view.sync_check.setChecked(False)
        left.setValue(50)
        self.assertEqual(right.value(), right.maximum())
        view.sync_check.setChecked(True)
        self.assertEqual(right.value(), 50)

    def test_soft_wrap_is_visual_and_local_to_each_comparison(self):
        text = '{"value":"' + "a" * 6000 + '"}'
        left, right = self.open_pair(text, text.replace("a", "b"))
        view = self.window.compare_view
        self.assertFalse(view.wrap_check.isChecked())
        result = view.result
        view.wrap_check.setChecked(True)
        self.app.processEvents()
        self.assertIs(view.result, result)
        self.assertGreater(view.left.document().firstBlock().lineCount(), 1)
        view.left.selectAll()
        self.assertEqual(view.left.createMimeDataFromSelection().text(), text)
        self.assertFalse(view.left.horizontalScrollBar().isVisible())
        bar = view.left.verticalScrollBar()
        bar.setValue(bar.maximum() // 2)
        self.assertGreater(view.right.verticalScrollBar().value(), 0)
        view.view_combo.setCurrentIndex(view.view_combo.findData("unified"))
        self.assertFalse(view.right.horizontalScrollBar().isVisible())
        self.assertEqual(left.toPlainText(), text)
        self.window.settings.setValue("diff_wrap", True)
        view.wrap_check.setChecked(False)
        self.window.add_tab()
        listing = self.window.document_list
        listing.clearSelection()
        for i in (1, 2):
            listing.item(i).setSelected(True)
        self.window._compare_selected_documents()
        self.wait_result()
        self.assertTrue(self.window.compare_view.wrap_check.isChecked())
        self.assertFalse(view.wrap_check.isChecked())

    def test_wrap_toggle_keeps_reading_position(self):
        text = "\n".join(f"{i} " + "a" * 550 for i in range(160))
        self.open_pair(text, text.replace("0 ", "zero "))
        view = self.window.compare_view
        for unified in (False, True):
            view.view_combo.setCurrentIndex(int(unified))
            for sync in (False, True):
                view.sync_check.setChecked(sync)
                view.right.verticalScrollBar().setValue(74)
                panes = (view.right,) if unified else (view.left, view.right)
                rows = [pane.firstVisibleBlock().blockNumber() for pane in panes]
                for enabled in (True, False):
                    view.wrap_check.setChecked(enabled)
                    self.app.processEvents()
                    self.assertEqual([pane.firstVisibleBlock().blockNumber() for pane in panes], rows)

    def test_wrap_toggle_keeps_long_line_offset_and_selection(self):
        text = '{"value":"' + "x" * 12000 + '"}'
        self.open_pair(text, text)
        view = self.window.compare_view
        pane = view.right
        pane.selectAll()
        view.wrap_check.setChecked(True)
        bar = pane.verticalScrollBar()
        bar.setValue(bar.maximum())
        self.assertGreater(bar.value(), 0)
        row = bar.value()
        view.wrap_check.setChecked(False)
        self.assertGreater(pane.horizontalScrollBar().value(), 0)
        view.wrap_check.setChecked(True)
        self.app.processEvents()
        self.assertLessEqual(abs(bar.value() - row), 1)
        self.assertEqual(pane.textCursor().selectedText(), text)

    def test_vertical_scroll_sync_reaches_both_ends(self):
        common = "\n".join(f"line {i}" for i in range(200))
        extra = "\n".join(f"extra {i}" for i in range(100))
        self.open_pair(common, f"{extra}\n{common}\n{extra}")
        view = self.window.compare_view
        left, right = (editor.verticalScrollBar() for editor in (view.left, view.right))
        self.assertGreater(right.maximum(), left.maximum())
        for source, target in ((left, right), (right, left)):
            source.setValue(10)
            source.setValue(source.maximum())
            self.assertEqual(target.value(), target.maximum())
            source.setValue(source.minimum())
            self.assertEqual(target.value(), target.minimum())

    def test_scroll_navigation_theme_language_and_session(self):
        left = "\n".join(f"line {i}" for i in range(200))
        right = left.replace("line 10\n", "changed 10\n").replace("line 170\n", "changed 170\n")
        self.open_pair(left, right)
        view = self.window.compare_view
        self.assertEqual(len(view.result.hunks), 2)
        view.navigate(1)
        self.assertEqual(view.hunk_index, 1)
        self.assertEqual(view.left.textCursor().blockNumber(), 170)
        view.left.verticalScrollBar().setValue(60)
        self.assertEqual(view.right.verticalScrollBar().value(), 60)
        view.sync_check.setChecked(False)
        view.left.verticalScrollBar().setValue(80)
        self.assertEqual(view.right.verticalScrollBar().value(), 60)
        self.window.apply_theme("dark")
        self.window.apply_language("en")
        self.assertTrue(view.left.dark)
        self.assertEqual(view.swap_button.text(), "Swap sides")
        payload = self.window._session_payload()
        self.assertEqual(len(payload["tabs"]), 2)
        self.assertEqual(payload["tabs"][0]["content"], left)
        button = self.window.documents_button
        self.assertTrue(button.isVisible())
        self.assertFalse(button.isEnabled())
        self.assertFalse(self.window.document_sidebar.isVisible())
        self.assertTrue(button.isChecked())
        compare_index = self.window.tab_bar.currentIndex()
        for width in (1500, 760):
            self.window.resize(width, 650)
            self.app.processEvents()
            stack = self.window.toolbar_stack
            self.assertGreaterEqual(stack.height(), view.toolbar.layout().heightForWidth(stack.width()))
            top = self.window.workspace_splitter.y()
            view.titles[0].click()
            self.app.processEvents()
            self.assertTrue(button.isEnabled())
            self.assertTrue(self.window.document_sidebar.isVisible())
            self.assertEqual(self.window.workspace_splitter.y(), top)
            self.window.tab_bar.setCurrentIndex(compare_index)
        view.titles[0].click()
        button.click()
        self.window.tab_bar.setCurrentIndex(compare_index)
        self.window.close_tab(compare_index)
        self.assertFalse(self.window.document_sidebar.isVisible())
        self.assertFalse(self.window.settings.value("documents_visible", type=bool))

    def test_large_repeated_input_keeps_event_loop_responsive(self):
        source = "  repeated JSON value\n" * 10000
        self.open_pair("initial", "initial")
        view = self.window.compare_view
        ticks = []
        timer = QTimer()
        timer.timeout.connect(lambda: ticks.append(view.busy))
        timer.start(5)
        release = Event()

        def gated_compare(left, right, *args, **kwargs):
            if left:
                if not release.wait(3):
                    raise TimeoutError("Event loop did not release the worker")
            return compare_text(left, right, *args, **kwargs)

        # A deterministic worker barrier proves the UI processes events while
        # work is pending, independent of the machine's matching speed.
        with patch("compare_view.compare_text", side_effect=gated_compare):
            view.start(source + "old", source + "new")
            QTimer.singleShot(40, release.set)
            self.wait_result()
        timer.stop()
        self.assertIn(True, ticks)
        self.assertEqual(len(view.result.hunks), 1)

    def test_multiple_tabs_reuse_modes_and_closed_sources(self):
        first, second = self.open_pair("  same\n", "same\n")
        first_view = self.window.compare_view
        first_tab = self.window.tabs[-1]
        original_title = self.window.tab_bar.tabText(2)
        first_view.ignore_combo.setCurrentIndex(1)
        self.wait_result()
        self.assertFalse(first_view.result.hunks)
        self.window._compare_selected_documents()
        self.assertEqual(len(self.window.tabs), 3)
        self.assertIs(self.window.compare_view, first_view)
        self.window.add_tab()
        third = self.window.editor
        third.setPlainText("third")
        self.window.document_list.clearSelection()
        self.window.document_list.item(1).setSelected(True)
        self.window.document_list.item(2).setSelected(True)
        self.window._compare_selected_documents()
        self.wait_result()
        second_view = self.window.compare_view
        self.assertIsNot(first_view, second_view)
        self.assertEqual(second_view.ignore_mode, IgnoreMode.NONE)
        self.assertEqual(self.window.document_list.count(), 3)
        self.window.tab_bar.moveTab(2, 4)
        self.assertIs(self.window.tabs[4].widget, first_view)
        self.window.tab_bar.setCurrentIndex(4)
        self.assertIs(self.window.compare_view, first_view)
        first_view.right.insertPlainText("😀\n")
        self.assertEqual(second.toPlainText(), first_view.right.toPlainText())
        self.assertEqual(second_view.left.toPlainText(), second.toPlainText())
        first_view.right.historyRequested.emit(False)
        self.assertEqual(second.toPlainText(), "same\n")
        first_view.right.historyRequested.emit(True)
        self.assertIn("😀", second.toPlainText())
        second.setPlainText("updated")
        first_view.ignore_combo.setCurrentIndex(2)
        self.wait_result()
        self.assertEqual(first_view.snapshot[1], "updated")
        self.window._swap_compare(first_tab)
        self.wait_result()
        self.assertEqual(self.window.tab_bar.tabText(4), " ↔ ".join(original_title.split(" ↔ ")[::-1]))
        self.window.close_tab(self.window._document_tab_index(second), confirm=False)
        self.assertFalse(first_view.available)
        self.assertFalse(second_view.available)
        first_view.ignore_combo.setCurrentIndex(0)
        self.wait_result()
        self.assertFalse(first_view.available)
        self.window._swap_compare(first_tab)
        self.wait_result()
        self.assertEqual(first_view.snapshot[0], "  same\n")
        payload = self.window._session_payload()
        self.assertEqual([item["content"] for item in payload["tabs"]], ["  same\n", "third"])
        self.window.tab_bar.setCurrentIndex(self.window.tabs.index(first_tab))
        self.window._close_current_view()
        self.assertNotIn(first_tab, self.window.tabs)
        self.window.tab_bar.setCurrentIndex(self.window._document_tab_index(first))
        self.assertIs(self.window.editor, first)

    def test_close_pending_comparison_discards_result_and_session_restores_documents(self):
        first, second = self.open_pair()
        view = self.window.compare_view
        request = view.request
        self.window._refresh_compare()
        self.window._close_current_view()
        self.assertGreater(view.request, request)
        self.assertEqual(len(self.window.tabs), 2)
        self.window._save_session(force=True)
        restored = JsonWindow()
        try:
            self.assertEqual(len(restored.tabs), 2)
            self.assertTrue(all(tab.kind == "document" for tab in restored.tabs))
            self.assertEqual(restored.editor.toPlainText(), second.toPlainText())
        finally:
            restored.deleteLater()

    def test_unicode_separators_and_partial_copy_keep_source(self):
        source = "😀a\u2028b\u2029c\r\nlast\r"
        self.open_pair(source, "different")
        editor = self.window.compare_view.left
        # Sources are QTextDocuments and already normalize separators. Also
        # verify the independent viewer handles raw separator characters.
        self.assertEqual(editor.source, self.window.editor_stack.widget(0).toPlainText())
        editor.setReadOnly(True)
        editor.set_result(compare_text(source, "different"))
        self.assertEqual(editor.document().blockCount(), len(editor.mapping))
        editor.selectAll()
        self.assertEqual(editor.createMimeDataFromSelection().text(), source)
        cursor = QTextCursor(editor.document())
        cursor.setPosition(2)
        cursor.setPosition(7, QTextCursor.MoveMode.KeepAnchor)
        editor.setTextCursor(cursor)
        self.assertEqual(editor.createMimeDataFromSelection().text(), "a\u2028b\u2029c")


if __name__ == "__main__":
    unittest.main()
