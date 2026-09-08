"""A clean-room Qt workbench.  It intentionally does not instantiate the legacy Tk pages."""
from __future__ import annotations

import os
import shutil
import threading
from pathlib import Path

from PySide6.QtCore import QEvent, QPointF, QRectF, QTimer, Qt, QSize, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QImage, QKeySequence, QPainter, QPainterPath, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QMainWindow, QMenu, QPlainTextEdit, QProgressBar,
    QMessageBox, QPushButton, QScrollArea, QSizePolicy, QStackedWidget, QStyle, QToolButton,
    QVBoxLayout, QWidget,
)

import config
import core
from project import (
    STATUS_APPROVED, STATUS_NEEDS_REVIEW, STATUS_SKIPPED, STATUS_UNREVIEWED,
    ProjectStore, WorkspaceStore, create_project_manifest, load_project_manifest, resolve_project_directory,
)


COLORS = {
    "canvas": "#F6F7F9", "surface": "#FFFFFF", "line": "#E3E5E9", "ink": "#1D1D1F",
    "muted": "#737780", "blue": "#0A74FF", "blue_soft": "#EAF3FF", "track": "#ECEEF2",
}
ROOT_DIR = Path(__file__).resolve().parent.parent
_ICON_CACHE: dict[tuple[str, bool, int], QIcon] = {}
_ASSET_ICON_CACHE: dict[tuple[str, int], QIcon] = {}


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


