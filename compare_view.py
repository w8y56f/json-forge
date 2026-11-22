"""Editable comparison views and asynchronous difference calculation."""
from bisect import bisect_right

from PySide6.QtCore import QEvent, Qt, QObject, QRunnable, QThreadPool, Signal, QMimeData, QPoint, QSize, QRect, QTimer
from PySide6.QtGui import QColor, QFont, QPalette, QPainter, QTextCursor, QTextFormat, QKeySequence, QPainterPath
from PySide6.QtWidgets import (
    QLayout, QStyle, QStyleOptionComboBox, QWidget, QPlainTextEdit, QTextEdit, QVBoxLayout, QHBoxLayout,
    QLabel, QToolButton, QPushButton, QSplitter, QCheckBox, QApplication, QMenu, QComboBox, QSizePolicy, QSplitterHandle,
)

from text_diff import compare_text, line_content, utf16_offset, IgnoreMode


HIGHLIGHTS = (("words", "按词", "Words"), ("lines", "按行", "Lines"),
              ("characters", "按字符", "Characters"))
VIEWERS = (("side", "并排视图", "Side by side"), ("unified", "统一视图", "Unified"))


def update_document_text(editor, previous, text):
    """Apply the smallest contiguous replacement without resetting undo."""
    start = 0
    limit = min(len(previous), len(text))
    while start < limit and previous[start] == text[start]:
        start += 1
    old_end, new_end = len(previous), len(text)
    while old_end > start and new_end > start and previous[old_end - 1] == text[new_end - 1]:
        old_end -= 1
        new_end -= 1
    if start == old_end == new_end:
        return
    cursor = QTextCursor(editor.document())
    cursor.setPosition(utf16_offset(previous, start))
    cursor.setPosition(utf16_offset(previous, old_end), QTextCursor.MoveMode.KeepAnchor)
    cursor.insertText(text[start:new_end])


class DiffSignals(QObject):
    finished = Signal(int, object, str)


class DiffJob(QRunnable):
    def __init__(self, request, left, right, mode, highlight):
        self.highlight = highlight
        super().__init__()
        self.request, self.left, self.right = request, left, right
        self.mode = mode
        self.signals = DiffSignals()
        self.cancelled = False

    def run(self):
        try:
            result = compare_text(self.left, self.right, lambda: self.cancelled, mode=self.mode, highlight=self.highlight)
            self.signals.finished.emit(self.request, result, "")
        except Exception as error:
            self.signals.finished.emit(self.request, None, str(error))


