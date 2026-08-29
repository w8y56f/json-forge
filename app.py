from __future__ import annotations

import sys
import platform

from PySide6.QtCore import QEvent, QRegularExpression, QSettings, Qt, QTimer
from PySide6.QtGui import (
    QAction, QActionGroup, QColor, QFont, QKeySequence, QShortcut,
    QTextCharFormat, QSyntaxHighlighter,
)
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QFrame, QHBoxLayout, QInputDialog, QLabel, QMainWindow, QMessageBox,
    QPushButton, QMenu, QSizePolicy, QStackedWidget, QStatusBar, QTabBar,
    QTabWidget, QToolButton, QToolTip, QVBoxLayout, QWidget, QPlainTextEdit,
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


class DocumentTabBar(QTabBar):
    """Tab bar whose hover hint reveals a title only when it is elided."""

    rename_hint = "双击标签标题可重命名"

    def resizeEvent(self, event):
        super().resizeEvent(event)
        callback = getattr(self, "overflow_callback", None)
        if callback is not None:
            QTimer.singleShot(0, callback)

    def tooltip_text(self, index: int) -> str:
        if index < 0 or index >= self.count():
            return ""
        title = self.tabText(index)
        available = self.tabRect(index).width() - 24  # horizontal text padding
        for position in (QTabBar.ButtonPosition.LeftSide, QTabBar.ButtonPosition.RightSide):
            button = self.tabButton(index, position)
            if button is not None and button.isVisible():
                available -= button.sizeHint().width() + 4
        rendered = self.fontMetrics().elidedText(title, self.elideMode(), max(1, available))
        return title if rendered != title else self.rename_hint

    def event(self, event):
        if event.type() == QEvent.Type.ToolTip:
            index = self.tabAt(event.pos())
            if index >= 0:
                QToolTip.showText(event.globalPos(), self.tooltip_text(index), self, self.tabRect(index))
                return True
        return super().event(event)


def setting_as_bool(settings: QSettings, key: str, default: bool) -> bool:
    value = settings.value(key, default)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off", ""}


