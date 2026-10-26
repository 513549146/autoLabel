"""统一读取工具栏精灵图和独立图标，并缓存缩放结果。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPen, QPixmap

ROOT_DIR = Path(__file__).resolve().parent.parent
_ICON_CACHE: dict[tuple[str, bool, int], QIcon] = {}
_ASSET_ICON_CACHE: dict[tuple[str, int, float], QIcon] = {}


def sprite_icon(name: str, nav=False, size=24) -> QIcon:
    """从固定网格中裁出图标，去除白底并按设备像素比缓存。"""
    key = (name, nav, size)
    if key in _ICON_CACHE:
        return _ICON_CACHE[key]
    path = ROOT_DIR / "assets" / ("nav-icons-v1.png" if nav else "ui-icons-v3.png")
    positions = (
        {"auto": (0, 0), "review": (1, 0), "approved": (0, 1), "export": (1, 1)}
        if nav
        else {
            "back": (0, 0),
            "forward": (1, 0),
            "pan": (2, 0),
            "select": (3, 0),
            "box": (0, 1),
            "undo": (1, 1),
            "redo": (2, 1),
            "delete": (3, 1),
        }
    )
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
            # 精灵图带白底；透明化背景，只保留线条。
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
    # 按较高设备像素比渲染，避免 Windows 缩放后线条模糊。
    scale = 3
    pixmap = QPixmap.fromImage(cell).scaled(size * scale, size * scale, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    pixmap.setDevicePixelRatio(scale)
    result = QIcon(pixmap)
    _ICON_CACHE[key] = result
    return result


def workflow_nav_icon(size=22) -> QIcon:
    """绘制工作流导航使用的中性节点连线图标。"""
    scale = 3
    pixmap = QPixmap(size * scale, size * scale)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(QColor("#596270"), 2.0 * scale, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    painter.drawLine(6 * scale, 7 * scale, 11 * scale, 11 * scale)
    painter.drawLine(11 * scale, 11 * scale, 17 * scale, 6 * scale)
    painter.drawLine(11 * scale, 11 * scale, 17 * scale, 17 * scale)
    painter.setBrush(QColor("#FFFFFF"))
    for x, y in ((5, 6), (11, 11), (17, 6), (17, 17)):
        painter.drawEllipse(QPointF(x * scale, y * scale), 2.3 * scale, 2.3 * scale)
    painter.end()
    pixmap.setDevicePixelRatio(scale)
    return QIcon(pixmap)


def asset_icon(filename: str, size=24, glyph_scale=1.0) -> QIcon:
    """裁掉独立图标的透明边缘并统一视觉尺寸。"""
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
    # 裁掉独立资源的透明边距，再留少量统一内边距，与 24px 工具图标对齐。
    padding = max(8, min(image.width(), image.height()) // 42)
    left, top = max(0, left - padding), max(0, top - padding)
    right, bottom = min(image.width(), right + padding + 1), min(image.height(), bottom + padding + 1)
    scale = 3
    glyph_size = max(1, round(size * scale * max(0.1, min(1.0, glyph_scale))))
    glyph = QPixmap.fromImage(image.copy(left, top, right - left, bottom - top)).scaled(
        glyph_size, glyph_size, Qt.KeepAspectRatio, Qt.SmoothTransformation
    )
    pixmap = QPixmap(size * scale, size * scale)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.drawPixmap((pixmap.width() - glyph.width()) // 2, (pixmap.height() - glyph.height()) // 2, glyph)
    painter.end()
    pixmap.setDevicePixelRatio(scale)
    icon = QIcon(pixmap)
    _ASSET_ICON_CACHE[key] = icon
    return icon