def asset_icon(filename: str, size=24) -> QIcon:
    """Crop transparent ImageGen padding and normalize the icon to toolbar ink."""
    key = (filename, size)
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
    pixmap = QPixmap.fromImage(image.copy(left, top, right - left, bottom - top)).scaled(size * scale, size * scale, Qt.KeepAspectRatio, Qt.SmoothTransformation)
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
        QToolButton#nav {{ border: 0; border-radius: 12px; text-align: left; padding: 10px 12px; font-size: 15px; color: #34373E; }}
        QToolButton#nav:checked {{ background: {COLORS['blue_soft']}; color: {COLORS['blue']}; font-weight: 700; }}
        QToolButton#nav:hover {{ background: #F3F5F8; }}
        QToolButton#recentProject {{ background: #F6F7F9; border: 0; border-radius: 9px; padding: 7px 10px; text-align: left; color: #454953; font-size: 12px; }}
        QToolButton#recentProject:hover {{ background: #EAF3FF; color: {COLORS['blue']}; }}
        QToolButton#recentProject:checked {{ background: #EAF3FF; color: {COLORS['blue']}; font-weight: 700; }}
        QProgressBar {{ height: 5px; border: 0; background: {COLORS['track']}; border-radius: 3px; }}
        QProgressBar::chunk {{ background: {COLORS['blue']}; border-radius: 3px; }}
        QProgressBar#reviewProgress {{ height: 4px; min-height: 4px; max-height: 4px; border-radius: 2px; }}
        QProgressBar#reviewProgress::chunk {{ border-radius: 2px; }}
        QFrame#paging {{ background: #FFFFFF; border: 1px solid {COLORS['line']}; border-radius: 11px; }}
        QToolButton#pagingChevron {{ background: transparent; border: 0; border-radius: 10px; font-size: 31px; font-weight: 300; color: #23272F; padding: 0 0 2px 0; }}
        QToolButton#pagingChevron:hover {{ background: #F3F5F8; }}
        QLabel#pagingPosition {{ border-left: 1px solid {COLORS['line']}; border-right: 1px solid {COLORS['line']}; font-size: 16px; padding: 0; }}
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
    """Interactive image canvas with lossless source rendering and VOC box editing."""

    COLORS = ["#8A3FFC", "#FF8A00", "#31C48D", "#FF3B30", "#F2C94C", "#EC4899", "#39A9FF"]
    shapes_changed = Signal(object)
    selection_changed = Signal(int)
    box_created = Signal(int)

    def __init__(self):
        super().__init__()
        self.setMinimumSize(480, 360)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.path: str | None = None
        self.shapes: list[dict] = []
        self._pixmap = QPixmap()
        self._display_cache = QPixmap()
        self._display_cache_key = None
        self.tool = "pan"
        self.zoom = 1.0
        self.pan = QPointF()
        self.selected_index = -1
        self.default_label = "object"
        self.visible_categories: set[str] | None = None
        self._drag_point = None
        self._drag_origin = None
        self._drag_shape = None
        self._drag_index = -1
        self._drag_mode = None
        self._resize_handle = None
        self._draft_start = None
        self._draft_end = None
        self._history: list[list[dict]] = []
        self._redo: list[list[dict]] = []

    @staticmethod
    def _copy_shapes(shapes):
        return [{"name": str(shape.get("name", "object")), "bbox": list(shape.get("bbox", (0, 0, 0, 0)))} for shape in shapes]

    @classmethod
    def color_for_label(cls, name: str) -> QColor:
        """Use a stable category colour instead of the box's list position."""
        value = sum((index + 1) * ord(character) for index, character in enumerate(str(name)))
        return QColor(cls.COLORS[value % len(cls.COLORS)])

    def set_content(self, path: str | None, shapes: list[dict]):
        self.path = path
        self.shapes = self._copy_shapes(shapes)
        self._pixmap = QPixmap(path) if path and os.path.isfile(path) else QPixmap()
        self._display_cache = QPixmap(); self._display_cache_key = None
        self.zoom = 1.0; self.pan = QPointF(); self.selected_index = -1
        self._history.clear(); self._redo.clear()
        self.update()

    def set_tool(self, name: str):
        self.tool = name
        self._drag_point = self._draft_start = self._draft_end = None
        self._drag_shape = None; self._drag_index = -1; self._drag_mode = self._resize_handle = None
        self.setCursor(Qt.OpenHandCursor if name == "pan" else Qt.CrossCursor if name == "box" else Qt.ArrowCursor)
        self.update()

    def set_default_label(self, name: str):
        self.default_label = name or "object"

    def set_visible_categories(self, categories: set[str] | None):
        self.visible_categories = set(categories) if categories is not None else None
        if self.selected_index >= 0 and self.visible_categories is not None:
            if self.shapes[self.selected_index].get("name") not in self.visible_categories:
                self._set_selected(-1)
        self.update()

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
            if self.visible_categories is not None and self.shapes[index].get("name") not in self.visible_categories:
                continue
            x1, y1, x2, y2 = self.shapes[index]["bbox"]
            if min(x1, x2) <= point.x() <= max(x1, x2) and min(y1, y2) <= point.y() <= max(y1, y2):
                return index
        return -1

    def _shape_rect(self, index: int):
        """Return an image-space box with stable left/top/right/bottom edges."""
        x1, y1, x2, y2 = self.shapes[index]["bbox"]
        return min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)

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

    def _commit(self, before):
        if before == self.shapes:
            return
        self._history.append(before)
        self._redo.clear()
        self.shapes_changed.emit(self._copy_shapes(self.shapes))
        self.update()

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
        self._commit(before)

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
            if self.visible_categories is not None and shape.get("name") not in self.visible_categories:
                continue
            x1, y1, x2, y2 = shape.get("bbox", (0, 0, 0, 0))
            color = self.color_for_label(shape.get("name", "object"))
            rect = QRectF(target.x() + x1 * scale, target.y() + y1 * scale, (x2 - x1) * scale, (y2 - y1) * scale).normalized()
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(color, 3 if index == self.selected_index else 2))
            if index == self.selected_index:
                # Keep the source visible while restoring the gentle, category-
                # coloured selection wash used by the workbench design.
                selected_fill = QColor(color)
                selected_fill.setAlpha(28)
                painter.fillRect(rect, selected_fill)
            painter.drawRect(rect)
            caption = shape.get("name", "object")
            painter.setFont(QFont("Microsoft YaHei UI", 10, QFont.Bold))
            width = max(46, painter.fontMetrics().horizontalAdvance(caption) + 16)
            caption_rect = QRectF(rect.x(), max(target.y(), rect.y() - 25), width, 24)
            painter.fillRect(caption_rect, color)
            painter.setPen(Qt.white)
            painter.drawText(caption_rect, Qt.AlignCenter, caption)
            if index == self.selected_index:
                painter.setBrush(Qt.white); painter.setPen(QPen(color, 2))
                for point in self._handle_positions(rect).values():
                    painter.drawEllipse(point, 4, 4)
        if self._draft_start and self._draft_end:
            start, end = self._to_canvas(self._draft_start), self._to_canvas(self._draft_end)
            painter.setPen(QPen(QColor("#0A74FF"), 2, Qt.DashLine))
            painter.drawRect(QRectF(start, end).normalized())

    def mousePressEvent(self, event):
        if self._pixmap.isNull() or event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)
        self.setFocus()
        point = event.position()
        image_point = self._to_image(point)
        if self.tool == "pan":
            self._drag_point = point; self._drag_origin = QPointF(self.pan); self.setCursor(Qt.ClosedHandCursor)
        elif self.tool == "box" and image_point:
            self._draft_start = self._draft_end = image_point
        elif self.tool == "select":
            handle = self._handle_at(point)
            if handle and image_point and self.selected_index >= 0:
                self._drag_point = image_point
                self._drag_index = self.selected_index
                self._drag_shape = self._copy_shapes([self.shapes[self.selected_index]])[0]
                self._drag_mode = "resize"; self._resize_handle = handle
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
        elif self.tool == "select" and self._drag_point and self._drag_shape and self._drag_index >= 0:
            current = self._to_image(point)
            if current:
                x1, y1, x2, y2 = self._drag_shape["bbox"]
                if self._drag_mode == "resize":
                    left, top, right, bottom = min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)
                    handle = self._resize_handle or ""
                    if "w" in handle: left = min(current.x(), right - 3)
                    if "e" in handle: right = max(current.x(), left + 3)
                    if "n" in handle: top = min(current.y(), bottom - 3)
                    if "s" in handle: bottom = max(current.y(), top + 3)
                    self.shapes[self._drag_index]["bbox"] = [int(left), int(top), int(right), int(bottom)]
                else:
                    dx, dy = current.x() - self._drag_point.x(), current.y() - self._drag_point.y()
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
                self.shapes.append({"name": self.default_label, "bbox": [int(min(x1, x2)), int(min(y1, y2)), int(max(x1, x2)), int(max(y1, y2))]})
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
        layout = QVBoxLayout(self); layout.setContentsMargins(24, 24, 24, 24); layout.setSpacing(14)
        header = QHBoxLayout(); header.addWidget(label("自动标注", "title")); header.addStretch(); chip = label("流程 1 / 4"); chip.setStyleSheet("background:#EAF3FF;color:#0A74FF;border-radius:10px;padding:6px 10px;font-weight:700;"); header.addWidget(chip); layout.addLayout(header)
        self.project_summary = label("从左侧项目菜单新建、导入或选择最近项目后即可开始。", "muted"); layout.addWidget(self.project_summary)
        data = card(); grid = QGridLayout(data); grid.setContentsMargins(22, 20, 22, 20); grid.setHorizontalSpacing(14); grid.setVerticalSpacing(12)
        grid.addWidget(label("项目数据", "section"), 0, 0, 1, 3); grid.addWidget(label("项目内图片", "muted"), 1, 0)
        self.images = QLineEdit(); self.images.setReadOnly(True); self.images.setPlaceholderText("新建项目后，图片将保存在这里"); grid.addWidget(self.images, 1, 1); self.import_images = button("导入图片"); self.import_images.clicked.connect(self._import_images); grid.addWidget(self.import_images, 1, 2)
        grid.addWidget(label("项目内标注", "muted"), 2, 0); self.annotations = QLineEdit(); self.annotations.setReadOnly(True); self.annotations.setPlaceholderText("与图片一起保存的 XML 标注"); grid.addWidget(self.annotations, 2, 1); self.import_annotations = button("导入标注"); self.import_annotations.clicked.connect(self._import_annotations); grid.addWidget(self.import_annotations, 2, 2); layout.addWidget(data)
        setup = card(); grid = QGridLayout(setup); grid.setContentsMargins(22, 20, 22, 20); grid.setHorizontalSpacing(14); grid.setVerticalSpacing(12)
        grid.addWidget(label("标注配置", "section"), 0, 0, 1, 3); grid.addWidget(label("检测模型", "muted"), 1, 0); self.model = FlatComboBox(); self.model.addItems(["Grounding DINO 1.5（推荐）", "Grounding DINO", "YOLO（微调）"]); grid.addWidget(self.model, 1, 1, 1, 2)
        grid.addWidget(label("类别提示词", "muted"), 2, 0); self.prompt = QLineEdit(config.PROMPT); grid.addWidget(self.prompt, 2, 1); categories = button("管理类别"); categories.clicked.connect(self._categories); grid.addWidget(categories, 2, 2); layout.addWidget(setup)
        actions = QHBoxLayout(); self.start = button("开始自动标注", primary=True); self.start.clicked.connect(self._start_labeling); actions.addWidget(self.start); self.stop = button("停止"); self.stop.clicked.connect(self._stop_labeling); self.stop.setEnabled(False); actions.addWidget(self.stop); actions.addWidget(label("完成后将写入项目内标注并进入“待审核”。", "muted")); actions.addStretch(); layout.addLayout(actions)
        self.progress = QProgressBar(); self.progress.setTextVisible(False); self.progress.setValue(0); layout.addWidget(self.progress)
        self.log = QPlainTextEdit(); self.log.setReadOnly(True); self.log.setPlaceholderText("运行记录会显示在这里"); self.log.setMinimumHeight(140); layout.addWidget(self.log, 1)
        self.log_message.connect(self.log.appendPlainText)
        self.progress_changed.connect(self.progress.setValue)
        self.batch_finished.connect(self._on_label_finished)
        self.batch_failed.connect(self._on_label_failed)

    def sync_project(self, root: str | None = None, name: str | None = None):
        active = bool(root and self.window.project_root)
        self.images.setText(self.window.images_dir if active else "")
        self.annotations.setText(self.window.annotations_dir if active else "")
        self.import_images.setEnabled(active); self.import_annotations.setEnabled(active)
        self.start.setEnabled(active and not (self._worker_thread and self._worker_thread.is_alive()))
        self.project_summary.setText(f"当前项目：{name or Path(root).name} · 图片、标注和审核记录均保存于项目目录。" if active else "先从左侧三点菜单新建项目或打开项目。")

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
        value, ok = QInputDialog.getText(self, "项目类别", "使用空格或句点分隔类别：", text=self.prompt.text())
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
        pending = [
            filename for filename in core.list_images(self.window.images_dir)
            if not os.path.isfile(os.path.join(self.window.annotations_dir, Path(filename).stem + ".xml"))
        ]
        if not pending:
            self.log.appendPlainText("没有待标注图片。已有 XML 标注已经可在“待审核”中处理。")
            self.window.refresh_project_state(); self.window.show_page("review"); return
        backend = {
            "Grounding DINO 1.5（推荐）": "gd15",
            "Grounding DINO": "gd_ogc",
            "YOLO（微调）": "yolo",
        }.get(self.model.currentText(), "gd15")
        self._stop_event.clear(); self.start.setEnabled(False); self.stop.setEnabled(True); self.progress.setValue(0)
        self.log.appendPlainText(f"准备自动标注 {len(pending)} 张图片，输出将保存到项目的 annotations 文件夹。")
        project_root = self.window.project_root
        self._worker_thread = threading.Thread(
            target=self._run_labeling,
            args=(project_root, self.window.images_dir, self.window.annotations_dir, pending, prompt, backend),
            daemon=True,
        )
        self._worker_thread.start()

    def _stop_labeling(self):
        if self._worker_thread and self._worker_thread.is_alive():
            self._stop_event.set(); self.stop.setEnabled(False); self.log.appendPlainText("将在当前图片处理完成后停止。")

    def _run_labeling(self, project_root: str, images_dir: str, annotations_dir: str, filenames: list[str], prompt: str, backend: str):
        completed = []
        try:
            self.log_message.emit("正在加载检测模型，请稍候…")
            detector = core.load_models(backend)
            self.log_message.emit("模型加载完成，开始推理。")
            total = len(filenames)
            for index, filename in enumerate(filenames):
                if self._stop_event.is_set():
                    self.log_message.emit("已按请求停止自动标注。"); break
                image_path = os.path.join(images_dir, filename)
                try:
                    annotations = core.auto_label(image_path, prompt, detector)
                    core.save_as_voc_xml(annotations, image_path, annotations_dir)
                    completed.append(filename)
                    self.log_message.emit(f"[完成] {filename}：{len(annotations)} 个目标")
                except Exception as exc:
                    self.log_message.emit(f"[失败] {filename}：{exc}")
                self.progress_changed.emit(int((index + 1) * 100 / total))
            self.batch_finished.emit(project_root, completed, self._stop_event.is_set())
        except ModuleNotFoundError as exc:
            self.batch_failed.emit(f"缺少模型依赖：{exc.name}。请安装 requirements.txt 中的依赖。")
        except Exception as exc:
            self.batch_failed.emit(f"自动标注失败：{exc}")

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
        QMessageBox.warning(self, "自动标注失败", message)