class MoreSettingsDialog(QDialog):
    def __init__(self, settings: QSettings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("更多设置")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        general = QWidget()
        general_layout = QVBoxLayout(general)
        general_layout.setContentsMargins(18, 18, 18, 18)
        form = QFormLayout()
        self.tab_style_combo = QComboBox()
        self.tab_style_combo.addItem("实用模式", "practical")
        self.tab_style_combo.addItem("扁平模式", "flat")
        saved_tab_style = settings.value("tab_style", "practical")
        selected_index = self.tab_style_combo.findData(saved_tab_style)
        self.tab_style_combo.setCurrentIndex(max(0, selected_index))
        form.addRow("Tab样式：", self.tab_style_combo)
        general_layout.addLayout(form)
        self.confirm_exit_checkbox = QCheckBox("退出程序时提示确认")
        self.confirm_exit_checkbox.setChecked(setting_as_bool(settings, "confirm_exit", True))
        general_layout.addWidget(self.confirm_exit_checkbox)
        general_layout.addStretch()
        tabs.addTab(general, "General")
        layout.addWidget(tabs)

        buttons = QDialogButtonBox()
        save_button = buttons.addButton("保存", QDialogButtonBox.ButtonRole.AcceptRole)
        cancel_button = buttons.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole)
        save_button.clicked.connect(self.accept)
        cancel_button.clicked.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self):
        self.settings.setValue("confirm_exit", self.confirm_exit_checkbox.isChecked())
        self.settings.setValue("tab_style", self.tab_style_combo.currentData())
        self.settings.sync()
        super().accept()


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
        self.more_settings_action = QAction("更多设置", self)
        self.theme_menu.addAction(self.more_settings_action)
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
        self.tab_bar = DocumentTabBar()
        self.tab_bar.setObjectName("documentTabs")
        self.tab_bar.setMovable(True)
        self.tab_bar.setTabsClosable(True)
        self.tab_bar.setExpanding(False)
        self.tab_bar.setElideMode(Qt.TextElideMode.ElideRight)
        self.tab_bar.setUsesScrollButtons(False)
        self.tab_bar.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.tab_bar.overflow_callback = self._update_tab_navigation
        self.tab_left_button = QToolButton()
        self.tab_left_button.setObjectName("tabScrollLeft")
        self.tab_left_button.setText("<")
        self.tab_left_button.setToolTip("向左浏览标签")
        self.tab_left_button.setAccessibleName("向左浏览标签")
        self.tab_right_button = QToolButton()
        self.tab_right_button.setObjectName("tabScrollRight")
        self.tab_right_button.setText(">")
        self.tab_right_button.setToolTip("向右浏览标签")
        self.tab_right_button.setAccessibleName("向右浏览标签")
        self.tab_left_button.hide()
        self.tab_right_button.hide()
        self.add_tab_button = QToolButton()
        self.add_tab_button.setObjectName("addTab")
        self.add_tab_button.setText("+(0)")
        self.add_tab_button.setToolTip("新建标签页")
        tab_row.addWidget(self.tab_bar)
        tab_row.addWidget(self.tab_left_button)
        tab_row.addWidget(self.tab_right_button)
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
        self.more_settings_action.triggered.connect(self.show_more_settings)
        self.add_tab_button.clicked.connect(self.add_tab)
        self.tab_left_button.clicked.connect(lambda: self.navigate_tab(-1))
        self.tab_right_button.clicked.connect(lambda: self.navigate_tab(1))
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
        self.editor_stack.insertWidget(index, editor)
        self._update_tab_count_button()
        self.tab_bar.setCurrentIndex(index)
        self.editor_stack.setCurrentIndex(index)
        editor.setFocus()

    def switch_tab(self, index: int):
        if 0 <= index < self.editor_stack.count():
            self.editor_stack.setCurrentIndex(index)
            self._refresh_active_status()
            self._update_tab_navigation()

    def close_tab(self, index: int):
        if index < 0:
            return
        editor = self.editor_stack.widget(index)
        if editor is None:
            return
        if editor.toPlainText() and not self._confirm_close_tab(self.tab_bar.tabText(index)):
            return
        if self.tab_bar.count() == 1:
            editor.clear()
            self.tab_bar.setTabText(0, f"untitled-{self.next_tab_number}")
            self.next_tab_number += 1
            self._update_tab_count_button()
            return
        self.tab_bar.removeTab(index)
        self.editor_stack.removeWidget(editor)
        editor.deleteLater()
        self._update_tab_count_button()
        self.switch_tab(self.tab_bar.currentIndex())

    def _update_tab_count_button(self):
        count = self.tab_bar.count()
        self.add_tab_button.setText(f"+({count})")
        self.add_tab_button.setToolTip(f"新建标签页（当前 {count} 个）")
        QTimer.singleShot(0, self._update_tab_navigation)

    def _update_tab_navigation(self):
        if not hasattr(self, "tab_bar"):
            return
        total_width = sum(self.tab_bar.tabSizeHint(index).width() for index in range(self.tab_bar.count()))
        overflow = total_width > self.tab_bar.width()
        self.tab_left_button.setVisible(overflow)
        self.tab_right_button.setVisible(overflow)
        current = self.tab_bar.currentIndex()
        self.tab_left_button.setEnabled(current > 0)
        self.tab_right_button.setEnabled(0 <= current < self.tab_bar.count() - 1)

    def navigate_tab(self, offset: int):
        target = self.tab_bar.currentIndex() + offset
        if 0 <= target < self.tab_bar.count():
            self.tab_bar.setCurrentIndex(target)
        self._update_tab_navigation()

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

    def _confirm_close_tab(self, title: str) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("关闭标签")
        box.setText(f"确定要关闭“{title}”吗？")
        box.setInformativeText("该标签中有内容，关闭后内容将丢失。")
        cancel_button = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        close_button = box.addButton("关闭", QMessageBox.ButtonRole.DestructiveRole)
        box.setDefaultButton(cancel_button)
        box.setEscapeButton(cancel_button)
        box.exec()
        return box.clickedButton() is close_button

    def show_more_settings(self):
        dialog = MoreSettingsDialog(self.settings, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.apply_theme(self.theme)

    def show_about(self):
        box = QMessageBox(self)
        box.setWindowTitle("关于 JSON Studio")
        box.setIcon(QMessageBox.Icon.Information)
        box.setText(f"JSON Studio {APP_VERSION}")
        box.setInformativeText(f"当前 Python 版本：{platform.python_version()}\n\n本地 JSON 格式化、转换与路径定位工具")
        box.exec()

    def _confirm_exit(self) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("退出 JSON Studio")
        box.setText("确定要退出 JSON Studio 吗？")
        cancel_button = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        exit_button = box.addButton("退出", QMessageBox.ButtonRole.AcceptRole)
        box.setDefaultButton(cancel_button)
        box.setEscapeButton(cancel_button)
        box.exec()
        return box.clickedButton() is exit_button

    def closeEvent(self, event):
        if not setting_as_bool(self.settings, "confirm_exit", True) or self._confirm_exit():
            event.accept()
        else:
            event.ignore()

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
                QToolButton#addTab { font-size: 16px; padding: 2px 9px; min-width: 44px; }
                QToolButton#tabScrollLeft, QToolButton#tabScrollRight {
                    font-size: 15px; font-weight: 700; padding: 2px 7px; min-width: 20px;
                }
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
            self._apply_tab_style_override(theme)
            return
        self.setStyleSheet("""
            QMainWindow, QWidget { background: #0B1120; color: #DDE7F3; }
            QLabel#title { font-size: 23px; font-weight: 700; color: #F8FAFC; }
            QLabel#subtitle, QLabel#hint { color: #73849A; }
            QLabel#hint { font-size: 12px; padding-left: 4px; }
            QLabel#hint[error="true"] { color: #FDA4AF; }
            QFrame#dragBar, QFrame#tabRow { background: #0F192B; border: none; }
            QToolButton#addTab { font-size: 16px; padding: 2px 9px; min-width: 44px; }
            QToolButton#tabScrollLeft, QToolButton#tabScrollRight {
                font-size: 15px; font-weight: 700; padding: 2px 7px; min-width: 20px;
            }
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
        self._apply_tab_style_override(theme)

    def _apply_tab_style_override(self, theme: str):
        tab_style = self.settings.value("tab_style", "practical")
        if tab_style not in ("practical", "flat"):
            tab_style = "practical"
        self.tab_style = tab_style
        if tab_style == "flat" and theme == "light":
            tab_style_sheet = """
                QTabBar::tab {
                    background: #E2E8F0; color: #475569;
                    border: 1px solid #CBD5E1; border-bottom: none;
                    border-top-left-radius: 8px; border-top-right-radius: 8px;
                    min-width: 105px; max-width: 180px; padding: 7px 12px;
                }
                QTabBar::tab:selected { background: #FFFFFF; color: #0F172A; }
                QTabBar::tab:hover:!selected { background: #D5DEE9; }
            """
        elif tab_style == "flat":
            tab_style_sheet = """
                QTabBar::tab {
                    background: #17243A; color: #91A2B8;
                    border: 1px solid #2B3A54; border-bottom: none;
                    border-top-left-radius: 8px; border-top-right-radius: 8px;
                    min-width: 105px; max-width: 180px; padding: 7px 12px;
                }
                QTabBar::tab:selected { background: #0E1728; color: #F8FAFC; }
                QTabBar::tab:hover:!selected { background: #21314B; }
            """
        elif theme == "light":
            tab_style_sheet = """
                QTabBar::tab {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                                stop:0 #FFFFFF, stop:1 #D9E1EB);
                    color: #475569;
                    border-top: 1px solid #FFFFFF; border-left: 1px solid #FFFFFF;
                    border-right: 2px solid #9AA9BB; border-bottom: 2px solid #9AA9BB;
                    border-radius: 6px; min-width: 105px; max-width: 180px;
                    padding: 7px 12px; margin-top: 0px; margin-bottom: 3px;
                }
                QTabBar::tab:selected {
                    background: #D3DCE7; color: #0F172A;
                    border-top: 2px solid #8797AA; border-left: 2px solid #8797AA;
                    border-right: 1px solid #F8FAFC; border-bottom: 1px solid #F8FAFC;
                    padding-top: 8px; padding-bottom: 6px; margin-top: 3px; margin-bottom: 0px;
                }
                QTabBar::tab:hover:!selected { background: #F1F5F9; color: #1E293B; }
            """
        else:
            tab_style_sheet = """
                QTabBar::tab {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                                stop:0 #2A3A52, stop:1 #17243A);
                    color: #C0CCDC;
                    border-top: 1px solid #53657E; border-left: 1px solid #53657E;
                    border-right: 2px solid #070C15; border-bottom: 2px solid #070C15;
                    border-radius: 6px; min-width: 105px; max-width: 180px;
                    padding: 7px 12px; margin-top: 0px; margin-bottom: 3px;
                }
                QTabBar::tab:selected {
                    background: #0A1220; color: #F8FAFC;
                    border-top: 2px solid #05080F; border-left: 2px solid #05080F;
                    border-right: 1px solid #40506A; border-bottom: 1px solid #40506A;
                    padding-top: 8px; padding-bottom: 6px; margin-top: 3px; margin-bottom: 0px;
                }
                QTabBar::tab:hover:!selected { background: #30415A; color: #F1F5F9; }
            """
        self.setStyleSheet(self.styleSheet() + tab_style_sheet)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("JSON Studio")
    app.setStyle("Fusion")
    window = JsonWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
