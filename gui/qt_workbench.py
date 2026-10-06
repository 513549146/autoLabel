"""A clean-room Qt workbench.  It intentionally does not instantiate the legacy Tk pages."""
from __future__ import annotations

import os
import random
import shutil
import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QEvent, QPointF, QRectF, QTimer, Qt, QSize, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QImage, QKeySequence, QPainter, QPainterPath, QPen, QPixmap, QPolygonF, QShortcut
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QMainWindow, QMenu, QPlainTextEdit, QProgressBar,
    QPushButton, QScrollArea, QSizePolicy, QSlider, QStackedWidget, QStyle, QToolButton,
    QVBoxLayout, QWidget,
)

import config
import core
from project import (
    ANNOTATION_VERSION_DIR, STATUS_APPROVED, STATUS_NEEDS_REVIEW, STATUS_SKIPPED, STATUS_UNREVIEWED,
    ProjectStore, WorkspaceStore, create_project_manifest, load_project_manifest, resolve_project_directory,
)


COLORS = {
    "canvas": "#F6F7F9", "surface": "#FFFFFF", "line": "#E3E5E9", "ink": "#1D1D1F",
    "muted": "#737780", "blue": "#0A74FF", "blue_soft": "#EAF3FF", "track": "#ECEEF2",
}
ROOT_DIR = Path(__file__).resolve().parent.parent
_ICON_CACHE: dict[tuple[str, bool, int], QIcon] = {}
_ASSET_ICON_CACHE: dict[tuple[str, int, float], QIcon] = {}