class ReviewPage(QWidget):
    def __init__(self, window):
        super().__init__(); self.window = window; self.files: list[str] = []; self.index = 0
        root = QVBoxLayout(self); root.setContentsMargins(24, 20, 24, 18); root.setSpacing(12)
        bar = card(); tools = QHBoxLayout(bar); tools.setContentsMargins(14, 10, 14, 10); tools.setSpacing(4)
        paging = QFrame(bar); paging.setObjectName("paging"); paging.setFixedHeight(40); paging_layout = QHBoxLayout(paging); paging_layout.setContentsMargins(0, 0, 0, 0); paging_layout.setSpacing(0)
        self.previous = tool(paging, QIcon(), "上一张\n快捷键：←"); self.previous.setObjectName("pagingChevron"); self.previous.setText("‹"); self.previous.clicked.connect(lambda: self.move(-1)); paging_layout.addWidget(self.previous)
        self.position = label("0 / 0", "pagingPosition"); self.position.setAlignment(Qt.AlignCenter); self.position.setFixedSize(112, 40); paging_layout.addWidget(self.position)
        self.next = tool(paging, QIcon(), "下一张\n快捷键：→"); self.next.setObjectName("pagingChevron"); self.next.setText("›"); self.next.clicked.connect(lambda: self.move(1)); paging_layout.addWidget(self.next); tools.addWidget(paging, 0, Qt.AlignVCenter)
        tools.addSpacing(16); self.tool_group = QButtonGroup(self); self.tool_group.setExclusive(True)
        self.pan_tool = tool(bar, sprite_icon("pan", size=24), "平移画布\n快捷键：H\n滚轮缩放，双击适应", checkable=True)
        self.select_tool = tool(bar, sprite_icon("select", size=24), "选择、移动和缩放标注框\n快捷键：V", checkable=True)
        self.box_tool = tool(bar, sprite_icon("box", size=24), "绘制矩形标注框\n快捷键：R", checkable=True)
        for control, mode in ((self.pan_tool, "pan"), (self.select_tool, "select"), (self.box_tool, "box")):
            self.tool_group.addButton(control); control.toggled.connect(lambda checked=False, value=mode: self.canvas.set_tool(value) if checked else None); tools.addWidget(control)
        tools.addSpacing(8); undo = tool(bar, sprite_icon("undo"), "撤销\n快捷键：Ctrl+Z"); undo.clicked.connect(self.undo); tools.addWidget(undo)
        redo = tool(bar, sprite_icon("redo"), "重做\n快捷键：Ctrl+Shift+Z"); redo.clicked.connect(self.redo); tools.addWidget(redo)
        remove = tool(bar, sprite_icon("delete"), "删除选中标注\n快捷键：Delete"); remove.clicked.connect(self.delete_selected); tools.addWidget(remove)
        tools.addSpacing(8); tools.addWidget(label("滚轮缩放   双击适应   Ctrl+S 保存   Ctrl+Z 撤销   Del 删除", "muted")); tools.addStretch()
        save = button("保存", primary=True); save.setToolTip("保存标注\n快捷键：Ctrl+S"); save.clicked.connect(self.save); tools.addWidget(save); root.addWidget(bar)
        work = QHBoxLayout(); work.setSpacing(14); canvas_card = card(); canvas_layout = QVBoxLayout(canvas_card); canvas_layout.setContentsMargins(10, 10, 10, 10); self.canvas = ImageCanvas(); self.canvas.shapes_changed.connect(self._canvas_changed); self.canvas.selection_changed.connect(self._selection_changed); self.canvas.box_created.connect(self._new_box_created); self.pan_tool.setChecked(True); canvas_layout.addWidget(self.canvas); work.addWidget(canvas_card, 1)
        inspector = card(); inspector.setFixedWidth(280); form = QVBoxLayout(inspector); form.setContentsMargins(20, 20, 20, 20); form.setSpacing(10); form.addWidget(label("标签", "section")); form.addSpacing(8)
        form.addWidget(label("显示类别（可多选）", "muted")); self.filter_host = QWidget(); self.filter_layout = QVBoxLayout(self.filter_host); self.filter_layout.setContentsMargins(0, 0, 0, 0); self.filter_layout.setSpacing(4); self.filter_scroll = QScrollArea(); self.filter_scroll.setWidgetResizable(True); self.filter_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff); self.filter_scroll.setFixedHeight(104); self.filter_scroll.setWidget(self.filter_host); form.addWidget(self.filter_scroll)
        form.addSpacing(8); form.addWidget(label("选中框标签", "muted")); self.category = QLineEdit(); self.category.setPlaceholderText("选择框后可修改标签名"); self.category.setEnabled(False); form.addWidget(self.category); form.addSpacing(8); form.addWidget(label("状态", "muted"))
        segments = QHBoxLayout(); self.status_group = QButtonGroup(self); self.status_group.setExclusive(True)
        for caption, state in (("审核", STATUS_NEEDS_REVIEW), ("通过", STATUS_APPROVED), ("跳过", STATUS_SKIPPED)):
            item = QPushButton(caption); item.setCheckable(True); item.setObjectName("secondary"); item.clicked.connect(lambda checked=False, s=state: self.set_status(s)); self.status_group.addButton(item); segments.addWidget(item)
            if state == STATUS_NEEDS_REVIEW: item.setChecked(True)
        form.addLayout(segments); form.addSpacing(8); form.addWidget(label("备注", "muted")); self.notes = QPlainTextEdit(); self.notes.setPlaceholderText("请输入备注（选填）"); self.notes.setMinimumHeight(112); self.notes.setMaximumHeight(156); self.notes.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding); form.addWidget(self.notes, 1); form.addSpacing(12); form.addStretch(0)
        self.image_nav_host = QWidget(); self.image_nav_host.setFixedHeight(40); image_nav = QHBoxLayout(self.image_nav_host); image_nav.setContentsMargins(0, 0, 0, 0); image_nav.setSpacing(8); previous = button("‹ 上一张"); previous.clicked.connect(lambda: self.move(-1)); following = button("下一张 ›"); following.clicked.connect(lambda: self.move(1)); image_nav.addWidget(previous); image_nav.addWidget(following); form.addWidget(self.image_nav_host, 0); work.addWidget(inspector); root.addLayout(work, 1)
        strip = card(); strip_layout = QVBoxLayout(strip); strip_layout.setContentsMargins(10, 10, 10, 10); self.thumbnails = QScrollArea(); self.thumbnails.setWidgetResizable(True); self.thumbnails.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff); self.thumbnails.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff); self.thumb_host = QWidget(); self.thumb_row = QHBoxLayout(self.thumb_host); self.thumb_row.setContentsMargins(0, 0, 0, 0); self.thumb_row.setSpacing(10); self.thumbnails.setWidget(self.thumb_host); strip_layout.addWidget(self.thumbnails); strip.setFixedHeight(112); root.addWidget(strip)
        footer = QHBoxLayout(); footer.addWidget(label("审核进度", "muted")); self.progress = QProgressBar(); self.progress.setObjectName("reviewProgress"); self.progress.setTextVisible(False); self.progress.setFixedHeight(4); footer.addWidget(self.progress, 1, Qt.AlignVCenter); self.progress_text = label("0%", "muted"); footer.addWidget(self.progress_text); root.addLayout(footer)
        QShortcut(QKeySequence("Left"), self, activated=lambda: self.move(-1))
        QShortcut(QKeySequence("Right"), self, activated=lambda: self.move(1))
        QShortcut(QKeySequence("H"), self, activated=lambda: self.pan_tool.setChecked(True))
        QShortcut(QKeySequence("V"), self, activated=lambda: self.select_tool.setChecked(True))
        QShortcut(QKeySequence("R"), self, activated=lambda: self.box_tool.setChecked(True))
        QShortcut(QKeySequence("Ctrl+S"), self, activated=self.save)
        self.category.editingFinished.connect(self._apply_selected_category)
        QShortcut(QKeySequence("Ctrl+Z"), self, activated=self.undo)
        QShortcut(QKeySequence("Ctrl+Shift+Z"), self, activated=self.redo)
        QShortcut(QKeySequence("Delete"), self, activated=self.delete_selected)

    def refresh(self):
        self.files = self.window.project_images(STATUS_NEEDS_REVIEW); self.index = min(self.index, max(0, len(self.files) - 1)); self._load()

    def _load(self):
        while self.thumb_row.count():
            item = self.thumb_row.takeAt(0); widget = item.widget(); widget.deleteLater() if widget else None
        total = len(self.files)
        current = self.index + 1 if total else 0
        percent = round(current * 100 / total) if total else 0
        self.position.setText(f"{current} / {total}"); self.progress.setValue(percent); self.progress_text.setText(f"{percent}%")
        if not total:
            self.canvas.set_content(None, []); return
        for index, filename in enumerate(self.files):
            item = QToolButton(); item.setFixedSize(116, 74); item.setCheckable(True); item.setChecked(index == self.index); item.setIcon(QIcon(os.path.join(self.window.images_dir, filename))); item.setIconSize(QSize(108, 66)); item.setStyleSheet("QToolButton {border: 2px solid transparent; border-radius: 8px;} QToolButton:checked {border-color:#0A74FF;}")
            item.clicked.connect(lambda checked=False, target=index: self.select(target)); self.thumb_row.addWidget(item)
        self.thumb_row.addStretch()
        filename = self.files[self.index]; image_path = os.path.join(self.window.images_dir, filename); xml_path = os.path.join(self.window.annotations_dir, Path(filename).stem + ".xml")
        try: shapes = [{"name": x["name"], "bbox": x["bbox"]} for x in core.load_voc_xml(xml_path)] if os.path.isfile(xml_path) else []
        except Exception: shapes = []
        self.canvas.set_content(image_path, shapes)
        self.category.clear(); self.category.setEnabled(False)
        self._rebuild_category_filters()

    def select(self, index: int): self.index = index; self._load()
    def move(self, delta: int):
        if self.files: self.index = max(0, min(len(self.files) - 1, self.index + delta)); self._load()
    def _available_categories(self):
        project_categories = self.window.project.categories if self.window.project else []
        names = project_categories + [shape.get("name", "") for shape in self.canvas.shapes]
        result = []
        for name in names:
            name = str(name).strip()
            if name and name not in result:
                result.append(name)
        return result

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
        self._rebuild_category_filters({name for name, item in self._filter_checks.items() if item.isChecked()} | {name})

    def _selection_changed(self, index: int):
        if 0 <= index < len(self.canvas.shapes):
            name = self.canvas.shapes[index].get("name", "")
            self.category.setText(name); self.category.setEnabled(True)
        else:
            self.category.clear(); self.category.setEnabled(False)

    def _new_box_created(self, index: int):
        categories = self._available_categories() or ["object"]
        value, ok = QInputDialog.getItem(self, "新增矩形标注", "类别（可输入新类别）：", categories, 0, True)
        if not ok or not value.strip():
            self.canvas.delete_selected()
            self.window.status_message("已取消新增标注")
            return
        self.category.setText(value.strip()); self.category.setEnabled(True)
        self._apply_selected_category()

    def _canvas_changed(self, _shapes):
        self.window.status_message("标注已修改，点击“保存”写入 XML")

    def undo(self):
        self.window.status_message("已撤销上一步修改" if self.canvas.undo() else "没有可撤销的修改")

    def redo(self):
        self.window.status_message("已重做上一步修改" if self.canvas.redo() else "没有可重做的修改")

    def delete_selected(self):
        self.window.status_message("已删除选中标注，点击“保存”写入 XML" if self.canvas.delete_selected() else "请先用选择工具选中标注框")

    def save(self):
        if not self.files:
            return
        filename = self.files[self.index]
        image_path = os.path.join(self.window.images_dir, filename)
        core.save_as_voc_xml(self.canvas.shapes, image_path, self.window.annotations_dir)
        self.window.status_message("已保存到项目 annotations 文件夹")
    def set_status(self, status):
        if not self.files or not self.window.project: return
        self.window.project.set_status(self.files[self.index], status); self.window.refresh_all(); self.refresh()