class DiffGutter(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor

    def paintEvent(self, event):
        editor = self.editor
        painter = QPainter(self)
        painter.fillRect(event.rect(), QColor("#172235" if editor.dark else "#EEF2F6"))
        block = editor.firstVisibleBlock()
        top = editor.blockBoundingGeometry(block).translated(editor.contentOffset()).top()
        while block.isValid() and top <= event.rect().bottom():
            height = editor.blockBoundingRect(block).height()
            row = block.blockNumber()
            if row < len(editor.mapping):
                original = editor.mapping[row]
                if row in editor.changed_rows:
                    painter.fillRect(QRect(0, round(top), 4, round(height)), editor.marker_color())
                painter.setPen(QColor("#94A3B8" if editor.dark else "#64748B"))
                painter.drawText(5, round(top), self.width() - 12, round(height),
                                 Qt.AlignmentFlag.AlignRight, editor.labels[row] if row < len(editor.labels) else ("" if original is None else str(original + 1)))
            top += height
            block = block.next()


class DiffEditor(QPlainTextEdit):
    historyRequested = Signal(bool)

    def __init__(self, side):
        super().__init__()
        self.side = side
        self.dark = False
        self.mapping, self.lines, self.changed_rows = [], (), set()
        self.spans = {}
        self.source = ""
        self.labels = []
        self.kinds = []
        self.projected = False
        self.raw_copy = False
        self.language = "zh_CN"
        self.setReadOnly(True)
        self.setUndoRedoEnabled(False)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff if side == "left"
                                        else Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.gutter = DiffGutter(self)
        self.blockCountChanged.connect(self.update_gutter)
        self.updateRequest.connect(lambda *_: self.gutter.update())
        self.setToolTip("显示原文行号，细线标记另一侧的变化位置。")
        self.update_gutter()

    def update_gutter(self):
        width = 18 + self.fontMetrics().horizontalAdvance("9") * len(str(max(1, self.blockCount())))
        if self.labels:
            width = max(width, 14 + self.fontMetrics().horizontalAdvance(max(self.labels, key=len)))
        on_right = self.side == "left"
        self.setViewportMargins(0 if on_right else width, 0, width if on_right else 0, 0)
        rect = self.contentsRect()
        viewport = self.viewport().geometry()
        x = viewport.right() + 1 if on_right else rect.left()
        self.gutter.setGeometry(x, viewport.top(), width, viewport.height())
        self.gutter.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_gutter()

    def marker_color(self):
        return QColor("#EF4444" if self.side == "left" else "#10B981")

    def set_result(self, result, *, update_text=True):
        self.lines = getattr(result, self.side + "_lines")
        self.source = "".join(self.lines)
        self.mapping = range(len(self.lines))
        self.spans = {getattr(row, self.side): getattr(row, self.side + "_spans")
                      for row in result.rows if row.changed and getattr(row, self.side) is not None}
        self.changed_rows = set(self.spans)
        self.boundaries = {}
        # QTextDocument interprets these characters as layout separators. Show
        # visible, equal-width-in-UTF16 symbols and copy from the source below.
        def display(line):
            return line_content(line).replace("\r", "␍").replace("\u2028", "␤").replace("\u2029", "¶")
        self.raw_copy = update_text
        if update_text:
            self.setPlainText("\n".join(display(line) for line in self.lines))
        self.update_gutter()
        self.decorate()

    def boundary_y(self, index):
        first = self.firstVisibleBlock()
        top = self.blockBoundingGeometry(first).translated(self.contentOffset()).top()
        # NoWrap renders one visual line per block. Use the actual geometry
        # for visible endpoints, extrapolating only outside the viewport.
        estimate = top + (index - first.blockNumber()) * self.fontMetrics().lineSpacing()
        if self.lineWrapMode() == QPlainTextEdit.LineWrapMode.WidgetWidth or -100 < estimate < self.viewport().height() + 100:
            block = self.document().findBlockByNumber(min(index, self.blockCount() - 1))
            rect = self.blockBoundingGeometry(block).translated(self.contentOffset())
            return rect.bottom() if index >= self.blockCount() else rect.top()
        return estimate

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self.viewport())
        for line, color in getattr(self, "boundaries", {}).items():
            y = round(self.boundary_y(line))
            if 0 <= y < self.viewport().height():
                painter.setPen(QColor(color))
                painter.drawLine(0, y, self.viewport().width(), y)

    def decorate(self):
        selections = []
        background = ("#542B38" if self.dark else "#FEE2E2") if self.side == "left" else (
            "#193F35" if self.dark else "#DCFCE7")
        strong = ("#913C50" if self.dark else "#FCA5A5") if self.side == "left" else (
            "#24634B" if self.dark else "#86EFAC")
        for row in sorted(self.changed_rows):
            block = self.document().findBlockByNumber(row)
            selection = QTextEdit.ExtraSelection()
            selection.cursor = QTextCursor(block)
            bg, fg = background, strong
            if self.kinds and self.kinds[row] == "-":
                bg, fg = ("#542B38", "#913C50") if self.dark else ("#FEE2E2", "#FCA5A5")
            if self.kinds and self.kinds[row] == "pad":
                bg = "#263142" if self.dark else "#E9EDF1"
            selection.format.setBackground(QColor(bg))
            selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
            selections.append(selection)
            for start, end in self.spans.get(row, ()):
                selection = QTextEdit.ExtraSelection()
                selection.cursor = QTextCursor(block)
                selection.cursor.setPosition(block.position() + utf16_offset(block.text(), start))
                selection.cursor.setPosition(block.position() + utf16_offset(block.text(), end),
                                             QTextCursor.MoveMode.KeepAnchor)
                selection.format.setBackground(QColor(fg))
                selections.append(selection)
        self.setExtraSelections(selections)
        self.gutter.update()

    def createMimeDataFromSelection(self):
        """Preserve raw separators when showing an escaped snapshot."""
        if self.projected and "pad" in self.kinds:
            cursor = self.textCursor()
            pieces = []
            block = self.document().findBlock(cursor.selectionStart())
            while block.isValid() and block.position() <= cursor.selectionEnd():
                row = block.blockNumber()
                if self.mapping[row] is not None:
                    part = QTextCursor(block)
                    part.setPosition(max(block.position(), cursor.selectionStart()))
                    part.setPosition(min(block.position() + block.length() - 1, cursor.selectionEnd()), QTextCursor.MoveMode.KeepAnchor)
                    pieces.append(part.selectedText())
                block = block.next()
            mime = QMimeData()
            mime.setText("\n".join(pieces))
            return mime
        if not self.raw_copy:
            return super().createMimeDataFromSelection()
        cursor = self.textCursor()
        start, end = cursor.selectionStart(), cursor.selectionEnd()
        mime = QMimeData()
        if start == 0 and end == self.document().characterCount() - 1:
            mime.setText(self.source)
            return mime
        parts = []
        block = self.document().findBlock(start)
        while block.isValid() and block.position() < end:
            row = block.blockNumber()
            original = self.mapping[row] if row < len(self.mapping) else None
            if original is not None:
                raw = line_content(self.lines[original]).encode("utf-16-le")
                a = max(0, start - block.position())
                b = min(block.length() - 1, end - block.position())
                parts.append(raw[a * 2:b * 2].decode("utf-16-le", errors="replace"))
                if end > block.position() + block.length() - 1 and self.lines[original].endswith("\n"):
                    parts.append("\r\n" if self.lines[original].endswith("\r\n") else "\n")
            block = block.next()
        mime.setText("".join(parts))
        return mime

    def copy(self):
        if self.textCursor().hasSelection():
            # Let QClipboard construct and own its MIME object. Passing a
            # Python-created MIME object through the native copy slot can
            # outlive its wrapper on Qt's offscreen platform at shutdown.
            QApplication.clipboard().setText(self.createMimeDataFromSelection().text())

    def keyPressEvent(self, event):
        for key, redo in ((QKeySequence.StandardKey.Undo, False), (QKeySequence.StandardKey.Redo, True)):
            if not self.isReadOnly() and event.matches(key):
                self.historyRequested.emit(redo)
                event.accept()
                return
        if event.matches(QKeySequence.StandardKey.Copy):
            self.copy()
            event.accept()
            return
        super().keyPressEvent(event)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        english = self.language == "en"
        action = menu.addAction("Copy" if english else "复制")
        action.setEnabled(self.textCursor().hasSelection())
        action.triggered.connect(self.copy)
        if not self.isReadOnly():
            menu.addAction("Undo" if english else "撤销", lambda: self.historyRequested.emit(False))
            menu.addAction("Redo" if english else "重做", lambda: self.historyRequested.emit(True))
            menu.addAction("Cut" if english else "剪切", self.cut).setEnabled(self.textCursor().hasSelection())
            menu.addAction("Paste" if english else "粘贴", self.paste).setEnabled(self.canPaste())
        menu.addAction("Select All" if english else "全选", self.selectAll)
        menu.exec(event.globalPos())