def sprite_icon(name: str, nav=False, size=24) -> QIcon:
    """Use the existing generated line-art as transparent Qt icons, never themed 3D glyphs."""
    key = (name, nav, size)
    if key in _ICON_CACHE:
        return _ICON_CACHE[key]
    path = ROOT_DIR / "assets" / ("nav-icons-v1.png" if nav else "ui-icons-v3.png")
    positions = ({"auto": (0, 0), "review": (1, 0), "approved": (0, 1), "export": (1, 1)} if nav else
                 {"back": (0, 0), "forward": (1, 0), "pan": (2, 0), "select": (3, 0), "box": (0, 1), "undo": (1, 1), "redo": (2, 1), "delete": (3, 1)})
    image = QImage(str(path))
    if image.isNull() or name not in positions:
        return QIcon()
    columns, rows = (2, 2) if nav else (4, 2)
    cell_w, cell_h = image.width() // columns, image.height() // rows
    col, row = positions[name]
    cell = image.copy(col * cell_w, row * cell_h, cell_w, cell_h).convertToFormat(QImage.Format_ARGB32)
    left, top, right, bottom = cell.width(), cell.height(), -1, -1
    for y in range(cell.height()):
        for x in range(cell.width()):
            color = cell.pixelColor(x, y)
            # Generated sheets have a white matte. Remove it and retain only line art.
            if min(color.red(), color.green(), color.blue()) > 238:
                color.setAlpha(0)
                cell.setPixelColor(x, y, color)
            else:
                left, top, right, bottom = min(left, x), min(top, y), max(right, x), max(bottom, y)
    if right >= left:
        margin = 22
        left, top = max(0, left - margin), max(0, top - margin)
        right, bottom = min(cell.width(), right + margin + 1), min(cell.height(), bottom + margin + 1)
        cell = cell.copy(left, top, right - left, bottom - top)
    # Render at a higher device-pixel ratio.  This keeps icons from the sprite
    # sheet as crisp as the ImageGen assets on scaled Windows displays.
    scale = 3
    pixmap = QPixmap.fromImage(cell).scaled(size * scale, size * scale, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    pixmap.setDevicePixelRatio(scale)
    result = QIcon(pixmap)
    _ICON_CACHE[key] = result
    return result


def asset_icon(filename: str, size=24, glyph_scale=1.0) -> QIcon:
    """Crop transparent ImageGen padding and normalize the icon to toolbar ink."""
    key = (filename, size, glyph_scale)
    if key in _ASSET_ICON_CACHE:
        return _ASSET_ICON_CACHE[key]
    image = QImage(str(ROOT_DIR / "assets" / filename)).convertToFormat(QImage.Format_ARGB32)
    if image.isNull():
        return QIcon()
    left, top, right, bottom = image.width(), image.height(), -1, -1
    for y in range(image.height()):
        for x in range(image.width()):
            color = image.pixelColor(x, y)
            if color.alpha() > 20:
                left, top, right, bottom = min(left, x), min(top, y), max(right, x), max(bottom, y)
                color.setRgb(35, 39, 47, min(255, max(210, color.alpha())))
                image.setPixelColor(x, y, color)
    if right < left:
        return QIcon()
    # Asset-specific transparent gutters are deliberately removed here.  A
    # small, fixed visual inset keeps the two custom tools aligned with the
    # rest of the 24 px toolbar glyphs.
    padding = max(8, min(image.width(), image.height()) // 42)
    left, top = max(0, left - padding), max(0, top - padding)
    right, bottom = min(image.width(), right + padding + 1), min(image.height(), bottom + padding + 1)
    scale = 3
    glyph_size = max(1, round(size * scale * max(0.1, min(1.0, glyph_scale))))
    glyph = QPixmap.fromImage(image.copy(left, top, right - left, bottom - top)).scaled(glyph_size, glyph_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    pixmap = QPixmap(size * scale, size * scale); pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap); painter.drawPixmap((pixmap.width() - glyph.width()) // 2, (pixmap.height() - glyph.height()) // 2, glyph); painter.end()
    pixmap.setDevicePixelRatio(scale)
    icon = QIcon(pixmap); _ASSET_ICON_CACHE[key] = icon
    return icon


def apply_app_style(app: QApplication):
    # Qt's off-screen and packaged environments do not always discover Windows'
    # CJK fonts automatically. Register a deterministic Chinese UI fallback.
    for font_path in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\NotoSansSC-VF.ttf"):
        if os.path.isfile(font_path):
            QFontDatabase.addApplicationFont(font_path)
    app.setFont(QFont("Microsoft YaHei UI", 10))
    app.setStyleSheet(f"""
        QWidget {{ font-family: 'Microsoft YaHei UI', 'Segoe UI'; color: {COLORS['ink']}; }}
        QMainWindow {{ background: {COLORS['canvas']}; }}
        QFrame#windowbar {{ background: #FFFFFF; border: 0; }}
        QFrame#card {{ background: {COLORS['surface']}; border: 1px solid {COLORS['line']}; border-radius: 16px; }}
        QFrame#sidebar {{ background: {COLORS['surface']}; border-right: 1px solid {COLORS['line']}; }}
        QLabel#title {{ font-size: 21px; font-weight: 700; }}
        QLabel#section {{ font-size: 13px; font-weight: 700; }}
        QLabel#muted {{ color: {COLORS['muted']}; font-size: 12px; }}
        QLineEdit, QComboBox, QPlainTextEdit {{ background: white; border: 1px solid #DCE0E6; border-radius: 10px; padding: 9px 11px; }}
        QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus {{ border: 1px solid {COLORS['blue']}; }}
        QComboBox::drop-down {{ border: 0; width: 26px; subcontrol-origin: padding; subcontrol-position: top right; }}
        QPushButton#primary {{ background: {COLORS['blue']}; color: white; border: 0; border-radius: 10px; padding: 10px 18px; font-weight: 700; }}
        QPushButton#primary:hover {{ background: #2A8AFF; }}
        QPushButton#secondary {{ background: #F1F3F6; color: #2E3138; border: 0; border-radius: 10px; padding: 10px 16px; font-weight: 600; }}
        QPushButton#secondary:hover {{ background: #E7EBF0; }}
        QPushButton#destructive {{ background: #FFF1F0; color: #D92D20; border: 0; border-radius: 9px; padding: 7px 10px; font-weight: 700; }}
        QPushButton#destructive:hover {{ background: #FFE1DE; }}
        QToolButton#tool {{ background: transparent; border: 0; border-radius: 10px; padding: 8px; }}
        QToolButton#tool:hover, QToolButton#tool:checked {{ background: {COLORS['blue_soft']}; }}
        QToolButton#shortcutGuide {{ background:#F1F3F6; border:0; border-radius:10px; color:#34373E; font-family:'Segoe UI Symbol', 'Microsoft YaHei UI'; font-size:19px; padding:0; }}
        QToolButton#shortcutGuide:hover, QToolButton#shortcutGuide:pressed {{ background:#E7EBF0; }}
        QToolButton#visibilityCheck {{ background:#FFFFFF; border:1px solid #C9CED6; border-radius:4px; color:#0A74FF; font-size:14px; font-weight:700; padding:0; }}
        QToolButton#visibilityCheck:hover {{ border-color:#0A74FF; background:#F6F9FE; }}
        QToolButton#visibilityCheck:checked {{ background:#FFFFFF; border-color:#0A74FF; color:#0A74FF; }}
        QToolButton#nav {{ border: 0; border-radius: 12px; text-align: left; padding: 10px 12px; font-size: 15px; color: #34373E; }}
        QToolButton#nav:checked {{ background: {COLORS['blue_soft']}; color: {COLORS['blue']}; font-weight: 700; }}
        QToolButton#nav:hover {{ background: #F3F5F8; }}
        QFrame#projectCard {{ background: #FFFFFF; border: 1px solid #E0E6EE; border-left: 3px solid #0A74FF; border-radius: 12px; }}
        QLabel#projectEyebrow {{ color: #6E7785; font-size: 10px; font-weight: 700; letter-spacing: .6px; }}
        QLabel#projectName {{ color: #20242B; font-size: 15px; font-weight: 750; }}
        QLabel#projectPath {{ color: #78818D; font-size: 10px; }}
        QLabel#projectStatus {{ color: #16794C; background: #EEF9F3; border-radius: 7px; font-size: 10px; font-weight: 650; padding: 3px 7px; }}
        QToolButton#recentProject {{ background: #F6F7F9; border: 0; border-radius: 9px; padding: 7px 10px; text-align: left; color: #454953; font-size: 12px; }}
        QToolButton#recentProject:hover {{ background: #EAF3FF; color: {COLORS['blue']}; }}
        QToolButton#recentProject:checked {{ background: #EAF3FF; color: {COLORS['blue']}; font-weight: 700; }}
        QProgressBar {{ height: 5px; border: 0; background: {COLORS['track']}; border-radius: 3px; }}
        QProgressBar::chunk {{ background: {COLORS['blue']}; border-radius: 3px; }}
        QProgressBar#reviewProgress {{ height: 4px; min-height: 4px; max-height: 4px; border-radius: 2px; }}
        QProgressBar#reviewProgress::chunk {{ border-radius: 2px; }}
        QFrame#paging {{ background: #FFFFFF; border: 1px solid {COLORS['line']}; border-radius: 11px; }}
        QToolButton#pagingChevron {{ background: transparent; border: 0; border-radius: 10px; font-family: 'Segoe UI Symbol', 'Microsoft YaHei UI'; font-size: 22px; font-weight: 600; color: #23272F; padding: 0; margin: 0; }}
        QToolButton#pagingChevron:hover {{ background: #F3F5F8; }}
        QLabel#pagingPosition {{ border-left: 1px solid {COLORS['line']}; border-right: 1px solid {COLORS['line']}; font-size: 16px; padding: 0; }}
        QFrame#annotationDrawer {{ background: rgba(255,255,255,248); border: 1px solid #DCE0E6; border-radius: 14px; }}
        QToolButton#annotationRow {{ background: #FFFFFF; border: 1px solid #E7E9ED; border-radius: 10px; text-align: left; padding: 7px 10px; color: #2E3138; }}
        QToolButton#annotationRow:hover {{ background: #F6F9FE; border-color: #CFE1FF; }}
        QToolButton#annotationRow:checked {{ background: #EAF3FF; border-color: #0A74FF; font-weight: 700; }}
        QFileDialog {{ background: #F7F8FA; }}
        QFileDialog QPushButton {{ min-height: 34px; background: #FFFFFF; border: 1px solid #D9DEE6; border-radius: 9px; padding: 0 14px; font-weight: 650; }}
        QFileDialog QPushButton:hover {{ background: #F1F6FE; border-color: #BFD8FF; color: #0A74FF; }}
        QFileDialog QLineEdit, QFileDialog QComboBox {{ background: #FFFFFF; border: 1px solid #DCE1E8; border-radius: 9px; padding: 7px 10px; }}
        QFileDialog QTreeView, QFileDialog QListView {{ background: #FFFFFF; border: 1px solid #E5E8ED; border-radius: 10px; selection-background-color: #EAF3FF; selection-color: #0A74FF; }}
        QMenu {{ background: #FFFFFF; border: 1px solid #E2E6EB; border-radius: 11px; padding: 6px; }}
        QMenu::item {{ padding: 8px 22px; border-radius: 7px; color: #2E3138; }}
        QMenu::item:selected {{ background: #EAF3FF; color: #0A74FF; }}
        QMenu::separator {{ height: 1px; background: #ECEEF2; margin: 5px 8px; }}
        QDialog#iosDialog {{ background: transparent; }}
        QFrame#iosDialogPanel {{ background: #FFFFFF; border: 1px solid #E5E8ED; border-radius: 18px; }}
        QLabel#iosDialogTitle {{ color: #1D1D1F; font-size: 17px; font-weight: 750; }}
        QLabel#iosDialogMessage {{ color: #68707C; font-size: 13px; line-height: 1.45; }}
        QPushButton#iosDialogPrimary, QPushButton#iosDialogSecondary, QPushButton#iosDialogDestructive {{ min-height: 38px; border-radius: 10px; font-size: 13px; font-weight: 700; }}
        QPushButton#iosDialogPrimary {{ background: #0A74FF; color: #FFFFFF; border: 0; }}
        QPushButton#iosDialogPrimary:hover {{ background: #2A8AFF; }}
        QPushButton#iosDialogSecondary {{ background: #F2F4F7; color: #3D4652; border: 0; }}
        QPushButton#iosDialogSecondary:hover {{ background: #E8ECF1; }}
        QPushButton#iosDialogDestructive {{ background: #FFF0EF; color: #D92D20; border: 0; }}
        QPushButton#iosDialogDestructive:hover {{ background: #FFE1DE; }}
        QToolTip {{ background:#2E3138; color:#FFFFFF; border:0; border-radius:7px; padding:5px 8px; font-size:12px; }}
    """)


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
    outer = QVBoxLayout(dialog); outer.setContentsMargins(14, 14, 14, 14)
    panel = QFrame(); panel.setObjectName("iosDialogPanel"); outer.addWidget(panel)
    layout = QVBoxLayout(panel); layout.setContentsMargins(28, 25, 28, 20); layout.setSpacing(10)
    heading = label(title, "iosDialogTitle"); heading.setAlignment(Qt.AlignHCenter); heading.setWordWrap(True); layout.addWidget(heading)
    copy = label(message, "iosDialogMessage"); copy.setAlignment(Qt.AlignHCenter); copy.setWordWrap(True); layout.addWidget(copy)
    return dialog, layout


def ios_alert(parent, title: str, message: str, confirm_text="好", cancel_text=None, destructive=False):
    dialog, layout = _ios_dialog(parent, title, message)
    layout.addSpacing(8)
    actions = QVBoxLayout(); actions.setSpacing(7)
    if cancel_text:
        cancel = QPushButton(cancel_text); cancel.setObjectName("iosDialogSecondary"); cancel.clicked.connect(dialog.reject); actions.addWidget(cancel)
    confirm = QPushButton(confirm_text); confirm.setObjectName("iosDialogDestructive" if destructive else "iosDialogPrimary"); confirm.clicked.connect(dialog.accept); actions.addWidget(confirm)
    layout.addLayout(actions)
    return dialog.exec() == QDialog.DialogCode.Accepted


def ios_text_input(parent, title: str, prompt: str, text="", placeholder="", confirm_text="完成"):
    dialog, layout = _ios_dialog(parent, title, prompt)
    field = QLineEdit(text); field.setPlaceholderText(placeholder); layout.addWidget(field)
    layout.addSpacing(6)
    actions = QHBoxLayout(); actions.setSpacing(8); cancel = QPushButton("取消"); cancel.setObjectName("iosDialogSecondary"); cancel.clicked.connect(dialog.reject); confirm = QPushButton(confirm_text); confirm.setObjectName("iosDialogPrimary"); confirm.clicked.connect(dialog.accept); actions.addWidget(cancel); actions.addWidget(confirm); layout.addLayout(actions)
    QTimer.singleShot(0, field.setFocus)
    accepted = dialog.exec() == QDialog.DialogCode.Accepted
    return field.text(), accepted


def ios_item_input(parent, title: str, prompt: str, items, current=0, editable=False, confirm_text="完成"):
    dialog, layout = _ios_dialog(parent, title, prompt)
    field = FlatComboBox(); field.addItems([str(item) for item in items]); field.setEditable(editable)
    if field.count(): field.setCurrentIndex(max(0, min(field.count() - 1, current)))
    layout.addWidget(field)
    layout.addSpacing(6)
    actions = QHBoxLayout(); actions.setSpacing(8); cancel = QPushButton("取消"); cancel.setObjectName("iosDialogSecondary"); cancel.clicked.connect(dialog.reject); confirm = QPushButton(confirm_text); confirm.setObjectName("iosDialogPrimary"); confirm.clicked.connect(dialog.accept); actions.addWidget(cancel); actions.addWidget(confirm); layout.addLayout(actions)
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
        painter = QPainter(self); painter.setRenderHint(QPainter.Antialiasing)
        if self.underMouse():
            painter.fillRect(self.rect(), QColor("#F0F6FF"))
        pen = QPen(QColor("#0A74FF" if self.underMouse() else "#7B8491"), 1.6)
        pen.setCapStyle(Qt.RoundCap); pen.setJoinStyle(Qt.RoundJoin); painter.setPen(pen)
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


class ImageCanvas(QWidget):
    """Interactive image canvas for boxes and polygons with lossless rendering."""

    COLORS = ["#8A3FFC", "#FF8A00", "#31C48D", "#FF3B30", "#F2C94C", "#EC4899", "#39A9FF"]
    shapes_changed = Signal(object)
    selection_changed = Signal(int)
    box_created = Signal(int)
    polygon_created = Signal(int)

    def __init__(self):
        super().__init__()
        self.setMinimumSize(480, 360)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.path: str | None = None
        self.shapes: list[dict] = []
        self._pixmap = QPixmap()
        self._pixmap_cache: dict[str, QPixmap] = {}
        self._display_cache = QPixmap()
        self._display_cache_key = None
        self.tool = "pan"
        self.zoom = 1.0
        self.pan = QPointF()
        self.selected_index = -1
        self.default_label = "object"
        self.visible_categories: set[str] | None = None
        self.hidden_indices: set[int] = set()
        self._drag_point = None
        self._drag_origin = None
        self._drag_shape = None
        self._drag_index = -1
        self._drag_mode = None
        self._resize_handle = None
        self._draft_start = None
        self._draft_end = None
        self._polygon_draft: list[list[int]] = []
        self._polygon_hover = None
        self._history: list[list[dict]] = []
        self._redo: list[list[dict]] = []

    @staticmethod
    def _copy_shapes(shapes):
        copied = []
        for shape in shapes:
            item = {"name": str(shape.get("name", "object")), "bbox": list(shape.get("bbox", (0, 0, 0, 0)))}
            if shape.get("type") == "polygon" and len(shape.get("points", [])) >= 3:
                item["type"] = "polygon"
                item["points"] = [list(point[:2]) for point in shape["points"]]
            if shape.get("score") is not None:
                item["score"] = shape["score"]
            if shape.get("origin") in {"manual", "revised", "verified"}:
                item["origin"] = shape["origin"]
            copied.append(item)
        return copied

    @staticmethod
    def provenance_text(shape):
        """Describe whether a shape is still a model prediction or human work."""
        origin = shape.get("origin")
        labels = {"manual": "人工标注", "revised": "已修订", "verified": "已核验"}
        if origin in labels:
            return labels[origin]
        try:
            if shape.get("score") is not None:
                return f"模型预测 · 置信度 {float(shape['score']) * 100:.0f}%"
        except (TypeError, ValueError):
            pass
        return "历史标注"

    @staticmethod
    def _mark_manually_touched(shape):
        """Remove stale model confidence while retaining a clear human-work state."""
        shape.pop("score", None)
        if shape.get("origin") != "manual":
            shape["origin"] = "revised"

    @classmethod
    def color_for_label(cls, name: str) -> QColor:
        """Use a stable category colour instead of the box's list position."""
        value = sum((index + 1) * ord(character) for index, character in enumerate(str(name)))
        return QColor(cls.COLORS[value % len(cls.COLORS)])

    def set_content(self, path: str | None, shapes: list[dict]):
        self.path = path
        self.shapes = self._copy_shapes(shapes)
        if path and os.path.isfile(path):
            cached = self._pixmap_cache.pop(path, None)
            self._pixmap = cached if cached is not None else QPixmap(path)
            self._pixmap_cache[path] = self._pixmap
            while len(self._pixmap_cache) > 12:
                self._pixmap_cache.pop(next(iter(self._pixmap_cache)))
        else:
            self._pixmap = QPixmap()
            self._pixmap_cache.clear()
        self._display_cache = QPixmap(); self._display_cache_key = None
        self.zoom = 1.0; self.pan = QPointF(); self.selected_index = -1
        self.hidden_indices.clear()
        self._history.clear(); self._redo.clear()
        self.update()

    def prefetch(self, paths):
        """Decode likely next images only while the reviewer is idle."""
        for path in paths:
            if not path or path in self._pixmap_cache or not os.path.isfile(path):
                continue
            pixmap = QPixmap(path)
            if not pixmap.isNull():
                self._pixmap_cache[path] = pixmap
        while len(self._pixmap_cache) > 12:
            self._pixmap_cache.pop(next(iter(self._pixmap_cache)))

    def set_tool(self, name: str):
        self.tool = name
        self._drag_point = self._draft_start = self._draft_end = None
        self._polygon_draft = []; self._polygon_hover = None
        self._drag_shape = None; self._drag_index = -1; self._drag_mode = self._resize_handle = None
        self.setCursor(Qt.OpenHandCursor if name == "pan" else Qt.CrossCursor if name in ("box", "polygon") else Qt.ArrowCursor)
        self.update()

    def set_default_label(self, name: str):
        self.default_label = name or "object"

    def set_visible_categories(self, categories: set[str] | None):
        self.visible_categories = set(categories) if categories is not None else None
        if self.selected_index >= 0 and self.visible_categories is not None:
            if self.shapes[self.selected_index].get("name") not in self.visible_categories:
                self._set_selected(-1)
        self.update()

    def set_hidden_indices(self, indices):
        self.hidden_indices = {int(index) for index in indices if 0 <= int(index) < len(self.shapes)}
        if self.selected_index in self.hidden_indices:
            self._set_selected(-1)
        self.update()

    def _is_visible(self, index: int):
        if index in self.hidden_indices:
            return False
        return self.visible_categories is None or self.shapes[index].get("name") in self.visible_categories

    def _geometry(self):
        if self._pixmap.isNull():
            return QRectF(), 1.0
        source = self._pixmap.size()
        bounds = QRectF(self.rect().adjusted(0, 0, -1, -1))
        fit = min(bounds.width() / source.width(), bounds.height() / source.height())
        scale = max(0.05, min(16.0, fit * self.zoom))
        width, height = source.width() * scale, source.height() * scale
        target = QRectF(round(bounds.center().x() - width / 2 + self.pan.x()), round(bounds.center().y() - height / 2 + self.pan.y()), max(1, round(width)), max(1, round(height)))
        return target, target.width() / source.width()

    def _to_image(self, point: QPointF):
        target, scale = self._geometry()
        if target.isNull() or not target.contains(point):
            return None
        return QPointF((point.x() - target.x()) / scale, (point.y() - target.y()) / scale)

    def _to_canvas(self, point: QPointF):
        target, scale = self._geometry()
        return QPointF(target.x() + point.x() * scale, target.y() + point.y() * scale)

    def _shape_index_at(self, point: QPointF):
        for index in range(len(self.shapes) - 1, -1, -1):
            if not self._is_visible(index):
                continue
            if self.shapes[index].get("type") == "polygon":
                path = QPainterPath()
                points = self._polygon_points(index)
                if points:
                    path.moveTo(points[0])
                    for polygon_point in points[1:]:
                        path.lineTo(polygon_point)
                    path.closeSubpath()
                    if path.contains(point):
                        return index
                continue
            x1, y1, x2, y2 = self.shapes[index]["bbox"]
            if min(x1, x2) <= point.x() <= max(x1, x2) and min(y1, y2) <= point.y() <= max(y1, y2):
                return index
        return -1

    def _shape_rect(self, index: int):
        """Return an image-space box with stable left/top/right/bottom edges."""
        x1, y1, x2, y2 = self.shapes[index]["bbox"]
        return min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)

    def _polygon_points(self, index: int):
        if not 0 <= index < len(self.shapes):
            return []
        return [QPointF(float(point[0]), float(point[1])) for point in self.shapes[index].get("points", []) if len(point) >= 2]

    @staticmethod
    def _bbox_for_points(points):
        xs, ys = [point[0] for point in points], [point[1] for point in points]
        return [int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))]

    def _polygon_vertex_at(self, point: QPointF):
        if not 0 <= self.selected_index < len(self.shapes) or self.shapes[self.selected_index].get("type") != "polygon":
            return -1
        _, scale = self._geometry()
        radius = max(6, 8 / max(scale, 0.01))
        for index, vertex in enumerate(self._polygon_points(self.selected_index)):
            if abs(point.x() - vertex.x()) <= radius and abs(point.y() - vertex.y()) <= radius:
                return index
        return -1

    @staticmethod
    def _handle_positions(rect: QRectF):
        return {
            "nw": rect.topLeft(), "n": QPointF(rect.center().x(), rect.top()), "ne": rect.topRight(),
            "e": QPointF(rect.right(), rect.center().y()), "se": rect.bottomRight(),
            "s": QPointF(rect.center().x(), rect.bottom()), "sw": rect.bottomLeft(),
            "w": QPointF(rect.left(), rect.center().y()),
        }

    def _selected_canvas_rect(self):
        if not 0 <= self.selected_index < len(self.shapes):
            return QRectF()
        target, scale = self._geometry()
        x1, y1, x2, y2 = self._shape_rect(self.selected_index)
        return QRectF(target.x() + x1 * scale, target.y() + y1 * scale, (x2 - x1) * scale, (y2 - y1) * scale)

    def _handle_at(self, point: QPointF):
        if 0 <= self.selected_index < len(self.shapes) and self.shapes[self.selected_index].get("type") == "polygon":
            image_point = self._to_image(point)
            return f"vertex:{self._polygon_vertex_at(image_point)}" if image_point and self._polygon_vertex_at(image_point) >= 0 else None
        rect = self._selected_canvas_rect()
        if rect.isNull():
            return None
        radius = 8
        for handle, center in self._handle_positions(rect).items():
            if abs(point.x() - center.x()) <= radius and abs(point.y() - center.y()) <= radius:
                return handle
        return None

    def _set_select_cursor(self, point: QPointF):
        handle = self._handle_at(point)
        if handle and handle.startswith("vertex:"):
            self.setCursor(Qt.CrossCursor)
            return
        cursors = {
            "nw": Qt.SizeFDiagCursor, "se": Qt.SizeFDiagCursor,
            "ne": Qt.SizeBDiagCursor, "sw": Qt.SizeBDiagCursor,
            "n": Qt.SizeVerCursor, "s": Qt.SizeVerCursor,
            "e": Qt.SizeHorCursor, "w": Qt.SizeHorCursor,
        }
        self.setCursor(cursors.get(handle, Qt.ArrowCursor))

    def _set_selected(self, index: int):
        if self.selected_index != index:
            self.selected_index = index
            self.selection_changed.emit(index)
            self.update()

    def focus_shape(self, index: int):
        """Select one visible box and bring its centre into the canvas viewport."""
        if not 0 <= index < len(self.shapes) or not self._is_visible(index):
            return False
        x1, y1, x2, y2 = self._shape_rect(index)
        current = self._to_canvas(QPointF((x1 + x2) / 2, (y1 + y2) / 2))
        self.pan += QPointF(self.rect().center()) - current
        self._set_selected(index)
        self.update()
        return True

    def _commit(self, before):
        if before == self.shapes:
            return
        self._history.append(before)
        self._redo.clear()
        self.shapes_changed.emit(self._copy_shapes(self.shapes))
        self.update()

    def _finish_polygon_draft(self):
        """Commit the in-progress polygon and immediately leave drawing mode."""
        if len(self._polygon_draft) < 3:
            return False
        before = self._copy_shapes(self.shapes)
        points = self._polygon_draft[:]
        self.shapes.append({"name": self.default_label, "origin": "manual", "type": "polygon", "points": points, "bbox": self._bbox_for_points(points)})
        self._polygon_draft = []; self._polygon_hover = None
        self._set_selected(len(self.shapes) - 1); self._commit(before); self.polygon_created.emit(self.selected_index)
        return True

    def delete_selected(self):
        if not 0 <= self.selected_index < len(self.shapes):
            return False
        before = self._copy_shapes(self.shapes)
        self.shapes.pop(self.selected_index)
        self._set_selected(-1)
        self._commit(before)
        return True

    def undo(self):
        if not self._history:
            return False
        self._redo.append(self._copy_shapes(self.shapes))
        self.shapes = self._history.pop(); self._set_selected(-1)
        self.shapes_changed.emit(self._copy_shapes(self.shapes)); self.update()
        return True

    def redo(self):
        if not self._redo:
            return False
        self._history.append(self._copy_shapes(self.shapes))
        self.shapes = self._redo.pop(); self._set_selected(-1)
        self.shapes_changed.emit(self._copy_shapes(self.shapes)); self.update()
        return True

    def apply_category(self, name: str):
        if not 0 <= self.selected_index < len(self.shapes) or not name:
            return
        before = self._copy_shapes(self.shapes)
        self.shapes[self.selected_index]["name"] = name
        # A model confidence no longer describes a manually reclassified shape.
        self._mark_manually_touched(self.shapes[self.selected_index])
        self._commit(before)

    def mark_all_verified(self):
        """Record the human approval state instead of inventing a 100% score."""
        if not self.shapes:
            return False
        before = self._copy_shapes(self.shapes)
        for shape in self.shapes:
            shape.pop("score", None)
            shape["origin"] = "verified"
        self._commit(before)
        return True

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing); painter.setRenderHint(QPainter.SmoothPixmapTransform, False)
        bounds = self.rect().adjusted(0, 0, -1, -1)
        clip = QPainterPath()
        clip.addRoundedRect(bounds, 12, 12)
        painter.setClipPath(clip)
        painter.fillRect(bounds, QColor("#ECEEF2"))
        if self._pixmap.isNull():
            painter.fillRect(bounds, QColor("#272A30"))
            painter.setPen(QColor("#CDD1D8"))
            painter.setFont(QFont("Microsoft YaHei UI", 13))
            painter.drawText(bounds, Qt.AlignCenter, "从左上角项目菜单新建、导入或选择最近项目\n待审核图片会显示在这里")
            return
        target, scale = self._geometry()
        cache_key = (self._pixmap.cacheKey(), int(target.width()), int(target.height()))
        if self._display_cache_key != cache_key:
            self._display_cache = self._pixmap.scaled(int(target.width()), int(target.height()), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            self._display_cache_key = cache_key
        painter.drawPixmap(target.topLeft(), self._display_cache)
        for index, shape in enumerate(self.shapes):
            if not self._is_visible(index):
                continue
            color = self.color_for_label(shape.get("name", "object"))
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(color, 3 if index == self.selected_index else 2))
            if shape.get("type") == "polygon":
                image_points = self._polygon_points(index)
                if len(image_points) < 3:
                    continue
                polygon = QPainterPath()
                canvas_points = [self._to_canvas(point) for point in image_points]
                polygon.moveTo(canvas_points[0])
                for polygon_point in canvas_points[1:]:
                    polygon.lineTo(polygon_point)
                polygon.closeSubpath()
                if index == self.selected_index:
                    selected_fill = QColor(color); selected_fill.setAlpha(28)
                    painter.fillPath(polygon, selected_fill)
                painter.drawPath(polygon)
                rect = polygon.boundingRect()
                if index == self.selected_index:
                    painter.setBrush(Qt.white); painter.setPen(QPen(color, 2))
                    for polygon_point in canvas_points:
                        painter.drawEllipse(polygon_point, 4, 4)
            else:
                x1, y1, x2, y2 = shape.get("bbox", (0, 0, 0, 0))
                rect = QRectF(target.x() + x1 * scale, target.y() + y1 * scale, (x2 - x1) * scale, (y2 - y1) * scale).normalized()
                if index == self.selected_index:
                    # Keep the source visible while restoring the gentle, category-
                    # coloured selection wash used by the workbench design.
                    selected_fill = QColor(color); selected_fill.setAlpha(28)
                    painter.fillRect(rect, selected_fill)
                painter.drawRect(rect)
            caption = shape.get("name", "object")
            provenance = self.provenance_text(shape)
            if provenance.startswith("模型预测"):
                caption += provenance.replace("模型预测 · 置信度 ", "  ")
            elif provenance != "历史标注":
                caption += f"  ·  {provenance}"
            painter.setFont(QFont("Microsoft YaHei UI", 10, QFont.Bold))
            width = max(46, painter.fontMetrics().horizontalAdvance(caption) + 16)
            caption_rect = QRectF(rect.x(), max(target.y(), rect.y() - 25), width, 24)
            painter.fillRect(caption_rect, color)
            painter.setPen(Qt.white)
            painter.drawText(caption_rect, Qt.AlignCenter, caption)
            if index == self.selected_index and shape.get("type") != "polygon":
                painter.setBrush(Qt.white); painter.setPen(QPen(color, 2))
                for point in self._handle_positions(rect).values():
                    painter.drawEllipse(point, 4, 4)
        if self._draft_start and self._draft_end:
            start, end = self._to_canvas(self._draft_start), self._to_canvas(self._draft_end)
            painter.setPen(QPen(QColor("#0A74FF"), 2, Qt.DashLine))
            painter.drawRect(QRectF(start, end).normalized())
        if self._polygon_draft:
            draft = [self._to_canvas(QPointF(point[0], point[1])) for point in self._polygon_draft]
            if self._polygon_hover:
                draft.append(self._to_canvas(self._polygon_hover))
            painter.setPen(QPen(QColor("#0A74FF"), 2, Qt.DashLine))
            if draft:
                painter.drawPolyline(QPolygonF(draft))
                painter.setBrush(QColor("#FFFFFF")); painter.setPen(QPen(QColor("#0A74FF"), 2))
                for polygon_point in draft[:-1] if self._polygon_hover else draft:
                    painter.drawEllipse(polygon_point, 4, 4)

    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton and self.tool == "polygon" and self._polygon_draft:
            self._polygon_draft = []
            self._polygon_hover = None
            self.update()
            event.accept()
            return
        if self._pixmap.isNull() or event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)
        self.setFocus()
        point = event.position()
        image_point = self._to_image(point)
        if self.tool == "pan":
            self._drag_point = point; self._drag_origin = QPointF(self.pan); self.setCursor(Qt.ClosedHandCursor)
        elif self.tool == "box" and image_point:
            self._draft_start = self._draft_end = image_point
        elif self.tool == "polygon" and image_point:
            if len(self._polygon_draft) >= 3:
                first = QPointF(self._polygon_draft[0][0], self._polygon_draft[0][1])
                _, scale = self._geometry()
                close_radius = max(6, 10 / max(scale, 0.01))
                if abs(image_point.x() - first.x()) <= close_radius and abs(image_point.y() - first.y()) <= close_radius:
                    self._finish_polygon_draft()
                    event.accept(); return
            self._polygon_draft.append([int(round(image_point.x())), int(round(image_point.y()))])
            self._polygon_hover = image_point
        elif self.tool == "select":
            handle = self._handle_at(point)
            if handle and image_point and self.selected_index >= 0:
                self._drag_point = image_point
                self._drag_index = self.selected_index
                self._drag_shape = self._copy_shapes([self.shapes[self.selected_index]])[0]
                self._drag_mode = "vertex" if handle.startswith("vertex:") else "resize"; self._resize_handle = handle
            else:
                index = self._shape_index_at(image_point) if image_point else -1
                self._set_selected(index)
                if index >= 0 and image_point:
                    self._drag_point = image_point; self._drag_index = index
                    self._drag_shape = self._copy_shapes([self.shapes[index]])[0]
                    self._drag_mode = "move"
        event.accept()

    def mouseMoveEvent(self, event):
        point = event.position()
        if self.tool == "pan" and self._drag_point is not None:
            self.pan = self._drag_origin + (point - self._drag_point); self.update()
        elif self.tool == "box" and self._draft_start:
            self._draft_end = self._to_image(point) or self._draft_end; self.update()
        elif self.tool == "polygon" and self._polygon_draft:
            self._polygon_hover = self._to_image(point); self.update()
        elif self.tool == "select" and self._drag_point and self._drag_shape and self._drag_index >= 0:
            current = self._to_image(point)
            if current:
                # Keep scores only for untouched model predictions; reviewers
                # should never see a stale confidence after changing geometry.
                self._mark_manually_touched(self.shapes[self._drag_index])
                x1, y1, x2, y2 = self._drag_shape["bbox"]
                if self._drag_mode == "vertex":
                    vertex = int((self._resize_handle or "vertex:-1").split(":", 1)[1])
                    if 0 <= vertex < len(self.shapes[self._drag_index].get("points", [])):
                        points = self.shapes[self._drag_index]["points"]
                        points[vertex] = [int(round(current.x())), int(round(current.y()))]
                        self.shapes[self._drag_index]["bbox"] = self._bbox_for_points(points)
                elif self._drag_mode == "resize":
                    left, top, right, bottom = min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)
                    handle = self._resize_handle or ""
                    if "w" in handle: left = min(current.x(), right - 3)
                    if "e" in handle: right = max(current.x(), left + 3)
                    if "n" in handle: top = min(current.y(), bottom - 3)
                    if "s" in handle: bottom = max(current.y(), top + 3)
                    self.shapes[self._drag_index]["bbox"] = [int(left), int(top), int(right), int(bottom)]
                else:
                    dx, dy = current.x() - self._drag_point.x(), current.y() - self._drag_point.y()
                    if self._drag_shape.get("type") == "polygon":
                        points = [[int(round(x + dx)), int(round(y + dy))] for x, y in self._drag_shape["points"]]
                        self.shapes[self._drag_index]["points"] = points
                        self.shapes[self._drag_index]["bbox"] = self._bbox_for_points(points)
                    else:
                        self.shapes[self._drag_index]["bbox"] = [x1 + dx, y1 + dy, x2 + dx, y2 + dy]
                self.update()
        elif self.tool == "select":
            self._set_select_cursor(point)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton:
            return super().mouseReleaseEvent(event)
        if self.tool == "pan":
            self._drag_point = self._drag_origin = None; self.setCursor(Qt.OpenHandCursor)
        elif self.tool == "box" and self._draft_start and self._draft_end:
            x1, y1 = self._draft_start.x(), self._draft_start.y(); x2, y2 = self._draft_end.x(), self._draft_end.y()
            self._draft_start = self._draft_end = None
            if abs(x2 - x1) >= 3 and abs(y2 - y1) >= 3:
                before = self._copy_shapes(self.shapes)
                self.shapes.append({"name": self.default_label, "origin": "manual", "bbox": [int(min(x1, x2)), int(min(y1, y2)), int(max(x1, x2)), int(max(y1, y2))]})
                self._set_selected(len(self.shapes) - 1); self._commit(before); self.box_created.emit(self.selected_index)
            self.update()
        elif self.tool == "select" and self._drag_shape and self._drag_index >= 0:
            before = self._copy_shapes(self.shapes)
            before[self._drag_index] = self._drag_shape
            self._drag_point = self._drag_shape = None
            self._drag_index = -1; self._drag_mode = self._resize_handle = None
            self._commit(before)
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event):
        if self._pixmap.isNull():
            return super().wheelEvent(event)
        point, before = event.position(), self._to_image(event.position())
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.zoom = max(0.15, min(12.0, self.zoom * factor))
        if before:
            after = self._to_canvas(before)
            self.pan += point - after
        self.update(); event.accept()

    def mouseDoubleClickEvent(self, event):
        if self.tool == "polygon" and event.button() == Qt.LeftButton:
            self._finish_polygon_draft()
            event.accept(); return
        if event.button() == Qt.LeftButton:
            self.zoom = 1.0; self.pan = QPointF(); self.update(); event.accept(); return
        super().mouseDoubleClickEvent(event)