class LibraryPage(QWidget):
    def __init__(self, window):
        super().__init__(); self.window = window; layout = QVBoxLayout(self); layout.setContentsMargins(24, 24, 24, 24); layout.setSpacing(14)
        header = QHBoxLayout(); header.addWidget(label("已通过", "title")); self.count = label("0 张已通过图片", "muted"); header.addWidget(self.count); header.addStretch(); export = button("进入训练集导出", primary=True); export.clicked.connect(lambda: window.show_page("export")); header.addWidget(export); layout.addLayout(header); layout.addWidget(label("这里只保存已确认的标注成果；修改标注请回到待审核队列。", "muted"))
        surface = card(); self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True); self.host = QWidget(); self.grid = QGridLayout(self.host); self.grid.setContentsMargins(18, 18, 18, 18); self.grid.setSpacing(14); self.grid.setAlignment(Qt.AlignTop | Qt.AlignLeft); self.scroll.setWidget(self.host); lay = QVBoxLayout(surface); lay.setContentsMargins(8, 8, 8, 8); lay.addWidget(self.scroll); layout.addWidget(surface, 1)

    def refresh(self):
        while self.grid.count():
            item = self.grid.takeAt(0); widget = item.widget(); widget.deleteLater() if widget else None
        files = self.window.project_images(STATUS_APPROVED); self.count.setText(f"{len(files)} 张已通过图片")
        if not files:
            empty = label("暂无已通过图片\n审核通过的结果会自动出现在这里", "muted"); empty.setAlignment(Qt.AlignCenter); self.grid.addWidget(empty, 0, 0); return
        for index, filename in enumerate(files):
            item = card(); item.setFixedSize(224, 202); lay = QVBoxLayout(item); lay.setContentsMargins(8, 8, 8, 8); image = QLabel(); image.setFixedSize(208, 124); pix = QPixmap(os.path.join(self.window.images_dir, filename)).scaled(image.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation); image.setPixmap(pix); image.setAlignment(Qt.AlignCenter); lay.addWidget(image); file_label = label(filename); file_label.setFixedWidth(208); file_label.setWordWrap(False); file_label.setToolTip(filename); lay.addWidget(file_label)
            bottom = QHBoxLayout(); badge = label("已通过"); badge.setStyleSheet("color:#0A74FF;background:#EAF3FF;border-radius:8px;padding:4px 7px;font-weight:700;"); bottom.addWidget(badge); bottom.addStretch(); remove = destructive_button("删除记录"); remove.setToolTip("将该结果移回待审核；不会删除原图或 XML 标注"); remove.clicked.connect(lambda checked=False, target=filename: self.remove_record(target)); bottom.addWidget(remove); lay.addLayout(bottom); self.grid.addWidget(item, index // 4, index % 4, Qt.AlignTop | Qt.AlignLeft)

    def remove_record(self, filename: str):
        choice = QMessageBox.question(
            self,
            "删除通过记录",
            f"要删除“{filename}”的通过记录吗？\n\n原图和 XML 标注不会删除，该图片会移回待审核队列。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if choice != QMessageBox.Yes or not self.window.project:
            return
        self.window.project.set_status(filename, STATUS_NEEDS_REVIEW)
        self.window.refresh_all()
        self.window.status_message(f"已删除通过记录：{filename}，已移回待审核")


class ExportPage(QWidget):
    def __init__(self, window):
        super().__init__(); self.window = window; layout = QVBoxLayout(self); layout.setContentsMargins(24, 24, 24, 24); layout.setSpacing(14)
        header = QHBoxLayout(); header.addWidget(label("训练集导出", "title")); header.addStretch(); chip = label("流程 4 / 4"); chip.setStyleSheet("background:#EAF3FF;color:#0A74FF;border-radius:10px;padding:6px 10px;font-weight:700;"); header.addWidget(chip); layout.addLayout(header); layout.addWidget(label("将已通过审核的图片导出为训练集；训练时使用同一项目的数据配置。", "muted"))
        stages = QHBoxLayout(); stages.setSpacing(14); stages.addWidget(self._export_card(), 1); stages.addWidget(self._train_card(), 1); layout.addLayout(stages)
        log_card = card(); log_layout = QVBoxLayout(log_card); log_layout.setContentsMargins(20, 20, 20, 20); log_layout.addWidget(label("运行记录", "section")); self.log = QPlainTextEdit(); self.log.setReadOnly(True); self.log.setPlaceholderText("导出与训练的运行记录会显示在这里"); log_layout.addWidget(self.log); layout.addWidget(log_card, 1)

    def _export_card(self):
        surface = card(); grid = QGridLayout(surface); grid.setContentsMargins(20, 20, 20, 20); grid.setSpacing(12); grid.addWidget(label("1 · 导出数据集", "section"), 0, 0, 1, 2); grid.addWidget(label("仅导出审核通过的标注结果。", "muted"), 1, 0, 1, 2); grid.addWidget(label("输出目录", "muted"), 2, 0); self.out = QLineEdit(os.path.join(config.BASE_DIR, "yolo_dataset")); grid.addWidget(self.out, 2, 1); grid.addWidget(label("验证集比例", "muted"), 3, 0); self.ratio = QLineEdit("0.2"); grid.addWidget(self.ratio, 3, 1); run = button("导出数据集", primary=True); run.clicked.connect(lambda: self.log.appendPlainText("已提交导出任务。")); grid.addWidget(run, 4, 1); return surface

    def _train_card(self):
        surface = card(); grid = QGridLayout(surface); grid.setContentsMargins(20, 20, 20, 20); grid.setSpacing(12); grid.addWidget(label("2 · 微调训练", "section"), 0, 0, 1, 2); grid.addWidget(label("使用导出的 YOLO 数据配置开始训练。", "muted"), 1, 0, 1, 2); grid.addWidget(label("预训练模型", "muted"), 2, 0); model = FlatComboBox(); model.addItems(["yolov8n.pt", "yolov8s.pt", "yolov11n.pt"]); grid.addWidget(model, 2, 1); grid.addWidget(label("训练轮数", "muted"), 3, 0); epochs = QLineEdit("100"); grid.addWidget(epochs, 3, 1); run = button("开始训练", primary=True); run.clicked.connect(lambda: self.log.appendPlainText("已提交训练任务。")); grid.addWidget(run, 4, 1); return surface


class Workbench(QMainWindow):
    def __init__(self):
        super().__init__(); self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window); self.setWindowTitle(" "); self.setWindowIcon(QIcon()); self.resize(1420, 900); self.setMinimumSize(1100, 720)
        self.workspace = WorkspaceStore()
        self.project: ProjectStore | None = None; self.project_root = ""; self.project_name = ""; self.images_dir = ""; self.annotations_dir = ""
        root = ResizableRoot(self); root.setObjectName("root"); self.setCentralWidget(root); root_layout = QVBoxLayout(root); root_layout.setContentsMargins(7, 7, 7, 7); root_layout.setSpacing(0); root_layout.addWidget(WindowChrome(self))
        layout = QHBoxLayout(); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(0); root_layout.addLayout(layout, 1)
        side = QFrame(); side.setObjectName("sidebar"); side.setFixedWidth(236); side_layout = QVBoxLayout(side); side_layout.setContentsMargins(16, 24, 16, 20); side_layout.setSpacing(6)
        title = QHBoxLayout(); title.addWidget(label("项目", "section")); title.addStretch(); more = QToolButton(); more.setText("•••"); more.setCursor(Qt.PointingHandCursor); more.setStyleSheet("border:0;font-weight:700;font-size:14px;color:#646873;"); more.clicked.connect(self.project_menu); title.addWidget(more); side_layout.addLayout(title); side_layout.addSpacing(10)
        self.recent_title = label("最近项目", "muted"); side_layout.addWidget(self.recent_title)
        self.recent_host = QWidget(); self.recent_layout = QVBoxLayout(self.recent_host); self.recent_layout.setContentsMargins(0, 2, 0, 2); self.recent_layout.setSpacing(5); side_layout.addWidget(self.recent_host)
        side_layout.addSpacing(12)
        self.nav_group = QButtonGroup(self); self.nav_group.setExclusive(True); self.nav = {}
        for key, title, icon in (("auto", "自动标注", sprite_icon("auto", nav=True)), ("review", "待审核", sprite_icon("review", nav=True)), ("approved", "已通过", sprite_icon("approved", nav=True)), ("export", "训练集导出", sprite_icon("export", nav=True))):
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

    def toggle_maximized(self):
        self.showNormal() if self.isMaximized() else self.showMaximized()

    def new_project(self):
        name, ok = QInputDialog.getText(self, "新建项目", "项目名称：")
        if not ok or not name.strip(): return
        parent = QFileDialog.getExistingDirectory(self, "选择项目保存位置", config.BASE_DIR)
        if not parent: return
        root = os.path.join(parent, name.strip())
        if os.path.exists(root):
            self.status_message("项目目录已存在"); return
        for folder in ("images", "annotations", "yolo_dataset"): os.makedirs(os.path.join(root, folder), exist_ok=True)
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
        except (KeyError, TypeError, ValueError):
            self.status_message("项目文件无效，无法读取目录配置"); return
        if not os.path.isdir(images):
            self.status_message("项目图片目录不存在"); return
        os.makedirs(annotations, exist_ok=True)
        self.project_root = root
        self.project_name = str(manifest.get("name") or Path(root).name)
        imported = self.use_folders(images, annotations, root)
        self.workspace.remember_project(root, self.project_name)
        self.refresh_recent_projects()
        self.auto.sync_project(root, self.project_name)
        self.status_message(f"已打开项目：{self.project_name} · 新导入 {imported} 张待审核")
        self.show_page("review")

    def use_folders(self, images: str, annotations: str, project_root: str | None = None):
        if project_root:
            self.project_root = os.path.abspath(project_root)
        self.images_dir, self.annotations_dir = images, annotations
        self.project = ProjectStore(annotations, images, self.project_root or project_root)
        existing = [
            filename for filename in core.list_images(images)
            if os.path.isfile(os.path.join(annotations, Path(filename).stem + ".xml"))
        ]
        queued = [
            filename for filename in existing
            if self.project.status_for(filename) in (STATUS_UNREVIEWED, STATUS_SKIPPED)
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
        return [file for file in files if status is None or self.project and self.project.status_for(file) == status]

    def refresh_all(self):
        files = self.project_images(); summary = self.project.summary(files) if self.project else {}
        self.nav["auto"].set_count(str(len(files)) if files else "")
        self.nav["review"].set_count(str(summary.get(STATUS_NEEDS_REVIEW, 0)) if summary.get(STATUS_NEEDS_REVIEW, 0) else "")
        self.nav["approved"].set_count(str(summary.get(STATUS_APPROVED, 0)) if summary.get(STATUS_APPROVED, 0) else "")
        self.review.refresh(); self.library.refresh()

    def show_page(self, key: str):
        self.pages.setCurrentWidget(self.keys[key]); self.nav[key].setChecked(True)
        if key == "review": self.review.refresh()
        if key == "approved": self.library.refresh()

    def status_message(self, text: str): self.statusBar().showMessage(text, 4000)

    def closeEvent(self, event):
        # Persist the active project at the moment the user closes the app as
        # well as when it was opened, so startup can reliably restore it.
        if self.project_root:
            self.workspace.remember_project(self.project_root, self.project_name)
        super().closeEvent(event)


def main():
    app = QApplication.instance() or QApplication([])
    apply_app_style(app)
    window = Workbench(); window.show()
    return app.exec()
