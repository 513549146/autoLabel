"""Qt 通用布局、控件和窗口外壳；图标与主题分别见 icons.py、theme.py。"""

from __future__ import annotations

import os
import random
import shutil
import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, QTimer, Qt, QSize, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QIcon,
    QImage,
    QImageReader,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QStackedWidget,
    QStyle,
    QToolButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QLayout,
)

import config
import core
from .icons import ROOT_DIR, asset_icon, sprite_icon, workflow_nav_icon
from .theme import COLORS, apply_app_style

from project import (
    ANNOTATION_VERSION_DIR,
    STATUS_APPROVED,
    STATUS_NEEDS_REVIEW,
    STATUS_SKIPPED,
    STATUS_UNREVIEWED,
    ProjectStore,
    WorkspaceStore,
    create_project_manifest,
    load_project_manifest,
    resolve_project_directory,
)


def load_oriented_pixmap(path: str) -> QPixmap:
    """Decode JPEG EXIF orientation before annotation coordinates are displayed."""
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    return QPixmap.fromImage(reader.read())


class FlowLayout(QLayout):
    """A compact wrapping layout for text-sized project chips."""

    def __init__(self, parent=None, spacing=5):
        super().__init__(parent)
        self._items = []
        self.setContentsMargins(0, 2, 0, 2)
        self.setSpacing(spacing)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._layout_items(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._layout_items(rect, False)

    def sizeHint(self):
        return QSize(0, self.heightForWidth(232))

    def minimumSize(self):
        return QSize(0, self.heightForWidth(120))

    def _layout_items(self, rect, test_only):
        left, top, right, bottom = self.getContentsMargins()
        area = rect.adjusted(left, top, -right, -bottom)
        x, y, line_height = area.x(), area.y(), 0
        spacing = self.spacing()
        for item in self._items:
            hint = item.sizeHint()
            if x > area.x() and x + hint.width() > area.right() + 1:
                x, y, line_height = area.x(), y + line_height + spacing, 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + spacing
            line_height = max(line_height, hint.height())
        return max(0, y + line_height - rect.y() + bottom)


def card() -> QFrame:
    frame = QFrame()
    frame.setObjectName("card")
    return frame


def label(text: str, object_name: str | None = None) -> QLabel:
    item = QLabel(text)
    if object_name:
        item.setObjectName(object_name)
    return item


def button(text: str, primary=False) -> QPushButton:
    item = QPushButton(text)
    item.setObjectName("primary" if primary else "secondary")
    item.setCursor(Qt.PointingHandCursor)
    return item


def destructive_button(text: str) -> QPushButton:
    item = QPushButton(text)
    item.setObjectName("destructive")
    item.setCursor(Qt.PointingHandCursor)
    return item


def _ios_dialog(parent, title: str, message: str):
    """Create a compact, titlebar-free alert surface inspired by iOS dialogs."""
    dialog = QDialog(parent)
    dialog.setObjectName("iosDialog")
    dialog.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
    dialog.setModal(True)
    dialog.setAttribute(Qt.WA_TranslucentBackground)
    dialog.setMinimumWidth(390)
    outer = QVBoxLayout(dialog)
    outer.setContentsMargins(14, 14, 14, 14)
    panel = QFrame()
    panel.setObjectName("iosDialogPanel")
    outer.addWidget(panel)
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(28, 25, 28, 20)
    layout.setSpacing(10)
    heading = label(title, "iosDialogTitle")
    heading.setAlignment(Qt.AlignHCenter)
    heading.setWordWrap(True)
    layout.addWidget(heading)
    copy = label(message, "iosDialogMessage")
    copy.setAlignment(Qt.AlignHCenter)
    copy.setWordWrap(True)
    layout.addWidget(copy)
    return dialog, layout


def ios_alert(parent, title: str, message: str, confirm_text="好", cancel_text=None, destructive=False):
    dialog, layout = _ios_dialog(parent, title, message)
    layout.addSpacing(8)
    actions = QVBoxLayout()
    actions.setSpacing(7)
    if cancel_text:
        cancel = QPushButton(cancel_text)
        cancel.setObjectName("iosDialogSecondary")
        cancel.clicked.connect(dialog.reject)
        actions.addWidget(cancel)
    confirm = QPushButton(confirm_text)
    confirm.setObjectName("iosDialogDestructive" if destructive else "iosDialogPrimary")
    confirm.clicked.connect(dialog.accept)
    actions.addWidget(confirm)
    layout.addLayout(actions)
    return dialog.exec() == QDialog.DialogCode.Accepted


def ios_text_input(parent, title: str, prompt: str, text="", placeholder="", confirm_text="完成"):
    dialog, layout = _ios_dialog(parent, title, prompt)
    field = QLineEdit(text)
    field.setPlaceholderText(placeholder)
    layout.addWidget(field)
    layout.addSpacing(6)
    actions = QHBoxLayout()
    actions.setSpacing(8)
    cancel = QPushButton("取消")
    cancel.setObjectName("iosDialogSecondary")
    cancel.clicked.connect(dialog.reject)
    confirm = QPushButton(confirm_text)
    confirm.setObjectName("iosDialogPrimary")
    confirm.clicked.connect(dialog.accept)
    actions.addWidget(cancel)
    actions.addWidget(confirm)
    layout.addLayout(actions)
    QTimer.singleShot(0, field.setFocus)
    accepted = dialog.exec() == QDialog.DialogCode.Accepted
    return field.text(), accepted


def ios_item_input(parent, title: str, prompt: str, items, current=0, editable=False, confirm_text="完成"):
    dialog, layout = _ios_dialog(parent, title, prompt)
    field = FlatComboBox()
    field.addItems([str(item) for item in items])
    field.setEditable(editable)
    if field.count():
        field.setCurrentIndex(max(0, min(field.count() - 1, current)))
    layout.addWidget(field)
    layout.addSpacing(6)
    actions = QHBoxLayout()
    actions.setSpacing(8)
    cancel = QPushButton("取消")
    cancel.setObjectName("iosDialogSecondary")
    cancel.clicked.connect(dialog.reject)
    confirm = QPushButton(confirm_text)
    confirm.setObjectName("iosDialogPrimary")
    confirm.clicked.connect(dialog.accept)
    actions.addWidget(cancel)
    actions.addWidget(confirm)
    layout.addLayout(actions)
    QTimer.singleShot(0, field.setFocus)
    accepted = dialog.exec() == QDialog.DialogCode.Accepted
    return field.currentText(), accepted


def tool(parent: QWidget, icon: QIcon, tip: str, checkable=False, icon_size=24) -> QToolButton:
    item = QToolButton(parent)
    item.setObjectName("tool")
    item.setIcon(icon)
    item.setIconSize(QSize(icon_size, icon_size))
    # Every toolbar target uses the same 40 px hit area, so an icon's source
    # aspect ratio can never shift its visual position or the group spacing.
    item.setFixedSize(40, 40)
    item.setToolTip(tip)
    item.setCheckable(checkable)
    item.setCursor(Qt.PointingHandCursor)
    return item


class DoubleChevronButton(QToolButton):
    """A restrained, iOS-like double chevron for collapsible inspector sections."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._collapsed = False
        self.setFixedSize(34, 18)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("收起类别筛选")

    def set_collapsed(self, collapsed):
        self._collapsed = bool(collapsed)
        self.setToolTip("展开类别筛选" if self._collapsed else "收起类别筛选")
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if self.underMouse():
            painter.fillRect(self.rect(), QColor("#F0F6FF"))
        pen = QPen(QColor("#0A74FF" if self.underMouse() else "#7B8491"), 1.6)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        for center_y in (6, 12):
            tip_y = center_y + 2.5 if self._collapsed else center_y - 2.5
            base_y = center_y - 1.5 if self._collapsed else center_y + 1.5
            painter.drawLine(QPointF(11, base_y), QPointF(17, tip_y))
            painter.drawLine(QPointF(17, tip_y), QPointF(23, base_y))
        painter.end()


class FlatComboBox(QComboBox):
    """Flat selector chrome with an intentionally visible, quiet chevron."""

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor("#737780"), 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        x, y = self.width() - 18, self.height() // 2 - 2
        painter.drawLine(x, y, x + 4, y + 4)
        painter.drawLine(x + 4, y + 4, x + 8, y)


class WindowChrome(QFrame):
    """A compact desktop title bar: colored workspace dots on the left, controls on the right."""

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self._drag_origin = None
        self.setFixedHeight(38)
        self.setObjectName("windowbar")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 0, 10, 0)
        layout.setSpacing(8)
        for color in ("#FF5F57", "#FEBC2E", "#28C840"):
            dot = QFrame()
            dot.setFixedSize(11, 11)
            dot.setStyleSheet(f"background:{color}; border-radius:5px;")
            layout.addWidget(dot)
        layout.addStretch()
        for caption, action, tip in (
            ("−", window.showMinimized, "最小化"),
            ("□", window.toggle_maximized, "最大化"),
            ("×", window.close, "关闭"),
        ):
            control = QToolButton()
            control.setText(caption)
            control.setToolTip(tip)
            control.setFixedSize(34, 30)
            control.setCursor(Qt.PointingHandCursor)
            control.setStyleSheet(
                "QToolButton {border:0;border-radius:8px;color:#3E424A;font-size:18px;} QToolButton:hover {background:#EEF1F5;} QToolButton:pressed {background:#E2E7ED;}"
            )
            control.clicked.connect(action)
            layout.addWidget(control)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and not self.window.isMaximized():
            self._drag_origin = event.globalPosition().toPoint() - self.window.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_origin is not None and event.buttons() & Qt.LeftButton and not self.window.isMaximized():
            self.window.move(event.globalPosition().toPoint() - self._drag_origin)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_origin = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.window.toggle_maximized()
        super().mouseDoubleClickEvent(event)


class ResizableRoot(QWidget):
    """Six-pixel resize gutter for the frameless workbench window."""

    EDGE = 7

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self._edges = (False, False, False, False)
        self._start_global = None
        self._start_geometry = None
        self.setMouseTracking(True)

    def track_descendants(self):
        """Keep the resize cursor in sync while the pointer crosses child widgets."""
        self.installEventFilter(self)
        for child in self.findChildren(QWidget):
            child.setMouseTracking(True)
            child.installEventFilter(self)

    def _edges_at(self, position):
        return (
            position.x() <= self.EDGE,
            position.x() >= self.width() - self.EDGE,
            position.y() <= self.EDGE,
            position.y() >= self.height() - self.EDGE,
        )

    def _set_resize_cursor(self, edges):
        left, right, top, bottom = edges
        if (left and top) or (right and bottom):
            self.setCursor(Qt.SizeFDiagCursor)
        elif (right and top) or (left and bottom):
            self.setCursor(Qt.SizeBDiagCursor)
        elif left or right:
            self.setCursor(Qt.SizeHorCursor)
        elif top or bottom:
            self.setCursor(Qt.SizeVerCursor)
        else:
            self.unsetCursor()

    def mousePressEvent(self, event):
        edges = self._edges_at(event.position().toPoint())
        if event.button() == Qt.LeftButton and any(edges) and not self.window.isMaximized():
            self._edges = edges
            self._start_global = event.globalPosition().toPoint()
            self._start_geometry = self.window.geometry()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._start_global is None:
            self._set_resize_cursor(self._edges_at(event.position().toPoint()))
            super().mouseMoveEvent(event)
            return
        delta = event.globalPosition().toPoint() - self._start_global
        left, right, top, bottom = self._edges
        rect = self._start_geometry
        min_width, min_height = self.window.minimumWidth(), self.window.minimumHeight()
        x, y, width, height = rect.x(), rect.y(), rect.width(), rect.height()
        if left:
            x, width = x + delta.x(), width - delta.x()
        if right:
            width += delta.x()
        if top:
            y, height = y + delta.y(), height - delta.y()
        if bottom:
            height += delta.y()
        if width < min_width:
            if left:
                x -= min_width - width
            width = min_width
        if height < min_height:
            if top:
                y -= min_height - height
            height = min_height
        self.window.setGeometry(x, y, width, height)
        event.accept()

    def eventFilter(self, watched, event):
        if self._start_global is None and event.type() == QEvent.MouseMove:
            # Mouse moves over the sidebar/page child widgets do not reach the root.
            # Resolve the edge from the global position so a stale resize cursor is
            # always reset immediately after leaving the seven-pixel gutter.
            point = self.mapFromGlobal(event.globalPosition().toPoint())
            self._set_resize_cursor(self._edges_at(point))
        elif self._start_global is None and event.type() == QEvent.Leave:
            point = self.mapFromGlobal(self.cursor().pos())
            if not self.rect().contains(point):
                self.unsetCursor()
        return super().eventFilter(watched, event)

    def mouseReleaseEvent(self, event):
        self._edges = (False, False, False, False)
        self._start_global = self._start_geometry = None
        self.unsetCursor()
        super().mouseReleaseEvent(event)


class SidebarItem(QToolButton):
    def __init__(self, title: str, icon: QIcon, parent):
        super().__init__(parent)
        self.title, self.count = title, ""
        self.setObjectName("nav")
        self.setCheckable(True)
        self.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.setIcon(icon)
        self.setIconSize(QSize(22, 22))
        self.setMinimumHeight(52)
        self.setCursor(Qt.PointingHandCursor)
        self._render()

    def set_count(self, count: str):
        self.count = count
        self._render()

    def _render(self):
        self.setText(f"{self.title}        {self.count}" if self.count else self.title)