class WindowChrome(QFrame):
    """A compact desktop title bar: colored workspace dots on the left, controls on the right."""

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self._drag_origin = None
        self.setFixedHeight(38)
        self.setObjectName("windowbar")
        layout = QHBoxLayout(self); layout.setContentsMargins(20, 0, 10, 0); layout.setSpacing(8)
        for color in ("#FF5F57", "#FEBC2E", "#28C840"):
            dot = QFrame(); dot.setFixedSize(11, 11); dot.setStyleSheet(f"background:{color}; border-radius:5px;")
            layout.addWidget(dot)
        layout.addStretch()
        for caption, action, tip in (("−", window.showMinimized, "最小化"), ("□", window.toggle_maximized, "最大化"), ("×", window.close, "关闭")):
            control = QToolButton(); control.setText(caption); control.setToolTip(tip); control.setFixedSize(34, 30); control.setCursor(Qt.PointingHandCursor)
            control.setStyleSheet("QToolButton {border:0;border-radius:8px;color:#3E424A;font-size:18px;} QToolButton:hover {background:#EEF1F5;} QToolButton:pressed {background:#E2E7ED;}")
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
        return (position.x() <= self.EDGE, position.x() >= self.width() - self.EDGE,
                position.y() <= self.EDGE, position.y() >= self.height() - self.EDGE)

    def _set_resize_cursor(self, edges):
        left, right, top, bottom = edges
        if (left and top) or (right and bottom): self.setCursor(Qt.SizeFDiagCursor)
        elif (right and top) or (left and bottom): self.setCursor(Qt.SizeBDiagCursor)
        elif left or right: self.setCursor(Qt.SizeHorCursor)
        elif top or bottom: self.setCursor(Qt.SizeVerCursor)
        else: self.unsetCursor()

    def mousePressEvent(self, event):
        edges = self._edges_at(event.position().toPoint())
        if event.button() == Qt.LeftButton and any(edges) and not self.window.isMaximized():
            self._edges = edges
            self._start_global = event.globalPosition().toPoint()
            self._start_geometry = self.window.geometry()
            event.accept(); return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._start_global is None:
            self._set_resize_cursor(self._edges_at(event.position().toPoint()))
            super().mouseMoveEvent(event); return
        delta = event.globalPosition().toPoint() - self._start_global
        left, right, top, bottom = self._edges
        rect = self._start_geometry
        min_width, min_height = self.window.minimumWidth(), self.window.minimumHeight()
        x, y, width, height = rect.x(), rect.y(), rect.width(), rect.height()
        if left: x, width = x + delta.x(), width - delta.x()
        if right: width += delta.x()
        if top: y, height = y + delta.y(), height - delta.y()
        if bottom: height += delta.y()
        if width < min_width:
            if left: x -= min_width - width
            width = min_width
        if height < min_height:
            if top: y -= min_height - height
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


