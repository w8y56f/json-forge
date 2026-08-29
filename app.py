from __future__ import annotations

import sys
import platform

from PySide6.QtCore import QRegularExpression, QSettings, Qt, QTimer
from PySide6.QtGui import (
    QAction, QActionGroup, QColor, QFont, QKeySequence, QShortcut,
    QTextCharFormat, QSyntaxHighlighter,
)
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QInputDialog, QLabel, QMainWindow,
    QMessageBox, QPushButton, QMenu, QSizePolicy, QStackedWidget, QStatusBar,
    QTabBar, QToolButton, QVBoxLayout, QWidget, QPlainTextEdit,
)

from json_tools import JsonToolError, parse_json_like, path_at_position, render_json, value_stats


APP_VERSION = "v1.0.0"


class DragBar(QFrame):
    """Dedicated title row that starts the native window move operation."""

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self.window().windowHandle()
            if handle is not None and handle.startSystemMove():
                event.accept()
                return
        super().mousePressEvent(event)


class JsonHighlighter(QSyntaxHighlighter):
    def __init__(self, document, theme: str = "dark"):
        super().__init__(document)
        self.set_theme(theme)

    def set_theme(self, theme: str):
        self.rules: list[tuple[QRegularExpression, QTextCharFormat]] = []
        colors = ({
            "string": "#A7F3D0", "key": "#7DD3FC", "number": "#FDE68A",
            "bool": "#C4B5FD", "null": "#FDA4AF", "punct": "#94A3B8",
        } if theme == "dark" else {
            "string": "#047857", "key": "#0369A1", "number": "#A16207",
            "bool": "#6D28D9", "null": "#BE123C", "punct": "#64748B",
        })

        def add(pattern: str, color: str, bold: bool = False):
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(color))
            if bold:
                fmt.setFontWeight(QFont.Weight.DemiBold)
            self.rules.append((QRegularExpression(pattern), fmt))

        add(r'"(?:\\.|[^"\\])*"', colors["string"])
        add(r"'(?:\\.|[^'\\])*'", colors["string"])
        # Key rules follow string rules so their color takes precedence.
        add(r'"(?:\\.|[^"\\])*"(?=\s*:)', colors["key"], True)
        add(r"'(?:\\.|[^'\\])*'(?=\s*:)", colors["key"], True)
        add(r"\b[A-Za-z_$][\w$]*(?=\s*:)", colors["key"], True)
        add(r"(?<![\w.])-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?", colors["number"])
        add(r"\b(?:true|false)\b", colors["bool"], True)
        add(r"\bnull\b", colors["null"], True)
        add(r"[{}\[\],:]", colors["punct"])
        self.rehighlight()

    def highlightBlock(self, text: str):
        for regex, fmt in self.rules:
            iterator = regex.globalMatch(text)
            while iterator.hasNext():
                match = iterator.next()
                self.setFormat(match.capturedStart(), match.capturedLength(), fmt)


class JsonWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.next_tab_number = 1
        self.settings = QSettings("LocalTools", "JSON Studio")
        self.theme = self.settings.value("theme", "dark")
        if self.theme not in ("light", "dark"):
            self.theme = "dark"
        self.default_hint = "⌘↵ 格式化   ·   ⌘⇧M 紧凑   ·   光标移动时自动显示 JSONPath"
        self.setWindowTitle("JSON Studio")
        self.resize(1080, 720)
        self.setMinimumSize(760, 480)
        self._build_ui()
        self._connect()
        self.apply_theme(self.theme)
        self.add_tab()
        self.editor.setFocus()

    @property
    def editor(self) -> QPlainTextEdit:
        return self.editor_stack.currentWidget()

    def _get_editor_state(self, name: str, default=None):
        return getattr(self.editor, name, default)

    def _set_editor_state(self, name: str, value) -> None:
        setattr(self.editor, name, value)

    @property
    def key_style(self):
        return self._get_editor_state("json_key_style", "double")

    @key_style.setter
    def key_style(self, value):
        self._set_editor_state("json_key_style", value)

    @property
    def compact_mode(self):
        return self._get_editor_state("json_compact_mode", False)

    @compact_mode.setter
    def compact_mode(self, value):
        self._set_editor_state("json_compact_mode", value)

    @property
    def current_value(self):
        return self._get_editor_state("json_current_value")

    @current_value.setter
    def current_value(self, value):
        self._set_editor_state("json_current_value", value)

    @property
    def rendered_text(self):
        return self._get_editor_state("json_rendered_text")

    @rendered_text.setter
    def rendered_text(self, value):
        self._set_editor_state("json_rendered_text", value)

    def _button(self, text: str, primary: bool = False) -> QPushButton:
        button = QPushButton(text)
        button.setProperty("primary", primary)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        return button

    def _build_ui(self):
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 10)
        layout.setSpacing(0)

        drag_bar = DragBar()
        drag_bar.setObjectName("dragBar")
        drag_bar.setMinimumHeight(42)
        title_row = QHBoxLayout(drag_bar)
        title_row.setContentsMargins(20, 6, 18, 4)
        title = QLabel("JSON Studio")
        title.setObjectName("title")
        subtitle = QLabel("粘贴、整理和定位 JSON")
        subtitle.setObjectName("subtitle")
        title_row.addWidget(title)
        title_row.addSpacing(10)
        title_row.addWidget(subtitle)
        title_row.addStretch()
        self.settings_button = QToolButton()
        self.settings_button.setText("⚙ 设置")
        self.settings_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.settings_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.theme_menu = QMenu(self.settings_button)
        self.theme_group = QActionGroup(self)
        self.theme_group.setExclusive(True)
        self.dark_action = QAction("深色主题", self, checkable=True)
        self.light_action = QAction("浅色主题", self, checkable=True)
        self.theme_group.addAction(self.dark_action)
        self.theme_group.addAction(self.light_action)
        self.theme_menu.addSection("外观")
        self.theme_menu.addActions((self.light_action, self.dark_action))
        self.theme_menu.addSeparator()
        self.about_action = QAction("关于", self)
        self.theme_menu.addAction(self.about_action)
        self.settings_button.setMenu(self.theme_menu)
        title_row.addWidget(self.settings_button)
        layout.addWidget(drag_bar)

        tab_row_widget = QFrame()
        tab_row_widget.setObjectName("tabRow")
        tab_row = QHBoxLayout(tab_row_widget)
        tab_row.setContentsMargins(20, 4, 20, 5)
        tab_row.setSpacing(5)
        self.tab_bar = QTabBar()
        self.tab_bar.setObjectName("documentTabs")
        self.tab_bar.setMovable(True)
        self.tab_bar.setTabsClosable(True)
        self.tab_bar.setExpanding(False)
        self.tab_bar.setElideMode(Qt.TextElideMode.ElideRight)
        self.tab_bar.setUsesScrollButtons(True)
        self.tab_bar.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.add_tab_button = QToolButton()
        self.add_tab_button.setObjectName("addTab")
        self.add_tab_button.setText("+")
        self.add_tab_button.setToolTip("新建标签页")
        tab_row.addWidget(self.tab_bar)
        tab_row.addWidget(self.add_tab_button)
        tab_row.addStretch()
        layout.addWidget(tab_row_widget)

        toolbar = QFrame()
        toolbar.setObjectName("toolbar")
        tools = QHBoxLayout(toolbar)
        tools.setContentsMargins(10, 8, 10, 8)
        tools.setSpacing(7)
        self.format_button = self._button("格式化", True)
        self.compact_button = self._button("移除空格")
        self.bare_button = self._button("键名无引号")
        self.double_button = self._button('键名双引号')
        self.single_button = self._button("键名单引号")
        self.paste_button = self._button("从剪贴板粘贴")
        self.clear_button = self._button("清空")
        for button in (self.format_button, self.compact_button, self.bare_button,
                       self.double_button, self.single_button):
            tools.addWidget(button)
        tools.addStretch()
        tools.addWidget(self.paste_button)
        tools.addWidget(self.clear_button)
        toolbar_container = QWidget()
        toolbar_layout = QVBoxLayout(toolbar_container)
        toolbar_layout.setContentsMargins(20, 7, 20, 0)
        toolbar_layout.addWidget(toolbar)
        layout.addWidget(toolbar_container)

        self.editor_stack = QStackedWidget()
        self.editor_stack.setObjectName("editorStack")
        editor_container = QWidget()
        editor_layout = QVBoxLayout(editor_container)
        editor_layout.setContentsMargins(20, 12, 20, 0)
        editor_layout.addWidget(self.editor_stack)
        layout.addWidget(editor_container, 1)

        self.hint = QLabel(self.default_hint)
        self.hint.setObjectName("hint")
        hint_container = QWidget()
        hint_layout = QHBoxLayout(hint_container)
        hint_layout.setContentsMargins(20, 8, 20, 0)
        hint_layout.addWidget(self.hint)
        layout.addWidget(hint_container)
        self.setCentralWidget(root)

        status = QStatusBar()
        status.setSizeGripEnabled(False)
        status.setMinimumHeight(38)
        self.path_label = QLabel("路径  $")
        self.path_label.setObjectName("path")
        self.copy_full = QToolButton()
        self.copy_full.setText("复制 $")
        self.copy_plain = QToolButton()
        self.copy_plain.setText("复制无 $")
        self.position_label = QLabel("行 1，列 1")
        self.stats_label = QLabel("等待输入")
        status.addWidget(self.path_label, 1)
        status.addWidget(self.copy_full)
        status.addWidget(self.copy_plain)
        status.addPermanentWidget(self.stats_label)
        status.addPermanentWidget(self.position_label)
        self.setStatusBar(status)

    def _connect(self):
        self.format_button.clicked.connect(lambda: self.apply_transform(False, "double"))
        self.compact_button.clicked.connect(lambda: self.apply_transform(True, "double"))
        self.bare_button.clicked.connect(lambda: self.apply_transform(self.compact_mode, "bare"))
        self.double_button.clicked.connect(lambda: self.apply_transform(self.compact_mode, "double"))
        self.single_button.clicked.connect(lambda: self.apply_transform(self.compact_mode, "single"))
        self.paste_button.clicked.connect(self.paste)
        self.clear_button.clicked.connect(lambda: self.editor.clear())
        self.copy_full.clicked.connect(lambda: self.copy_path(True))
        self.copy_plain.clicked.connect(lambda: self.copy_path(False))
        self.dark_action.triggered.connect(lambda: self.apply_theme("dark"))
        self.light_action.triggered.connect(lambda: self.apply_theme("light"))
        self.about_action.triggered.connect(self.show_about)
        self.add_tab_button.clicked.connect(self.add_tab)
        self.tab_bar.currentChanged.connect(self.switch_tab)
        self.tab_bar.tabCloseRequested.connect(self.close_tab)
        self.tab_bar.tabBarDoubleClicked.connect(self.rename_tab)
        self.tab_bar.tabMoved.connect(self.move_tab)
        QShortcut(QKeySequence("Ctrl+Return"), self, activated=lambda: self.apply_transform(False, "double"))
        QShortcut(QKeySequence("Ctrl+Shift+M"), self, activated=lambda: self.apply_transform(True, "double"))
        QShortcut(QKeySequence("Ctrl+T"), self, activated=self.add_tab)
        QShortcut(QKeySequence("Ctrl+W"), self, activated=lambda: self.close_tab(self.tab_bar.currentIndex()))

    def _create_editor(self) -> QPlainTextEdit:
        editor = QPlainTextEdit()
        editor.setObjectName("editor")
        editor.setPlaceholderText('在这里粘贴 JSON，例如：\n日志前缀... {"user": {"name": "Alice"}} ...尾部内容')
        editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        editor.setTabStopDistance(28)
        font = QFont("JetBrains Mono")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSize(13)
        editor.setFont(font)
        editor.json_current_value = None
        editor.json_rendered_text = None
        editor.json_key_style = "double"
        editor.json_compact_mode = False
        editor.json_stats_text = "等待输入"
        editor.json_highlighter = JsonHighlighter(editor.document(), self.theme)
        editor.cursorPositionChanged.connect(lambda current=editor: self._editor_cursor_changed(current))
        editor.textChanged.connect(lambda current=editor: self._editor_text_changed(current))
        return editor

    def add_tab(self, checked: bool = False):
        editor = self._create_editor()
        title = f"untitled-{self.next_tab_number}"
        self.next_tab_number += 1
        index = self.tab_bar.addTab(title)
        self.tab_bar.setTabToolTip(index, "双击标签标题可重命名")
        self.editor_stack.insertWidget(index, editor)
        self.tab_bar.setCurrentIndex(index)
        self.editor_stack.setCurrentIndex(index)
        editor.setFocus()

    def switch_tab(self, index: int):
        if 0 <= index < self.editor_stack.count():
            self.editor_stack.setCurrentIndex(index)
            self._refresh_active_status()

    def close_tab(self, index: int):
        if index < 0:
            return
        if self.tab_bar.count() == 1:
            self.editor.clear()
            self.tab_bar.setTabText(0, f"untitled-{self.next_tab_number}")
            self.next_tab_number += 1
            return
        editor = self.editor_stack.widget(index)
        self.tab_bar.removeTab(index)
        self.editor_stack.removeWidget(editor)
        editor.deleteLater()
        self.switch_tab(self.tab_bar.currentIndex())

    def move_tab(self, old_index: int, new_index: int):
        editor = self.editor_stack.widget(old_index)
        if editor is None:
            return
        self.editor_stack.removeWidget(editor)
        self.editor_stack.insertWidget(new_index, editor)
        self.editor_stack.setCurrentIndex(new_index)

    def rename_tab(self, index: int):
        if index < 0:
            return
        current = self.tab_bar.tabText(index)
        title, accepted = QInputDialog.getText(self, "重命名标签", "标签名称：", text=current)
        title = title.strip()
        if accepted and title:
            self.tab_bar.setTabText(index, title)

    def show_about(self):
        box = QMessageBox(self)
        box.setWindowTitle("关于 JSON Studio")
        box.setIcon(QMessageBox.Icon.Information)
        box.setText(f"JSON Studio {APP_VERSION}")
        box.setInformativeText(f"当前 Python 版本：{platform.python_version()}\n\n本地 JSON 格式化、转换与路径定位工具")
        box.exec()

    def paste(self):
        text = QApplication.clipboard().text()
        if text:
            self.editor.setPlainText(text)
            self._flash("已从剪贴板粘贴")

    def apply_transform(self, compact: bool, style: str):
        text = self.editor.toPlainText()
        if not text.strip():
            self._flash("请先粘贴 JSON", error=True)
            return
        if self.current_value is not None and text == self.rendered_text:
            value = self.current_value
            output = render_json(value, compact=compact, key_style=style)
            prefix = suffix = 0
        else:
            try:
                parsed = parse_json_like(text)
            except (JsonToolError, ValueError) as exc:
                self._flash(str(exc), error=True)
                return
            if parsed.mixed and not self._confirm_mixed_mode(parsed.key_styles):
                self._flash("已取消，原始内容保持不变")
                return
            value = parsed.value
            prefix, suffix = parsed.start, len(text) - parsed.end
            output = render_json(value, compact=compact, key_style=style)
        cursor = self.editor.textCursor()
        self.current_value = value
        self.rendered_text = output
        self.editor.setPlainText(output)
        cursor.setPosition(min(cursor.position(), len(output)))
        self.editor.setTextCursor(cursor)
        self.key_style = style
        self.compact_mode = compact
        count, depth = value_stats(value)
        self.editor.json_stats_text = f"{count} 个节点  ·  深度 {depth}"
        self.stats_label.setText(self.editor.json_stats_text)
        removed = prefix + suffix
        message = "已压缩 JSON" if compact else "已格式化 JSON"
        if removed:
            message += f"，并移除首尾 {removed} 个字符"
        if style == "bare":
            message += "；特殊键名保留引号"
        self._flash(message)
        self.update_path()

    def _confirm_mixed_mode(self, styles) -> bool:
        names = {"double": "双引号", "single": "单引号", "bare": "无引号"}
        used = "、".join(names[style] for style in ("double", "single", "bare") if style in styles)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("检测到混合模式")
        box.setText("给定的 JSON 字符串是混合模式")
        box.setInformativeText(
            f"检测到属性名同时使用了：{used}。\n\n"
            "选择“净化并继续”会先修正为双引号标准 JSON，再执行刚才的操作。"
        )
        cancel_button = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        continue_button = box.addButton("净化并继续", QMessageBox.ButtonRole.AcceptRole)
        box.setDefaultButton(continue_button)
        box.setEscapeButton(cancel_button)
        box.exec()
        return box.clickedButton() is continue_button

    def _editor_text_changed(self, editor: QPlainTextEdit):
        text = editor.toPlainText()
        if not text:
            editor.json_current_value = None
            editor.json_rendered_text = None
            editor.json_stats_text = "等待输入"
        elif editor.json_rendered_text is not None and text != editor.json_rendered_text:
            editor.json_current_value = None
            editor.json_rendered_text = None
            editor.json_stats_text = "内容已修改"
        if editor is self.editor:
            self.stats_label.setText(editor.json_stats_text)
            self.update_path()

    def _editor_cursor_changed(self, editor: QPlainTextEdit):
        if editor is self.editor:
            self.update_path()

    def _refresh_active_status(self):
        editor = self.editor
        if editor is None:
            return
        self.stats_label.setText(editor.json_stats_text)
        self.update_path()

    def update_path(self):
        cursor = self.editor.textCursor()
        text = self.editor.toPlainText()
        path = path_at_position(text, cursor.position(), True) if text else "$"
        self.path_label.setText(f"路径  {path}")
        self.path_label.setToolTip(path)
        self.position_label.setText(f"行 {cursor.blockNumber() + 1}，列 {cursor.positionInBlock() + 1}")

    def copy_path(self, include_root: bool):
        text = self.editor.toPlainText()
        path = path_at_position(text, self.editor.textCursor().position(), include_root)
        QApplication.clipboard().setText(path)
        self._flash(f"已复制：{path}")

    def _flash(self, message: str, error: bool = False):
        self.hint.setText(message)
        self.hint.setProperty("error", error)
        self.hint.style().unpolish(self.hint)
        self.hint.style().polish(self.hint)

        def restore_hint():
            self.hint.setText(self.default_hint)
            self.hint.setProperty("error", False)
            self.hint.style().unpolish(self.hint)
            self.hint.style().polish(self.hint)

        QTimer.singleShot(3500, restore_hint)

    def apply_theme(self, theme: str):
        self.theme = theme
        self.settings.setValue("theme", theme)
        self.dark_action.setChecked(theme == "dark")
        self.light_action.setChecked(theme == "light")
        if hasattr(self, "editor_stack"):
            for index in range(self.editor_stack.count()):
                self.editor_stack.widget(index).json_highlighter.set_theme(theme)
        if theme == "light":
            self.setStyleSheet("""
                QMainWindow, QWidget { background: #F4F7FB; color: #1E293B; }
                QLabel#title { font-size: 23px; font-weight: 700; color: #0F172A; }
                QLabel#subtitle, QLabel#hint { color: #64748B; }
                QLabel#hint { font-size: 12px; padding-left: 4px; }
                QLabel#hint[error="true"] { color: #BE123C; }
                QFrame#dragBar, QFrame#tabRow { background: #E8EEF6; border: none; }
                QTabBar::tab {
                    background: #E2E8F0; color: #475569; border: 1px solid #CBD5E1;
                    border-bottom: none; border-top-left-radius: 8px; border-top-right-radius: 8px;
                    min-width: 105px; max-width: 180px; padding: 7px 12px;
                }
                QTabBar::tab:selected { background: #FFFFFF; color: #0F172A; }
                QTabBar::tab:hover:!selected { background: #D5DEE9; }
                QToolButton#addTab { font-size: 18px; padding: 2px 9px; }
                QFrame#toolbar { background: #FFFFFF; border: 1px solid #CBD5E1; border-radius: 10px; }
                QPushButton, QToolButton {
                    background: #F8FAFC; color: #334155; border: 1px solid #CBD5E1;
                    border-radius: 7px; padding: 7px 11px; font-weight: 600;
                }
                QPushButton:hover, QToolButton:hover { background: #E2E8F0; border-color: #94A3B8; }
                QPushButton:pressed, QToolButton:pressed { background: #CCFBF1; }
                QPushButton[primary="true"] { background: #0D9488; color: white; border-color: #0F766E; }
                QPushButton[primary="true"]:hover { background: #0F766E; }
                QPlainTextEdit#editor {
                    background: #FFFFFF; color: #1E293B; border: 1px solid #CBD5E1;
                    border-radius: 11px; padding: 14px; selection-background-color: #99F6E4;
                    selection-color: #134E4A;
                }
                QPlainTextEdit#editor:focus { border-color: #0D9488; }
                QStatusBar { background: #FFFFFF; border-top: 1px solid #CBD5E1; color: #64748B; }
                QStatusBar QLabel { background: transparent; padding: 3px 8px; color: #64748B; }
                QStatusBar QLabel#path { color: #0F766E; font-family: monospace; font-weight: 600; }
                QStatusBar QToolButton { padding: 3px 8px; margin: 2px; }
                QMenu { background: #FFFFFF; color: #1E293B; border: 1px solid #CBD5E1; padding: 5px; }
                QMenu::item { padding: 7px 24px; border-radius: 5px; }
                QMenu::item:selected { background: #CCFBF1; }
                QScrollBar:vertical { background: #F1F5F9; width: 11px; }
                QScrollBar::handle:vertical { background: #CBD5E1; border-radius: 5px; min-height: 24px; }
            """)
            return
        self.setStyleSheet("""
            QMainWindow, QWidget { background: #0B1120; color: #DDE7F3; }
            QLabel#title { font-size: 23px; font-weight: 700; color: #F8FAFC; }
            QLabel#subtitle, QLabel#hint { color: #73849A; }
            QLabel#hint { font-size: 12px; padding-left: 4px; }
            QLabel#hint[error="true"] { color: #FDA4AF; }
            QFrame#dragBar, QFrame#tabRow { background: #0F192B; border: none; }
            QTabBar::tab {
                background: #17243A; color: #91A2B8; border: 1px solid #2B3A54;
                border-bottom: none; border-top-left-radius: 8px; border-top-right-radius: 8px;
                min-width: 105px; max-width: 180px; padding: 7px 12px;
            }
            QTabBar::tab:selected { background: #0E1728; color: #F8FAFC; }
            QTabBar::tab:hover:!selected { background: #21314B; }
            QToolButton#addTab { font-size: 18px; padding: 2px 9px; }
            QFrame#toolbar { background: #111B2E; border: 1px solid #243149; border-radius: 10px; }
            QPushButton, QToolButton {
                background: #17243A; color: #C9D5E5; border: 1px solid #2B3A54;
                border-radius: 7px; padding: 7px 11px; font-weight: 600;
            }
            QPushButton:hover, QToolButton:hover { background: #21314B; border-color: #3D5272; }
            QPushButton:pressed, QToolButton:pressed { background: #0F766E; }
            QPushButton[primary="true"] { background: #0D9488; color: white; border-color: #14B8A6; }
            QPushButton[primary="true"]:hover { background: #0F766E; }
            QPlainTextEdit#editor {
                background: #0E1728; color: #D7E0EC; border: 1px solid #26344C;
                border-radius: 11px; padding: 14px; selection-background-color: #155E75;
            }
            QPlainTextEdit#editor:focus { border-color: #2DD4BF; }
            QStatusBar { background: #101A2B; border-top: 1px solid #26344C; color: #91A2B8; }
            QStatusBar QLabel { background: transparent; padding: 3px 8px; color: #91A2B8; }
            QStatusBar QLabel#path { color: #5EEAD4; font-family: monospace; font-weight: 600; }
            QStatusBar QToolButton { padding: 3px 8px; margin: 2px; }
            QMenu { background: #111B2E; color: #DDE7F3; border: 1px solid #2B3A54; padding: 5px; }
            QMenu::item { padding: 7px 24px; border-radius: 5px; }
            QMenu::item:selected { background: #21314B; }
            QScrollBar:vertical { background: #0E1728; width: 11px; }
            QScrollBar::handle:vertical { background: #334155; border-radius: 5px; min-height: 24px; }
        """)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("JSON Studio")
    app.setStyle("Fusion")
    window = JsonWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
