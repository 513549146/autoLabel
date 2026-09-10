"""A clean-room Qt workbench.  It intentionally does not instantiate the legacy Tk pages."""
from __future__ import annotations

import os
import shutil
import threading
from pathlib import Path

from PySide6.QtCore import QEvent, QPointF, QRectF, QTimer, Qt, QSize, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QImage, QKeySequence, QPainter, QPainterPath, QPen, QPixmap, QPolygonF, QShortcut
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
            copied.append(item)
        return copied

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
        self.hidden_indices.clear()
        self._history.clear(); self._redo.clear()
        self.update()

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
        self.shapes[self.selected_index].pop("score", None)
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
            try:
                if shape.get("score") is not None:
                    caption += f"  {float(shape['score']) * 100:.0f}%"
            except (TypeError, ValueError):
                pass
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
                self.shapes[self._drag_index].pop("score", None)
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
        if self.tool == "polygon" and event.button() == Qt.LeftButton:
            if len(self._polygon_draft) >= 3:
                before = self._copy_shapes(self.shapes)
                points = self._polygon_draft[:]
                self.shapes.append({"name": self.default_label, "type": "polygon", "points": points, "bbox": self._bbox_for_points(points)})
                self._polygon_draft = []; self._polygon_hover = None
                self._set_selected(len(self.shapes) - 1); self._commit(before); self.polygon_created.emit(self.selected_index)
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
        self._autosave_timer = QTimer(self); self._autosave_timer.setSingleShot(True); self._autosave_timer.timeout.connect(self._autosave_current)
        self._notes_timer = QTimer(self); self._notes_timer.setSingleShot(True); self._notes_timer.timeout.connect(self._save_note)
        root = QVBoxLayout(self); root.setContentsMargins(24, 20, 24, 18); root.setSpacing(12)
        bar = card(); bar_layout = QVBoxLayout(bar); bar_layout.setContentsMargins(14, 8, 14, 8); bar_layout.setSpacing(3); tools = QHBoxLayout(); tools.setContentsMargins(0, 0, 0, 0); tools.setSpacing(4)
        paging = QFrame(bar); paging.setObjectName("paging"); paging.setFixedHeight(40); paging_layout = QHBoxLayout(paging); paging_layout.setContentsMargins(0, 0, 0, 0); paging_layout.setSpacing(0)
        self.previous = tool(paging, QIcon(), "上一张（←）"); self.previous.setObjectName("pagingChevron"); self.previous.setText("‹"); self.previous.clicked.connect(lambda: self.move(-1)); paging_layout.addWidget(self.previous)
        self.position = label("0 / 0", "pagingPosition"); self.position.setAlignment(Qt.AlignCenter); self.position.setFixedSize(112, 40); paging_layout.addWidget(self.position)
        self.next = tool(paging, QIcon(), "下一张（→）"); self.next.setObjectName("pagingChevron"); self.next.setText("›"); self.next.clicked.connect(lambda: self.move(1)); paging_layout.addWidget(self.next); tools.addWidget(paging, 0, Qt.AlignVCenter)
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
        self.issue_filter = FlatComboBox(); self.issue_filter.addItem("全部待审核", "all"); self.issue_filter.addItem("仅问题项", "issues"); self.issue_filter.addItem("低置信度", "low_confidence"); self.issue_filter.addItem("空标注", "empty"); self.issue_filter.setToolTip("按自动检查结果筛选审核队列"); self.issue_filter.currentIndexChanged.connect(self.refresh); review_actions.addWidget(self.issue_filter)
        shortcut_menu = QMenu(self)
        shortcut_menu.setStyleSheet("QMenu {background:#FFFFFF;border:1px solid #E3E5E9;border-radius:12px;padding:6px;} QMenu::item {padding:7px 16px;border-radius:7px;color:#2E3138;} QMenu::item:selected {background:#EAF3FF;color:#0A74FF;} QMenu::item:disabled {color:#737780;font-weight:700;} QMenu::separator {height:1px;background:#ECEEF2;margin:5px 8px;}")
        for caption in ("画布工具", "H  平移画布", "V  选择、移动和缩放", "R  矩形标注", "P  多边形标注"):
            action = shortcut_menu.addAction(caption); action.setEnabled(caption != "画布工具")
        shortcut_menu.addSeparator()
        for caption in ("审核与文件", "← / →  上一张 / 下一张", "Ctrl+Z / Ctrl+Shift+Z  撤销 / 重做", "Delete  删除", "Ctrl+S  保存", "L  标注清单", "A / S  通过 / 跳过并下一张"):
            action = shortcut_menu.addAction(caption); action.setEnabled(caption != "审核与文件")
        shortcut_button = QToolButton(bar); shortcut_button.setObjectName("shortcutGuide"); shortcut_button.setText("⌘"); shortcut_button.setFixedSize(40, 40); shortcut_button.setToolTip("快捷键速查"); shortcut_button.clicked.connect(lambda: shortcut_menu.exec(shortcut_button.mapToGlobal(shortcut_button.rect().bottomLeft()))); shortcut_button.setCursor(Qt.PointingHandCursor); review_actions.insertWidget(0, shortcut_button)
        self.list_button = button("标注清单"); self.list_button.setCheckable(True); self.list_button.setToolTip("打开标注清单（L）"); self.list_button.clicked.connect(self.toggle_annotation_drawer); review_actions.addWidget(self.list_button)
        save = button("保存", primary=True); save.setToolTip("保存标注（Ctrl+S）"); save.clicked.connect(self.save); review_actions.addWidget(save); tools.addLayout(review_actions); bar_layout.addLayout(tools); root.addWidget(bar)
        work = QHBoxLayout(); work.setSpacing(14); self.canvas_card = card(); canvas_layout = QVBoxLayout(self.canvas_card); canvas_layout.setContentsMargins(10, 10, 10, 10); self.canvas = ImageCanvas(); self.canvas.shapes_changed.connect(self._canvas_changed); self.canvas.selection_changed.connect(self._selection_changed); self.canvas.box_created.connect(self._new_box_created); self.canvas.polygon_created.connect(self._new_box_created); self.pan_tool.setChecked(True); canvas_layout.addWidget(self.canvas); work.addWidget(self.canvas_card, 1)
        self._build_annotation_drawer()
        inspector = card(); inspector.setFixedWidth(280); inspector_layout = QVBoxLayout(inspector); inspector_layout.setContentsMargins(20, 20, 20, 20); inspector_layout.setSpacing(0); form_host = QWidget(); form = QVBoxLayout(form_host); form.setContentsMargins(0, 0, 0, 0); form.setSpacing(10); form.addWidget(label("标签", "section")); form.addSpacing(8)
        form.addWidget(label("显示类别（可多选）", "muted")); self.filter_host = QWidget(); self.filter_layout = QVBoxLayout(self.filter_host); self.filter_layout.setContentsMargins(0, 0, 0, 0); self.filter_layout.setSpacing(4); self.filter_scroll = QScrollArea(); self.filter_scroll.setWidgetResizable(True); self.filter_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff); self.filter_scroll.setFixedHeight(104); self.filter_scroll.setWidget(self.filter_host); form.addWidget(self.filter_scroll)
        form.addSpacing(8); form.addWidget(label("选中框标签", "muted")); self.category = QLineEdit(); self.category.setPlaceholderText("选择框后可修改标签名"); self.category.setEnabled(False); form.addWidget(self.category); form.addSpacing(8); form.addWidget(label("状态", "muted"))
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
        self.image_nav_host = QWidget(); self.image_nav_host.setFixedHeight(40); image_nav = QHBoxLayout(self.image_nav_host); image_nav.setContentsMargins(0, 0, 0, 0); image_nav.setSpacing(8); previous = button("‹ 上一张"); previous.clicked.connect(lambda: self.move(-1)); following = button("下一张 ›"); following.clicked.connect(lambda: self.move(1)); image_nav.addWidget(previous); image_nav.addWidget(following); inspector_layout.addWidget(self.image_nav_host, 0); work.addWidget(inspector); root.addLayout(work, 1)
        strip = card(); strip_layout = QVBoxLayout(strip); strip_layout.setContentsMargins(10, 10, 10, 10); self.thumbnails = QScrollArea(); self.thumbnails.setWidgetResizable(True); self.thumbnails.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff); self.thumbnails.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff); self.thumb_host = QWidget(); self.thumb_row = QHBoxLayout(self.thumb_host); self.thumb_row.setContentsMargins(0, 0, 0, 0); self.thumb_row.setSpacing(10); self.thumbnails.setWidget(self.thumb_host); strip_layout.addWidget(self.thumbnails); strip.setFixedHeight(112); root.addWidget(strip)
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
            confidence = ""
            try:
                if shape.get("score") is not None:
                    confidence = f"  ·  {float(shape['score']) * 100:.0f}%"
            except (TypeError, ValueError):
                pass
            item = QToolButton(); item.setObjectName("annotationRow"); item.setCheckable(True); item.setChecked(index == self.canvas.selected_index); item.setToolButtonStyle(Qt.ToolButtonTextOnly); item.setText(f"{name}   #{index + 1}  ·  {shape_type}{confidence}\n{x1}, {y1}   ·   {width} × {height}"); item.setFixedHeight(54)
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

    @staticmethod
    def _iou(first, second):
        ax1, ay1, ax2, ay2 = first; bx1, by1, bx2, by2 = second
        left, top, right, bottom = max(ax1, bx1), max(ay1, by1), min(ax2, bx2), min(ay2, by2)
        overlap = max(0, right - left) * max(0, bottom - top)
        if not overlap:
            return 0.0
        union = max(1, (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - overlap)
        return overlap / union

    def _issues_for(self, filename):
        """Return lightweight QA flags used to prioritize human review."""
        xml_path = os.path.join(self.window.annotations_dir, Path(filename).stem + ".xml")
        try:
            shapes = core.load_voc_xml(xml_path) if os.path.isfile(xml_path) else []
        except Exception:
            return {"invalid"}
        if not shapes:
            return {"empty"}
        image = QImage(os.path.join(self.window.images_dir, filename))
        image_width, image_height = image.width(), image.height()
        issues = set()
        for index, shape in enumerate(shapes):
            x1, y1, x2, y2 = [float(value) for value in shape.get("bbox", (0, 0, 0, 0))]
            width, height = abs(x2 - x1), abs(y2 - y1)
            if width * height < config.REVIEW_MIN_BOX_AREA:
                issues.add("tiny")
            if image_width > 0 and (x1 < 0 or y1 < 0 or x2 > image_width or y2 > image_height):
                issues.add("out_of_bounds")
            try:
                if shape.get("score") is not None and float(shape["score"]) < config.REVIEW_LOW_CONFIDENCE:
                    issues.add("low_confidence")
            except (TypeError, ValueError):
                pass
            for other in shapes[:index]:
                ox1, oy1, ox2, oy2 = [float(value) for value in other.get("bbox", (0, 0, 0, 0))]
                other_box = (min(ox1, ox2), min(oy1, oy2), max(ox1, ox2), max(oy1, oy2))
                current_box = (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
                if shape.get("name") == other.get("name") and self._iou(current_box, other_box) >= config.REVIEW_DUPLICATE_IOU:
                    issues.add("duplicate")
        return issues

    def _filtered_files(self):
        files = self.window.project_images(STATUS_NEEDS_REVIEW)
        mode = self.issue_filter.currentData() if hasattr(self, "issue_filter") else "all"
        if mode == "all":
            return files
        return [filename for filename in files if (mode in self._issues_for(filename) if mode != "issues" else bool(self._issues_for(filename)))]

    def _update_quality_hint(self, filename):
        labels = {
            "empty": "空标注", "low_confidence": "低置信度", "tiny": "极小框",
            "out_of_bounds": "越界框", "duplicate": "疑似重复框", "invalid": "标注文件异常",
        }
        issues = self._issues_for(filename)
        self.quality_hint.setText("问题提示：" + "、".join(labels[item] for item in sorted(issues)) if issues else "自动检查：未发现明显问题")
        self.quality_hint.setStyleSheet("color:#D97706;" if issues else "color:#17834C;")

    def refresh(self):
        self._save_current(silent=True)
        self.files = self._filtered_files(); self.index = min(self.index, max(0, len(self.files) - 1)); self._load()

    def _load(self):
        while self.thumb_row.count():
            item = self.thumb_row.takeAt(0); widget = item.widget(); widget.deleteLater() if widget else None
        total = len(self.files)
        current = self.index + 1 if total else 0
        percent = round(current * 100 / total) if total else 0
        self.position.setText(f"{current} / {total}"); self.progress.setValue(percent); self.progress_text.setText(f"{percent}%")
        if not total:
            self.notes.blockSignals(True); self.notes.clear(); self.notes.blockSignals(False)
            self.quality_hint.setText("")
            self.canvas.set_content(None, []); self._refresh_annotation_drawer(); return
        for index, filename in enumerate(self.files):
            item = QToolButton(); item.setFixedSize(116, 74); item.setCheckable(True); item.setChecked(index == self.index); item.setIcon(QIcon(os.path.join(self.window.images_dir, filename))); item.setIconSize(QSize(108, 66)); item.setStyleSheet("QToolButton {border: 2px solid transparent; border-radius: 8px;} QToolButton:checked {border-color:#0A74FF;}")
            item.clicked.connect(lambda checked=False, target=index: self.select(target)); self.thumb_row.addWidget(item)
        self.thumb_row.addStretch()
        filename = self.files[self.index]; image_path = os.path.join(self.window.images_dir, filename); xml_path = os.path.join(self.window.annotations_dir, Path(filename).stem + ".xml")
        try: shapes = core.load_voc_xml(xml_path) if os.path.isfile(xml_path) else []
        except Exception: shapes = []
        self.canvas.set_content(image_path, shapes)
        self.notes.blockSignals(True); self.notes.setPlainText(self.window.project.note_for(filename) if self.window.project else ""); self.notes.blockSignals(False)
        self._update_quality_hint(filename)
        self.category.clear(); self.category.setEnabled(False)
        self._rebuild_category_filters()
        self._refresh_annotation_drawer()

    def select(self, index: int):
        self._save_current(silent=True); self.index = index; self._load()
    def move(self, delta: int):
        if self.files:
            self._save_current(silent=True); self.index = max(0, min(len(self.files) - 1, self.index + delta)); self._load()
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
        self._refresh_annotation_drawer()

    def _selection_changed(self, index: int):
        if 0 <= index < len(self.canvas.shapes):
            name = self.canvas.shapes[index].get("name", "")
            self.category.setText(name); self.category.setEnabled(True)
        else:
            self.category.clear(); self.category.setEnabled(False)
        self._refresh_annotation_drawer()

    def _new_box_created(self, index: int):
        categories = self._available_categories() or ["object"]
        annotation_type = "多边形" if 0 <= index < len(self.canvas.shapes) and self.canvas.shapes[index].get("type") == "polygon" else "矩形"
        value, ok = QInputDialog.getItem(self, f"新增{annotation_type}标注", "类别（可输入新类别）：", categories, 0, True)
        if not ok or not value.strip():
            self.canvas.delete_selected()
            self.window.status_message("已取消新增标注")
            return
        self.category.setText(value.strip()); self.category.setEnabled(True)
        self._apply_selected_category()

    def _canvas_changed(self, _shapes):
        self._autosave_timer.start(650)
        self.window.status_message("标注已修改，正在自动保存…")
        self._refresh_annotation_drawer()

    def _autosave_current(self):
        self._save_current(silent=False, automatic=True)

    def flush_autosave(self):
        """Persist the visible image before the workbench swaps project paths."""
        self._save_current(silent=True)

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
        filename = self.files[self.index]
        image_path = os.path.join(self.window.images_dir, filename)
        core.save_as_voc_xml(self.canvas.shapes, image_path, self.window.annotations_dir)
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

    def _quick_review(self, status):
        focused = QApplication.focusWidget()
        if isinstance(focused, (QLineEdit, QPlainTextEdit, QComboBox)):
            return
        self.set_status(status)

    def set_status(self, status):
        if not self.files or not self.window.project:
            return
        filename = self.files[self.index]
        self._save_current(silent=True)
        self.window.project.set_status(filename, status)
        self.window.refresh_all()
        self.window.status_message("已通过并进入下一张" if status == STATUS_APPROVED else "已跳过并进入下一张" if status == STATUS_SKIPPED else "已更新审核状态")


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
        # ``ReviewPage`` still points at the previously open image at this
        # point. Flush it before replacing the project's directory fields.
        self.review.flush_autosave()
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