class AutoPage(QWidget):
    log_message = Signal(str)
    progress_changed = Signal(int)
    batch_finished = Signal(str, object, bool)
    batch_failed = Signal(str)

    def __init__(self, window):
        super().__init__()
        self.window = window
        self._worker_thread = None
        self._stop_event = threading.Event()
        self.custom_model_path = ""
        layout = QVBoxLayout(self); layout.setContentsMargins(24, 24, 24, 24); layout.setSpacing(14)
        header = QHBoxLayout(); header.addWidget(label("自动标注", "title")); header.addStretch(); self.import_model = button("导入自定义模型"); self.import_model.setToolTip("导入项目专用的 YOLO .pt 权重"); self.import_model.clicked.connect(self._import_custom_model); header.addWidget(self.import_model); layout.addLayout(header)
        self.project_summary = label("从左侧项目菜单新建、导入或选择最近项目后即可开始。", "muted"); layout.addWidget(self.project_summary)
        data = card(); grid = QGridLayout(data); grid.setContentsMargins(22, 20, 22, 20); grid.setHorizontalSpacing(14); grid.setVerticalSpacing(12)
        grid.addWidget(label("项目数据", "section"), 0, 0, 1, 3); grid.addWidget(label("项目内图片", "muted"), 1, 0)
        self.images = QLineEdit(); self.images.setReadOnly(True); self.images.setPlaceholderText("新建项目后，图片将保存在这里"); grid.addWidget(self.images, 1, 1); self.import_images = button("导入图片"); self.import_images.clicked.connect(self._import_images); grid.addWidget(self.import_images, 1, 2)
        grid.addWidget(label("项目内标注", "muted"), 2, 0); self.annotations = QLineEdit(); self.annotations.setReadOnly(True); self.annotations.setPlaceholderText("与图片一起保存的 XML 标注"); grid.addWidget(self.annotations, 2, 1); self.import_annotations = button("导入标注"); self.import_annotations.clicked.connect(self._import_annotations); grid.addWidget(self.import_annotations, 2, 2); layout.addWidget(data)
        setup = card(); grid = QGridLayout(setup); grid.setContentsMargins(22, 20, 22, 20); grid.setHorizontalSpacing(14); grid.setVerticalSpacing(12)
        grid.addWidget(label("标注配置", "section"), 0, 0, 1, 3); grid.addWidget(label("检测模型", "muted"), 1, 0); self.model = FlatComboBox(); self.model.addItem("YOLOE（开放词表，推荐）", "yoloe"); self.model.addItem("Grounding DINO（兼容旧模型）", "gd15"); self.model.addItem("Grounding DINO（旧版）", "gd_ogc"); self.model.addItem("YOLO（微调）", "yolo"); self.model.addItem("自定义 YOLO（请在右上角导入）", "custom_yolo"); grid.addWidget(self.model, 1, 1, 1, 2)
        grid.addWidget(label("类别提示词", "muted"), 2, 0); self.prompt = QLineEdit(config.PROMPT); grid.addWidget(self.prompt, 2, 1); categories = button("管理类别"); categories.clicked.connect(self._categories); grid.addWidget(categories, 2, 2)
        grid.addWidget(label("最低置信度", "muted"), 3, 0); confidence_host = QWidget(); confidence_layout = QHBoxLayout(confidence_host); confidence_layout.setContentsMargins(0, 0, 0, 0); confidence_layout.setSpacing(10); self.confidence = QSlider(Qt.Horizontal); self.confidence.setRange(10, 95); self.confidence.setValue(round(config.BOX_THRESHOLD * 100)); self.confidence.setToolTip("低一些可保留更多候选框（适合排查单人漏检）；高一些可过滤不准确的框。"); self.confidence_value = label("", "muted"); self.confidence_value.setFixedWidth(38); self.confidence_value.setAlignment(Qt.AlignRight | Qt.AlignVCenter); confidence_layout.addWidget(self.confidence, 1); confidence_layout.addWidget(self.confidence_value); grid.addWidget(confidence_host, 3, 1, 1, 2); self.confidence.valueChanged.connect(self._update_confidence_label); self._update_confidence_label(self.confidence.value())
        grid.addWidget(label("处理范围", "muted"), 4, 0); self.run_scope = FlatComboBox(); self.run_scope.addItem("仅未标注图片（安全）", "new"); self.run_scope.addItem("风险队列（高风险优先）", "risk"); self.run_scope.addItem("所有待审核图片", "review"); self.run_scope.setToolTip("重新预标注会先保留当前 XML 版本，可在审核页恢复"); grid.addWidget(self.run_scope, 4, 1, 1, 2); layout.addWidget(setup)
        actions = QHBoxLayout(); self.start = button("开始自动标注", primary=True); self.start.clicked.connect(self._start_labeling); actions.addWidget(self.start); self.stop = button("停止"); self.stop.clicked.connect(self._stop_labeling); self.stop.setEnabled(False); actions.addWidget(self.stop); actions.addWidget(label("重跑会自动保留当前标注版本，再写入新的预标注结果。", "muted")); actions.addStretch(); layout.addLayout(actions)
        self.progress = QProgressBar(); self.progress.setTextVisible(False); self.progress.setValue(0); layout.addWidget(self.progress)
        self.log = QPlainTextEdit(); self.log.setReadOnly(True); self.log.setPlaceholderText("运行记录会显示在这里"); self.log.setMinimumHeight(140); layout.addWidget(self.log, 1)
        self.log_message.connect(self.log.appendPlainText)
        self.progress_changed.connect(self.progress.setValue)
        self.batch_finished.connect(self._on_label_finished)
        self.batch_failed.connect(self._on_label_failed)

    def _update_confidence_label(self, value):
        self.confidence_value.setText(f"{int(value)}%")

    def sync_project(self, root: str | None = None, name: str | None = None):
        active = bool(root and self.window.project_root)
        self.images.setText(self.window.images_dir if active else "")
        self.annotations.setText(self.window.annotations_dir if active else "")
        self.import_images.setEnabled(active); self.import_annotations.setEnabled(active)
        self.import_model.setEnabled(active)
        self.custom_model_path = ""
        if active and self.window.project:
            stored = self.window.project.metadata_value("custom_yolo_weights")
            if stored:
                try:
                    candidate = resolve_project_directory(self.window.project_root, stored)
                    if os.path.isfile(candidate):
                        self.custom_model_path = candidate
                except ValueError:
                    pass
        self._render_custom_model_choice()
        self.start.setEnabled(active and not (self._worker_thread and self._worker_thread.is_alive()))
        self.project_summary.setText("图片、标注和审核记录均保存在当前工作区内。" if active else "先从左侧三点菜单新建项目或打开项目。")

    def _render_custom_model_choice(self):
        index = self.model.findData("custom_yolo")
        title = f"自定义 YOLO · {Path(self.custom_model_path).name}" if self.custom_model_path else "自定义 YOLO（请在右上角导入）"
        if index >= 0:
            self.model.setItemText(index, title)

    def _import_custom_model(self):
        if not self.window.project_root or not self.window.project:
            self.window.status_message("请先新建或打开项目，再导入自定义模型")
            return
        source, _ = QFileDialog.getOpenFileName(self, "导入自定义 YOLO 模型", self.window.project_root, "YOLO 模型 (*.pt)")
        if not source:
            return
        models_dir = os.path.join(self.window.project_root, "models")
        os.makedirs(models_dir, exist_ok=True)
        target = os.path.join(models_dir, Path(source).name)
        if os.path.abspath(source) != os.path.abspath(target) and os.path.exists(target):
            if not ios_alert(self, "替换项目模型", f"项目内已存在同名模型“{Path(target).name}”。是否替换？", "替换", "取消"):
                return
        if os.path.abspath(source) != os.path.abspath(target):
            shutil.copy2(source, target)
        self.custom_model_path = target
        self.window.project.set_metadata_value("custom_yolo_weights", os.path.relpath(target, self.window.project_root))
        self._render_custom_model_choice()
        self.model.setCurrentIndex(self.model.findData("custom_yolo"))
        self.window.status_message(f"已导入自定义模型：{Path(target).name}")

    def _import_images(self):
        if not self.window.project_root:
            self.window.status_message("请先新建或打开项目"); return
        source = QFileDialog.getExistingDirectory(self, "选择要导入的图片目录", self.window.project_root)
        if not source or os.path.abspath(source) == os.path.abspath(self.window.images_dir):
            return
        copied = skipped = 0
        for filename in core.list_images(source):
            target = os.path.join(self.window.images_dir, filename)
            if os.path.exists(target):
                skipped += 1; continue
            shutil.copy2(os.path.join(source, filename), target); copied += 1
        self.window.refresh_project_state()
        self.log.appendPlainText(f"已导入 {copied} 张图片到项目目录（{skipped} 张同名文件未覆盖）。")

    def _import_annotations(self):
        if not self.window.project_root:
            self.window.status_message("请先新建或打开项目"); return
        source = QFileDialog.getExistingDirectory(self, "选择要导入的 XML 标注目录", self.window.project_root)
        if not source or os.path.abspath(source) == os.path.abspath(self.window.annotations_dir):
            return
        image_stems = {Path(filename).stem for filename in core.list_images(self.window.images_dir)}
        copied = skipped = 0
        for xml in Path(source).glob("*.xml"):
            if xml.stem not in image_stems:
                skipped += 1; continue
            target = os.path.join(self.window.annotations_dir, xml.name)
            if os.path.exists(target):
                skipped += 1; continue
            shutil.copy2(xml, target); copied += 1
        imported = self.window.refresh_project_state()
        self.log.appendPlainText(f"已导入 {copied} 份 XML 标注，{imported} 张图片已进入待审核（{skipped} 份未导入）。")

    def _categories(self):
        value, ok = ios_text_input(self, "项目类别", "使用空格或句点分隔类别：", self.prompt.text(), confirm_text="保存")
        if ok:
            self.prompt.setText(value)
            if self.window.project:
                self.window.project.set_categories(core.parse_categories(value))

    def _start_labeling(self):
        if not self.window.project_root:
            self.log.appendPlainText("请先从左侧项目菜单新建、导入或选择最近项目。"); return
        if self._worker_thread and self._worker_thread.is_alive():
            return
        prompt = self.prompt.text().strip()
        if not prompt:
            self.log.appendPlainText("提示词不能为空。"); return
        if self.window.project:
            self.window.project.set_categories(core.parse_categories(prompt))
        scope = self.run_scope.currentData() or "new"
        pending = self._target_files(scope)
        if not pending:
            self.log.appendPlainText("当前范围没有需要处理的图片。可切换到“风险队列”或“所有待审核图片”重新预标注。")
            self.window.refresh_project_state(); self.window.show_page("review"); return
        if scope != "new":
            if not ios_alert(self, "确认重新预标注", f"将重新预标注 {len(pending)} 张图片。当前 XML 会先保存为可恢复版本，再写入新的模型结果。\n\n是否继续？", "继续", "取消"):
                return
        backend = self.model.currentData() or "yoloe"
        if backend == "custom_yolo" and not self.custom_model_path:
            ios_alert(self, "请先导入模型", "请先使用右上角“导入自定义模型”选择 YOLO .pt 权重。")
            return
        self._stop_event.clear(); self.start.setEnabled(False); self.stop.setEnabled(True); self.progress.setValue(0)
        box_threshold = self.confidence.value() / 100
        self.log.appendPlainText(f"准备处理 {len(pending)} 张图片（{self.run_scope.currentText()} · 最低置信度 {box_threshold:.0%}）。")
        project_root = self.window.project_root
        self._worker_thread = threading.Thread(
            target=self._run_labeling,
            args=(project_root, self.window.images_dir, self.window.annotations_dir, pending, prompt, backend, scope, box_threshold, self.custom_model_path),
            daemon=True,
        )
        self._worker_thread.start()

    def _target_files(self, scope):
        result = []
        filenames = core.list_images(self.window.images_dir)
        statuses = self.window.project.statuses_for(filenames) if self.window.project else {}
        for filename in filenames:
            image_path = os.path.join(self.window.images_dir, filename)
            xml_path = os.path.join(self.window.annotations_dir, Path(filename).stem + ".xml")
            if scope == "new":
                if not os.path.isfile(xml_path):
                    result.append(filename)
            elif scope == "review":
                if statuses.get(filename) == STATUS_NEEDS_REVIEW:
                    result.append(filename)
            elif scope == "risk" and statuses.get(filename) == STATUS_NEEDS_REVIEW and os.path.isfile(xml_path):
                try:
                    risk, _ = core.annotation_risk(core.load_voc_xml(xml_path), image_path)
                    if risk >= config.REVIEW_HIGH_RISK:
                        result.append(filename)
                except Exception:
                    result.append(filename)
        return result

    def _stop_labeling(self):
        if self._worker_thread and self._worker_thread.is_alive():
            self._stop_event.set(); self.stop.setEnabled(False); self.log.appendPlainText("将在当前图片处理完成后停止。")

    def _run_labeling(self, project_root: str, images_dir: str, annotations_dir: str, filenames: list[str], prompt: str, backend: str, scope: str, box_threshold: float, custom_model_path: str = ""):
        completed = []
        try:
            project = ProjectStore(annotations_dir, images_dir, project_root)
            self.log_message.emit("正在加载检测模型，请稍候…")
            detector = core.load_models(backend, custom_model_path if backend == "custom_yolo" else None)
            self.log_message.emit("模型加载完成，开始推理。")
            total = len(filenames)
            for index, filename in enumerate(filenames):
                if self._stop_event.is_set():
                    self.log_message.emit("已按请求停止自动标注。"); break
                image_path = os.path.join(images_dir, filename)
                try:
                    xml_path = os.path.join(annotations_dir, Path(filename).stem + ".xml")
                    if os.path.isfile(xml_path):
                        self._snapshot_annotation(project, filename, image_path, xml_path, "重跑前结果", Path(custom_model_path).name if backend == "custom_yolo" else backend, prompt)
                    annotations = core.auto_label(image_path, prompt, detector, box_threshold=box_threshold)
                    core.save_as_voc_xml(annotations, image_path, annotations_dir)
                    self._snapshot_annotation(project, filename, image_path, xml_path, "自动预标注", Path(custom_model_path).name if backend == "custom_yolo" else backend, prompt)
                    completed.append(filename)
                    self.log_message.emit(f"[完成] {filename}：{len(annotations)} 个目标 · 已保存版本")
                except Exception as exc:
                    self.log_message.emit(f"[失败] {filename}：{exc}")
                self.progress_changed.emit(int((index + 1) * 100 / total))
            self.batch_finished.emit(project_root, completed, self._stop_event.is_set())
        except ModuleNotFoundError as exc:
            self.batch_failed.emit(f"缺少模型依赖：{exc.name}。请安装 requirements.txt 中的依赖。")
        except Exception as exc:
            self.batch_failed.emit(f"自动标注失败：{exc}")

    @staticmethod
    def _snapshot_annotation(project, filename, image_path, xml_path, source, backend, prompt):
        """Copy the current XML into the project version store and index it."""
        if not os.path.isfile(xml_path):
            return
        version_root = os.path.join(project.project_dir, ANNOTATION_VERSION_DIR, Path(filename).stem)
        os.makedirs(version_root, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        snapshot = os.path.join(version_root, f"{stamp}-{source}.xml")
        shutil.copy2(xml_path, snapshot)
        annotations = core.load_voc_xml(snapshot)
        risk, rules = core.annotation_risk(annotations, image_path)
        project.record_annotation_version(filename, snapshot, source, backend, prompt, len(annotations), risk, rules)

    def _on_label_finished(self, project_root: str, completed: list[str], stopped: bool):
        self.start.setEnabled(bool(self.window.project_root)); self.stop.setEnabled(False)
        if project_root != self.window.project_root:
            self.log.appendPlainText("任务完成，但当前已切换到其他项目；结果仍已保存到原项目。")
            return
        if completed and self.window.project:
            self.window.project.mark_batch_for_review(completed)
        self.window.refresh_all()
        self.log.appendPlainText(f"自动标注结束：{len(completed)} 张已写入项目并进入待审核。")
        if completed:
            self.window.show_page("review")

    def _on_label_failed(self, message: str):
        self.start.setEnabled(bool(self.window.project_root)); self.stop.setEnabled(False)
        self.log.appendPlainText(message)
        ios_alert(self, "自动标注失败", message, confirm_text="知道了", destructive=True)


class ReviewPage(QWidget):
    def __init__(self, window):
        super().__init__(); self.window = window; self.files: list[str] = []; self.index = 0; self._risk_cache = {}; self._risk_project_root = ""; self._spot_check_files = None; self._thumbnail_buttons = {}; self._thumbnail_signature = (); self._thumbnail_generation = 0; self._thumbnail_scroll_anchor = 0; self._thumbnail_scroll_guard = False; self._thumbnail_icons = {}; self._thumbnail_render_queue = []; self._thumbnail_render_pending = set(); self._thumbnail_risk_queue = []; self._thumbnail_risk_pending = set(); self._quality_filename = ""; self._current_dirty = False
        self._autosave_timer = QTimer(self); self._autosave_timer.setSingleShot(True); self._autosave_timer.timeout.connect(self._autosave_current)
        self._notes_timer = QTimer(self); self._notes_timer.setSingleShot(True); self._notes_timer.timeout.connect(self._save_note)
        self._nav_hold_direction = 0; self._nav_hold_timer = QTimer(self); self._nav_hold_timer.setSingleShot(True); self._nav_hold_timer.timeout.connect(self._start_hold_navigation); self._nav_repeat_timer = QTimer(self); self._nav_repeat_timer.timeout.connect(self._repeat_navigation)
        self._thumbnail_scroll_timer = QTimer(self); self._thumbnail_scroll_timer.setSingleShot(True); self._thumbnail_scroll_timer.timeout.connect(self._refresh_thumbnails_for_scroll)
        self._thumbnail_render_timer = QTimer(self); self._thumbnail_render_timer.setSingleShot(True); self._thumbnail_render_timer.timeout.connect(self._process_thumbnail_render)
        self._thumbnail_risk_timer = QTimer(self); self._thumbnail_risk_timer.setSingleShot(True); self._thumbnail_risk_timer.timeout.connect(self._process_thumbnail_risk)
        self._quality_timer = QTimer(self); self._quality_timer.setSingleShot(True); self._quality_timer.timeout.connect(self._flush_quality_hint)
        self._prefetch_timer = QTimer(self); self._prefetch_timer.setSingleShot(True); self._prefetch_timer.timeout.connect(self._prefetch_adjacent_images)
        root = QVBoxLayout(self); root.setContentsMargins(24, 20, 24, 18); root.setSpacing(12)
        bar = card(); bar_layout = QVBoxLayout(bar); bar_layout.setContentsMargins(14, 8, 14, 8); bar_layout.setSpacing(3); tools = QHBoxLayout(); tools.setContentsMargins(0, 0, 0, 0); tools.setSpacing(4)
        paging = QFrame(bar); paging.setObjectName("paging"); paging.setFixedHeight(40); paging_layout = QHBoxLayout(paging); paging_layout.setContentsMargins(0, 0, 0, 0); paging_layout.setSpacing(0)
        self.previous = tool(paging, QIcon(), "上一张（←）；长按快速跳转"); self.previous.setObjectName("pagingChevron"); self.previous.setText("‹"); self._connect_navigation_button(self.previous, -1); paging_layout.addWidget(self.previous)
        self.position = label("0 / 0", "pagingPosition"); self.position.setAlignment(Qt.AlignCenter); self.position.setFixedSize(112, 40); paging_layout.addWidget(self.position)
        self.next = tool(paging, QIcon(), "下一张（→）；长按快速跳转"); self.next.setObjectName("pagingChevron"); self.next.setText("›"); self._connect_navigation_button(self.next, 1); paging_layout.addWidget(self.next); tools.addWidget(paging, 0, Qt.AlignVCenter)
        tools.addSpacing(16); self.tool_group = QButtonGroup(self); self.tool_group.setExclusive(True)
        self.pan_tool = tool(bar, sprite_icon("pan", size=24), "平移画布（H）\n滚轮缩放，双击适应", checkable=True)
        self.select_tool = tool(bar, sprite_icon("select", size=24), "选择、移动和缩放标注框（V）", checkable=True)
        self.box_tool = tool(bar, sprite_icon("box", size=24), "绘制矩形标注框（R）", checkable=True)
        self.polygon_tool = tool(bar, asset_icon("polygon-tool-icon-v2.png", size=24, glyph_scale=0.78), "绘制多边形标注（P）", checkable=True)
        for control, mode in ((self.pan_tool, "pan"), (self.select_tool, "select"), (self.box_tool, "box"), (self.polygon_tool, "polygon")):
            self.tool_group.addButton(control); control.toggled.connect(lambda checked=False, value=mode: self.canvas.set_tool(value) if checked else None); tools.addWidget(control)
        tools.addSpacing(8); undo = tool(bar, sprite_icon("undo"), "撤销（Ctrl+Z）"); undo.clicked.connect(self.undo); tools.addWidget(undo)
        redo = tool(bar, sprite_icon("redo"), "重做（Ctrl+Shift+Z）"); redo.clicked.connect(self.redo); tools.addWidget(redo)
        remove = tool(bar, sprite_icon("delete"), "删除选中标注（Delete）"); remove.clicked.connect(self.delete_selected); tools.addWidget(remove)
        tools.addStretch()
        review_actions = QHBoxLayout(); review_actions.setContentsMargins(0, 0, 0, 0); review_actions.setSpacing(8)
        self.issue_filter = FlatComboBox(); self.issue_filter.addItem("全部待审核", "all"); self.issue_filter.addItem("风险队列", "risk"); self.issue_filter.addItem("仅问题项", "issues"); self.issue_filter.addItem("低置信度", "low_confidence"); self.issue_filter.addItem("空标注", "empty"); self.issue_filter.setToolTip("“全部待审核”不会预扫描全部图片；需要按风险排序时再切换到“风险队列”。"); self.issue_filter.currentIndexChanged.connect(self.refresh); review_actions.addWidget(self.issue_filter)
        shortcut_menu = QMenu(self)
        shortcut_menu.setStyleSheet("QMenu {background:#FFFFFF;border:1px solid #E3E5E9;border-radius:12px;padding:6px;} QMenu::item {padding:7px 16px;border-radius:7px;color:#2E3138;} QMenu::item:selected {background:#EAF3FF;color:#0A74FF;} QMenu::item:disabled {color:#737780;font-weight:700;} QMenu::separator {height:1px;background:#ECEEF2;margin:5px 8px;}")
        for caption in ("画布工具", "H  平移画布", "V  选择、移动和缩放", "R  矩形标注", "P  多边形标注"):
            action = shortcut_menu.addAction(caption); action.setEnabled(caption != "画布工具")
        shortcut_menu.addSeparator()
        for caption in ("审核与文件", "← / →  上一张 / 下一张", "Ctrl+Z / Ctrl+Shift+Z  撤销 / 重做", "Delete  删除", "Ctrl+S  保存", "L  标注清单", "A / S  通过 / 跳过并下一张"):
            action = shortcut_menu.addAction(caption); action.setEnabled(caption != "审核与文件")
        shortcut_button = QToolButton(bar); shortcut_button.setObjectName("shortcutGuide"); shortcut_button.setText("⌘"); shortcut_button.setFixedSize(40, 40); shortcut_button.setToolTip("快捷键速查"); shortcut_button.clicked.connect(lambda: shortcut_menu.exec(shortcut_button.mapToGlobal(shortcut_button.rect().bottomLeft()))); shortcut_button.setCursor(Qt.PointingHandCursor); review_actions.insertWidget(0, shortcut_button)
        versions = button("版本"); versions.setToolTip("查看或恢复当前图片的预标注版本"); versions.clicked.connect(self._show_versions); review_actions.addWidget(versions)
        self.list_button = button("标注清单"); self.list_button.setCheckable(True); self.list_button.setToolTip("打开标注清单（L）"); self.list_button.clicked.connect(self.toggle_annotation_drawer); review_actions.addWidget(self.list_button)
        save = button("保存", primary=True); save.setToolTip("保存标注（Ctrl+S）"); save.clicked.connect(self.save); review_actions.addWidget(save); tools.addLayout(review_actions); bar_layout.addLayout(tools); self.queue_summary = label("", "muted"); self.queue_summary.setObjectName("queueSummary"); bar_layout.addWidget(self.queue_summary); root.addWidget(bar)
        work = QHBoxLayout(); work.setSpacing(14); self.canvas_card = card(); canvas_layout = QVBoxLayout(self.canvas_card); canvas_layout.setContentsMargins(10, 10, 10, 10); self.canvas = ImageCanvas(); self.canvas.shapes_changed.connect(self._canvas_changed); self.canvas.selection_changed.connect(self._selection_changed); self.canvas.box_created.connect(self._new_box_created); self.canvas.polygon_created.connect(self._new_box_created); self.pan_tool.setChecked(True); canvas_layout.addWidget(self.canvas); work.addWidget(self.canvas_card, 1)
        self._build_annotation_drawer()
        inspector = card(); inspector.setFixedWidth(280); inspector_layout = QVBoxLayout(inspector); inspector_layout.setContentsMargins(20, 20, 20, 20); inspector_layout.setSpacing(0); form_host = QWidget(); form = QVBoxLayout(form_host); form.setContentsMargins(0, 0, 0, 0); form.setSpacing(10); form.addWidget(label("标签", "section")); form.addSpacing(8)
        form.addWidget(label("显示类别（可多选）", "muted")); self.filter_collapsed = False; self.filter_host = QWidget(); self.filter_layout = QVBoxLayout(self.filter_host); self.filter_layout.setContentsMargins(0, 0, 0, 0); self.filter_layout.setSpacing(4); self.filter_layout.setAlignment(Qt.AlignTop); self.filter_scroll = QScrollArea(); self.filter_scroll.setWidgetResizable(True); self.filter_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff); self.filter_scroll.setFixedHeight(104); self.filter_scroll.setWidget(self.filter_host)
        filter_section = QWidget(); filter_section_layout = QVBoxLayout(filter_section); filter_section_layout.setContentsMargins(0, 0, 0, 0); filter_section_layout.setSpacing(0); filter_section_layout.addWidget(self.filter_scroll)
        collapse_row = QHBoxLayout(); collapse_row.setContentsMargins(0, 0, 0, 0); collapse_row.setSpacing(7); left_line = QFrame(); left_line.setFixedHeight(1); left_line.setStyleSheet("background:#E7EAF0;"); right_line = QFrame(); right_line.setFixedHeight(1); right_line.setStyleSheet("background:#E7EAF0;"); self.filter_collapse = DoubleChevronButton(); self.filter_collapse.clicked.connect(self.toggle_category_filters); collapse_row.addWidget(left_line, 1); collapse_row.addWidget(self.filter_collapse); collapse_row.addWidget(right_line, 1); filter_section_layout.addLayout(collapse_row); form.addWidget(filter_section)
        form.addSpacing(8); form.addWidget(label("选中框标签", "muted")); self.category = QLineEdit(); self.category.setPlaceholderText("选择框后可修改标签名"); self.category.setEnabled(False); form.addWidget(self.category); self.annotation_state_hint = label("", "muted"); self.annotation_state_hint.setWordWrap(True); form.addWidget(self.annotation_state_hint); form.addSpacing(8); form.addWidget(label("状态", "muted"))
        segments = QHBoxLayout(); self.status_group = QButtonGroup(self); self.status_group.setExclusive(True)
        for caption, state in (("审核", STATUS_NEEDS_REVIEW), ("通过", STATUS_APPROVED), ("跳过", STATUS_SKIPPED)):
            item = QPushButton(caption); item.setCheckable(True); item.setObjectName("secondary"); item.clicked.connect(lambda checked=False, s=state: self.set_status(s)); self.status_group.addButton(item); segments.addWidget(item)
            if state == STATUS_NEEDS_REVIEW: item.setChecked(True)
        form.addLayout(segments); self.quality_hint = label("", "muted"); self.quality_hint.setWordWrap(True); form.addWidget(self.quality_hint)
        # Keep notes and navigation outside the scrollable controls. Both stay
        # visible and have a hard boundary even in a short workbench window.
        notes_host = QWidget(); notes_host.setFixedHeight(121); notes_layout = QVBoxLayout(notes_host); notes_layout.setContentsMargins(0, 0, 0, 0); notes_layout.setSpacing(7); notes_layout.addWidget(label("备注", "muted")); self.notes = QPlainTextEdit(); self.notes.setPlaceholderText("请输入备注（选填）"); self.notes.setFixedHeight(98); self.notes.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed); notes_layout.addWidget(self.notes)
        # The top form scrolls independently when vertical room is scarce.
        form_scroll = QScrollArea(); form_scroll.setWidgetResizable(True); form_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff); form_scroll.setFrameShape(QFrame.NoFrame); form_scroll.setStyleSheet("""
            QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }
            QScrollBar:vertical { background: transparent; width: 10px; margin: 5px 1px; border: 0; }
            QScrollBar::handle:vertical { background: rgba(60, 60, 67, 72); min-height: 34px; border-radius: 4px; margin: 1px 2px; }
            QScrollBar::handle:vertical:hover { background: rgba(60, 60, 67, 118); }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; background: transparent; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
        """); form_scroll.setWidget(form_host); inspector_layout.addWidget(form_scroll, 1)
        inspector_layout.addSpacing(12); inspector_layout.addWidget(notes_host, 0); inspector_layout.addSpacing(16)
        self.image_nav_host = QWidget(); self.image_nav_host.setFixedHeight(40); image_nav = QHBoxLayout(self.image_nav_host); image_nav.setContentsMargins(0, 0, 0, 0); image_nav.setSpacing(8); self.bottom_previous = button("‹ 上一张"); self._connect_navigation_button(self.bottom_previous, -1); self.bottom_next = button("下一张 ›"); self.bottom_next.setToolTip("点击下一张；长按可快速跳转"); self._connect_navigation_button(self.bottom_next, 1); image_nav.addWidget(self.bottom_previous); image_nav.addWidget(self.bottom_next); inspector_layout.addWidget(self.image_nav_host, 0); work.addWidget(inspector); root.addLayout(work, 1)
        strip = card(); strip_layout = QVBoxLayout(strip); strip_layout.setContentsMargins(10, 8, 10, 7); strip_layout.setSpacing(0); self.thumbnails = QScrollArea(); self.thumbnails.setWidgetResizable(False); self.thumbnails.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded); self.thumbnails.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff); self.thumb_host = QWidget(); self.thumb_host.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed); self.thumb_host.setFixedHeight(74); self.thumbnails.setWidget(self.thumb_host); self.thumbnails.horizontalScrollBar().valueChanged.connect(self._thumbnail_scroll_changed); strip_layout.addWidget(self.thumbnails); strip.setFixedHeight(106); root.addWidget(strip)
        footer = QHBoxLayout(); footer.addWidget(label("审核进度", "muted")); self.progress = QProgressBar(); self.progress.setObjectName("reviewProgress"); self.progress.setTextVisible(False); self.progress.setFixedHeight(4); footer.addWidget(self.progress, 1, Qt.AlignVCenter); self.progress_text = label("0%", "muted"); footer.addWidget(self.progress_text); root.addLayout(footer)
        QShortcut(QKeySequence("Left"), self, activated=lambda: self.move(-1))
        QShortcut(QKeySequence("Right"), self, activated=lambda: self.move(1))
        QShortcut(QKeySequence("H"), self, activated=lambda: self.pan_tool.setChecked(True))
        QShortcut(QKeySequence("V"), self, activated=lambda: self.select_tool.setChecked(True))
        QShortcut(QKeySequence("R"), self, activated=lambda: self.box_tool.setChecked(True))
        QShortcut(QKeySequence("P"), self, activated=lambda: self.polygon_tool.setChecked(True))
        QShortcut(QKeySequence("L"), self, activated=self.toggle_annotation_drawer)
        QShortcut(QKeySequence("Ctrl+S"), self, activated=self.save)
        self.category.editingFinished.connect(self._apply_selected_category)
        QShortcut(QKeySequence("Ctrl+Z"), self, activated=self.undo)
        QShortcut(QKeySequence("Ctrl+Shift+Z"), self, activated=self.redo)
        QShortcut(QKeySequence("Delete"), self, activated=self.delete_selected)
        QShortcut(QKeySequence("A"), self, activated=lambda: self._quick_review(STATUS_APPROVED))
        QShortcut(QKeySequence("S"), self, activated=lambda: self._quick_review(STATUS_SKIPPED))
        self.notes.textChanged.connect(self._schedule_note_save)

    def _build_annotation_drawer(self):
        """Build an overlay list so dense images never squeeze the canvas."""
        self.annotation_drawer = QFrame(self.canvas_card)
        self.annotation_drawer.setObjectName("annotationDrawer")
        self.annotation_drawer.setFixedWidth(360)
        self.annotation_drawer.setVisible(False)
        drawer = QVBoxLayout(self.annotation_drawer); drawer.setContentsMargins(16, 16, 16, 16); drawer.setSpacing(10)
        header = QHBoxLayout(); header.addWidget(label("标注清单", "section")); self.drawer_count = label("0 个框", "muted"); header.addWidget(self.drawer_count); header.addStretch()
        close = QToolButton(); close.setText("×"); close.setToolTip("关闭标注清单（L）"); close.setFixedSize(28, 28); close.setCursor(Qt.PointingHandCursor); close.setStyleSheet("border:0;border-radius:8px;font-size:20px;color:#555B66;"); close.clicked.connect(lambda: self.toggle_annotation_drawer(False)); header.addWidget(close); drawer.addLayout(header)
        self.drawer_search = QLineEdit(); self.drawer_search.setPlaceholderText("搜索类别或框编号"); self.drawer_search.textChanged.connect(self._refresh_annotation_drawer); drawer.addWidget(self.drawer_search)
        self.drawer_category = FlatComboBox(); self.drawer_category.currentIndexChanged.connect(self._refresh_annotation_drawer); drawer.addWidget(self.drawer_category)
        self.drawer_scroll = QScrollArea(); self.drawer_scroll.setWidgetResizable(True); self.drawer_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff); self.drawer_scroll.setStyleSheet("""
            QScrollArea, QScrollArea > QWidget > QWidget { border:0; background:transparent; }
            QScrollBar:vertical { background:transparent; width:10px; margin:5px 1px; border:0; }
            QScrollBar::handle:vertical { background:rgba(60, 60, 67, 72); min-height:34px; border-radius:4px; margin:1px 2px; }
            QScrollBar::handle:vertical:hover { background:rgba(60, 60, 67, 118); }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; background:transparent; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background:transparent; }
        """); self.drawer_rows = QWidget(); self.drawer_rows.setStyleSheet("background:transparent;"); self.drawer_rows_layout = QVBoxLayout(self.drawer_rows); self.drawer_rows_layout.setContentsMargins(0, 0, 0, 0); self.drawer_rows_layout.setSpacing(7); self.drawer_scroll.setWidget(self.drawer_rows); drawer.addWidget(self.drawer_scroll, 1)
        self.canvas_card.installEventFilter(self)

    def eventFilter(self, watched, event):
        if watched is getattr(self, "canvas_card", None) and event.type() == QEvent.Resize:
            self._position_annotation_drawer()
        return super().eventFilter(watched, event)

    def _position_annotation_drawer(self):
        if not self.annotation_drawer.isVisible():
            return
        margin = 12
        width = min(360, max(260, self.canvas_card.width() - margin * 2))
        height = max(180, self.canvas_card.height() - margin * 2)
        self.annotation_drawer.setGeometry(self.canvas_card.width() - width - margin, margin, width, height)
        self.annotation_drawer.raise_()

    def toggle_annotation_drawer(self, checked=None):
        visible = not self.annotation_drawer.isVisible() if checked is None else bool(checked)
        self.annotation_drawer.setVisible(visible)
        self.list_button.setChecked(visible)
        if visible:
            self._refresh_annotation_drawer()
            self._position_annotation_drawer()

    def _drawer_categories(self):
        current = self.drawer_category.currentData()
        categories = self._available_categories()
        self.drawer_category.blockSignals(True); self.drawer_category.clear(); self.drawer_category.addItem("全部类别", "")
        for name in categories:
            self.drawer_category.addItem(name, name)
        target = self.drawer_category.findData(current)
        self.drawer_category.setCurrentIndex(target if target >= 0 else 0); self.drawer_category.blockSignals(False)

    def _refresh_annotation_drawer(self):
        if not hasattr(self, "annotation_drawer"):
            return
        self._drawer_categories()
        query = self.drawer_search.text().strip().lower()
        category = self.drawer_category.currentData() or ""
        while self.drawer_rows_layout.count():
            entry = self.drawer_rows_layout.takeAt(0); widget = entry.widget(); widget.deleteLater() if widget else None
        shown = 0
        for index, shape in enumerate(self.canvas.shapes):
            name = str(shape.get("name", "object"))
            if category and name != category:
                continue
            x1, y1, x2, y2 = self.canvas._shape_rect(index)
            width, height = int(x2 - x1), int(y2 - y1)
            searchable = f"{name} {index + 1} {x1} {y1} {width} {height}".lower()
            if query and query not in searchable:
                continue
            row = QWidget(); row_layout = QHBoxLayout(row); row_layout.setContentsMargins(0, 0, 0, 0); row_layout.setSpacing(6)
            shape_type = "多边形" if shape.get("type") == "polygon" else "矩形"
            provenance = ImageCanvas.provenance_text(shape)
            item = QToolButton(); item.setObjectName("annotationRow"); item.setCheckable(True); item.setChecked(index == self.canvas.selected_index); item.setToolButtonStyle(Qt.ToolButtonTextOnly); item.setText(f"{name}   #{index + 1}  ·  {shape_type}\n{provenance}   ·   {width} × {height}"); item.setFixedHeight(54)
            color = self.canvas.color_for_label(name).name(); item.setStyleSheet(f"QToolButton {{background:#FFFFFF; border:1px solid #E7E9ED; border-left:4px solid {color}; border-radius:10px; text-align:left; padding:7px 10px; color:#2E3138;}} QToolButton:hover {{background:#F6F9FE; border-color:#CFE1FF; border-left:4px solid {color};}} QToolButton:checked {{background:#EAF3FF; border-color:#0A74FF; border-left:4px solid {color}; font-weight:700;}}")
            item.setToolTip("定位并选中此标注框"); item.clicked.connect(lambda checked=False, target=index: self._select_annotation_from_list(target)); row_layout.addWidget(item, 1)
            visible = QToolButton(); visible.setObjectName("visibilityCheck"); visible.setCheckable(True); visible.setFixedSize(18, 18); visible.setChecked(index not in self.canvas.hidden_indices); visible.setText("✓" if visible.isChecked() else ""); visible.setToolTip("在画布中显示此标注"); visible.toggled.connect(lambda checked, target=index, control=visible: self._toggle_annotation_visibility(control, target, checked)); row_layout.addWidget(visible, 0, Qt.AlignVCenter)
            self.drawer_rows_layout.addWidget(row); shown += 1
        if not shown:
            empty = label("没有匹配的标注框", "muted"); empty.setAlignment(Qt.AlignCenter); self.drawer_rows_layout.addWidget(empty)
        self.drawer_rows_layout.addStretch()
        self.drawer_count.setText(f"{len(self.canvas.shapes)} 个框 · 显示 {shown}")

    def _set_annotation_visible(self, index: int, visible: bool):
        hidden = set(self.canvas.hidden_indices)
        hidden.discard(index) if visible else hidden.add(index)
        self.canvas.set_hidden_indices(hidden)
        self._refresh_annotation_drawer()

    def _toggle_annotation_visibility(self, control: QToolButton, index: int, visible: bool):
        control.setText("✓" if visible else "")
        self._set_annotation_visible(index, visible)

    def _select_annotation_from_list(self, index: int):
        if not 0 <= index < len(self.canvas.shapes):
            return
        name = self.canvas.shapes[index].get("name", "")
        if name in self._filter_checks and not self._filter_checks[name].isChecked():
            self._filter_checks[name].setChecked(True)
        hidden = set(self.canvas.hidden_indices); hidden.discard(index); self.canvas.set_hidden_indices(hidden)
        self.select_tool.setChecked(True)
        self.canvas.focus_shape(index)
        self._refresh_annotation_drawer()

    def _issues_for(self, filename):
        """Evaluate the shared QA rule set for one review image."""
        xml_path = os.path.join(self.window.annotations_dir, Path(filename).stem + ".xml")
        try:
            shapes = core.load_voc_xml(xml_path) if os.path.isfile(xml_path) else []
        except Exception:
            return {"invalid"}
        return core.annotation_issues(shapes, os.path.join(self.window.images_dir, filename))

    def _risk_for(self, filename):
        cached = self._risk_cache.get(filename)
        if cached is not None:
            return cached
        xml_path = os.path.join(self.window.annotations_dir, Path(filename).stem + ".xml")
        try:
            shapes = core.load_voc_xml(xml_path) if os.path.isfile(xml_path) else []
            result = core.annotation_risk(shapes, os.path.join(self.window.images_dir, filename))
        except Exception:
            result = 100, {"invalid"}
        self._risk_cache[filename] = result
        return result

    def _update_queue_summary(self):
        if self._spot_check_files is not None:
            records = self.window.project.spot_checks_for(self._spot_check_files) if self.window.project else {}
            pending = sum(record.get("state") == "sampled" for record in records.values())
            self.queue_summary.setText(f"最终抽检队列 · 待核验 {pending} 张；通过后保留为训练候选，需修改则点“审核”退回待审核")
            return
        files = self.window.project_images(STATUS_NEEDS_REVIEW)
        if self.issue_filter.currentData() == "risk":
            scores = [self._risk_for(filename)[0] for filename in files]
            high = sum(score >= config.REVIEW_HIGH_RISK for score in scores)
            attention = sum(0 < score < config.REVIEW_HIGH_RISK for score in scores)
            safe = sum(score == 0 for score in scores)
            self.queue_summary.setText(f"风险队列：高风险 {high} 张 · 待关注 {attention} 张 · 已隐藏无风险 {safe} 张")
            self.issue_filter.setItemText(self.issue_filter.findData("risk"), f"风险队列（{high + attention}）")
        else:
            self.queue_summary.setText(f"待审核 {len(files)} 张 · 可切换“风险队列”按质量规则筛选")
            self.issue_filter.setItemText(self.issue_filter.findData("risk"), "风险队列")

    def _filtered_files(self):
        if self._spot_check_files is not None:
            records = self.window.project.spot_checks_for(self._spot_check_files) if self.window.project else {}
            return [filename for filename in self._spot_check_files if records.get(filename, {}).get("state") == "sampled"]
        files = self.window.project_images(STATUS_NEEDS_REVIEW)
        mode = self.issue_filter.currentData() if hasattr(self, "issue_filter") else "all"
        if mode == "risk":
            return sorted(
                (filename for filename in files if self._risk_for(filename)[0] > 0),
                key=lambda filename: (-self._risk_for(filename)[0], filename.lower()),
            )
        if mode == "all":
            return files
        result = [filename for filename in files if (mode in self._issues_for(filename) if mode != "issues" else bool(self._issues_for(filename)))]
        return sorted(result, key=lambda filename: (-self._risk_for(filename)[0], filename.lower()))

    def _update_quality_hint(self, filename):
        labels = {
            "empty": "空标注", "low_confidence": "低置信度", "tiny": "极小框",
            "out_of_bounds": "越界框", "duplicate": "疑似重复框", "invalid": "标注文件异常",
        }
        risk, issues = self._risk_for(filename)
        level = "高" if risk >= config.REVIEW_HIGH_RISK else "中" if risk else "低"
        detail = "、".join(labels[item] for item in sorted(issues)) if issues else "未发现明显问题"
        self.quality_hint.setText(f"风险{level} · {risk}分\n质量规则：{detail}")
        self.quality_hint.setStyleSheet("color:#C2410C;" if risk >= config.REVIEW_HIGH_RISK else "color:#D97706;" if issues else "color:#17834C;")

    def _schedule_quality_hint(self, filename):
        if filename in self._risk_cache:
            self._update_quality_hint(filename)
            return
        self._quality_filename = filename
        self.quality_hint.setText("正在检查标注质量…")
        self.quality_hint.setStyleSheet("color:#737780;")
        self._quality_timer.start(140)

    def _flush_quality_hint(self):
        if self.files and self._quality_filename == self.files[self.index]:
            self._update_quality_hint(self._quality_filename)

    def refresh(self):
        self._save_current(silent=True)
        project_root = os.path.abspath(self.window.project_root) if self.window.project_root else ""
        if project_root != self._risk_project_root:
            self._risk_cache = {}
            self._risk_project_root = project_root
        self._update_queue_summary()
        files = self._filtered_files()
        if files != self.files:
            self._thumbnail_generation += 1
        self.files = files; self.index = min(self.index, max(0, len(self.files) - 1)); self._load()

    def open_spot_check(self, filenames):
        self._spot_check_files = list(dict.fromkeys(filenames)); self.issue_filter.setEnabled(False); self.refresh()

    def clear_spot_check(self):
        if self._spot_check_files is None:
            return
        self._spot_check_files = None; self.issue_filter.setEnabled(True)

    def _connect_navigation_button(self, control, direction):
        control.pressed.connect(lambda value=direction: self._begin_navigation_hold(value))
        control.released.connect(self._end_navigation_hold)

    def _begin_navigation_hold(self, direction):
        if not self.files:
            return
        self._nav_hold_direction = direction
        self._nav_hold_timer.start(380)

    def _end_navigation_hold(self):
        direction = self._nav_hold_direction
        if not direction:
            return
        if self._nav_hold_timer.isActive():
            self._nav_hold_timer.stop()
            self.move(direction)
        self._nav_repeat_timer.stop()
        self._nav_hold_direction = 0

    def _start_hold_navigation(self):
        if not self._nav_hold_direction:
            return
        self.move(self._nav_hold_direction)
        self._nav_repeat_timer.start(150)

    def _repeat_navigation(self):
        direction = self._nav_hold_direction
        at_end = direction > 0 and self.index >= len(self.files) - 1
        at_start = direction < 0 and self.index <= 0
        if not direction or not self.files or at_end or at_start:
            self._nav_repeat_timer.stop()
            return
        self.move(direction)

    def _ensure_thumbnail_strip(self, anchor_index=None):
        total = len(self.files)
        visible_slots = max(8, int(max(1, self.thumbnails.viewport().width()) / 126) + 2)
        window_size = min(total, max(30, visible_slots * 3))
        anchor = self.index if anchor_index is None else max(0, min(total - 1, anchor_index))
        current_start = self._thumbnail_signature[1] if len(self._thumbnail_signature) == 3 and self._thumbnail_signature[0] == self._thumbnail_generation else -1
        buffer = max(4, window_size // 3)
        if current_start >= 0 and current_start + buffer <= anchor < current_start + window_size - buffer and len(self._thumbnail_buttons) == window_size:
            return
        start = max(0, min(anchor - window_size // 2, total - window_size))
        visible_indices = range(start, start + window_size)
        signature = (self._thumbnail_generation, start, window_size)
        if signature == self._thumbnail_signature and len(self._thumbnail_buttons) == window_size:
            return
        for item in self._thumbnail_buttons.values():
            item.setParent(None); item.deleteLater()
        self._thumbnail_render_timer.stop()
        self._thumbnail_render_queue = []
        self._thumbnail_render_pending = set()
        self._thumbnail_risk_timer.stop()
        self._thumbnail_risk_queue = []
        self._thumbnail_risk_pending = set()
        self._thumbnail_signature = signature
        self._thumbnail_buttons = {}
        for index in sorted(visible_indices, key=lambda value: abs(value - anchor)):
            filename = self.files[index]
            item = QToolButton(self.thumb_host); item.move(index * 126, 0); item.setFixedSize(116, 74); item.setCheckable(True); item.setIcon(self._thumbnail_icons.get(filename, QIcon())); item.setIconSize(QSize(108, 66)); item.setToolTip(f"#{index + 1} · {filename}")
            if filename not in self._thumbnail_icons:
                self._queue_thumbnail_render(index, filename)
            cached = self._risk_cache.get(filename)
            if cached is None:
                self._apply_thumbnail_risk(item, None)
                self._queue_thumbnail_risk(index, filename)
            else:
                self._apply_thumbnail_risk(item, cached[0])
            item.clicked.connect(lambda checked=False, target=index: self.select(target)); item.show(); self._thumbnail_buttons[index] = item
        self.thumb_host.setFixedWidth(max(1, total * 126 - 10))

    def _schedule_thumbnail_fill(self, delay=80):
        self._thumbnail_render_timer.stop()
        self._thumbnail_risk_timer.stop()
        if self._thumbnail_render_queue:
            self._thumbnail_render_timer.start(delay)
        if self._thumbnail_risk_queue:
            self._thumbnail_risk_timer.start(delay + 35)

    def _queue_thumbnail_render(self, index, filename):
        if filename not in self._thumbnail_render_pending:
            self._thumbnail_render_pending.add(filename)
            self._thumbnail_render_queue.append((index, filename))

    def _process_thumbnail_render(self):
        while self._thumbnail_render_queue:
            index, filename = self._thumbnail_render_queue.pop(0)
            self._thumbnail_render_pending.discard(filename)
            item = self._thumbnail_buttons.get(index)
            if item is None or index >= len(self.files) or self.files[index] != filename:
                continue
            item.setIcon(self._thumbnail_icon(filename))
            break
        if self._thumbnail_render_queue:
            self._thumbnail_render_timer.start(20)

    def _thumbnail_icon(self, filename):
        cached = self._thumbnail_icons.pop(filename, None)
        if cached is not None:
            self._thumbnail_icons[filename] = cached
            return cached
        pixmap = QPixmap(os.path.join(self.window.images_dir, filename))
        if not pixmap.isNull():
            pixmap = pixmap.scaled(108, 66, Qt.KeepAspectRatio, Qt.FastTransformation)
        icon = QIcon(pixmap)
        self._thumbnail_icons[filename] = icon
        while len(self._thumbnail_icons) > 80:
            self._thumbnail_icons.pop(next(iter(self._thumbnail_icons)))
        return icon

    @staticmethod
    def _thumbnail_risk_color(risk):
        if risk is None:
            return "#D8DEE7"
        if risk >= config.REVIEW_HIGH_RISK:
            return "#FF3B30"
        if risk > 0:
            return "#E6B800"
        return "#31A56C"

    def _apply_thumbnail_risk(self, item, risk):
        color = self._thumbnail_risk_color(risk)
        item.setStyleSheet("QToolButton {border:2px solid transparent; border-radius:8px; background:#FFFFFF;} QToolButton:hover {border-color:#BFD8FF; background:#F8FBFF;} QToolButton:checked {border-color:#0A74FF; background:#EEF6FF;}")
        badge = item.findChild(QLabel, "thumbnailRiskBadge")
        if badge is None:
            badge = QLabel(item); badge.setObjectName("thumbnailRiskBadge"); badge.setAttribute(Qt.WA_TransparentForMouseEvents); badge.setAlignment(Qt.AlignCenter); badge.setGeometry(6, 6, 30, 19); badge.show()
        badge.setText("…" if risk is None else str(risk))
        badge.setStyleSheet(f"background:#FFFFFF;color:{color};border:1px solid {color};border-radius:9px;font-size:10px;font-weight:700;")

    def _queue_thumbnail_risk(self, index, filename):
        if filename not in self._thumbnail_risk_pending:
            self._thumbnail_risk_pending.add(filename)
            self._thumbnail_risk_queue.append((index, filename))

    def _process_thumbnail_risk(self):
        while self._thumbnail_risk_queue:
            index, filename = self._thumbnail_risk_queue.pop(0)
            self._thumbnail_risk_pending.discard(filename)
            item = self._thumbnail_buttons.get(index)
            if item is None or index >= len(self.files) or self.files[index] != filename:
                continue
            risk = self._risk_for(filename)[0]
            self._apply_thumbnail_risk(item, risk)
            break
        if self._thumbnail_risk_queue:
            self._thumbnail_risk_timer.start(28)

    def _thumbnail_scroll_changed(self, value):
        if self._thumbnail_scroll_guard or not self.files:
            return
        step = 126
        self._thumbnail_scroll_anchor = max(0, min(len(self.files) - 1, int((value + self.thumbnails.viewport().width() / 2) / step)))
        self._thumbnail_scroll_timer.start(70)

    def _refresh_thumbnails_for_scroll(self):
        self._ensure_thumbnail_strip(self._thumbnail_scroll_anchor)
        self._update_thumbnail_selection(scroll_current=False)
        self._schedule_thumbnail_fill(16)

    def _update_thumbnail_selection(self, scroll_current=True):
        for index, item in self._thumbnail_buttons.items():
            item.setChecked(index == self.index)
        if scroll_current:
            QTimer.singleShot(0, self._scroll_to_current_thumbnail)

    def _scroll_to_current_thumbnail(self):
        if self.index not in self._thumbnail_buttons:
            return
        item = self._thumbnail_buttons[self.index]
        target = item.x() + item.width() // 2 - self.thumbnails.viewport().width() // 2
        scrollbar = self.thumbnails.horizontalScrollBar()
        self._thumbnail_scroll_guard = True
        scrollbar.setValue(max(scrollbar.minimum(), min(scrollbar.maximum(), target)))
        self._thumbnail_scroll_guard = False

    def _load(self):
        self._ensure_thumbnail_strip()
        total = len(self.files)
        current = self.index + 1 if total else 0
        percent = round(current * 100 / total) if total else 0
        self.position.setText(f"{current} / {total}"); self.progress.setValue(percent); self.progress_text.setText(f"{percent}%")
        self._update_thumbnail_selection()
        self._schedule_thumbnail_fill(100)
        if not total:
            self.notes.blockSignals(True); self.notes.clear(); self.notes.blockSignals(False)
            self.quality_hint.setText("")
            self._update_annotation_state_hint(-1)
            self.canvas.set_content(None, []); self._refresh_annotation_drawer(); return
        filename = self.files[self.index]; image_path = os.path.join(self.window.images_dir, filename); xml_path = os.path.join(self.window.annotations_dir, Path(filename).stem + ".xml")
        try: shapes = core.load_voc_xml(xml_path) if os.path.isfile(xml_path) else []
        except Exception: shapes = []
        self.canvas.set_content(image_path, shapes)
        self._current_dirty = False
        self.notes.blockSignals(True); self.notes.setPlainText(self.window.project.note_for(filename) if self.window.project else ""); self.notes.blockSignals(False)
        self._schedule_quality_hint(filename)
        self._prefetch_timer.start(90)
        self.category.clear(); self.category.setEnabled(False)
        self._update_annotation_state_hint(-1)
        self._rebuild_category_filters()
        self._refresh_annotation_drawer()

    def _prefetch_adjacent_images(self):
        if not self.files:
            return
        nearby = [
            os.path.join(self.window.images_dir, self.files[offset])
            for offset in (self.index + 1, self.index + 2, self.index - 1)
            if 0 <= offset < len(self.files)
        ]
        self.canvas.prefetch(nearby)

    def select(self, index: int):
        self._save_current(silent=True); self.index = index; self._load()
    def move(self, delta: int):
        if self.files:
            target = max(0, min(len(self.files) - 1, self.index + delta))
            if target != self.index:
                self._save_current(silent=True); self.index = target; self._load()
    def _available_categories(self):
        project_categories = self.window.project.categories if self.window.project else []
        names = project_categories + [shape.get("name", "") for shape in self.canvas.shapes]
        result = []
        for name in names:
            name = str(name).strip()
            if name and name not in result:
                result.append(name)
        return result

    def toggle_category_filters(self):
        """Collapse the visibility filters without hiding the rest of the inspector."""
        self.filter_collapsed = not self.filter_collapsed
        self.filter_scroll.setVisible(not self.filter_collapsed)
        self.filter_collapse.set_collapsed(self.filter_collapsed)

    def _rebuild_category_filters(self, selected=None):
        select_all = selected is None
        if selected is None:
            selected = set()
        categories = self._available_categories()
        while self.filter_layout.count():
            entry = self.filter_layout.takeAt(0); widget = entry.widget(); widget.deleteLater() if widget else None
        self._filter_checks = {}
        for name in categories:
            item = QCheckBox(name); item.setChecked(select_all or name in selected); item.toggled.connect(self._apply_category_filters)
            self.filter_layout.addWidget(item); self._filter_checks[name] = item
        self.filter_layout.addStretch()
        self._apply_category_filters()

    def _apply_category_filters(self):
        self.canvas.set_visible_categories({name for name, item in self._filter_checks.items() if item.isChecked()})

    def _apply_selected_category(self):
        name = self.category.text().strip()
        if not name or not 0 <= self.canvas.selected_index < len(self.canvas.shapes):
            return
        if self.window.project and name not in self.window.project.categories:
            self.window.project.set_categories(self.window.project.categories + [name])
        self.canvas.set_default_label(name)
        self.canvas.apply_category(name)
        self._update_annotation_state_hint(self.canvas.selected_index)
        self._rebuild_category_filters({name for name, item in self._filter_checks.items() if item.isChecked()} | {name})
        self._refresh_annotation_drawer()

    def _selection_changed(self, index: int):
        if 0 <= index < len(self.canvas.shapes):
            name = self.canvas.shapes[index].get("name", "")
            self.category.setText(name); self.category.setEnabled(True)
        else:
            self.category.clear(); self.category.setEnabled(False)
        self._update_annotation_state_hint(index)
        self._refresh_annotation_drawer()

    def _update_annotation_state_hint(self, index):
        if 0 <= index < len(self.canvas.shapes):
            self.annotation_state_hint.setText("标注来源：" + ImageCanvas.provenance_text(self.canvas.shapes[index]))
        else:
            self.annotation_state_hint.setText("")

    def _new_box_created(self, index: int):
        categories = self._available_categories() or ["object"]
        annotation_type = "多边形" if 0 <= index < len(self.canvas.shapes) and self.canvas.shapes[index].get("type") == "polygon" else "矩形"
        value, ok = ios_item_input(self, f"新增{annotation_type}标注", "类别（可输入新类别）：", categories, 0, True, "创建")
        if not ok or not value.strip():
            self.canvas.delete_selected()
            self.window.status_message("已取消新增标注")
            return
        self.category.setText(value.strip()); self.category.setEnabled(True)
        self._apply_selected_category()

    def _canvas_changed(self, _shapes):
        self._current_dirty = True
        self._autosave_timer.start(650)
        self._update_annotation_state_hint(self.canvas.selected_index)
        self.window.status_message("标注已修改，正在自动保存…")
        self._refresh_annotation_drawer()

    def _autosave_current(self):
        self._save_current(silent=False, automatic=True)

    def flush_autosave(self):
        """Persist the visible image before the workbench swaps project paths."""
        self._save_current(silent=True)

    def clear_project_context(self):
        """Drop the previous project's canvas before directory paths are replaced."""
        self._autosave_timer.stop()
        self._notes_timer.stop()
        self._thumbnail_scroll_timer.stop()
        self._thumbnail_render_timer.stop()
        self._thumbnail_risk_timer.stop()
        self._quality_timer.stop()
        self._prefetch_timer.stop()
        self.files = []
        self.index = 0
        self._spot_check_files = None
        self.issue_filter.setEnabled(True)
        self._risk_cache = {}
        self._risk_project_root = ""
        self._thumbnail_signature = ()
        self._thumbnail_generation += 1
        self._thumbnail_icons = {}
        self._thumbnail_render_queue = []
        self._thumbnail_render_pending = set()
        self._thumbnail_risk_queue = []
        self._thumbnail_risk_pending = set()
        self._current_dirty = False
        for item in self._thumbnail_buttons.values():
            item.setParent(None); item.deleteLater()
        self._thumbnail_buttons = {}
        self.thumb_host.setFixedWidth(1)
        self.canvas.set_content(None, [])
        self.notes.blockSignals(True); self.notes.clear(); self.notes.blockSignals(False)

    def _schedule_note_save(self):
        self._notes_timer.start(500)

    def _save_note(self):
        if self.files and self.window.project:
            self.window.project.set_note(self.files[self.index], self.notes.toPlainText())

    def _save_current(self, silent=False, automatic=False):
        if not self.files:
            return False
        self._autosave_timer.stop()
        self._notes_timer.stop(); self._save_note()
        if not self._current_dirty:
            return False
        filename = self.files[self.index]
        self._risk_cache.pop(filename, None)
        image_path = os.path.join(self.window.images_dir, filename)
        core.save_as_voc_xml(self.canvas.shapes, image_path, self.window.annotations_dir)
        self._current_dirty = False
        self._update_quality_hint(filename)
        if not silent:
            self.window.status_message("标注已自动保存" if automatic else "已保存到项目 annotations 文件夹")
        return True

    def undo(self):
        self.window.status_message("已撤销上一步修改" if self.canvas.undo() else "没有可撤销的修改")

    def redo(self):
        self.window.status_message("已重做上一步修改" if self.canvas.redo() else "没有可重做的修改")

    def delete_selected(self):
        self.window.status_message("已删除选中标注，正在自动保存…" if self.canvas.delete_selected() else "请先用选择工具选中标注框")

    def save(self):
        self._save_current()

    def _show_versions(self):
        if not self.files or not self.window.project:
            return
        filename = self.files[self.index]
        versions = [version for version in self.window.project.annotation_versions_for(filename) if os.path.isfile(version["path"])]
        if not versions:
            ios_alert(self, "标注版本", "当前图片还没有可恢复的预标注版本。\n重新预标注时会自动创建版本。")
            return
        labels = [
            f"{version['created_at'][:19].replace('T', ' ')} · {version['source']} · {version['annotation_count']} 个框 · 风险 {version['risk_score']}"
            for version in versions
        ]
        selected, ok = ios_item_input(self, "恢复标注版本", "选择要恢复的结果：", labels, 0, False, "恢复")
        if not ok:
            return
        version = versions[labels.index(selected)]
        if not ios_alert(self, "确认恢复版本", "将先保存当前 XML 版本，再恢复所选历史结果。是否继续？", "恢复", "取消"):
            return
        self._save_current(silent=True)
        image_path = os.path.join(self.window.images_dir, filename)
        xml_path = os.path.join(self.window.annotations_dir, Path(filename).stem + ".xml")
        AutoPage._snapshot_annotation(self.window.project, filename, image_path, xml_path, "恢复前当前结果", "", "")
        shutil.copy2(version["path"], xml_path)
        self._load()
        self.window.status_message("已恢复所选标注版本")

    def _quick_review(self, status):
        focused = QApplication.focusWidget()
        if isinstance(focused, (QLineEdit, QPlainTextEdit, QComboBox)):
            return
        self.set_status(status)

    def set_status(self, status):
        if not self.files or not self.window.project:
            return
        filename = self.files[self.index]
        if status == STATUS_APPROVED:
            self.canvas.mark_all_verified()
        self._save_current(silent=True)
        if status == STATUS_APPROVED and self._spot_check_files is not None and filename in self._spot_check_files:
            self.window.project.mark_spot_checks([filename], "passed")
            self.window.refresh_all()
            self.window.status_message("抽检通过并进入下一张")
            return
        self.window.project.set_status(filename, status)
        self.window.refresh_all()
        self.window.status_message("已通过并进入下一张" if status == STATUS_APPROVED else "已跳过并进入下一张" if status == STATUS_SKIPPED else "已更新审核状态")


class LibraryPage(QWidget):
    readiness_finished = Signal(int, object)

    def __init__(self, window):
        super().__init__(); self.window = window; self.files = []; self.selected = set(); self._analysis = {}; self._analysis_signature = (); self._analysis_token = 0; self._spot_checks = {}
        layout = QVBoxLayout(self); layout.setContentsMargins(24, 24, 24, 24); layout.setSpacing(14)
        header = QHBoxLayout(); header.addWidget(label("已通过 · 训练候选", "title")); self.count = label("0 张已通过图片", "muted"); header.addWidget(self.count); header.addStretch(); self.readiness_summary = label("尚未形成训练候选", "muted"); header.addWidget(self.readiness_summary); layout.addLayout(header); layout.addWidget(label("这是最终确认区：在此做最终筛选、抽检和训练数据准备；需要修改标注时可移回待审核。", "muted"))
        dashboard = card(); dashboard.setFixedHeight(82); dashboard_layout = QHBoxLayout(dashboard); dashboard_layout.setContentsMargins(16, 10, 16, 10); dashboard_layout.setSpacing(16)
        state = QVBoxLayout(); state.setSpacing(3); self.target_button = QToolButton(); self.target_button.setText(f"目标 {config.MIN_TRAIN_IMAGES}"); self.target_button.setFixedHeight(22); self.target_button.setCursor(Qt.PointingHandCursor); self.target_button.setToolTip("设置本项目的训练图片目标"); self.target_button.clicked.connect(self.edit_training_target); self.target_button.setStyleSheet("QToolButton { background:#F2F5F8; border:0; border-radius:8px; color:#596270; font-size:11px; font-weight:700; padding:4px 8px; } QToolButton:hover { background:#EAF3FF; color:#0A74FF; }"); state.addWidget(self.target_button, 0, Qt.AlignLeft)
        readiness_row = QHBoxLayout(); readiness_row.setSpacing(7); self.readiness_dot = QFrame(); self.readiness_dot.setFixedSize(9, 9); self.readiness_dot.setStyleSheet("background:#E6B800;border-radius:4px;"); readiness_row.addWidget(self.readiness_dot); self.readiness = label("待补充", "section"); readiness_row.addWidget(self.readiness); readiness_row.addStretch(); state.addLayout(readiness_row); dashboard_layout.addLayout(state)
        self.size_metric = self._metric("候选", "0 / 50"); self.coverage_metric = self._metric("覆盖", "–"); self.quality_metric = self._metric("完整", "–")
        for metric in (self.size_metric, self.coverage_metric, self.quality_metric): dashboard_layout.addWidget(metric[0])
        divider = QFrame(); divider.setFixedWidth(1); divider.setStyleSheet("background:#E7EAF0;"); dashboard_layout.addWidget(divider)
        self.class_scroll = QScrollArea(); self.class_scroll.setFixedHeight(48); self.class_scroll.setAlignment(Qt.AlignLeft | Qt.AlignVCenter); self.class_scroll.setFrameShape(QFrame.NoFrame); self.class_scroll.setWidgetResizable(False); self.class_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff); self.class_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff); self.class_scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background:transparent; border:0; }"); self.class_host = QWidget(); self.class_host.setStyleSheet("background:transparent;"); self.class_host.setFixedHeight(34); self.class_host.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed); self.class_pills = QHBoxLayout(self.class_host); self.class_pills.setContentsMargins(0, 0, 0, 0); self.class_pills.setSpacing(12); self.class_scroll.setWidget(self.class_host); dashboard_layout.addWidget(self.class_scroll, 1); layout.addWidget(dashboard)
        controls = card(); control_layout = QHBoxLayout(controls); control_layout.setContentsMargins(14, 10, 14, 10); control_layout.setSpacing(8); self.search = QLineEdit(); self.search.setPlaceholderText("搜索文件名"); self.search.textChanged.connect(self._render_grid); control_layout.addWidget(self.search, 1); self.category_filter = FlatComboBox(); self.category_filter.currentIndexChanged.connect(self._render_grid); control_layout.addWidget(self.category_filter); self.spot_filter = FlatComboBox(); self.spot_filter.addItem("全部候选", "all"); self.spot_filter.addItem("待抽检", "sampled"); self.spot_filter.addItem("抽检通过", "passed"); self.spot_filter.currentIndexChanged.connect(self._render_grid); control_layout.addWidget(self.spot_filter); self.selection_info = label("未选择", "muted"); control_layout.addWidget(self.selection_info); self.sample_size = FlatComboBox(); self.sample_size.addItem("抽取 5 张", 5); self.sample_size.addItem("抽取 10 张", 10); self.sample_size.addItem("抽取 20 张", 20); control_layout.addWidget(self.sample_size); sample = button("随机抽取"); sample.clicked.connect(self.start_spot_check); control_layout.addWidget(sample); self.spot_progress = label("待抽检 0 · 已完成 0", "muted"); control_layout.addWidget(self.spot_progress); self.begin_spot_check = button("开始抽检", primary=True); self.begin_spot_check.clicked.connect(self.begin_spot_check_review); self.begin_spot_check.setEnabled(False); control_layout.addWidget(self.begin_spot_check); self.batch_return = destructive_button("批量移回待审核"); self.batch_return.clicked.connect(self.return_selected); self.batch_return.setEnabled(False); control_layout.addWidget(self.batch_return); layout.addWidget(controls)
        surface = card(); self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True); self.host = QWidget(); self.grid = QGridLayout(self.host); self.grid.setContentsMargins(18, 18, 18, 18); self.grid.setSpacing(14); self.grid.setAlignment(Qt.AlignTop | Qt.AlignLeft); self.scroll.setWidget(self.host); lay = QVBoxLayout(surface); lay.setContentsMargins(8, 8, 8, 8); lay.addWidget(self.scroll); layout.addWidget(surface, 1)
        self.readiness_finished.connect(self._apply_readiness)

    def _metric(self, caption, value):
        host = QWidget(); host.setFixedWidth(76); layout = QVBoxLayout(host); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(1); value_label = label(value, "section"); layout.addWidget(value_label); layout.addWidget(label(caption, "muted")); progress = QProgressBar(); progress.setRange(0, 100); progress.setTextVisible(False); progress.setFixedHeight(3); layout.addWidget(progress)
        return host, value_label, progress

    @staticmethod
    def _set_metric(metric, value, percent, color):
        _, value_label, progress = metric; value_label.setText(value); progress.setValue(max(0, min(100, int(percent)))); progress.setStyleSheet(f"QProgressBar {{background:#E9EDF2;border:0;border-radius:3px;}} QProgressBar::chunk {{background:{color};border-radius:3px;}}")

    def _training_target(self):
        if not self.window.project:
            return config.MIN_TRAIN_IMAGES
        try:
            return max(1, int(self.window.project.metadata_value("training_target_images", config.MIN_TRAIN_IMAGES)))
        except (TypeError, ValueError):
            return config.MIN_TRAIN_IMAGES

    def edit_training_target(self):
        if not self.window.project:
            return
        value, accepted = ios_text_input(self, "训练图片目标", "设置本项目建议达到的已通过图片数量。该目标仅用于就绪提醒，可随时调整。", str(self._training_target()), "例如 200", "保存")
        if not accepted:
            return
        try:
            target = int(value.strip())
            if target < 1:
                raise ValueError
        except ValueError:
            ios_alert(self, "目标数量无效", "请输入大于 0 的整数。", confirm_text="知道了", destructive=True)
            return
        self.window.project.set_metadata_value("training_target_images", target)
        self.refresh()

    def _render_class_chips(self, counts=None):
        """Show only categories that actually occur in the approved candidate set."""
        names = [name for name, count in (counts or {}).items() if count > 0]
        while self.class_pills.count():
            item = self.class_pills.takeAt(0); widget = item.widget(); widget.deleteLater() if widget else None
        content_widgets = []
        if not names:
            message = label("正在统计已通过图片的类别…" if counts is None else "已通过图片暂无标注", "muted")
            message.setAlignment(Qt.AlignVCenter); self.class_pills.addWidget(message); content_widgets.append(message)
        for name in names:
            accent = ImageCanvas.color_for_label(name)
            accent.setHsv(accent.hue(), min(105, max(55, accent.saturation())), 165)
            pill = label(f"{name}   {counts[name]}")
            pill.setStyleSheet(f"color:#3F4855; background:transparent; border:1px solid #C9D1DB; border-left:3px solid {accent.name()}; border-radius:10px; padding:5px 10px 5px 8px; font-size:12px; font-weight:700;")
            pill.setToolTip(f"{name}：{counts[name]} 个已通过标注框")
            self.class_pills.addWidget(pill); content_widgets.append(pill)
        content_width = sum(widget.sizeHint().width() for widget in content_widgets)
        content_width += self.class_pills.spacing() * max(0, len(content_widgets) - 1)
        self.class_host.setFixedWidth(max(1, content_width)); self.class_host.setFixedHeight(34)

    def refresh(self):
        self.files = self.window.project_images(STATUS_APPROVED); self.selected.intersection_update(self.files); self._spot_checks = self.window.project.spot_checks_for(self.files) if self.window.project else {}
        target = self._training_target(); self.target_button.setText(f"目标 {target}")
        self.count.setText(f"{len(self.files)} 张已通过图片"); self.readiness_summary.setText("训练候选尚待检查" if self.files else "尚未形成训练候选")
        self._set_metric(self.size_metric, f"{len(self.files)} / {target}", len(self.files) * 100 / target, "#31A56C" if len(self.files) >= target else "#E6B800")
        self._sync_categories(); self._queue_readiness(); self._render_class_chips(self._analysis.get("class_counts") if self._analysis else None); self._update_spot_progress(); self._render_grid()

    def _sync_categories(self):
        current = self.category_filter.currentData() or "all"
        self.category_filter.blockSignals(True); self.category_filter.clear(); self.category_filter.addItem("全部类别", "all")
        for name in (self.window.project.categories if self.window.project else []): self.category_filter.addItem(name, name)
        index = self.category_filter.findData(current); self.category_filter.setCurrentIndex(max(0, index)); self.category_filter.blockSignals(False)

    def _queue_readiness(self):
        categories = tuple(self.window.project.categories) if self.window.project else ()
        signature = (tuple(self.files), categories)
        if signature == self._analysis_signature:
            return
        self._analysis_signature = signature; self._analysis = {}; self._analysis_token += 1; token = self._analysis_token
        self._set_metric(self.coverage_metric, "…", 0, "#0A74FF"); self._set_metric(self.quality_metric, "…", 0, "#0A74FF")
        categories = list(categories); files = list(self.files)
        annotations_dir = self.window.annotations_dir
        threading.Thread(target=lambda: self.readiness_finished.emit(token, core.training_readiness(annotations_dir, files, categories)), daemon=True).start()

    def _apply_readiness(self, token, analysis):
        if token != self._analysis_token:
            return
        self._analysis = analysis
        counts = analysis.get("class_counts", {}); covered = sum(count > 0 for count in counts.values()); total_classes = len(counts); complete = len(self.files) - len(analysis.get("empty", []))
        self._set_metric(self.coverage_metric, f"{covered} / {total_classes} 类" if total_classes else "未设类别", covered * 100 / max(1, total_classes), "#31A56C" if total_classes and covered == total_classes else "#E6B800")
        self._set_metric(self.quality_metric, f"{complete} / {len(self.files)} 张", complete * 100 / max(1, len(self.files)), "#31A56C" if complete == len(self.files) else "#FF8A00")
        is_ready = len(self.files) >= self._training_target() and total_classes > 0 and covered == total_classes and complete == len(self.files)
        self.readiness.setText("就绪" if is_ready else "待补充"); self.readiness_summary.setText("可导出训练集" if is_ready else "训练候选尚待检查"); self.readiness_dot.setStyleSheet(f"background:{'#31A56C' if is_ready else '#E6B800'};border-radius:4px;")
        self._render_class_chips(counts)
        self._render_grid()

    def _visible_files(self):
        query, category, spot = self.search.text().strip().lower(), self.category_filter.currentData() or "all", self.spot_filter.currentData() or "all"
        labels = self._analysis.get("labels_by_file", {})
        result = []
        for filename in self.files:
            if query and query not in filename.lower(): continue
            if category != "all" and labels and category not in labels.get(filename, []): continue
            state = self._spot_checks.get(filename, {}).get("state")
            if spot != "all" and state != spot: continue
            result.append(filename)
        return result

    def _render_grid(self):
        while self.grid.count():
            item = self.grid.takeAt(0); widget = item.widget(); widget.deleteLater() if widget else None
        visible = self._visible_files(); self.selection_info.setText(f"已选择 {len(self.selected)} 张" if self.selected else "未选择"); self.batch_return.setEnabled(bool(self.selected))
        if not visible:
            empty = label("当前筛选没有训练候选", "muted"); empty.setAlignment(Qt.AlignCenter); self.grid.addWidget(empty, 0, 0); return
        for index, filename in enumerate(visible):
            item = card(); item.setFixedSize(224, 226); lay = QVBoxLayout(item); lay.setContentsMargins(8, 8, 8, 8); image = QLabel(); image.setFixedSize(208, 124); pix = QPixmap(os.path.join(self.window.images_dir, filename)).scaled(image.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation); image.setPixmap(pix); image.setAlignment(Qt.AlignCenter); lay.addWidget(image)
            row = QHBoxLayout(); check = QCheckBox(); check.setChecked(filename in self.selected); check.toggled.connect(lambda checked=False, target=filename: self.toggle_selected(target, checked)); row.addWidget(check); file_label = label(filename); file_label.setFixedWidth(180); file_label.setWordWrap(False); file_label.setToolTip(filename); row.addWidget(file_label); lay.addLayout(row)
            bottom = QHBoxLayout(); state = self._spot_checks.get(filename, {}).get("state"); badge = label("待抽检" if state == "sampled" else "抽检通过" if state == "passed" else "训练候选"); badge.setStyleSheet("color:#B45309;background:#FFF7E6;border-radius:8px;padding:4px 7px;font-weight:700;" if state == "sampled" else "color:#17834C;background:#EEF9F3;border-radius:8px;padding:4px 7px;font-weight:700;" if state == "passed" else "color:#0A74FF;background:#EAF3FF;border-radius:8px;padding:4px 7px;font-weight:700;"); bottom.addWidget(badge); bottom.addStretch()
            remove = destructive_button("移回待审核"); remove.clicked.connect(lambda checked=False, target=filename: self.remove_record(target)); bottom.addWidget(remove); lay.addLayout(bottom); self.grid.addWidget(item, index // 4, index % 4, Qt.AlignTop | Qt.AlignLeft)

    def toggle_selected(self, filename, checked):
        (self.selected.add(filename) if checked else self.selected.discard(filename)); self._render_grid()

    def start_spot_check(self):
        if not self.window.project or not self.files:
            return
        candidates = [name for name in self.files if name not in self._spot_checks]
        chosen = random.sample(candidates, min(int(self.sample_size.currentData()), len(candidates)))
        if not chosen:
            self.window.status_message("没有尚未抽取的训练候选"); return
        self.window.project.mark_spot_checks(chosen, "sampled"); self._spot_checks = self.window.project.spot_checks_for(self.files); self.spot_filter.setCurrentIndex(self.spot_filter.findData("sampled")); self._update_spot_progress(); self._render_grid(); self.window.status_message(f"已随机抽取 {len(chosen)} 张，点击“开始抽检”进入审核页")

    def _update_spot_progress(self):
        pending = sum(record.get("state") == "sampled" for record in self._spot_checks.values())
        passed = sum(record.get("state") == "passed" for record in self._spot_checks.values())
        self.spot_progress.setText(f"待抽检 {pending} · 已完成 {passed}")
        self.begin_spot_check.setEnabled(bool(pending))

    def begin_spot_check_review(self):
        sampled = [filename for filename, record in self._spot_checks.items() if record.get("state") == "sampled"]
        if sampled:
            self.window.open_spot_check_review(sampled)

    def return_selected(self):
        names = sorted(self.selected)
        if not names or not self.window.project or not ios_alert(self, "批量移回待审核", f"将 {len(names)} 张训练候选移回待审核？原图和 XML 不会删除。", "移回待审核", "取消"):
            return
        self.window.project.set_statuses(names, STATUS_NEEDS_REVIEW); self.selected.clear(); self.window.refresh_all(); self.window.status_message(f"已移回 {len(names)} 张待审核")

    def remove_record(self, filename: str):
        if not ios_alert(self, "移回待审核", f"要将“{filename}”移回待审核吗？\n\n原图和 XML 标注不会删除。", "移回待审核", "取消") or not self.window.project:
            return
        self.window.project.set_statuses([filename], STATUS_NEEDS_REVIEW); self.window.refresh_all(); self.window.status_message(f"已移回待审核：{filename}")


class ExportPage(QWidget):
    log_message = Signal(str)
    operation_finished = Signal(str, object)
    operation_failed = Signal(str)

    def __init__(self, window):
        super().__init__(); self.window = window; self._worker_thread = None; self._data_yaml = ""; self._last_export_id = 0
        layout = QVBoxLayout(self); layout.setContentsMargins(24, 24, 24, 24); layout.setSpacing(14)
        header = QHBoxLayout(); header.addWidget(label("模型微调", "title")); header.addStretch(); layout.addLayout(header); layout.addWidget(label("闭环流程：已通过 → 导出标准 YOLO 数据集 → 后台微调 → 项目 models/ 保存 best.pt。", "muted"))
        stages = QHBoxLayout(); stages.setSpacing(14); stages.addWidget(self._export_card(), 1); stages.addWidget(self._train_card(), 1); layout.addLayout(stages)
        log_card = card(); log_layout = QVBoxLayout(log_card); log_layout.setContentsMargins(20, 20, 20, 20); log_layout.addWidget(label("运行记录", "section")); self.log = QPlainTextEdit(); self.log.setReadOnly(True); self.log.setPlaceholderText("导出与训练的运行记录会显示在这里"); log_layout.addWidget(self.log); layout.addWidget(log_card, 1)
        self.log_message.connect(self.log.appendPlainText)
        self.operation_finished.connect(self._operation_complete)
        self.operation_failed.connect(self._operation_error)

    def _export_card(self):
        surface = card(); grid = QGridLayout(surface); grid.setContentsMargins(20, 20, 20, 20); grid.setSpacing(12); grid.addWidget(label("1 · 导出数据集", "section"), 0, 0, 1, 2); grid.addWidget(label("仅导出“已通过 · 训练候选”的图片与 XML 标注。", "muted"), 1, 0, 1, 2); grid.addWidget(label("输出目录", "muted"), 2, 0); self.out = QLineEdit(); self.out.setReadOnly(True); self.out.setPlaceholderText("打开项目后自动设为项目内 yolo_dataset 文件夹"); grid.addWidget(self.out, 2, 1); grid.addWidget(label("验证集比例", "muted"), 3, 0); self.ratio = QLineEdit("0.2"); grid.addWidget(self.ratio, 3, 1); self.export_status = label("尚未导出", "muted"); grid.addWidget(self.export_status, 4, 0, 1, 2); self.export_run = button("导出数据集", primary=True); self.export_run.clicked.connect(self._start_export); grid.addWidget(self.export_run, 5, 1); return surface

    def sync_project(self, project_root):
        self.out.setText(os.path.join(project_root, "yolo_dataset") if project_root else "")
        self._data_yaml = os.path.join(project_root, "yolo_dataset", "data.yaml") if project_root else ""
        ready = bool(self._data_yaml and os.path.isfile(self._data_yaml))
        exports = self.window.project.recent_dataset_exports(1) if self.window.project else []
        self._last_export_id = exports[0]["id"] if exports else 0
        self.export_status.setText("已找到 data.yaml，可开始训练" if ready else "尚未导出")
        self.train_run.setEnabled(ready and not self._is_busy())
        self.export_run.setEnabled(bool(project_root) and not self._is_busy())

    def _train_card(self):
        surface = card(); grid = QGridLayout(surface); grid.setContentsMargins(20, 20, 20, 20); grid.setSpacing(12); grid.addWidget(label("2 · 微调训练", "section"), 0, 0, 1, 2); grid.addWidget(label("训练在后台运行；完成后 best.pt 会复制到当前项目 models/。", "muted"), 1, 0, 1, 2); grid.addWidget(label("预训练模型", "muted"), 2, 0); self.train_model = FlatComboBox(); self.train_model.addItems(["yolov8n.pt", "yolov8s.pt", "yolo11n.pt"]); grid.addWidget(self.train_model, 2, 1); grid.addWidget(label("训练轮数", "muted"), 3, 0); self.epochs = QLineEdit("100"); grid.addWidget(self.epochs, 3, 1); self.train_status = label("请先导出数据集", "muted"); grid.addWidget(self.train_status, 4, 0, 1, 2); self.train_run = button("开始训练", primary=True); self.train_run.clicked.connect(self._start_training); self.train_run.setEnabled(False); grid.addWidget(self.train_run, 5, 1); return surface

    def _is_busy(self):
        return bool(self._worker_thread and self._worker_thread.is_alive())

    def _start_export(self):
        if self._is_busy() or not self.window.project:
            return
        files = self.window.project_images(STATUS_APPROVED)
        if not files:
            ios_alert(self, "没有训练候选", "请先在审核页将至少一张图片标记为“通过”。")
            return
        try:
            ratio = float(self.ratio.text().strip())
        except ValueError:
            ios_alert(self, "验证集比例无效", "请输入 0 到 0.9 之间的数字，例如 0.2。", confirm_text="知道了", destructive=True)
            return
        output = self.out.text()
        if os.path.isdir(output) and os.listdir(output):
            if not ios_alert(self, "覆盖训练集", "项目内已有导出数据集。继续会用本次已通过结果完整替换它。", "覆盖并导出", "取消", destructive=True):
                return
        self._set_busy(True)
        categories = list(self.window.project.categories)
        self.log_message.emit(f"开始导出 {len(files)} 张训练候选图片…")
        self._worker_thread = threading.Thread(target=self._run_export, args=(files, categories, ratio, output), daemon=True)
        self._worker_thread.start()

    def _run_export(self, files, categories, ratio, output):
        try:
            summary = core.export_yolo_dataset(self.window.images_dir, self.window.annotations_dir, files, output, categories, ratio)
            self.operation_finished.emit("export", summary)
        except Exception as error:
            self.operation_failed.emit(f"导出失败：{error}")

    def _start_training(self):
        if self._is_busy() or not self.window.project:
            return
        data_yaml = self._data_yaml or os.path.join(self.out.text(), "data.yaml")
        if not os.path.isfile(data_yaml):
            ios_alert(self, "请先导出数据集", "当前项目中未找到 data.yaml。请先完成第 1 步。")
            return
        try:
            epochs = int(self.epochs.text().strip())
            if epochs < 1:
                raise ValueError
        except ValueError:
            ios_alert(self, "训练轮数无效", "请输入大于 0 的整数。", confirm_text="知道了", destructive=True)
            return
        self._set_busy(True)
        model, runs_dir = self.train_model.currentText(), os.path.join(self.window.project_root, "training_runs")
        self.log_message.emit(f"开始微调：{model} · {epochs} 轮。训练可持续数分钟，请保持应用打开。")
        self._worker_thread = threading.Thread(target=self._run_training, args=(data_yaml, model, epochs, runs_dir), daemon=True)
        self._worker_thread.start()

    def _run_training(self, data_yaml, model, epochs, runs_dir):
        try:
            result = core.train_yolo_dataset(data_yaml, model, epochs, runs_dir)
            models_dir = os.path.join(self.window.project_root, "models")
            os.makedirs(models_dir, exist_ok=True)
            target = os.path.join(models_dir, f"{Path(result['run_dir']).name}-best.pt")
            shutil.copy2(result["best_weights"], target)
            result["project_weights"] = target; result["model"] = model; result["epochs"] = epochs
            self.operation_finished.emit("train", result)
        except Exception as error:
            self.operation_failed.emit(f"训练失败：{error}")

    def _operation_complete(self, operation, result):
        self._worker_thread = None
        if operation == "export":
            self._data_yaml = result["data_yaml"]
            if self.window.project:
                self._last_export_id = self.window.project.record_dataset_export(result["root"], result["data_yaml"], result["train_images"], result["val_images"], result["objects"], result["classes"], result["validation_ratio"])
            self.export_status.setText(f"已导出：训练 {result['train_images']} 张 · 验证 {result['val_images']} 张 · {len(result['classes'])} 类")
            self.train_status.setText("数据集已就绪，可开始训练")
            self.log.appendPlainText(f"导出完成：{result['objects']} 个目标，data.yaml 已写入项目 yolo_dataset/。")
        else:
            if self.window.project:
                self.window.project.record_training_run(self._last_export_id or None, result["run_dir"], result["project_weights"], result["model"], result["epochs"])
            self.train_status.setText("训练完成，best.pt 已保存到项目 models/")
            self.log.appendPlainText(f"训练完成：{result['project_weights']}")
        self._set_busy(False)

    def _operation_error(self, message):
        self._worker_thread = None
        self._set_busy(False)
        self.log.appendPlainText(message)
        ios_alert(self, "任务未完成", message, confirm_text="知道了", destructive=True)

    def _set_busy(self, busy):
        self.export_run.setEnabled(bool(self.window.project_root) and not busy)
        self.train_run.setEnabled(bool(self._data_yaml and os.path.isfile(self._data_yaml)) and not busy)
        self.ratio.setEnabled(not busy); self.train_model.setEnabled(not busy); self.epochs.setEnabled(not busy)


class Workbench(QMainWindow):
    def __init__(self):
        super().__init__(); self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window); self.setWindowTitle(" "); self.setWindowIcon(QIcon()); self.resize(1420, 900); self.setMinimumSize(1100, 720)
        self.workspace = WorkspaceStore()
        self.project: ProjectStore | None = None; self.project_root = ""; self.project_name = ""; self.images_dir = ""; self.annotations_dir = ""
        root = ResizableRoot(self); root.setObjectName("root"); self.setCentralWidget(root); root_layout = QVBoxLayout(root); root_layout.setContentsMargins(7, 7, 7, 7); root_layout.setSpacing(0); root_layout.addWidget(WindowChrome(self))
        layout = QHBoxLayout(); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(0); root_layout.addLayout(layout, 1)
        side = QFrame(); side.setObjectName("sidebar"); side.setFixedWidth(264); side_layout = QVBoxLayout(side); side_layout.setContentsMargins(16, 24, 16, 20); side_layout.setSpacing(6)
        title = QHBoxLayout(); title.addWidget(label("项目", "section")); title.addStretch(); more = QToolButton(); more.setText("•••"); more.setCursor(Qt.PointingHandCursor); more.setStyleSheet("border:0;font-weight:700;font-size:14px;color:#646873;"); more.clicked.connect(self.project_menu); title.addWidget(more); side_layout.addLayout(title); side_layout.addSpacing(10)
        self.project_card = QFrame(); self.project_card.setObjectName("projectCard"); project_layout = QVBoxLayout(self.project_card); project_layout.setContentsMargins(12, 11, 12, 11); project_layout.setSpacing(5)
        project_layout.addWidget(label("当前工作区", "projectEyebrow"))
        self.current_project = label("还未打开项目", "projectName"); self.current_project.setWordWrap(False); project_layout.addWidget(self.current_project)
        self.current_project_path = label("新建或打开一个项目开始", "projectPath"); self.current_project_path.setWordWrap(False); project_layout.addWidget(self.current_project_path)
        project_footer = QHBoxLayout(); project_footer.setContentsMargins(0, 2, 0, 0); self.project_status = label("等待选择项目", "projectStatus"); project_footer.addWidget(self.project_status); project_footer.addStretch(); project_layout.addLayout(project_footer)
        side_layout.addWidget(self.project_card)
        side_layout.addSpacing(8)
        self.recent_title = label("最近项目", "muted"); side_layout.addWidget(self.recent_title)
        self.recent_host = QWidget(); self.recent_layout = QVBoxLayout(self.recent_host); self.recent_layout.setContentsMargins(0, 2, 0, 2); self.recent_layout.setSpacing(5); side_layout.addWidget(self.recent_host)
        side_layout.addSpacing(12)
        self.nav_group = QButtonGroup(self); self.nav_group.setExclusive(True); self.nav = {}
        for key, title, icon in (("auto", "自动标注", sprite_icon("auto", nav=True)), ("review", "待审核", sprite_icon("review", nav=True)), ("approved", "已通过", sprite_icon("approved", nav=True)), ("export", "模型微调", sprite_icon("export", nav=True))):
            item = SidebarItem(title, icon, self); item.clicked.connect(lambda checked=False, page=key: self.show_page(page)); self.nav_group.addButton(item); side_layout.addWidget(item); self.nav[key] = item
        side_layout.addStretch(); side_layout.addWidget(label("本地优先 · 数据可控", "muted")); layout.addWidget(side)
        self.pages = QStackedWidget(); layout.addWidget(self.pages, 1); self.auto = AutoPage(self); self.review = ReviewPage(self); self.library = LibraryPage(self); self.export = ExportPage(self); self.keys = {"auto": self.auto, "review": self.review, "approved": self.library, "export": self.export}
        for page in self.keys.values(): self.pages.addWidget(page)
        root.track_descendants()
        self.auto.sync_project()
        self.show_page("auto")
        self.refresh_recent_projects()
        QTimer.singleShot(0, self.restore_last_project)

    def project_menu(self):
        menu = QMenu(self)
        menu.addAction("新建项目", self.new_project)
        menu.addAction("导入 / 打开项目", self.open_project)
        if self.project_root:
            menu.addSeparator()
            menu.addAction("关闭当前项目", self.close_project)
            menu.addAction("从最近项目中移除", lambda: self.remove_project_from_workspace(self.project_root))
            menu.addAction("删除当前项目及全部数据…", lambda: self.delete_project(self.project_root))
        recent = self.workspace.recent_projects()
        if recent:
            recent_menu = menu.addMenu("最近项目")
            for path, name in recent:
                action = recent_menu.addAction(name)
                action.setToolTip(path)
                action.triggered.connect(lambda checked=False, target=path: self.load_project(target))
        menu.exec(self.cursor().pos())

    def restore_last_project(self):
        path = self.workspace.last_project()
        if path and os.path.isdir(path):
            self.load_project(path)

    def refresh_recent_projects(self):
        """Expose project history where it can be selected without opening a menu."""
        while self.recent_layout.count():
            item = self.recent_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        recents = self.workspace.recent_projects()
        self.recent_title.setVisible(bool(recents))
        self.recent_host.setVisible(bool(recents))
        for path, name in recents[:4]:
            item = QToolButton(self.recent_host)
            item.setObjectName("recentProject")
            item.setText(name)
            item.setToolTip(path)
            item.setToolButtonStyle(Qt.ToolButtonTextOnly)
            item.setCheckable(True)
            item.setChecked(os.path.normcase(os.path.abspath(path)) == os.path.normcase(self.project_root))
            item.setMinimumHeight(32)
            item.setCursor(Qt.PointingHandCursor)
            item.clicked.connect(lambda checked=False, target=path: self.load_project(target))
            self.recent_layout.addWidget(item)

    def remove_project_from_workspace(self, project_root: str):
        """Forget a launcher entry but keep all project data intact."""
        root = os.path.abspath(project_root)
        self.workspace.forget_project(root)
        if os.path.normcase(root) == os.path.normcase(self.project_root):
            self.close_project(update_workspace=False)
        self.refresh_recent_projects()
        self.status_message("已从最近项目中移除；项目文件仍保留在原位置")

    def delete_project(self, project_root: str):
        """Permanently delete a valid project only after an explicit name check."""
        root = os.path.abspath(project_root)
        manifest = load_project_manifest(root)
        app_root = os.path.abspath(config.BASE_DIR)
        if not manifest or not os.path.isdir(root) or os.path.normcase(root) == os.path.normcase(app_root):
            ios_alert(self, "无法删除项目", "为保护应用和普通文件夹，只能删除带 autolabel.project.json 的独立项目目录。", confirm_text="知道了", destructive=True)
            return
        name = str(manifest.get("name") or Path(root).name)
        if not ios_alert(self, "删除项目及全部数据", f"将永久删除项目“{name}”及其中所有图片、XML 标注、模型、版本和训练集。\n\n此操作无法撤销。", "删除项目", "取消", destructive=True):
            return
        typed, ok = ios_text_input(self, "确认删除项目", f"请输入项目名称“{name}”以确认删除：", placeholder=name, confirm_text="确认删除")
        if not ok or typed.strip() != name:
            self.status_message("项目名称不匹配，已取消删除")
            return
        is_current = os.path.normcase(root) == os.path.normcase(self.project_root)
        if is_current:
            self.close_project(update_workspace=False)
        self.workspace.forget_project(root)
        try:
            shutil.rmtree(root)
        except OSError as error:
            ios_alert(self, "删除失败", f"无法删除项目目录：\n{error}", confirm_text="知道了", destructive=True)
            return
        self.refresh_recent_projects()
        self.status_message(f"已删除项目：{name}")

    def close_project(self, update_workspace=True):
        """Detach the current workspace without deleting any on-disk project files."""
        closing_root = self.project_root
        if closing_root:
            self.review.flush_autosave()
        self.review.clear_project_context()
        self.project = None; self.project_root = ""; self.project_name = ""; self.images_dir = ""; self.annotations_dir = ""
        self.auto.sync_project(); self.export.sync_project("")
        self._render_current_project()
        self.show_page("auto")
        self.refresh_all()
        self.refresh_recent_projects()
        if update_workspace:
            self.workspace.clear_last_project(closing_root)
            self.status_message("已关闭项目；文件仍保留在原位置")

    def toggle_maximized(self):
        self.showNormal() if self.isMaximized() else self.showMaximized()

    def new_project(self):
        name, ok = ios_text_input(self, "新建项目", "项目名称：", placeholder="例如：街景行人标注", confirm_text="下一步")
        if not ok or not name.strip(): return
        parent = QFileDialog.getExistingDirectory(self, "选择项目保存位置", config.BASE_DIR)
        if not parent: return
        root = os.path.join(parent, name.strip())
        if os.path.exists(root):
            self.status_message("项目目录已存在"); return
        for folder in ("images", "annotations", "annotation_versions", "models", "yolo_dataset"): os.makedirs(os.path.join(root, folder), exist_ok=True)
        create_project_manifest(root, name.strip())
        self.load_project(root)

    def open_project(self):
        root = QFileDialog.getExistingDirectory(self, "导入 / 打开项目", config.BASE_DIR)
        if root: self.load_project(root)

    def load_project(self, root: str):
        root = os.path.abspath(root)
        manifest = load_project_manifest(root)
        if not manifest:
            # Adopt the original folder convention once, then retain an explicit
            # project file so all future opens restore the exact same workspace.
            if not os.path.isdir(os.path.join(root, "images")):
                self.status_message("所选目录不是 autoLabel 项目：缺少 autolabel.project.json 和 images 文件夹"); return
            manifest = create_project_manifest(root, Path(root).name)
        try:
            directories = manifest["directories"]
            images = resolve_project_directory(root, directories.get("images", "images"))
            annotations = resolve_project_directory(root, directories.get("annotations", "annotations"))
            models = resolve_project_directory(root, directories.get("models", "models"))
            versions = resolve_project_directory(root, directories.get("annotation_versions", ANNOTATION_VERSION_DIR))
            dataset = resolve_project_directory(root, directories.get("dataset", "yolo_dataset"))
        except (KeyError, TypeError, ValueError):
            self.status_message("项目文件无效，无法读取目录配置"); return
        if not os.path.isdir(images):
            self.status_message("项目图片目录不存在"); return
        for directory in (annotations, models, versions, dataset):
            os.makedirs(directory, exist_ok=True)
        self.project_root = root
        self.project_name = str(manifest.get("name") or Path(root).name)
        imported = self.use_folders(images, annotations, root)
        self._render_current_project()
        self.export.sync_project(root)
        self.workspace.remember_project(root, self.project_name)
        self.refresh_recent_projects()
        self.auto.sync_project(root, self.project_name)
        self.status_message(f"已打开项目：{self.project_name} · 新导入 {imported} 张待审核")
        self.show_page("review")

    def use_folders(self, images: str, annotations: str, project_root: str | None = None):
        # ``ReviewPage`` still points at the previously open image at this
        # point. Flush it before replacing the project's directory fields.
        self.review.flush_autosave()
        self.review.clear_project_context()
        if project_root:
            self.project_root = os.path.abspath(project_root)
        root = self.project_root or os.path.abspath(project_root or "")
        try:
            paths_are_isolated = root and all(os.path.commonpath((root, os.path.abspath(path))) == root for path in (images, annotations))
        except ValueError:
            paths_are_isolated = False
        if not paths_are_isolated:
            raise ValueError("图片和标注目录必须位于当前项目文件夹内")
        self.images_dir, self.annotations_dir = images, annotations
        self.project = ProjectStore(annotations, images, self.project_root or project_root)
        existing = [
            filename for filename in core.list_images(images)
            if os.path.isfile(os.path.join(annotations, Path(filename).stem + ".xml"))
        ]
        statuses = self.project.statuses_for(existing)
        queued = [
            filename for filename in existing
            if statuses.get(filename) in (STATUS_UNREVIEWED, STATUS_SKIPPED)
        ]
        self.project.mark_batch_for_review(existing)
        self.refresh_all()
        return len(queued)

    def refresh_project_state(self):
        """Re-scan the project after a one-time image/XML import."""
        if not self.project_root or not self.images_dir or not self.annotations_dir:
            return 0
        return self.use_folders(self.images_dir, self.annotations_dir, self.project_root)

    def project_images(self, status=None):
        if not self.images_dir: return []
        files = core.list_images(self.images_dir)
        if status is None or not self.project:
            return files
        statuses = self.project.statuses_for(files)
        return [file for file in files if statuses.get(file) == status]

    def refresh_all(self):
        files = self.project_images(); summary = self.project.summary(files) if self.project else {}
        self.nav["auto"].set_count(str(len(files)) if files else "")
        self.nav["review"].set_count(str(summary.get(STATUS_NEEDS_REVIEW, 0)) if summary.get(STATUS_NEEDS_REVIEW, 0) else "")
        self.nav["approved"].set_count(str(summary.get(STATUS_APPROVED, 0)) if summary.get(STATUS_APPROVED, 0) else "")
        current_page = self.pages.currentWidget()
        if current_page is self.review:
            self.review.refresh()
        elif current_page is self.library:
            self.library.refresh()

    def _render_current_project(self):
        if not self.project_root:
            self.current_project.setText("还未打开项目")
            self.current_project_path.setText("新建或打开一个项目开始")
            self.project_status.setText("等待选择项目")
            self.project_card.setToolTip("当前项目工作区")
            self.setWindowTitle("autoLabel")
            return
        display_name = self.project_name or Path(self.project_root).name
        self.current_project.setText(display_name)
        self.current_project_path.setText(self.project_root)
        self.current_project_path.setToolTip(self.project_root)
        self.project_status.setText("项目已打开")
        self.project_card.setToolTip(f"当前项目工作区\n{self.project_root}")
        self.setWindowTitle(f"autoLabel · {self.project_name}")

    def show_page(self, key: str):
        self.pages.setCurrentWidget(self.keys[key]); self.nav[key].setChecked(True)
        if key == "review": self.review.clear_spot_check(); self.review.refresh()
        if key == "approved": self.library.refresh()

    def open_spot_check_review(self, filenames):
        """Enter the regular canvas with only the sampled approved images queued."""
        self.review.open_spot_check(filenames)
        self.pages.setCurrentWidget(self.review); self.nav["review"].setChecked(True)

    def status_message(self, text: str): self.statusBar().showMessage(text, 4000)

    def closeEvent(self, event):
        # Persist the active project at the moment the user closes the app as
        # well as when it was opened, so startup can reliably restore it.
        if self.project_root:
            self.workspace.remember_project(self.project_root, self.project_name)
        super().closeEvent(event)


def main():
    QApplication.setAttribute(Qt.AA_DontUseNativeDialogs, True)
    app = QApplication.instance() or QApplication([])
    app.setWindowIcon(QIcon())
    apply_app_style(app)
    window = Workbench(); window.show()
    return app.exec()
