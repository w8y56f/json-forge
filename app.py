from __future__ import annotations

import sys
import platform
import re
import os
import json
import tempfile
from pathlib import Path

from PySide6.QtCore import (
    QEvent, QLockFile, QPoint, QRect, QRegularExpression, QSettings, QSize,
    Qt, QTimer, Signal,
)
from PySide6.QtGui import (
    QAction, QActionGroup, QColor, QFont, QIcon, QKeySequence, QPainter, QPen,
    QPixmap, QPolygon, QShortcut, QTextBlockUserData, QTextCharFormat, QTextCursor,
    QSyntaxHighlighter,
)
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPushButton, QMenu, QSizePolicy, QStackedWidget, QStatusBar,
    QStyle, QTabBar, QTabWidget, QTextEdit, QToolButton, QToolTip, QVBoxLayout,
    QWidget, QPlainTextEdit,
)

from json_tools import (
    JsonToolError, parse_json_like, path_at_position, render_json,
    searchable_spans, value_stats,
)


APP_VERSION = "v1.0.0"
APP_NAME = "JSON Forge"
SETTINGS_ORGANIZATION = "LocalTools"
SETTINGS_APPLICATION = APP_NAME
LEGACY_SETTINGS_APPLICATION = "JSON Studio"


def settings_file_path() -> Path:
    """Return the portable settings path next to the application source."""
    override = os.environ.get("JSON_STUDIO_SETTINGS_PATH")
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).resolve().parent / "config" / "settings.ini"


def default_settings_file_path() -> Path:
    """Return the checked-in defaults file path."""
    override = os.environ.get("JSON_STUDIO_DEFAULT_SETTINGS_PATH")
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).resolve().parent / "config" / "settings.default.ini"


_DEFAULT_SETTINGS_CACHE: dict[str, object] | None = None


def default_settings_values(force_reload: bool = False) -> dict[str, object]:
    """Read default values from settings.default.ini, cached per process."""
    global _DEFAULT_SETTINGS_CACHE
    if _DEFAULT_SETTINGS_CACHE is not None and not force_reload:
        return dict(_DEFAULT_SETTINGS_CACHE)
    values: dict[str, object] = {}
    path = default_settings_file_path()
    try:
        if path.is_file():
            defaults = QSettings(str(path), QSettings.Format.IniFormat)
            defaults.setFallbacksEnabled(False)
            defaults.sync()
            if defaults.status() == QSettings.Status.NoError:
                values = {key: defaults.value(key) for key in defaults.allKeys()}
    except (OSError, RuntimeError):
        values = {}
    _DEFAULT_SETTINGS_CACHE = values
    return dict(values)


def default_setting(key: str, fallback=None):
    """Return a setting's configured default, with a code-level fallback."""
    return default_settings_values().get(key, fallback)


def reset_settings_to_defaults(settings: QSettings) -> bool:
    """Replace all current settings with settings.default.ini values."""
    defaults = default_settings_values(force_reload=True)
    if not defaults:
        return False
    settings.clear()
    for key, value in defaults.items():
        settings.setValue(key, value)
    settings.sync()
    return settings.status() == QSettings.Status.NoError


def session_file_path() -> Path:
    """Return the portable last-session snapshot path."""
    override = os.environ.get("JSON_STUDIO_SESSION_PATH")
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).resolve().parent / "cache" / "session.json"


def instance_lock_path() -> Path:
    """Return the lock path used to enforce the single-instance setting."""
    override = os.environ.get("JSON_STUDIO_INSTANCE_LOCK_PATH")
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).resolve().parent / "cache" / "json-forge.lock"


def acquire_instance_lock() -> tuple[QLockFile | None, bool]:
    """Try to acquire the process lock.

    Returns ``(lock, True)`` when another instance owns the lock. If the lock
    cannot be created because the directory is not writable, returns
    ``(None, False)`` so the application can still start.
    """
    path = instance_lock_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = QLockFile(str(path))
        if lock.tryLock(0):
            return lock, False
        if lock.error() == QLockFile.LockError.LockFailedError:
            return None, True
    except (OSError, RuntimeError):
        pass
    return None, False


def _legacy_settings() -> QSettings:
    """Open the previous native settings store without global fallbacks."""
    settings = QSettings(SETTINGS_ORGANIZATION, LEGACY_SETTINGS_APPLICATION)
    settings.setFallbacksEnabled(False)
    return settings


def create_app_settings() -> QSettings:
    """Create portable INI settings, migrating the previous store once.

    If the application directory is read-only (for example, a protected
    installation directory), the native QSettings store remains a safe
    fallback so preferences can still be saved.
    """
    path = settings_file_path()
    was_missing = not path.exists()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if not os.access(path, os.R_OK | os.W_OK):
                raise OSError(f"settings file is not writable: {path}")
        elif not os.access(path.parent, os.W_OK):
            raise OSError(f"settings directory is not writable: {path.parent}")

        settings = QSettings(str(path), QSettings.Format.IniFormat)
        if was_missing:
            for key, value in default_settings_values().items():
                settings.setValue(key, value)
            legacy = _legacy_settings()
            for key in legacy.allKeys():
                settings.setValue(key, legacy.value(key))
        settings.sync()
        if settings.status() != QSettings.Status.NoError:
            raise OSError(f"unable to write settings: {path}")
        return settings
    except (OSError, RuntimeError):
        return QSettings(SETTINGS_ORGANIZATION, SETTINGS_APPLICATION)


def localized(language: str, chinese: str, english: str, **values) -> str:
    """Return and format a small UI string in the selected language."""
    template = english if language == "en" else chinese
    return template.format(**values) if values else template


def platform_shortcut_hint(platform_name: str | None = None, language: str = "zh_CN") -> str:
    """Return status-bar shortcut labels using the current platform's notation."""
    platform_name = platform_name or sys.platform
    if platform_name == "darwin":
        format_shortcut = "⌘↵"
        compact_shortcut = "⌘⇧M"
    else:
        format_shortcut = "Ctrl+Enter"
        compact_shortcut = "Ctrl+Shift+M"
    return localized(
        language,
        f"{format_shortcut} 格式化   ·   {compact_shortcut} 紧凑"
        "   ·   光标移动时自动显示 JSONPath",
        f"{format_shortcut} Format   ·   {compact_shortcut} Minify"
        "   ·   Move the cursor to show JSONPath",
    )