class RibbonHandle(QSplitterHandle):
    def paintEvent(self, event):
        super().paintEvent(event)
        view = self.splitter().view
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        width = self.width()
        for left, right, color in view.regions:
            ends = []
            for editor, (start, stop) in zip((view.left, view.right), (left, right)):
                offset = editor.viewport().mapToGlobal(editor.viewport().rect().topLeft()).y() - self.mapToGlobal(self.rect().topLeft()).y()
                ends.extend(offset + editor.boundary_y(i) for i in (start, stop))
            a, b, c, d = ends
            if max(ends) < 0 or min(ends) > self.height():
                continue
            path = QPainterPath()
            path.moveTo(0, a)
            path.cubicTo(width * .45, a, width * .55, c, width, c)
            path.lineTo(width, d)
            path.cubicTo(width * .55, d, width * .45, b, 0, b)
            path.closeSubpath()
            fill = QColor(color)
            fill.setAlpha(130)
            painter.fillPath(path, fill)


class RibbonSplitter(QSplitter):
    def createHandle(self):
        return RibbonHandle(self.orientation(), self)


class ToolbarRow(QLayout):
    """Lay out toolbar groups left to right, wrapping complete groups."""
    def __init__(self):
        super().__init__()
        self.items = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(8)

    def addItem(self, item):
        self.items.append(item)

    def count(self):
        return len(self.items)

    def itemAt(self, index):
        return self.items[index] if 0 <= index < self.count() else None

    def takeAt(self, index):
        return self.items.pop(index) if 0 <= index < self.count() else None

    def minimumSize(self):
        size = QSize(0, 0)
        for item in self.items:
            size = size.expandedTo(item.minimumSize())
        return size

    def sizeHint(self):
        sizes = [item.sizeHint() for item in self.items if not item.isEmpty()]
        return QSize(sum(s.width() for s in sizes) + self.spacing() * max(0, len(sizes)-1),
                     max((s.height() for s in sizes), default=0))

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._arrange(QRect(0, 0, width, 0), False)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._arrange(rect, True)

    def _arrange(self, rect, apply):
        x = y = height = 0
        for item in self.items:
            if item.isEmpty():
                continue
            size = item.sizeHint()
            if x and x + size.width() > rect.width():
                y += height + self.spacing()
                x = height = 0
            if apply:
                item.setGeometry(QRect(rect.x() + x, rect.y() + y, min(size.width(), rect.width()), size.height()))
            x += size.width() + self.spacing()
            height = max(height, size.height())
        return y + height


class ToolbarCombo(QComboBox):
    def sizeHint(self):
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        metrics = self.fontMetrics()
        content = QSize(metrics.horizontalAdvance(self.currentText()) + 4, metrics.height())
        return self.style().sizeFromContents(QStyle.ContentsType.CT_ComboBox, option, content, self)

    def minimumSizeHint(self):
        return self.sizeHint()

    def showPopup(self):
        self.view().setMinimumWidth(self.view().sizeHintForColumn(0) + 12)
        super().showPopup()