def search_option_icon(kind: str) -> QIcon:
    """Create compact, theme-neutral icons for search option buttons."""
    pixmap = QPixmap(22, 22)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor("#64748B"), 1.4)
    painter.setPen(pen)
    if kind == "case":
        font = QFont("Sans Serif", 8)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "Aa")
    elif kind == "word":
        font = QFont("Sans Serif", 7)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(pixmap.rect().adjusted(2, 0, -2, -2), Qt.AlignmentFlag.AlignCenter, "ab")
        painter.drawLine(3, 17, 19, 17)
        painter.drawLine(3, 14, 3, 19)
        painter.drawLine(19, 14, 19, 19)
    else:
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawRect(4, 5, 14, 12)
        painter.drawLine(7, 9, 15, 9)
        painter.drawLine(7, 13, 13, 13)
    painter.end()
    return QIcon(pixmap)


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
    def __init__(self, settings: QSettings, language: str, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.language = language
        tr = lambda zh, en: localized(language, zh, en)
        self.setWindowTitle(tr("更多设置", "More Settings"))
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        general = QWidget()
        general_layout = QVBoxLayout(general)
        general_layout.setContentsMargins(18, 18, 18, 18)
        form = QFormLayout()
        self.tab_style_combo = QComboBox()
        self.tab_style_combo.addItem(tr("实用模式", "Practical"), "practical")
        self.tab_style_combo.addItem(tr("扁平模式", "Flat"), "flat")
        saved_tab_style = settings.value("tab_style", default_setting("tab_style", "practical"))
        selected_index = self.tab_style_combo.findData(saved_tab_style)
        self.tab_style_combo.setCurrentIndex(max(0, selected_index))
        form.addRow(tr("Tab样式：", "Tab style:"), self.tab_style_combo)
        self.language_combo = QComboBox()
        self.language_combo.addItem("中文", "zh_CN")
        self.language_combo.addItem("English", "en")
        saved_language = settings.value("language", default_setting("language", "zh_CN"))
        language_index = self.language_combo.findData(saved_language)
        self.language_combo.setCurrentIndex(max(0, language_index))
        form.addRow(tr("语言：", "Language:"), self.language_combo)
        general_layout.addLayout(form)
        self.line_numbers_checkbox = QCheckBox(tr("显示行号", "Show line numbers"))
        self.line_numbers_checkbox.setChecked(
            setting_as_bool(settings, "show_line_numbers", default_setting("show_line_numbers", True))
        )
        general_layout.addWidget(self.line_numbers_checkbox)
        self.confirm_exit_checkbox = QCheckBox(tr("退出程序时提示确认", "Confirm before exiting"))
        self.confirm_exit_checkbox.setChecked(
            setting_as_bool(settings, "confirm_exit", default_setting("confirm_exit", True))
        )
        general_layout.addWidget(self.confirm_exit_checkbox)
        self.single_instance_checkbox = QCheckBox(
            tr("禁止多实例（仅允许打开一个窗口）", "Prevent multiple instances (allow one window)")
        )
        self.single_instance_checkbox.setChecked(
            setting_as_bool(settings, "single_instance", default_setting("single_instance", True))
        )
        general_layout.addWidget(self.single_instance_checkbox)
        self.brace_guides_checkbox = QCheckBox(tr("收尾连接虚线", "Closing Brace Guide"))
        self.brace_guides_checkbox.setChecked(
            setting_as_bool(settings, "brace_guides", default_setting("brace_guides", True))
        )
        general_layout.addWidget(self.brace_guides_checkbox)
        self.restore_defaults_button = QPushButton(
            tr("恢复默认配置", "Restore Default Settings")
        )
        self.restore_defaults_button.clicked.connect(self.restore_defaults)
        general_layout.addWidget(self.restore_defaults_button)
        general_layout.addStretch()
        tabs.addTab(general, "General")
        layout.addWidget(tabs)

        buttons = QDialogButtonBox()
        save_button = buttons.addButton(tr("保存", "Save"), QDialogButtonBox.ButtonRole.AcceptRole)
        cancel_button = buttons.addButton(tr("取消", "Cancel"), QDialogButtonBox.ButtonRole.RejectRole)
        save_button.clicked.connect(self.accept)
        cancel_button.clicked.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self):
        self.settings.setValue("confirm_exit", self.confirm_exit_checkbox.isChecked())
        self.settings.setValue("show_line_numbers", self.line_numbers_checkbox.isChecked())
        self.settings.setValue("tab_style", self.tab_style_combo.currentData())
        self.settings.setValue("language", self.language_combo.currentData())
        self.settings.setValue("single_instance", self.single_instance_checkbox.isChecked())
        self.settings.setValue("brace_guides", self.brace_guides_checkbox.isChecked())
        self.settings.sync()
        super().accept()

    def _load_controls_from_settings(self):
        tab_style = self.settings.value("tab_style", default_setting("tab_style", "practical"))
        tab_style_index = self.tab_style_combo.findData(tab_style)
        self.tab_style_combo.setCurrentIndex(max(0, tab_style_index))
        language = self.settings.value("language", default_setting("language", "zh_CN"))
        language_index = self.language_combo.findData(language)
        self.language_combo.setCurrentIndex(max(0, language_index))
        self.line_numbers_checkbox.setChecked(
            setting_as_bool(
                self.settings,
                "show_line_numbers",
                default_setting("show_line_numbers", True),
            )
        )
        self.confirm_exit_checkbox.setChecked(
            setting_as_bool(
                self.settings,
                "confirm_exit",
                default_setting("confirm_exit", True),
            )
        )
        self.single_instance_checkbox.setChecked(
            setting_as_bool(
                self.settings,
                "single_instance",
                default_setting("single_instance", True),
            )
        )
        self.brace_guides_checkbox.setChecked(
            setting_as_bool(
                self.settings,
                "brace_guides",
                default_setting("brace_guides", True),
            )
        )

    def restore_defaults(self):
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(localized(self.language, "恢复默认配置", "Restore Default Settings"))
        box.setText(localized(
            self.language,
            "确定要恢复默认配置吗？",
            "Restore all settings to their defaults?",
        ))
        box.setInformativeText(localized(
            self.language,
            "当前设置将被 settings.default.ini 中的值覆盖。",
            "Current settings will be replaced with values from settings.default.ini.",
        ))
        cancel_button = box.addButton(
            localized(self.language, "取消", "Cancel"),
            QMessageBox.ButtonRole.RejectRole,
        )
        restore_button = box.addButton(
            localized(self.language, "确认恢复", "Restore"),
            QMessageBox.ButtonRole.AcceptRole,
        )
        box.setDefaultButton(cancel_button)
        box.setEscapeButton(cancel_button)
        box.exec()
        if box.clickedButton() is not restore_button:
            return

        if not reset_settings_to_defaults(self.settings):
            QMessageBox.warning(
                self,
                localized(self.language, "恢复失败", "Restore Failed"),
                localized(
                    self.language,
                    "未找到有效的 config/settings.default.ini。",
                    "A valid config/settings.default.ini was not found.",
                ),
            )
            return
        self._load_controls_from_settings()


class JsonHighlighter(QSyntaxHighlighter):
    def __init__(self, document, theme: str = "light"):
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
        # Keep keys colored but at the editor's normal font weight. On macOS,
        # mixing a bold Latin monospace font with a bold CJK fallback makes Qt
        # shape otherwise identical leading spaces at different widths.
        add(r'"(?:\\.|[^"\\])*"(?=\s*:)', colors["key"])
        add(r"'(?:\\.|[^'\\])*'(?=\s*:)", colors["key"])
        add(r"\b[A-Za-z_$][\w$]*(?=\s*:)", colors["key"])
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


class EditorGutter(QWidget):
    def __init__(self, editor: "JsonEditor"):
        super().__init__(editor)
        self.editor = editor
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def sizeHint(self):
        return QSize(self.editor.gutter_width(), 0)

    def paintEvent(self, event):
        self.editor.paint_gutter(event)

    def mousePressEvent(self, event):
        self.editor.gutter_mouse_press(event)


class BookmarkData(QTextBlockUserData):
    """Marker attached to a document block so bookmarks follow line edits."""