class SourceTitle(QToolButton):
    def display_text(self):
        return self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight,
                                             self.contentsRect().width())

    def link_rect(self):
        return self.fontMetrics().boundingRect(self.contentsRect(),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.display_text())

    def hitButton(self, pos):
        return self.link_rect().contains(pos)

    def mouseMoveEvent(self, event):
        shape = Qt.CursorShape.PointingHandCursor if self.hitButton(event.position().toPoint()) else Qt.CursorShape.ArrowCursor
        self.setCursor(shape)
        super().mouseMoveEvent(event)

    def event(self, event):
        if event.type() == QEvent.Type.ToolTip and not self.hitButton(event.pos()):
            event.ignore()
            return True
        return super().event(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        font = self.font()
        font.setUnderline(True)
        painter.setFont(font)
        painter.setPen(self.palette().color(QPalette.ColorRole.Link))
        painter.drawText(self.contentsRect(), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         self.display_text())
        if self.hasFocus():
            painter.drawRect(self.link_rect())


class CompareView(QWidget):
    textEdited = Signal(int, str)
    historyRequested = Signal(int, bool)
    swapRequested = Signal()

    def __init__(self, highlighter_factory, parent=None, *, highlight="words", viewer="side", align=False, sync=True, wrap=False):
        super().__init__(parent)
        self.language = "zh_CN"
        self.result = None
        self.request = 0
        self.job = None
        self.busy = False
        self.available = True
        self.error = ""
        self.snapshot = ("", "")
        self.hunk_index = -1
        self.jobs = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.toolbar = QWidget(self)
        toolbar_layout = QVBoxLayout(self.toolbar)
        toolbar_layout.setContentsMargins(20, 7, 20, 0)
        layout.addWidget(self.toolbar)
        controls = ToolbarRow()
        self.swap_button = QPushButton()
        self.previous_button = QPushButton()
        self.next_button = QPushButton()
        self.wrap_check = QCheckBox()
        self.wrap_check.setChecked(wrap)
        self.sync_check = QCheckBox()
        self.sync_check.setChecked(sync)
        self.ignore_combo = QComboBox()
        self.ignore_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        for mode in IgnoreMode:
            self.ignore_combo.addItem("", mode.value)
        self.align_check = QCheckBox()
        self.align_check.setChecked(align)
        buttons = QWidget()
        button_row = QHBoxLayout(buttons)
        button_row.setContentsMargins(0, 0, 4, 0)
        for button in (self.swap_button, self.previous_button, self.next_button):
            button_row.addWidget(button)
        controls.addWidget(buttons)
        for checkbox in (self.sync_check, self.wrap_check, self.align_check):
            controls.addWidget(checkbox)
        toolbar_layout.addLayout(controls)
        options = ToolbarRow()
        self.view_label, self.rule_label, self.highlight_label = QLabel(), QLabel(), QLabel()
        self.view_combo, self.highlight_combo = ToolbarCombo(), ToolbarCombo()
        for combo, choices, selected in ((self.view_combo, VIEWERS, viewer),
                                         (self.highlight_combo, HIGHLIGHTS, highlight)):
            for value, zh, en in choices:
                combo.addItem(zh, value)
            combo.setCurrentIndex(max(0, combo.findData(selected)))
        for label, combo in ((self.view_label, self.view_combo), (self.rule_label, self.ignore_combo),
                             (self.highlight_label, self.highlight_combo)):
            group = QWidget()
            fields = QHBoxLayout(group)
            fields.setContentsMargins(0, 0, 0, 0)
            fields.addWidget(label)
            fields.addWidget(combo)
            label.setBuddy(combo)
            combo.currentTextChanged.connect(combo.updateGeometry)
            options.addWidget(group)
        toolbar_layout.addLayout(options)
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.message)
        self.regions = []
        self.scroll_maps = {}
        self.splitter = RibbonSplitter()
        self.splitter.view = self
        self.splitter.setHandleWidth(16)
        self.left, self.right = DiffEditor("left"), DiffEditor("right")
        self.left.setAccessibleName("Left comparison text")
        self.right.setAccessibleName("Right comparison text")
        self.titles = []
        for editor in (self.left, self.right):
            pane = QWidget()
            column = QVBoxLayout(pane)
            column.setContentsMargins(0, 0, 0, 0)
            title = SourceTitle()
            title.setAutoRaise(True)
            title.setMouseTracking(True)
            title.setContentsMargins(6, 2, 6, 2)
            title.setMinimumWidth(0)
            title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            column.addWidget(title)
            column.addWidget(editor)
            editor.highlighter = highlighter_factory(editor.document())
            self.titles.append(title)
            self.splitter.addWidget(pane)
        for editor in (self.left, self.right):
            editor.updateRequest.connect(lambda *_: self.splitter.handle(1).update())
        self.splitter.setSizes([500, 500])
        layout.addWidget(self.splitter, 1)
        self.left.verticalScrollBar().valueChanged.connect(lambda value: self.sync_scroll(self.right, value))
        self.right.verticalScrollBar().valueChanged.connect(lambda value: self.sync_scroll(self.left, value))
        self._syncing = False
        self.left.horizontalScrollBar().valueChanged.connect(
            lambda value: self.sync_scroll(self.right, value, horizontal=True))
        self.right.horizontalScrollBar().valueChanged.connect(
            lambda value: self.sync_scroll(self.left, value, horizontal=True))
        self.sync_check.toggled.connect(self.sync_from_left)
        self.ignore_combo.currentIndexChanged.connect(self._ignore_changed)
        self.mirroring = False
        self.recalculate = QTimer(self)
        self.recalculate.setSingleShot(True)
        self.recalculate.setInterval(150)
        self.recalculate.timeout.connect(self.launch_diff)
        for side, editor in enumerate((self.left, self.right)):
            editor.document().contentsChange.connect(lambda p, r, a, side=side: self.projection_edit(side, p, r, a))
            editor.textChanged.connect(lambda side=side: self.pane_edited(side))
            editor.historyRequested.connect(lambda redo, side=side: self.historyRequested.emit(side, redo))
        self.swap_button.clicked.connect(self.swapRequested)
        self.previous_button.clicked.connect(lambda: self.navigate(-1))
        self.next_button.clicked.connect(lambda: self.navigate(1))
        self.view_combo.currentIndexChanged.connect(self.change_layout)
        self.align_check.toggled.connect(self.change_layout)
        self.highlight_combo.currentIndexChanged.connect(self._ignore_changed)
        self.wrap_check.toggled.connect(self.set_soft_wrap)
        self.set_soft_wrap(wrap)
        self.change_layout()
        self.retranslate("zh_CN")

    def tr(self, zh, en):
        return en if self.language == "en" else zh

    def retranslate(self, language):
        self.language = language
        self.sync_check.setToolTip(self.tr("同步左右两侧的水平和垂直滚动",
                                           "Synchronize horizontal and vertical scrolling between panes"))
        self.view_label.setText(self.tr("视图模式", "View Mode"))
        self.rule_label.setText(self.tr("忽略差异", "Ignore Differences"))
        self.left.language = self.right.language = language
        for button, zh, en in (
            (self.swap_button, "交换左右", "Swap sides"),
            (self.previous_button, "上一处", "Previous"),
            (self.next_button, "下一处", "Next"),
            (self.wrap_check, "自动换行", "Soft wrap"),
            (self.sync_check, "同步滚动", "Sync scrolling"),
        ):
            button.setText(self.tr(zh, en))
        for editor in (self.left, self.right):
            editor.setToolTip(self.tr("显示原文行号，细线标记另一侧的变化位置。",
                                     "Original line numbers. Thin lines mark changes on the opposite side."))
        labels = (
            ("不忽略", "None"),
            ("忽略行首行尾空白", "Leading and trailing whitespace"),
            ("忽略行首行尾空白及空行", "Leading/trailing whitespace and blank lines"),
        )
        for index, (zh, en) in enumerate(labels):
            self.ignore_combo.setItemText(index, self.tr(zh, en))
        tips = (
            ("比较空行，以及行首、行尾和行中的所有空格和 Tab。", "Compare empty lines and all leading, trailing and internal spaces and tabs."),
            ("忽略行首行尾的空格和 Tab。保留空行及行中的空格和 Tab。", "Ignore leading and trailing spaces and tabs. Keep empty lines and internal spaces and tabs."),
            ("忽略空行、纯空白行及行首行尾空白。保留行中的空格和 Tab。", "Ignore blank lines and leading/trailing spaces and tabs. Keep internal spaces and tabs."),
        )
        for index, tip in enumerate(tips):
            self.ignore_combo.setItemData(index, self.tr(*tip), Qt.ItemDataRole.ToolTipRole)
        self.update_ignore_tip()
        self.highlight_label.setText(self.tr("差异高亮", "Highlight Differences"))
        self.align_check.setText(self.tr("对齐差异", "Align changes"))
        for combo, choices in ((self.view_combo, VIEWERS), (self.highlight_combo, HIGHLIGHTS)):
            for i, (_, zh, en) in enumerate(choices):
                combo.setItemText(i, self.tr(zh, en))
        self.update_message()

    @property
    def ignore_mode(self):
        return IgnoreMode(self.ignore_combo.currentData())

    def update_ignore_tip(self):
        self.ignore_combo.setToolTip(self.ignore_combo.currentData(Qt.ItemDataRole.ToolTipRole) or "")

    def _ignore_changed(self, *_):
        self.update_ignore_tip()
        self.start(*self.snapshot)

    def swap_snapshot(self):
        self.start(*self.snapshot[::-1])

    def dispose(self):
        self.recalculate.stop()
        self.request += 1
        for job in self.jobs.values():
            job.cancelled = True

    def set_title_font_size(self, size):
        for title in self.titles:
            font = title.font()
            font.setPointSize(size)
            title.setFont(font)

    def set_appearance(self, theme, point_size):
        dark = theme == "dark"
        for editor in (self.left, self.right):
            editor.dark = dark
            font = QFont("JetBrains Mono")
            font.setStyleHint(QFont.StyleHint.Monospace)
            font.setPointSize(point_size)
            editor.setFont(font)
            editor.setTabStopDistance(editor.fontMetrics().horizontalAdvance("  "))
            editor.setStyleSheet("QPlainTextEdit {background: %s; color: %s; border: 1px solid %s;}" % (
                "#0E1728" if dark else "#FFFFFF", "#F1F5F9" if dark else "#1E293B",
                "#334155" if dark else "#CBD5E1"))
            editor.highlighter.set_theme(theme)
            editor.update_gutter()
            editor.decorate()

    def set_soft_wrap(self, enabled):
        self.layout().activate()
        panes = (self.left, self.right)
        anchors = [(pane, pane.cursorForPosition(QPoint(0, round(pane.document().documentMargin())))) for pane in panes
                   if pane.isVisible()]
        self._syncing = True
        try:
            for pane in panes:
                pane.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth if enabled else QPlainTextEdit.LineWrapMode.NoWrap)
                pane.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff if enabled else Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
                pane.update_gutter()
                pane.viewport().update()
            for pane, anchor in anchors:
                block = anchor.block()
                pane.blockBoundingRect(block)
                line = block.layout().lineForTextPosition(anchor.positionInBlock())
                if line.isValid():
                    pane.verticalScrollBar().setValue(block.firstLineNumber() + line.lineNumber())
                    if not enabled:
                        bar = pane.horizontalScrollBar()
                        bar.setValue(bar.value() + pane.cursorRect(anchor).left() - round(pane.document().documentMargin()))
        finally:
            self._syncing = False
        self.splitter.handle(1).update()

    def sync_from_left(self, enabled):
        if enabled:
            self.sync_scroll(self.right, self.left.verticalScrollBar().value())
            self.sync_scroll(self.right, self.left.horizontalScrollBar().value(), horizontal=True)

    def sync_scroll(self, target, value, *, horizontal=False):
        if not self.unified and self.sync_check.isChecked() and not self._syncing:
            self._syncing = True
            source = self.right if target is self.left else self.left
            source_bar = source.horizontalScrollBar() if horizontal else source.verticalScrollBar()
            scrollbar = target.horizontalScrollBar() if horizontal else target.verticalScrollBar()
            if value == source_bar.minimum():
                value = scrollbar.minimum()
            elif value == source_bar.maximum():
                value = scrollbar.maximum()
            elif not horizontal:
                wrapped = self.wrap_check.isChecked()
                if wrapped:
                    block = source.firstVisibleBlock()
                    value = block.blockNumber() + (value - block.firstLineNumber()) / max(1, block.lineCount())
                if target in self.scroll_maps:
                    xs, ys = self.scroll_maps[target]
                    i = max(0, bisect_right(xs, value) - 1)
                    mapped = ys[i]
                    if i + 1 < len(xs):
                        mapped += (value - xs[i]) * (ys[i+1] - ys[i]) / (xs[i+1] - xs[i])
                    value = mapped
                if wrapped:
                    row = min(max(0, int(value)), target.blockCount() - 1)
                    block = target.document().findBlockByNumber(row)
                    target.blockBoundingRect(block)
                    value = block.firstLineNumber() + (value - row) * block.lineCount()
                value = round(value)
            scrollbar.setValue(value)
            self._syncing = False

    @property
    def unified(self):
        return self.view_combo.currentData() == "unified"

    @property
    def primary_editor(self):
        return self.right if self.unified else self.left

    def change_layout(self, *_):
        self.splitter.widget(0).setVisible(not self.unified)
        self.splitter.refresh()
        self.sync_check.setVisible(not self.unified)
        self.align_check.setVisible(not self.unified)
        if self.result is not None:
            self.render_result()

    def projection_edit(self, side, position, removed, added):
        pane = (self.left, self.right)[side]
        if self.mirroring or not pane.projected:
            return
        old = pane.projection_text.encode("utf-16-le")
        current = pane.toPlainText()
        if current == pane.projection_text:
            return
        prefix = old[:2 * position].decode("utf-16-le")
        replaced = old[2 * position:2 * (position + removed)].decode("utf-16-le")
        first = prefix.count("\n")
        last = first + replaced.count("\n")
        ids = pane.mapping[first:last + 1]
        valid = ids and all(i is not None for i in ids) and not pane.isReadOnly()
        if not valid:
            self.mirroring = True
            try:
                update_document_text(pane, current, pane.projection_text)
                cursor = pane.textCursor()
                cursor.setPosition(min(position, pane.document().characterCount()-1))
                pane.setTextCursor(cursor)
            finally:
                self.mirroring = False
            return
        insert = current.encode("utf-16-le")[2 * position:2 * (position + added)].decode("utf-16-le")
        lines = self.snapshot[side].split("\n")
        start = sum(len(line) + 1 for line in lines[:ids[0]]) + len(prefix.rsplit("\n", 1)[-1])
        text = self.snapshot[side]
        text = text[:start] + insert + text[start + len(replaced):]
        delta = insert.count("\n") - (last-first)
        pane.mapping = (list(pane.mapping[:first]) + list(range(ids[0], ids[0]+insert.count("\n")+1))
                        + [None if i is None else i + delta for i in pane.mapping[last+1:]])
        pane.kinds[first:last+1] = [""] * (insert.count("\n")+1)
        pane.labels = []
        pane.projection_text = current
        pane.source = text
        self.textEdited.emit(side, text)

    def render_result(self):
        result = self.result
        self.mirroring = self._syncing = True
        try:
            self.regions.clear()
            self.scroll_maps.clear()
            self.left.boundaries = {}
            self.right.boundaries = {}
            for side, pane in enumerate((self.left, self.right)):
                projected = (self.unified and side == 1) or (not self.unified and self.align_check.isChecked())
                if not projected and not pane.projected:
                    pane.set_result(result, update_text=False)
                    continue
                source_lines = (result.left_lines, result.right_lines)[side]
                old_row = pane.textCursor().blockNumber()
                old_col = pane.textCursor().positionInBlock()
                anchor = pane.mapping[old_row] if old_row < len(pane.mapping) else None
                old_top = pane.firstVisibleBlock().blockNumber()
                top = pane.mapping[old_top] if old_top < len(pane.mapping) else None
                entries = []
                for row in result.rows:
                    li, ri = row.left, row.right
                    if self.unified and side == 1:
                        if li is not None and (row.changed or ri is None):
                            entries.append((None, result.left_lines[li], f"{li+1}   −" if row.changed else f"{li+1}   ", "-" if row.changed else "", row.left_spans))
                        if ri is not None:
                            sign = "+" if row.changed else ""
                            number = str(li+1) if li is not None and not row.changed else ""
                            entries.append((ri, result.right_lines[ri], f"{number}  {ri+1} {sign}", sign, row.right_spans))
                    else:
                        i = (li, ri)[side]
                        if i is not None:
                            entries.append((i, source_lines[i], str(i+1), ("-", "+")[side] if row.changed else "", (row.left_spans,row.right_spans)[side]))
                        elif projected:
                            entries.append((None, "", "", "pad", ()))
                display = "\n".join(line_content(e[1]) for e in entries)
                different = pane.toPlainText() != display
                pane.projected = projected
                pane.projection_text = display
                pane.mapping = [e[0] for e in entries]
                pane.labels = [e[2] for e in entries] if projected else []
                pane.kinds = [e[3] for e in entries]
                pane.spans = {i:e[4] for i,e in enumerate(entries) if e[3]}
                pane.changed_rows = set(pane.spans)
                pane.lines = source_lines
                pane.source = self.snapshot[side]
                if different:
                    pane.setPlainText(display)
                    if anchor in pane.mapping and anchor is not None:
                        block = pane.document().findBlockByNumber(pane.mapping.index(anchor))
                        cursor = QTextCursor(block)
                        cursor.setPosition(block.position()+min(old_col,block.length()-1))
                        pane.setTextCursor(cursor)
                    if top is not None and top in pane.mapping:
                        block = pane.document().findBlockByNumber(pane.mapping.index(top))
                        pane.verticalScrollBar().setValue(block.firstLineNumber())
                pane.update_gutter()
                pane.decorate()
            self.build_regions(result)
            if self.align_check.isChecked() and not self.unified:
                self.scroll_maps.clear()
                self.regions.clear()
                for i,row in enumerate(result.rows):
                    if row.changed:
                        self.regions.append(((i,i+1),(i,i+1),"#6798cf"))
                self.left.boundaries = self.right.boundaries = {}
        finally:
            self.mirroring = self._syncing = False

    def pane_edited(self, side):
        if self.mirroring:
            return
        editor = (self.left, self.right)[side]
        if editor.projected:
            return
        text = editor.toPlainText()
        if text != self.snapshot[side] and not editor.isReadOnly():
            self.textEdited.emit(side, text)

    def update_source(self, side, text):
        if self.snapshot[side] == text:
            return
        texts = list(self.snapshot)
        texts[side] = text
        self.start(*texts, debounce=True)

    def start(self, left, right, *, debounce=False):
        initial = self.request == 0
        self.recalculate.stop()
        if self.job is not None:
            self.job.cancelled = True
        self.request += 1
        self.snapshot = (left, right)
        self.busy, self.error = True, ""
        self.hunk_index = -1
        self.result = None
        self.regions.clear()
        self.scroll_maps.clear()
        self.mirroring = True
        self._syncing = True
        try:
            for editor, text in zip((self.left, self.right), self.snapshot):
                if editor.projected and editor.source == text:
                    continue
                editor.projected = False
                editor.labels = []
                editor.kinds = []
                previous = editor.toPlainText()
                if previous != text:
                    vertical = editor.verticalScrollBar().value()
                    horizontal = editor.horizontalScrollBar().value()
                    update_document_text(editor, previous, text)
                    if initial:
                        editor.moveCursor(QTextCursor.MoveOperation.Start)
                    editor.verticalScrollBar().setValue(vertical)
                    editor.horizontalScrollBar().setValue(horizontal)
                editor.source = text
                editor.raw_copy = False
                editor.mapping = range(editor.blockCount())
                editor.changed_rows.clear()
                editor.spans.clear()
                editor.boundaries = {}
                editor.setExtraSelections([])
                editor.update_gutter()
                editor.viewport().update()
        finally:
            self._syncing = False
            self.mirroring = False
        self.splitter.handle(1).update()
        self.update_message()
        if debounce:
            self.recalculate.start()
        else:
            self.launch_diff()

    def launch_diff(self):
        job = DiffJob(self.request, *self.snapshot, self.ignore_mode, self.highlight_combo.currentData())
        self.job = job
        self.jobs[self.request] = job
        job.signals.finished.connect(self.receive_result)
        QThreadPool.globalInstance().start(job)

    def receive_result(self, request, result, error):
        self.jobs.pop(request, None)
        if request != self.request:
            return
        self.job = None
        self.busy = False
        self.error = error
        self.result = result
        if result is not None:
            self.left.boundaries = {}
            self.right.boundaries = {}
            self.render_result()
            if result.hunks:
                self.hunk_index = min(range(len(self.navigation)), key=lambda i: abs(
                    self.navigation[i][1 if self.right.hasFocus() else 0] -
                    (self.right if self.right.hasFocus() else self.left).textCursor().blockNumber()))
        self.update_message()

    def build_regions(self, result):
        left = right = 0
        anchors = [(0, 0)]
        self.navigation = []
        starts = set(result.hunks)
        region_start = None
        for index, row in enumerate(result.rows):
            if region_start is not None and (not row.changed or index in starts):
                self.add_region(region_start, (left, right))
                region_start = None
            if index in starts:
                self.navigation.append((left, right))
            if row.changed and region_start is None:
                region_start = (left, right)
            if not row.changed and row.left is not None and row.right is not None:
                anchors.extend(((left, right), (left + 1, right + 1)))
            left += row.left is not None
            right += row.right is not None
        if region_start is not None:
            self.add_region(region_start, (left, right))
        anchors.append((left, right))
        for editor, reverse in ((self.right, False), (self.left, True)):
            pairs = dict((b, a) if reverse else (a, b) for a, b in anchors)
            self.scroll_maps[editor] = list(pairs), list(pairs.values())
        self.splitter.handle(1).update()

    def add_region(self, start, end):
        left, right = zip(start, end)
        color = "#6798cf"
        if left[0] == left[1]:
            color = "#36a969"
            self.left.boundaries[left[0]] = color
        elif right[0] == right[1]:
            color = "#dc6069"
            self.right.boundaries[right[0]] = color
        self.regions.append((left, right, color))

    def update_message(self):
        if self.busy:
            text = self.tr("正在计算差异…", "Calculating differences…")
        elif self.error:
            text = self.tr("对比失败：", "Comparison failed: ") + self.error
        elif self.result is not None and self.result.hunks:
            text = self.tr("差异块", "Change") + f" {self.hunk_index + 1} / {len(self.result.hunks)}"
        else:
            text = (self.tr("没有差异", "No differences") if self.ignore_mode == IgnoreMode.NONE else
                    self.tr("按当前规则没有差异", "No differences under current rules"))
        if not self.available:
            text += self.tr(" · 来源已关闭，保留当前快照", " · Source closed; showing snapshot")
        self.message.setText(text)
        self.swap_button.setEnabled(True)
        navigable = not self.busy and self.result is not None and bool(self.result.hunks)
        self.previous_button.setEnabled(navigable)
        self.next_button.setEnabled(navigable)

    def navigate(self, direction):
        if not self.result or not self.result.hunks:
            return
        self.hunk_index = ((0 if direction > 0 else len(self.result.hunks) - 1)
                           if self.hunk_index < 0 else (self.hunk_index + direction) % len(self.result.hunks))
        self._syncing = True
        for editor, row in zip((self.left, self.right), self.navigation[self.hunk_index]):
            if editor.projected and row in editor.mapping:
                row = editor.mapping.index(row)
            row = min(row, editor.blockCount() - 1)
            cursor = QTextCursor(editor.document().findBlockByNumber(row))
            editor.setTextCursor(cursor)
            editor.centerCursor()
        self._syncing = False
        self.update_message()