class JsonEditor(QPlainTextEdit):
    """Plain-text editor with optional line numbers and JSON node folding."""

    foldStateChanged = Signal(bool)
    bookmarksChanged = Signal()
    fold_column_width = 20
    bookmark_column_width = 18

    def __init__(self, theme: str, show_line_numbers: bool, parent=None):
        super().__init__(parent)
        self.show_line_numbers = show_line_numbers
        self.editor_theme = theme
        self.brace_guides_visible = True
        self.fold_regions: dict[int, int] = {}
        self.collapsed_blocks: set[int] = set()
        self.brace_pairs: dict[int, int] = {}
        self.highlighted_brace_positions: set[int] = set()
        self.brace_extra_selections: list[QTextEdit.ExtraSelection] = []
        self.search_extra_selections: list[QTextEdit.ExtraSelection] = []
        self.gutter = EditorGutter(self)
        self.blockCountChanged.connect(self._update_gutter_width)
        self.updateRequest.connect(self._update_gutter_area)
        self.textChanged.connect(self._rebuild_fold_regions)
        self.cursorPositionChanged.connect(self._update_brace_highlight)
        self._update_gutter_width()

    def set_line_numbers_visible(self, visible: bool):
        self.show_line_numbers = visible
        self._update_gutter_width()
        self.gutter.update()

    def set_editor_theme(self, theme: str):
        self.editor_theme = theme
        self.gutter.update()
        self._update_brace_highlight()

    def set_brace_guides_visible(self, visible: bool):
        self.brace_guides_visible = visible
        self.viewport().update()

    def set_bookmark(self, block_number: int | None = None, marked: bool | None = None):
        if block_number is None:
            block_number = self.textCursor().blockNumber()
        block = self.document().findBlockByNumber(block_number)
        if not block.isValid():
            return False
        currently_marked = isinstance(block.userData(), BookmarkData)
        new_value = not currently_marked if marked is None else marked
        if new_value == currently_marked:
            return currently_marked
        block.setUserData(BookmarkData() if new_value else None)
        self.gutter.update()
        self.bookmarksChanged.emit()
        return new_value

    def bookmark_block_numbers(self) -> list[int]:
        numbers = []
        block = self.document().firstBlock()
        while block.isValid():
            if isinstance(block.userData(), BookmarkData):
                numbers.append(block.blockNumber())
            block = block.next()
        return numbers

    def clear_bookmarks(self):
        changed = False
        block = self.document().firstBlock()
        while block.isValid():
            if isinstance(block.userData(), BookmarkData):
                block.setUserData(None)
                changed = True
            block = block.next()
        if changed:
            self.gutter.update()
            self.bookmarksChanged.emit()

    def gutter_width(self) -> int:
        number_width = 0
        if self.show_line_numbers:
            digits = max(2, len(str(max(1, self.blockCount()))))
            number_width = self.fontMetrics().horizontalAdvance("9") * digits + 12
        return self.bookmark_column_width + number_width + self.fold_column_width

    def _update_gutter_width(self, _=None):
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)

    def _update_gutter_area(self, rect, dy):
        if dy:
            self.gutter.scroll(0, dy)
        else:
            self.gutter.update(0, rect.y(), self.gutter.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_gutter_width()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        contents = self.contentsRect()
        self.gutter.setGeometry(QRect(contents.left(), contents.top(), self.gutter_width(), contents.height()))

    def _brace_guide_segments(self):
        """Return visible cross-line brace guides as (x1, y1, x2, y2, color)."""
        if not self.brace_guides_visible:
            return []
        guides = []
        guide_color = "#64748B" if self.editor_theme == "light" else "#38BDF8"
        active_color = "#EF4444"
        for opening, closing in sorted(
            (opening, closing)
            for opening, closing in self.brace_pairs.items()
            if opening < closing
        ):
            opening_block = self.document().findBlock(opening)
            closing_block = self.document().findBlock(closing)
            if (
                not opening_block.isValid()
                or not closing_block.isValid()
                or opening_block.blockNumber() == closing_block.blockNumber()
                or not opening_block.isVisible()
                or not closing_block.isVisible()
            ):
                continue
            opening_cursor = QTextCursor(self.document())
            opening_cursor.setPosition(opening)
            closing_cursor = QTextCursor(self.document())
            closing_cursor.setPosition(closing)
            opening_rect = self.cursorRect(opening_cursor)
            closing_rect = self.cursorRect(closing_cursor)
            if max(opening_rect.bottom(), closing_rect.bottom()) < 0 or min(
                opening_rect.top(), closing_rect.top()
            ) > self.viewport().height():
                continue
            x1 = max(1, opening_rect.left() - 4)
            x2 = max(1, closing_rect.left() - 4)
            y1 = opening_rect.center().y()
            y2 = closing_rect.center().y()
            color = active_color if {opening, closing} == self.highlighted_brace_positions else guide_color
            guides.append((x1, y1, x2, y2, color))
        return guides

    def paintEvent(self, event):
        # Draw guides first so the editor text remains crisp above them.
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        for x1, y1, x2, y2, color in self._brace_guide_segments():
            pen = QPen(QColor(color), 1, Qt.PenStyle.DashLine)
            pen.setDashPattern([2, 3])
            painter.setPen(pen)
            # Align the guide with the closing bracket's column. Formatted
            # JSON places opening braces after a property name, while closing
            # braces return to the parent indentation level; no horizontal
            # segment is drawn back to the opening brace.
            painter.drawLine(x2, y1, x2, y2)
        painter.end()
        super().paintEvent(event)

    def paint_gutter(self, event):
        painter = QPainter(self.gutter)
        dark = self.editor_theme == "dark"
        painter.fillRect(event.rect(), QColor("#0A1322" if dark else "#EEF2F7"))
        number_color = QColor("#64748B" if dark else "#7C8A9D")
        marker_color = QColor("#5EEAD4" if dark else "#0F766E")

        block = self.firstVisibleBlock()
        block_number = block.blockNumber()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())
        number_area_width = self.gutter_width() - self.bookmark_column_width - self.fold_column_width

        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                if self.show_line_numbers:
                    painter.setPen(number_color)
                    painter.drawText(self.bookmark_column_width, top, number_area_width - 4, self.fontMetrics().height(),
                                     Qt.AlignmentFlag.AlignRight, str(block_number + 1))
                if isinstance(block.userData(), BookmarkData):
                    bookmark_color = QColor("#2563EB" if not dark else "#38BDF8")
                    if block_number == self.textCursor().blockNumber():
                        bookmark_color = QColor("#F97316" if not dark else "#FBBF24")
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(bookmark_color)
                    bookmark_center_y = top + self.fontMetrics().height() // 2
                    painter.drawEllipse(
                        QPoint(self.bookmark_column_width // 2, bookmark_center_y),
                        4,
                        4,
                    )
                if block_number in self.fold_regions:
                    center_x = self.bookmark_column_width + number_area_width + self.fold_column_width // 2
                    center_y = top + self.fontMetrics().height() // 2
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(marker_color)
                    if block_number in self.collapsed_blocks:
                        points = QPolygon([
                            QPoint(center_x - 3, center_y - 5),
                            QPoint(center_x - 3, center_y + 5),
                            QPoint(center_x + 4, center_y),
                        ])
                    else:
                        points = QPolygon([
                            QPoint(center_x - 5, center_y - 3),
                            QPoint(center_x + 5, center_y - 3),
                            QPoint(center_x, center_y + 4),
                        ])
                    painter.drawPolygon(points)
            block = block.next()
            block_number += 1
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())

    def gutter_mouse_press(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        x = event.position().x()
        if x < self.bookmark_column_width:
            y = event.position().y()
            block = self.firstVisibleBlock()
            top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
            while block.isValid():
                height = round(self.blockBoundingRect(block).height())
                if block.isVisible() and top <= y < top + height:
                    self.set_bookmark(block.blockNumber())
                    return
                if block.isVisible():
                    top += height
                block = block.next()
            return
        marker_start = self.gutter_width() - self.fold_column_width
        if x < marker_start:
            return
        y = event.position().y()
        block = self.firstVisibleBlock()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        while block.isValid():
            height = round(self.blockBoundingRect(block).height())
            if block.isVisible() and top <= y < top + height:
                self.toggle_fold(block.blockNumber())
                return
            if block.isVisible():
                top += height
            block = block.next()

    def _rebuild_fold_regions(self):
        # Editing invalidates existing block numbers, so expand before rebuilding.
        block = self.document().firstBlock()
        while block.isValid():
            block.setVisible(True)
            block.setLineCount(1)
            block = block.next()

        regions: dict[int, int] = {}
        stack: list[tuple[str, int]] = []
        quote: str | None = None
        escaped = False
        block_number = 0
        for char in self.toPlainText():
            if char == "\n":
                block_number += 1
                continue
            if quote:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote:
                    quote = None
                continue
            if char in "\"'":
                quote = char
            elif char in "[{":
                stack.append((char, block_number))
            elif char in "]}" and stack:
                expected = "[" if char == "]" else "{"
                if stack[-1][0] == expected:
                    _, start_block = stack.pop()
                    if block_number > start_block:
                        regions[start_block] = max(regions.get(start_block, start_block), block_number)
        self.fold_regions = regions
        self.collapsed_blocks.intersection_update(regions)
        self._rebuild_brace_pairs()
        self._apply_fold_visibility()

    def _rebuild_brace_pairs(self):
        text = self.toPlainText()
        pairs: dict[int, int] = {}
        stack: list[tuple[str, int]] = []
        expected_open = {"}": "{", "]": "[", ")": "("}
        quote: str | None = None
        escaped = False
        qt_position = 0
        for char in text:
            # QTextCursor positions are UTF-16 code-unit offsets, whereas
            # Python string indexes count Unicode code points. Astral
            # characters such as emoji therefore occupy two Qt positions.
            position = qt_position
            qt_position += 2 if ord(char) > 0xFFFF else 1
            if quote:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote:
                    quote = None
                continue
            if char in "\"'":
                quote = char
            elif char in "{[(":
                stack.append((char, position))
            elif char in "}])" and stack:
                expected = expected_open[char]
                # Recover from an incomplete/mismatched section instead of
                # allowing one stale opener to poison every pair after it.
                matching_index = next(
                    (index for index in range(len(stack) - 1, -1, -1)
                     if stack[index][0] == expected),
                    None,
                )
                if matching_index is not None:
                    _, opening = stack[matching_index]
                    del stack[matching_index:]
                    pairs[opening] = position
                    pairs[position] = opening
        self.brace_pairs = pairs
        self._update_brace_highlight()

    def set_search_extra_selections(self, selections: list[QTextEdit.ExtraSelection]):
        self.search_extra_selections = selections
        self._apply_decorations()

    def _apply_decorations(self):
        # Brace selections come last so their foreground remains red even
        # when the same character is also part of a search result.
        self.setExtraSelections(self.search_extra_selections + self.brace_extra_selections)

    def _paired_bracket_at(self, position: int) -> int | None:
        # Read through QTextCursor instead of indexing the Python string:
        # both the caret and brace-pair table use Qt's UTF-16 coordinates.
        before = QTextCursor(self.document())
        before.setPosition(position)
        if before.movePosition(
            QTextCursor.MoveOperation.PreviousCharacter,
            QTextCursor.MoveMode.KeepAnchor,
        ):
            before_position = before.selectionStart()
            if before.selectedText() in "{}[]()" and before_position in self.brace_pairs:
                return before_position

        current = QTextCursor(self.document())
        current.setPosition(position)
        if current.movePosition(
            QTextCursor.MoveOperation.NextCharacter,
            QTextCursor.MoveMode.KeepAnchor,
        ):
            current_position = current.selectionStart()
            if current.selectedText() in "{}[]()" and current_position in self.brace_pairs:
                return current_position
        return None

    def _select_container_contents_at(self, position: int) -> bool:
        # For double-click selection, prefer the bracket directly under the
        # caret before the one immediately preceding it. This distinguishes
        # the outer bracket correctly in adjacent closers such as "}}".
        bracket = None
        for operation in (
            QTextCursor.MoveOperation.NextCharacter,
            QTextCursor.MoveOperation.PreviousCharacter,
        ):
            candidate = QTextCursor(self.document())
            candidate.setPosition(position)
            if not candidate.movePosition(operation, QTextCursor.MoveMode.KeepAnchor):
                continue
            candidate_position = candidate.selectionStart()
            if candidate.selectedText() in "{}[]" and candidate_position in self.brace_pairs:
                bracket = candidate_position
                break
        if bracket is None:
            return False

        character_cursor = QTextCursor(self.document())
        character_cursor.setPosition(bracket)
        character_cursor.movePosition(
            QTextCursor.MoveOperation.NextCharacter,
            QTextCursor.MoveMode.KeepAnchor,
        )
        character = character_cursor.selectedText()
        if character in "{[":
            opening = bracket
            closing = self.brace_pairs.get(bracket)
        elif character in "}]":
            opening = self.brace_pairs.get(bracket)
            closing = bracket
        else:
            return False

        if opening is None or closing is None or closing <= opening:
            return False

        opening_block = self.document().findBlock(opening).blockNumber()
        if opening_block in self.collapsed_blocks:
            self.collapsed_blocks.remove(opening_block)
            self._apply_fold_visibility()

        selection = QTextCursor(self.document())
        selection.setPosition(opening + 1)
        selection.setPosition(closing, QTextCursor.MoveMode.KeepAnchor)
        self.setTextCursor(selection)
        return True

    def mouseDoubleClickEvent(self, event):
        click_position = self.cursorForPosition(event.position().toPoint()).position()
        if self._select_container_contents_at(click_position):
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def _update_brace_highlight(self):
        if not hasattr(self, "brace_pairs"):
            return
        bracket_position = self._paired_bracket_at(self.textCursor().position())
        positions: set[int] = set()
        if bracket_position is not None and bracket_position in self.brace_pairs:
            positions = {bracket_position, self.brace_pairs[bracket_position]}
        self.highlighted_brace_positions = positions
        color = QColor("#FF6B6B" if self.editor_theme == "dark" else "#FF0000")
        selections: list[QTextEdit.ExtraSelection] = []
        for position in sorted(positions):
            cursor = QTextCursor(self.document())
            cursor.setPosition(position)
            cursor.movePosition(
                QTextCursor.MoveOperation.NextCharacter,
                QTextCursor.MoveMode.KeepAnchor,
            )
            selection = QTextEdit.ExtraSelection()
            selection.cursor = cursor
            selection.format.setForeground(color)
            selection.format.setFontWeight(QFont.Weight.Black)
            selections.append(selection)
        self.brace_extra_selections = selections
        self._apply_decorations()
        self.viewport().update()

    def toggle_fold(self, start_block: int):
        if start_block not in self.fold_regions:
            return
        if start_block in self.collapsed_blocks:
            self.collapsed_blocks.remove(start_block)
        else:
            self.collapsed_blocks.add(start_block)
            cursor = self.textCursor()
            if start_block < cursor.blockNumber() <= self.fold_regions[start_block]:
                target = self.document().findBlockByNumber(start_block)
                cursor.setPosition(target.position() + max(0, target.length() - 1))
                self.setTextCursor(cursor)
        self._apply_fold_visibility()

    def _apply_fold_visibility(self):
        block = self.document().firstBlock()
        while block.isValid():
            block.setVisible(True)
            block.setLineCount(1)
            block = block.next()
        for start, end in sorted((start, self.fold_regions[start]) for start in self.collapsed_blocks):
            block = self.document().findBlockByNumber(start + 1)
            while block.isValid() and block.blockNumber() <= end:
                block.setVisible(False)
                block.setLineCount(0)
                block = block.next()
        self.document().markContentsDirty(0, self.document().characterCount())
        self.viewport().update()
        self.gutter.update()
        self.foldStateChanged.emit(bool(self.collapsed_blocks))

    def reveal_block(self, block_number: int):
        containing = {
            start for start in self.collapsed_blocks
            if start < block_number <= self.fold_regions.get(start, start)
        }
        if containing:
            self.collapsed_blocks.difference_update(containing)
            self._apply_fold_visibility()


class JsonWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.next_tab_number = 1
        self.search_matches: list[tuple[int, int]] = []
        self.search_index = -1
        self.search_selection_range: tuple[int, int] | None = None
        self.initial_search_selection: tuple[int, int] | None = None
        self.search_candidate_selection: tuple[int, int] | None = None
        self.selecting_search_match = False
        self._session_ready = False
        self._session_restoring = False
        self._session_dirty = False
        self._last_session_snapshot: str | None = None
        self._session_save_timer = QTimer(self)
        self._session_save_timer.setSingleShot(True)
        self._session_save_timer.setInterval(700)
        self._session_save_timer.timeout.connect(self._save_session_if_dirty)
        self.settings = create_app_settings()
        self.theme = self.settings.value("theme", default_setting("theme", "light"))
        if self.theme not in ("light", "dark"):
            self.theme = "light"
        self.language = self.settings.value("language", default_setting("language", "zh_CN"))
        if self.language not in ("zh_CN", "en"):
            self.language = "zh_CN"
        self.default_hint = platform_shortcut_hint(language=self.language)
        self.setWindowTitle(APP_NAME)
        self.resize(1080, 720)
        self.setMinimumSize(760, 480)
        self._build_ui()
        self._connect()
        self.apply_language(self.language)
        self.apply_theme(self.theme)
        restored = self._restore_session()
        if not restored:
            self.add_tab()
        self._session_ready = True
        self._last_session_snapshot = self._session_snapshot() if restored else None
        self._session_dirty = False
        self.editor.setFocus()

    @property
    def editor(self) -> QPlainTextEdit:
        return self.editor_stack.currentWidget()

    def tr(self, chinese: str, english: str, **values) -> str:
        return localized(self.language, chinese, english, **values)

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

    def _session_payload(self) -> dict:
        tabs = []
        for index in range(self.tab_bar.count()):
            editor = self.editor_stack.widget(index)
            if editor is None:
                continue
            cursor = editor.textCursor()
            tabs.append({
                "title": self.tab_bar.tabText(index),
                "content": editor.toPlainText(),
                "cursor_position": cursor.position(),
                "cursor_anchor": cursor.anchor(),
                "vertical_scroll": editor.verticalScrollBar().value(),
                "horizontal_scroll": editor.horizontalScrollBar().value(),
                "bookmarks": editor.bookmark_block_numbers(),
                "collapsed_blocks": sorted(editor.collapsed_blocks),
            })
        return {
            "version": 1,
            "active_tab": max(0, self.tab_bar.currentIndex()),
            "next_tab_number": self.next_tab_number,
            "tabs": tabs,
        }

    def _session_snapshot(self) -> str:
        return json.dumps(
            self._session_payload(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _session_int(value, default: int = 0) -> int:
        if isinstance(value, bool):
            return default
        try:
            return int(value)
        except (TypeError, ValueError, OverflowError):
            return default

    def _mark_session_dirty(self):
        if self._session_restoring or not self._session_ready:
            return
        self._session_dirty = True
        self._session_save_timer.start()

    def _save_session_if_dirty(self):
        self._save_session()

    def _save_session(self, force: bool = False) -> bool:
        if not force and not self._session_dirty:
            return False
        snapshot = self._session_snapshot()
        path = session_file_path()
        if snapshot == self._last_session_snapshot and path.exists():
            self._session_dirty = False
            return False

        temporary_name = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                prefix=".session-",
                suffix=".tmp",
                dir=str(path.parent),
                delete=False,
            ) as temporary:
                temporary_name = temporary.name
                json.dump(self._session_payload(), temporary, ensure_ascii=False, indent=2)
                temporary.write("\n")
            os.replace(temporary_name, path)
        except (OSError, TypeError, ValueError):
            if temporary_name:
                try:
                    os.unlink(temporary_name)
                except OSError:
                    pass
            return False

        self._last_session_snapshot = snapshot
        self._session_dirty = False
        return True

    def _restore_session(self) -> bool:
        path = session_file_path()
        if not path.is_file():
            return False
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return False
        if not isinstance(payload, dict) or payload.get("version") != 1:
            return False
        saved_tabs = payload.get("tabs")
        if not isinstance(saved_tabs, list):
            return False
        tabs = [item for item in saved_tabs if isinstance(item, dict)]
        if not tabs:
            return False

        self._session_restoring = True
        try:
            for item in tabs:
                self.add_tab()
                index = self.tab_bar.count() - 1
                editor = self.editor_stack.widget(index)
                title = item.get("title")
                if isinstance(title, str) and title.strip():
                    self.tab_bar.setTabText(index, title)
                content = item.get("content", "")
                editor.setPlainText(content if isinstance(content, str) else "")

                collapsed = item.get("collapsed_blocks", [])
                if isinstance(collapsed, list):
                    editor.collapsed_blocks = {
                        int(block)
                        for block in collapsed
                        if isinstance(block, int) and not isinstance(block, bool)
                        and block in editor.fold_regions
                    }
                    if editor.collapsed_blocks:
                        editor._apply_fold_visibility()

                bookmarks = item.get("bookmarks", [])
                if isinstance(bookmarks, list):
                    for block in bookmarks:
                        if isinstance(block, int) and not isinstance(block, bool):
                            editor.set_bookmark(block, True)

                maximum = max(0, editor.document().characterCount() - 1)
                position = max(0, min(
                    maximum,
                    self._session_int(item.get("cursor_position", 0)),
                ))
                anchor = max(0, min(
                    maximum,
                    self._session_int(item.get("cursor_anchor", position), position),
                ))
                cursor = editor.textCursor()
                cursor.setPosition(anchor)
                cursor.setPosition(position, QTextCursor.MoveMode.KeepAnchor)
                editor.setTextCursor(cursor)
                editor.verticalScrollBar().setValue(
                    max(0, self._session_int(item.get("vertical_scroll", 0)))
                )
                editor.horizontalScrollBar().setValue(
                    max(0, self._session_int(item.get("horizontal_scroll", 0)))
                )

            active = max(0, min(
                self.tab_bar.count() - 1,
                self._session_int(payload.get("active_tab", 0)),
            ))
            self.tab_bar.setCurrentIndex(active)
            self.editor_stack.setCurrentIndex(active)

            next_number = payload.get("next_tab_number")
            if isinstance(next_number, int) and not isinstance(next_number, bool):
                self.next_tab_number = max(self.next_tab_number, next_number)
            self._update_tab_count_button()
            self._refresh_active_status()
            self._update_fold_button()
            return True
        except (TypeError, ValueError, OverflowError):
            return False
        finally:
            self._session_restoring = False

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
        title = QLabel(APP_NAME)
        title.setObjectName("title")
        self.subtitle = QLabel("粘贴、整理和定位 JSON")
        self.subtitle.setObjectName("subtitle")
        title_row.addWidget(title)
        title_row.addSpacing(10)
        title_row.addWidget(self.subtitle)
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
        self.appearance_section = self.theme_menu.addSection("外观")
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
        self.tab_bar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
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
        self.compact_button = self._button("压缩JSON")
        self.bare_button = self._button("键名无引号")
        self.double_button = self._button('键名双引号')
        self.single_button = self._button("键名单引号")
        self.fold_button = self._button("折叠")
        self.fold_button.setEnabled(False)
        self.paste_button = self._button("从剪贴板粘贴")
        self.clear_button = self._button("清空")
        for button in (self.format_button, self.compact_button, self.bare_button,
                       self.double_button, self.single_button):
            tools.addWidget(button)
        tools.addStretch()
        tools.addWidget(self.fold_button)
        tools.addWidget(self.paste_button)
        tools.addWidget(self.clear_button)
        toolbar_container = QWidget()
        toolbar_layout = QVBoxLayout(toolbar_container)
        toolbar_layout.setContentsMargins(20, 7, 20, 0)
        toolbar_layout.addWidget(toolbar)
        layout.addWidget(toolbar_container)

        self.editor_stack = QStackedWidget()
        self.editor_stack.setObjectName("editorStack")
        self.editor_container = QWidget()
        editor_layout = QVBoxLayout(self.editor_container)
        editor_layout.setContentsMargins(20, 12, 20, 0)
        editor_layout.addWidget(self.editor_stack)
        layout.addWidget(self.editor_container, 1)
        self._build_search_bar()
        self.editor_container.installEventFilter(self)

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
        self.bookmark_label = QLabel("书签 0 / 0")
        status.addWidget(self.path_label, 1)
        status.addWidget(self.copy_full)
        status.addWidget(self.copy_plain)
        status.addWidget(self.bookmark_label)
        status.addPermanentWidget(self.stats_label)
        status.addPermanentWidget(self.position_label)
        self.setStatusBar(status)

    def _build_search_bar(self):
        self.search_bar = QFrame(self.editor_container)
        self.search_bar.setObjectName("searchBar")
        self.search_bar.setFixedHeight(43)
        search_layout = QHBoxLayout(self.search_bar)
        search_layout.setContentsMargins(7, 5, 7, 5)
        search_layout.setSpacing(3)

        self.search_input = QLineEdit()
        self.search_input.setObjectName("searchInput")
        self.search_input.setPlaceholderText("查找")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setMinimumWidth(180)
        self.search_input.installEventFilter(self)
        self.match_label = QLabel("0 of 0")
        self.match_label.setObjectName("matchCount")
        self.match_label.setMinimumWidth(58)
        self.match_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.case_button = QToolButton()
        self.case_button.setIcon(search_option_icon("case"))
        self.case_button.setToolTip("大小写敏感")
        self.case_button.setCheckable(True)
        self.case_button.setObjectName("searchOption")
        self.word_button = QToolButton()
        self.word_button.setIcon(search_option_icon("word"))
        self.word_button.setToolTip("Whole Word")
        self.word_button.setCheckable(True)
        self.word_button.setObjectName("searchOption")
        self.selection_button = QToolButton()
        self.selection_button.setIcon(search_option_icon("selection"))
        self.selection_button.setToolTip("Find in Selection")
        self.selection_button.setCheckable(True)
        self.selection_button.setObjectName("searchOption")

        self.search_scope = QComboBox()
        self.search_scope.setObjectName("searchScope")
        self.search_scope.addItem("都搜索", "all")
        self.search_scope.addItem("仅属性名", "keys")
        self.search_scope.addItem("仅属性值", "values")

        self.search_up_button = QToolButton()
        self.search_up_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowUp))
        self.search_up_button.setToolTip("上一个匹配（Shift+Enter）")
        self.search_down_button = QToolButton()
        self.search_down_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowDown))
        self.search_down_button.setToolTip("下一个匹配（Enter）")
        self.search_close_button = QToolButton()
        self.search_close_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogCloseButton))
        self.search_close_button.setToolTip("关闭查找（Esc）")

        for button in (
            self.case_button, self.word_button, self.selection_button,
            self.search_up_button, self.search_down_button, self.search_close_button,
        ):
            button.setFixedSize(28, 28)

        for widget in (
            self.search_input, self.match_label, self.case_button, self.word_button,
            self.selection_button, self.search_scope, self.search_up_button,
            self.search_down_button, self.search_close_button,
        ):
            search_layout.addWidget(widget)
        self.search_bar.hide()

    def eventFilter(self, watched, event):
        if watched is self.editor_container and event.type() == QEvent.Type.Resize:
            self._position_search_bar()
        if watched is self.search_input and event.type() == QEvent.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                direction = -1 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
                self.navigate_search(direction)
                return True
        return super().eventFilter(watched, event)

    def _position_search_bar(self):
        if not hasattr(self, "search_bar"):
            return
        width = min(690, max(560, self.editor_container.width() - 50))
        self.search_bar.setGeometry(
            self.editor_container.width() - width - 25,
            15,
            width,
            self.search_bar.height(),
        )
        self.search_bar.raise_()

    def _connect(self):
        self.format_button.clicked.connect(lambda: self.apply_transform(False, "double"))
        self.compact_button.clicked.connect(lambda: self.apply_transform(True, "double"))
        self.bare_button.clicked.connect(lambda: self.apply_transform(self.compact_mode, "bare"))
        self.double_button.clicked.connect(lambda: self.apply_transform(self.compact_mode, "double"))
        self.single_button.clicked.connect(lambda: self.apply_transform(self.compact_mode, "single"))
        self.fold_button.clicked.connect(self.toggle_all_folds)
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
        self.tab_bar.customContextMenuRequested.connect(self.show_tab_context_menu)
        for shortcut in ("Ctrl+F2", "Meta+F2"):
            QShortcut(QKeySequence(shortcut), self, activated=self.toggle_bookmark)
        QShortcut(QKeySequence("F2"), self, activated=lambda: self.navigate_bookmark(1))
        QShortcut(QKeySequence("Shift+F2"), self, activated=lambda: self.navigate_bookmark(-1))
        self.search_input.textChanged.connect(lambda: self.perform_search())
        self.case_button.toggled.connect(lambda: self.perform_search())
        self.word_button.toggled.connect(lambda: self.perform_search())
        self.selection_button.toggled.connect(self._toggle_find_in_selection)
        self.search_scope.currentIndexChanged.connect(lambda: self.perform_search())
        self.search_up_button.clicked.connect(lambda: self.navigate_search(-1))
        self.search_down_button.clicked.connect(lambda: self.navigate_search(1))
        self.search_close_button.clicked.connect(self.close_search)
        QShortcut(QKeySequence("Ctrl+Return"), self, activated=lambda: self.apply_transform(False, "double"))
        QShortcut(QKeySequence("Ctrl+Shift+M"), self, activated=lambda: self.apply_transform(True, "double"))
        QShortcut(QKeySequence("Ctrl+T"), self, activated=self.add_tab)
        QShortcut(QKeySequence("Ctrl+W"), self, activated=lambda: self.close_tab(self.tab_bar.currentIndex()))
        QShortcut(QKeySequence.StandardKey.Find, self, activated=self.open_search)
        QShortcut(QKeySequence("Escape"), self, activated=self.close_search)
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(lambda: self._save_session(force=True))

    def _create_editor(self) -> JsonEditor:
        editor = JsonEditor(
            self.theme,
            setting_as_bool(
                self.settings,
                "show_line_numbers",
                default_setting("show_line_numbers", True),
            ),
        )
        editor.set_brace_guides_visible(
            setting_as_bool(
                self.settings,
                "brace_guides",
                default_setting("brace_guides", True),
            )
        )
        editor.setObjectName("editor")
        editor.setPlaceholderText(self.tr(
            '在这里粘贴 JSON，例如：\n日志前缀... {"user": {"name": "Alice"}} ...尾部内容',
            'Paste JSON here, for example:\nLog prefix... {"user": {"name": "Alice"}} ...suffix',
        ))
        editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        font = QFont("JetBrains Mono")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSize(13)
        editor.setFont(font)
        # One formatting level is a tab displayed at the same width as the
        # original two-space indentation. Tabs remain stable when Qt falls
        # back to CJK fonts for syntax-highlighted property names.
        editor.setTabStopDistance(editor.fontMetrics().horizontalAdvance("  "))
        editor.json_current_value = None
        editor.json_rendered_text = None
        editor.json_key_style = "double"
        editor.json_compact_mode = False
        editor.json_stats_text = self.tr("等待输入", "Waiting for input")
        editor.json_stats_state = "waiting"
        editor.json_stats_counts = None
        editor.json_highlighter = JsonHighlighter(editor.document(), self.theme)
        editor._update_brace_highlight()
        editor.foldStateChanged.connect(lambda _collapsed, current=editor: self._fold_state_changed(current))
        editor.bookmarksChanged.connect(lambda current=editor: self._editor_bookmarks_changed(current))
        editor.cursorPositionChanged.connect(lambda current=editor: self._editor_cursor_changed(current))
        editor.selectionChanged.connect(lambda current=editor: self._editor_selection_changed(current))
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
        self._update_fold_button()
        editor.setFocus()
        self._mark_session_dirty()

    def switch_tab(self, index: int):
        if 0 <= index < self.editor_stack.count():
            self.editor_stack.setCurrentIndex(index)
            self._refresh_active_status()
            self._update_tab_navigation()
            self._update_fold_button()
            if self.search_bar.isVisible():
                self.search_selection_range = None
                self.initial_search_selection = None
                self.search_candidate_selection = None
                self.selection_button.blockSignals(True)
                self.selection_button.setChecked(False)
                self.selection_button.blockSignals(False)
                self.perform_search()
            self._mark_session_dirty()

    def _fold_state_changed(self, editor: JsonEditor):
        if editor is self.editor:
            self._update_fold_button()
        self._mark_session_dirty()

    def _editor_bookmarks_changed(self, editor: JsonEditor):
        if editor is self.editor:
            self._update_bookmark_status()
        self._mark_session_dirty()

    def _update_fold_button(self):
        editor = self.editor
        if editor is None:
            self.fold_button.setText(self.tr("折叠", "Collapse"))
            self.fold_button.setEnabled(False)
            return
        collapsed = bool(editor.collapsed_blocks)
        self.fold_button.setText(
            self.tr("展开", "Expand") if collapsed else self.tr("折叠", "Collapse")
        )
        self.fold_button.setEnabled(bool(editor.fold_regions))

    def toggle_all_folds(self):
        editor = self.editor
        if editor is None or not editor.fold_regions:
            self._flash(self.tr("当前内容没有可折叠的对象或数组", "No collapsible objects or arrays"))
            return
        if editor.collapsed_blocks:
            editor.collapsed_blocks.clear()
        else:
            editor.collapsed_blocks = set(editor.fold_regions)
        editor._apply_fold_visibility()

    def toggle_bookmark(self):
        editor = self.editor
        if editor is None:
            return
        editor.set_bookmark()

    def navigate_bookmark(self, direction: int):
        editor = self.editor
        if editor is None:
            return
        bookmarks = editor.bookmark_block_numbers()
        if not bookmarks:
            self._flash(self.tr("当前没有书签", "No bookmarks"))
            return
        current_line = editor.textCursor().blockNumber()
        if direction > 0:
            target = next((line for line in bookmarks if line > current_line), bookmarks[0])
        else:
            target = next((line for line in reversed(bookmarks) if line < current_line), bookmarks[-1])
        editor.reveal_block(target)
        block = editor.document().findBlockByNumber(target)
        cursor = QTextCursor(editor.document())
        cursor.setPosition(block.position())
        editor.setTextCursor(cursor)
        editor.ensureCursorVisible()
        self._update_bookmark_status()

    def _update_bookmark_status(self):
        if not hasattr(self, "bookmark_label") or self.editor is None:
            return
        bookmarks = self.editor.bookmark_block_numbers()
        current_line = self.editor.textCursor().blockNumber()
        current_index = bookmarks.index(current_line) + 1 if current_line in bookmarks else 0
        self.bookmark_label.setText(self.tr(
            "书签 {index} / {total}",
            "Bookmarks {index} / {total}",
            index=current_index,
            total=len(bookmarks),
        ))

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
            self._mark_session_dirty()
            return
        self.tab_bar.removeTab(index)
        self.editor_stack.removeWidget(editor)
        editor.deleteLater()
        self._update_tab_count_button()
        self.switch_tab(self.tab_bar.currentIndex())
        self._mark_session_dirty()

    def _update_tab_count_button(self):
        count = self.tab_bar.count()
        self.add_tab_button.setText(f"+({count})")
        self.add_tab_button.setToolTip(self.tr(
            "新建标签页（当前 {count} 个）",
            "New tab ({count} open)",
            count=count,
        ))
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
        self._mark_session_dirty()

    def rename_tab(self, index: int):
        if index < 0:
            return
        current = self.tab_bar.tabText(index)
        title, accepted = QInputDialog.getText(
            self,
            self.tr("重命名标签", "Rename Tab"),
            self.tr("标签名称：", "Tab name:"),
            text=current,
        )
        title = title.strip()
        if accepted and title:
            self.tab_bar.setTabText(index, title)
            self._mark_session_dirty()

    def show_tab_context_menu(self, position: QPoint):
        index = self.tab_bar.tabAt(position)
        if index < 0:
            return
        menu = self._create_tab_context_menu(index)
        menu.exec(self.tab_bar.mapToGlobal(position))

    def _create_tab_context_menu(self, index: int) -> QMenu:
        menu = QMenu(self.tab_bar)
        rename_action = menu.addAction(self.tr("重命名", "Rename"))
        rename_action.triggered.connect(lambda _checked=False: self.rename_tab(index))
        return menu

    def _confirm_close_tab(self, title: str) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(self.tr("关闭标签", "Close Tab"))
        box.setText(self.tr("确定要关闭“{title}”吗？", 'Close "{title}"?', title=title))
        box.setInformativeText(self.tr(
            "该标签中有内容，关闭后内容将丢失。",
            "This tab contains text. Its contents will be lost.",
        ))
        cancel_button = box.addButton(self.tr("取消", "Cancel"), QMessageBox.ButtonRole.RejectRole)
        close_button = box.addButton(self.tr("关闭", "Close"), QMessageBox.ButtonRole.DestructiveRole)
        box.setDefaultButton(cancel_button)
        box.setEscapeButton(cancel_button)
        box.exec()
        return box.clickedButton() is close_button

    def show_more_settings(self):
        dialog = MoreSettingsDialog(self.settings, self.language, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.apply_language(
                self.settings.value("language", default_setting("language", "zh_CN"))
            )
            brace_guides_visible = setting_as_bool(
                self.settings,
                "brace_guides",
                default_setting("brace_guides", True),
            )
            for index in range(self.editor_stack.count()):
                self.editor_stack.widget(index).set_brace_guides_visible(brace_guides_visible)
            theme = self.settings.value("theme", default_setting("theme", "light"))
            if theme not in ("light", "dark"):
                theme = "light"
            self.apply_theme(theme)

    def show_about(self):
        box = QMessageBox(self)
        box.setWindowTitle(self.tr(f"关于 {APP_NAME}", f"About {APP_NAME}"))
        box.setIcon(QMessageBox.Icon.Information)
        box.setText(f"{APP_NAME} {APP_VERSION}")
        box.setInformativeText(self.tr(
            "当前 Python 版本：{version}\n\n本地 JSON 格式化、转换与路径定位工具"
            "\n\nPowered by Stone Wang",
            "Python version: {version}\n\nA local tool for formatting, converting, and navigating JSON"
            "\n\nPowered by Stone Wang",
            version=platform.python_version(),
        ))
        box.exec()

    def _confirm_exit(self) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(self.tr(f"退出 {APP_NAME}", f"Exit {APP_NAME}"))
        box.setText(self.tr(f"确定要退出 {APP_NAME} 吗？", f"Exit {APP_NAME}?"))
        cancel_button = box.addButton(self.tr("取消", "Cancel"), QMessageBox.ButtonRole.RejectRole)
        exit_button = box.addButton(self.tr("退出", "Exit"), QMessageBox.ButtonRole.AcceptRole)
        box.setDefaultButton(cancel_button)
        box.setEscapeButton(cancel_button)
        box.exec()
        return box.clickedButton() is exit_button

    def closeEvent(self, event):
        if not setting_as_bool(
            self.settings,
            "confirm_exit",
            default_setting("confirm_exit", True),
        ) or self._confirm_exit():
            self._session_save_timer.stop()
            self._save_session(force=True)
            event.accept()
        else:
            event.ignore()

    def open_search(self):
        if self.search_bar.isVisible():
            self.search_input.setFocus()
            self.search_input.selectAll()
            return
        cursor = self.editor.textCursor()
        self.initial_search_selection = (
            (cursor.selectionStart(), cursor.selectionEnd()) if cursor.hasSelection() else None
        )
        self.search_candidate_selection = self.initial_search_selection
        selected = cursor.selectedText().replace("\u2029", "\n")
        self.search_bar.show()
        self._position_search_bar()
        self.search_bar.raise_()
        if selected and "\n" not in selected:
            self.search_input.setText(selected)
        else:
            self.perform_search()
        self.search_input.setFocus()
        self.search_input.selectAll()

    def close_search(self):
        if not hasattr(self, "search_bar") or not self.search_bar.isVisible():
            return
        self.search_bar.hide()
        self.search_matches = []
        self.search_index = -1
        self.search_selection_range = None
        self.initial_search_selection = None
        self.search_candidate_selection = None
        self.selection_button.blockSignals(True)
        self.selection_button.setChecked(False)
        self.selection_button.blockSignals(False)
        self.editor.set_search_extra_selections([])
        self.editor.setFocus()

    def _toggle_find_in_selection(self, checked: bool):
        if checked:
            selection = self.search_candidate_selection or self.initial_search_selection
            if selection is None:
                cursor = self.editor.textCursor()
                if cursor.hasSelection():
                    selection = (cursor.selectionStart(), cursor.selectionEnd())
            if selection is None or selection[0] == selection[1]:
                self.selection_button.blockSignals(True)
                self.selection_button.setChecked(False)
                self.selection_button.blockSignals(False)
                self._flash(self.tr(
                    "请先在编辑器中选择搜索范围",
                    "Select a search range in the editor first",
                ))
                return
            self.search_selection_range = selection
        else:
            self.search_selection_range = None
        self.perform_search()

    @staticmethod
    def _is_word_character(char: str) -> bool:
        return bool(char) and (char.isalnum() or char == "_")

    def perform_search(self):
        if not self.search_bar.isVisible():
            return
        editor = self.editor
        text = editor.toPlainText()
        query = self.search_input.text()
        self.search_matches = []
        self.search_index = -1
        if not query:
            self.match_label.setText("0 of 0")
            self._update_search_highlights()
            return

        flags = 0 if self.case_button.isChecked() else re.IGNORECASE
        pattern = re.compile(re.escape(query), flags)
        scope = self.search_scope.currentData()
        allowed: list[tuple[int, int]] | None = None
        if scope != "all":
            key_spans, value_spans = searchable_spans(text)
            allowed = key_spans if scope == "keys" else value_spans

        for match in pattern.finditer(text):
            start, end = match.span()
            if self.word_button.isChecked():
                before = text[start - 1] if start else ""
                after = text[end] if end < len(text) else ""
                if self._is_word_character(before) or self._is_word_character(after):
                    continue
            if self.search_selection_range:
                range_start, range_end = self.search_selection_range
                if start < range_start or end > range_end:
                    continue
            if allowed is not None and not any(start >= left and end <= right for left, right in allowed):
                continue
            self.search_matches.append((start, end))

        if self.search_matches:
            cursor_position = editor.textCursor().selectionStart()
            self.search_index = next(
                (index for index, (start, _) in enumerate(self.search_matches) if start >= cursor_position),
                0,
            )
            self._select_search_match()
        else:
            self.match_label.setText("0 of 0")
            self._update_search_highlights()

    def navigate_search(self, offset: int):
        if not self.search_matches:
            self.perform_search()
            if not self.search_matches:
                return
        self.search_index = (self.search_index + offset) % len(self.search_matches)
        self._select_search_match()

    def _select_search_match(self):
        if not self.search_matches or self.search_index < 0:
            return
        editor = self.editor
        start, end = self.search_matches[self.search_index]
        block_number = editor.document().findBlock(start).blockNumber()
        editor.reveal_block(block_number)
        cursor = QTextCursor(editor.document())
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        self.selecting_search_match = True
        try:
            editor.setTextCursor(cursor)
        finally:
            self.selecting_search_match = False
        editor.centerCursor()
        self.match_label.setText(f"{self.search_index + 1} of {len(self.search_matches)}")
        self._update_search_highlights()

    def _update_search_highlights(self):
        editor = self.editor
        selections: list[QTextEdit.ExtraSelection] = []
        if self.search_selection_range:
            range_start, range_end = self.search_selection_range
            range_selection = QTextEdit.ExtraSelection()
            range_cursor = QTextCursor(editor.document())
            range_cursor.setPosition(range_start)
            range_cursor.setPosition(range_end, QTextCursor.MoveMode.KeepAnchor)
            range_selection.cursor = range_cursor
            range_color = "#1E3A5F" if self.theme == "dark" else "#DBEAFE"
            range_selection.format.setBackground(QColor(range_color))
            selections.append(range_selection)
        for index, (start, end) in enumerate(self.search_matches):
            selection = QTextEdit.ExtraSelection()
            cursor = QTextCursor(editor.document())
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            selection.cursor = cursor
            color = "#FB923C" if index == self.search_index else "#FDE68A"
            if self.theme == "dark" and index != self.search_index:
                color = "#A16207"
            selection.format.setBackground(QColor(color))
            selection.format.setForeground(QColor("#111827"))
            selections.append(selection)
        editor.set_search_extra_selections(selections)

    def paste(self):
        text = QApplication.clipboard().text()
        if text:
            self.editor.setPlainText(text)
            self._flash(self.tr("已从剪贴板粘贴", "Pasted from clipboard"))

    def _localized_json_error(self, message: str) -> str:
        if self.language != "en":
            return message
        exact = {
            "没有找到完整、有效的 JSON 对象或数组": "No complete, valid JSON object or array was found",
            "字符串包含无效的 Unicode 转义": "The string contains an invalid Unicode escape",
            "字符串缺少结束引号": "The string is missing its closing quote",
            "缺少 JSON 值": "A JSON value is missing",
            "对象缺少结束大括号": "The object is missing its closing brace",
            "数组缺少结束方括号": "The array is missing its closing bracket",
        }
        if message in exact:
            return exact[message]
        match = re.fullmatch(
            r"没有找到完整、有效的 JSON 对象或数组（附近第 (\d+) 行、第 (\d+) 列）",
            message,
        )
        if match:
            return (
                "No complete, valid JSON object or array was found "
                f"(near line {match.group(1)}, column {match.group(2)})"
            )
        match = re.fullmatch(r"字符串包含无效转义：(.+)", message)
        if match:
            return f"The string contains an invalid escape: {match.group(1)}"
        match = re.fullmatch(r"第 (\d+) 个字符附近缺少 (.+)", message)
        if match:
            return f"Missing {match.group(2)} near character {match.group(1)}"
        match = re.fullmatch(r"第 (\d+) 个字符附近不是有效的 JSON 值", message)
        if match:
            return f"Invalid JSON value near character {match.group(1)}"
        match = re.fullmatch(r"第 (\d+) 个字符附近不是有效的属性名", message)
        if match:
            return f"Invalid property name near character {match.group(1)}"
        match = re.fullmatch(r"第 (\d+) 个字符附近缺少逗号或结束大括号", message)
        if match:
            return f"Missing a comma or closing brace near character {match.group(1)}"
        return "Invalid JSON: " + message

    def apply_transform(self, compact: bool, style: str):
        text = self.editor.toPlainText()
        if not text.strip():
            self._flash(self.tr("请先粘贴 JSON", "Paste JSON first"), error=True)
            return
        if self.current_value is not None and text == self.rendered_text:
            value = self.current_value
            output = render_json(value, compact=compact, key_style=style)
            prefix = suffix = 0
        else:
            try:
                parsed = parse_json_like(text)
            except (JsonToolError, ValueError) as exc:
                self._flash(self._localized_json_error(str(exc)), error=True)
                return
            if parsed.mixed and not self._confirm_mixed_mode(parsed.key_styles):
                self._flash(self.tr("已取消，原始内容保持不变", "Cancelled; original content unchanged"))
                return
            value = parsed.value
            prefix, suffix = parsed.start, len(text) - parsed.end
            output = render_json(value, compact=compact, key_style=style)
        cursor = self.editor.textCursor()
        self.current_value = value
        self.rendered_text = output
        self.editor.clear_bookmarks()
        self.editor.setPlainText(output)
        cursor.setPosition(min(cursor.position(), len(output)))
        self.editor.setTextCursor(cursor)
        self.key_style = style
        self.compact_mode = compact
        count, depth = value_stats(value)
        self.editor.json_stats_state = "computed"
        self.editor.json_stats_counts = (count, depth)
        self.editor.json_stats_text = self._localized_editor_stats(self.editor)
        self.stats_label.setText(self.editor.json_stats_text)
        removed = prefix + suffix
        message = (
            self.tr("已压缩 JSON", "JSON minified")
            if compact else self.tr("已格式化 JSON", "JSON formatted")
        )
        if removed:
            message += self.tr(
                "，并移除首尾 {removed} 个字符",
                "; removed {removed} surrounding characters",
                removed=removed,
            )
        if style == "bare":
            message += self.tr("；特殊键名保留引号", "; special keys remain quoted")
        self._flash(message)
        self.update_path()

    def _confirm_mixed_mode(self, styles) -> bool:
        names = {
            "double": self.tr("双引号", "double quotes"),
            "single": self.tr("单引号", "single quotes"),
            "bare": self.tr("无引号", "unquoted"),
        }
        separator = "、" if self.language == "zh_CN" else ", "
        used = separator.join(names[style] for style in ("double", "single", "bare") if style in styles)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(self.tr("检测到混合模式", "Mixed Mode Detected"))
        box.setText(self.tr("给定的 JSON 字符串是混合模式", "The JSON string uses mixed key styles"))
        box.setInformativeText(self.tr(
            "检测到属性名同时使用了：{used}。\n\n"
            "选择“净化并继续”会先修正为双引号标准 JSON，再执行刚才的操作。",
            "Property names use: {used}.\n\n"
            'Choose "Normalize and Continue" to convert to standard double-quoted JSON first, '
            "then perform the requested operation.",
            used=used,
        ))
        cancel_button = box.addButton(self.tr("取消", "Cancel"), QMessageBox.ButtonRole.RejectRole)
        continue_button = box.addButton(
            self.tr("净化并继续", "Normalize and Continue"),
            QMessageBox.ButtonRole.AcceptRole,
        )
        box.setDefaultButton(continue_button)
        box.setEscapeButton(cancel_button)
        box.exec()
        return box.clickedButton() is continue_button

    def _editor_text_changed(self, editor: QPlainTextEdit):
        self._mark_session_dirty()
        text = editor.toPlainText()
        if not text:
            editor.json_current_value = None
            editor.json_rendered_text = None
            editor.json_stats_state = "waiting"
            editor.json_stats_counts = None
            editor.json_stats_text = self._localized_editor_stats(editor)
        elif editor.json_rendered_text is not None and text != editor.json_rendered_text:
            editor.json_current_value = None
            editor.json_rendered_text = None
            editor.json_stats_state = "modified"
            editor.json_stats_counts = None
            editor.json_stats_text = self._localized_editor_stats(editor)
        if editor is self.editor:
            self.stats_label.setText(editor.json_stats_text)
            self.update_path()
            self._update_bookmark_status()
            if self.search_bar.isVisible():
                self.perform_search()

    def _editor_cursor_changed(self, editor: QPlainTextEdit):
        self._mark_session_dirty()
        if editor is self.editor:
            self.update_path()
            self._update_bookmark_status()

    def _editor_selection_changed(self, editor: QPlainTextEdit):
        if (
            editor is not self.editor
            or not self.search_bar.isVisible()
            or self.selection_button.isChecked()
            or self.selecting_search_match
        ):
            return
        cursor = editor.textCursor()
        if cursor.hasSelection():
            self.search_candidate_selection = (cursor.selectionStart(), cursor.selectionEnd())

    def _refresh_active_status(self):
        editor = self.editor
        if editor is None:
            return
        self.stats_label.setText(editor.json_stats_text)
        self.update_path()
        self._update_bookmark_status()

    def update_path(self):
        cursor = self.editor.textCursor()
        text = self.editor.toPlainText()
        path = path_at_position(text, cursor.position(), True) if text else "$"
        self.path_label.setText(self.tr("路径  {path}", "Path  {path}", path=path))
        self.path_label.setToolTip(path)
        self.position_label.setText(self.tr(
            "行 {line}，列 {column}",
            "Line {line}, Col {column}",
            line=cursor.blockNumber() + 1,
            column=cursor.positionInBlock() + 1,
        ))

    def copy_path(self, include_root: bool):
        text = self.editor.toPlainText()
        path = path_at_position(text, self.editor.textCursor().position(), include_root)
        QApplication.clipboard().setText(path)
        self._flash(self.tr("已复制：{path}", "Copied: {path}", path=path))

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

    def _localized_editor_stats(self, editor: QPlainTextEdit) -> str:
        state = getattr(editor, "json_stats_state", "waiting")
        if state == "computed" and getattr(editor, "json_stats_counts", None):
            count, depth = editor.json_stats_counts
            return self.tr(
                "{count} 个节点  ·  深度 {depth}",
                "{count} nodes  ·  depth {depth}",
                count=count,
                depth=depth,
            )
        if state == "modified":
            return self.tr("内容已修改", "Modified")
        return self.tr("等待输入", "Waiting for input")

    def apply_language(self, language: str):
        if language not in ("zh_CN", "en"):
            language = "zh_CN"
        self.language = language
        self.settings.setValue("language", language)
        self.settings.sync()
        self.default_hint = platform_shortcut_hint(language=language)

        self.subtitle.setText(self.tr("粘贴、整理和定位 JSON", "Paste, format, and navigate JSON"))
        self.settings_button.setText(self.tr("⚙ 设置", "⚙ Settings"))
        self.appearance_section.setText(self.tr("外观", "Appearance"))
        self.dark_action.setText(self.tr("深色主题", "Dark theme"))
        self.light_action.setText(self.tr("浅色主题", "Light theme"))
        self.more_settings_action.setText(self.tr("更多设置", "More Settings"))
        self.about_action.setText(self.tr("关于", "About"))

        self.tab_bar.rename_hint = self.tr(
            "双击标签标题可重命名",
            "Double-click the tab title to rename",
        )
        left_tip = self.tr("向左浏览标签", "Browse tabs to the left")
        right_tip = self.tr("向右浏览标签", "Browse tabs to the right")
        self.tab_left_button.setToolTip(left_tip)
        self.tab_left_button.setAccessibleName(left_tip)
        self.tab_right_button.setToolTip(right_tip)
        self.tab_right_button.setAccessibleName(right_tip)

        self.format_button.setText(self.tr("格式化", "Format"))
        self.compact_button.setText(self.tr("压缩JSON", "Minify JSON"))
        self.bare_button.setText(self.tr("键名无引号", "Unquoted Keys"))
        self.double_button.setText(self.tr("键名双引号", "Double-Quoted Keys"))
        self.single_button.setText(self.tr("键名单引号", "Single-Quoted Keys"))
        self.paste_button.setText(self.tr("从剪贴板粘贴", "Paste from Clipboard"))
        self.clear_button.setText(self.tr("清空", "Clear"))

        self.search_input.setPlaceholderText(self.tr("查找", "Find"))
        self.case_button.setToolTip(self.tr("大小写敏感", "Match Case"))
        self.word_button.setToolTip(self.tr("全字匹配", "Whole Word"))
        self.selection_button.setToolTip(self.tr("在选区中查找", "Find in Selection"))
        self.search_scope.setItemText(0, self.tr("都搜索", "Search All"))
        self.search_scope.setItemText(1, self.tr("仅属性名", "Keys Only"))
        self.search_scope.setItemText(2, self.tr("仅属性值", "Values Only"))
        self.search_up_button.setToolTip(self.tr("上一个匹配（Shift+Enter）", "Previous Match (Shift+Enter)"))
        self.search_down_button.setToolTip(self.tr("下一个匹配（Enter）", "Next Match (Enter)"))
        self.search_close_button.setToolTip(self.tr("关闭查找（Esc）", "Close Find (Esc)"))

        placeholder = self.tr(
            '在这里粘贴 JSON，例如：\n日志前缀... {"user": {"name": "Alice"}} ...尾部内容',
            'Paste JSON here, for example:\nLog prefix... {"user": {"name": "Alice"}} ...suffix',
        )
        for index in range(self.editor_stack.count()):
            editor = self.editor_stack.widget(index)
            editor.setPlaceholderText(placeholder)
            editor.json_stats_text = self._localized_editor_stats(editor)

        self.copy_full.setText(self.tr("复制 $", "Copy $"))
        self.copy_plain.setText(self.tr("复制无 $", "Copy without $"))
        self._update_bookmark_status()
        self.hint.setText(self.default_hint)
        self._update_fold_button()
        self._update_tab_count_button()
        self._refresh_active_status()

    def apply_theme(self, theme: str):
        self.theme = theme
        self.settings.setValue("theme", theme)
        self.dark_action.setChecked(theme == "dark")
        self.light_action.setChecked(theme == "light")
        if hasattr(self, "editor_stack"):
            show_line_numbers = setting_as_bool(
                self.settings,
                "show_line_numbers",
                default_setting("show_line_numbers", True),
            )
            for index in range(self.editor_stack.count()):
                editor = self.editor_stack.widget(index)
                editor.json_highlighter.set_theme(theme)
                editor.set_editor_theme(theme)
                editor.set_line_numbers_visible(show_line_numbers)
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
                QFrame#searchBar {
                    background: #FFFFFF; border: 1px solid #94A3B8;
                    border-radius: 6px;
                }
                QFrame#searchBar QLineEdit {
                    background: #FFFFFF; color: #1E293B; border: 1px solid #CBD5E1;
                    border-radius: 4px; padding: 5px 7px;
                }
                QFrame#searchBar QLineEdit:focus { border-color: #0D9488; }
                QFrame#searchBar QToolButton { padding: 2px; border-radius: 4px; }
                QFrame#searchBar QToolButton:checked { background: #99F6E4; border-color: #0D9488; }
                QFrame#searchBar QLabel#matchCount { color: #64748B; font-size: 11px; }
                QFrame#searchBar QComboBox { padding: 4px 6px; min-width: 82px; }
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
            QFrame#searchBar {
                background: #17243A; border: 1px solid #3D5272;
                border-radius: 6px;
            }
            QFrame#searchBar QLineEdit {
                background: #0E1728; color: #D7E0EC; border: 1px solid #3D5272;
                border-radius: 4px; padding: 5px 7px;
            }
            QFrame#searchBar QLineEdit:focus { border-color: #2DD4BF; }
            QFrame#searchBar QToolButton { padding: 2px; border-radius: 4px; }
            QFrame#searchBar QToolButton:checked { background: #0F766E; border-color: #2DD4BF; }
            QFrame#searchBar QLabel#matchCount { color: #AAB8CA; font-size: 11px; }
            QFrame#searchBar QComboBox { padding: 4px 6px; min-width: 82px; }
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
        tab_style = self.settings.value("tab_style", default_setting("tab_style", "practical"))
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
        if hasattr(self, "search_bar") and self.search_bar.isVisible():
            self._update_search_highlights()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyle("Fusion")
    startup_settings = create_app_settings()
    instance_lock = None
    if setting_as_bool(
        startup_settings,
        "single_instance",
        default_setting("single_instance", True),
    ):
        instance_lock, already_running = acquire_instance_lock()
        if already_running:
            language = startup_settings.value("language", default_setting("language", "zh_CN"))
            QMessageBox.information(
                None,
                APP_NAME,
                localized(
                    language,
                    f"{APP_NAME} 已经在运行中。",
                    f"{APP_NAME} is already running.",
                ),
            )
            return 0
    window = JsonWindow()
    # Keep the QLockFile alive for the full lifetime of the process.
    window._instance_lock = instance_lock
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
