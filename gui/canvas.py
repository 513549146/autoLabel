"""审核画布：负责图片坐标、标注绘制、交互和本次编辑的撤销记录。"""

from __future__ import annotations

from .ui import *


class ImageCanvas(QWidget):
    """图片和标注的交互控件；页面通过 Qt 信号接收变更并负责持久化。"""

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
        self._drag_canvas_start = None
        self._pan_click_index = -1
        self._pan_dragged = False
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
            if shape.get("display_name"):
                item["display_name"] = str(shape["display_name"])
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
        """区分模型原始预测与人工创建、修改或核验的标注。"""
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
        """人工改动后移除旧模型置信度，并记录人工处理状态。"""
        shape.pop("score", None)
        if shape.get("origin") != "manual":
            shape["origin"] = "revised"

    @classmethod
    def color_for_label(cls, name: str) -> QColor:
        """按类别名称固定颜色，避免清单顺序改变后颜色跳变。"""
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
        self._display_cache = QPixmap()
        self._display_cache_key = None
        self.zoom = 1.0
        self.pan = QPointF()
        self.selected_index = -1
        self.hidden_indices.clear()
        self._history.clear()
        self._redo.clear()
        self.update()

    def prefetch(self, paths):
        """空闲时预解码相邻图片，减少审核翻页等待。"""
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
        self._polygon_draft = []
        self._polygon_hover = None
        self._drag_shape = None
        self._drag_index = -1
        self._drag_mode = self._resize_handle = None
        self._drag_canvas_start = None
        self._pan_click_index = -1
        self._pan_dragged = False
        self.setCursor(
            Qt.OpenHandCursor if name == "pan" else Qt.CrossCursor if name in ("box", "polygon") else Qt.ArrowCursor
        )
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
        target = QRectF(
            round(bounds.center().x() - width / 2 + self.pan.x()),
            round(bounds.center().y() - height / 2 + self.pan.y()),
            max(1, round(width)),
            max(1, round(height)),
        )
        return target, target.width() / source.width()

    def _to_image(self, point: QPointF):
        target, scale = self._geometry()
        if target.isNull() or not target.contains(point):
            return None
        return QPointF((point.x() - target.x()) / scale, (point.y() - target.y()) / scale)

    def _to_image_clamped(self, point: QPointF):
        """拖动控制点越过图片边缘时，仍允许把框拉回可见区域。"""
        target, scale = self._geometry()
        if target.isNull():
            return None
        return QPointF(
            max(0.0, min(self._pixmap.width(), (point.x() - target.x()) / scale)),
            max(0.0, min(self._pixmap.height(), (point.y() - target.y()) / scale)),
        )

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
        """返回图片坐标系中按左、上、右、下排列的框。"""
        x1, y1, x2, y2 = self.shapes[index]["bbox"]
        return min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)

    def _polygon_points(self, index: int):
        if not 0 <= index < len(self.shapes):
            return []
        return [
            QPointF(float(point[0]), float(point[1]))
            for point in self.shapes[index].get("points", [])
            if len(point) >= 2
        ]

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
            "nw": rect.topLeft(),
            "n": QPointF(rect.center().x(), rect.top()),
            "ne": rect.topRight(),
            "e": QPointF(rect.right(), rect.center().y()),
            "se": rect.bottomRight(),
            "s": QPointF(rect.center().x(), rect.bottom()),
            "sw": rect.bottomLeft(),
            "w": QPointF(rect.left(), rect.center().y()),
        }

    def _selected_canvas_rect(self):
        if not 0 <= self.selected_index < len(self.shapes):
            return QRectF()
        target, scale = self._geometry()
        x1, y1, x2, y2 = self._shape_rect(self.selected_index)
        return QRectF(target.x() + x1 * scale, target.y() + y1 * scale, (x2 - x1) * scale, (y2 - y1) * scale)

    def _visible_handle_positions(self, rect: QRectF):
        """Project off-image handles onto the visible image, without changing the box."""
        target, _ = self._geometry()
        visible = target.intersected(QRectF(self.rect().adjusted(0, 0, -1, -1)))
        if visible.isEmpty():
            return {}
        inset = min(7.0, visible.width() / 2, visible.height() / 2)
        left, right = visible.left() + inset, visible.right() - inset
        top, bottom = visible.top() + inset, visible.bottom() - inset
        return {
            handle: QPointF(max(left, min(right, center.x())), max(top, min(bottom, center.y())))
            for handle, center in self._handle_positions(rect).items()
        }

    def _handle_at(self, point: QPointF):
        if 0 <= self.selected_index < len(self.shapes) and self.shapes[self.selected_index].get("type") == "polygon":
            image_point = self._to_image(point)
            return (
                f"vertex:{self._polygon_vertex_at(image_point)}"
                if image_point and self._polygon_vertex_at(image_point) >= 0
                else None
            )
        rect = self._selected_canvas_rect()
        if rect.isNull():
            return None
        radius = 8
        for handle, center in self._visible_handle_positions(rect).items():
            if abs(point.x() - center.x()) <= radius and abs(point.y() - center.y()) <= radius:
                return handle
        return None

    def _set_select_cursor(self, point: QPointF):
        handle = self._handle_at(point)
        if handle and handle.startswith("vertex:"):
            self.setCursor(Qt.CrossCursor)
            return
        cursors = {
            "nw": Qt.SizeFDiagCursor,
            "se": Qt.SizeFDiagCursor,
            "ne": Qt.SizeBDiagCursor,
            "sw": Qt.SizeBDiagCursor,
            "n": Qt.SizeVerCursor,
            "s": Qt.SizeVerCursor,
            "e": Qt.SizeHorCursor,
            "w": Qt.SizeHorCursor,
        }
        self.setCursor(cursors.get(handle, Qt.ArrowCursor))

    def _set_hover_cursor(self, point: QPointF):
        if self.tool == "polygon" and self._polygon_draft:
            self.setCursor(Qt.CrossCursor)
        elif self._handle_at(point):
            self._set_select_cursor(point)
        else:
            image_point = self._to_image(point)
            index = self._shape_index_at(image_point) if image_point else -1
            if index == self.selected_index and index >= 0:
                self.setCursor(Qt.ArrowCursor)
            elif index >= 0 or self.tool == "pan":
                self.setCursor(Qt.OpenHandCursor)
            elif self.tool == "select":
                self.setCursor(Qt.ArrowCursor)
            else:
                self.setCursor(Qt.CrossCursor)

    def _begin_pan_drag(self, point: QPointF, click_index: int):
        self._drag_point = point
        self._drag_origin = QPointF(self.pan)
        self._pan_click_index = click_index
        self._pan_dragged = False
        self.setCursor(Qt.ClosedHandCursor)

    def _begin_shape_drag(self, index: int, image_point: QPointF, canvas_point: QPointF, handle=None):
        self._drag_point = image_point
        self._drag_origin = None
        self._drag_canvas_start = canvas_point
        self._drag_index = index
        self._drag_shape = self._copy_shapes([self.shapes[index]])[0]
        self._drag_mode = "vertex" if handle and handle.startswith("vertex:") else "resize" if handle else "move"
        self._resize_handle = handle

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
        width, height = self._pixmap.width(), self._pixmap.height()
        if width > 0 and height > 0:
            visible_left, visible_right = max(0, x1), min(width, x2)
            visible_top, visible_bottom = max(0, y1), min(height, y2)
            center_x = (
                (visible_left + visible_right) / 2
                if visible_left <= visible_right
                else max(0, min(width, (x1 + x2) / 2))
            )
            center_y = (
                (visible_top + visible_bottom) / 2
                if visible_top <= visible_bottom
                else max(0, min(height, (y1 + y2) / 2))
            )
        else:
            center_x, center_y = (x1 + x2) / 2, (y1 + y2) / 2
        current = self._to_canvas(QPointF(center_x, center_y))
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
        self.shapes.append(
            {
                "name": self.default_label,
                "origin": "manual",
                "type": "polygon",
                "points": points,
                "bbox": self._bbox_for_points(points),
            }
        )
        self._polygon_draft = []
        self._polygon_hover = None
        self._set_selected(len(self.shapes) - 1)
        self._commit(before)
        self.polygon_created.emit(self.selected_index)
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
        self.shapes = self._history.pop()
        self._set_selected(-1)
        self.shapes_changed.emit(self._copy_shapes(self.shapes))
        self.update()
        return True

    def redo(self):
        if not self._redo:
            return False
        self._history.append(self._copy_shapes(self.shapes))
        self.shapes = self._redo.pop()
        self._set_selected(-1)
        self.shapes_changed.emit(self._copy_shapes(self.shapes))
        self.update()
        return True

    def apply_category(self, name: str, display_name: str | None = None):
        if not 0 <= self.selected_index < len(self.shapes) or not name:
            return
        before = self._copy_shapes(self.shapes)
        self.shapes[self.selected_index]["name"] = name
        if display_name:
            self.shapes[self.selected_index]["display_name"] = display_name
        else:
            self.shapes[self.selected_index].pop("display_name", None)
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
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, False)
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
            self._display_cache = self._pixmap.scaled(
                int(target.width()), int(target.height()), Qt.IgnoreAspectRatio, Qt.SmoothTransformation
            )
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
                    selected_fill = QColor(color)
                    selected_fill.setAlpha(28)
                    painter.fillPath(polygon, selected_fill)
                painter.drawPath(polygon)
                rect = polygon.boundingRect()
                if index == self.selected_index:
                    painter.setBrush(Qt.white)
                    painter.setPen(QPen(color, 2))
                    for polygon_point in canvas_points:
                        painter.drawEllipse(polygon_point, 4, 4)
            else:
                x1, y1, x2, y2 = shape.get("bbox", (0, 0, 0, 0))
                rect = QRectF(
                    target.x() + x1 * scale, target.y() + y1 * scale, (x2 - x1) * scale, (y2 - y1) * scale
                ).normalized()
                if index == self.selected_index:
                    # Keep the source visible while restoring the gentle, category-
                    # coloured selection wash used by the workbench design.
                    selected_fill = QColor(color)
                    selected_fill.setAlpha(28)
                    painter.fillRect(rect, selected_fill)
                painter.drawRect(rect)
            caption = shape.get("display_name") or shape.get("name", "object")
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
                painter.setBrush(Qt.white)
                painter.setPen(QPen(color, 2))
                for point in self._visible_handle_positions(rect).values():
                    painter.drawEllipse(point, 4, 4)
        if self._draft_start and self._draft_end:
            start, end = self._to_canvas(self._draft_start), self._to_canvas(self._draft_end)
            # A selected prior box draws white resize handles. Reset the brush
            # before the next drag so that state cannot fill its draft rectangle.
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor("#0A74FF"), 2, Qt.DashLine))
            painter.drawRect(QRectF(start, end).normalized())
        if self._polygon_draft:
            draft = [self._to_canvas(QPointF(point[0], point[1])) for point in self._polygon_draft]
            if self._polygon_hover:
                draft.append(self._to_canvas(self._polygon_hover))
            painter.setPen(QPen(QColor("#0A74FF"), 2, Qt.DashLine))
            if draft:
                painter.drawPolyline(QPolygonF(draft))
                painter.setBrush(QColor("#FFFFFF"))
                painter.setPen(QPen(QColor("#0A74FF"), 2))
                for polygon_point in draft[:-1] if self._polygon_hover else draft:
                    painter.drawEllipse(polygon_point, 4, 4)

    def mousePressEvent(self, event):
        """按下时确定本次手势：已选标注可编辑，未选标注先用于平移或点击选中。"""
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
        if self.tool == "polygon" and image_point and self._polygon_draft:
            if len(self._polygon_draft) >= 3:
                first = QPointF(self._polygon_draft[0][0], self._polygon_draft[0][1])
                _, scale = self._geometry()
                close_radius = max(6, 10 / max(scale, 0.01))
                if (
                    abs(image_point.x() - first.x()) <= close_radius
                    and abs(image_point.y() - first.y()) <= close_radius
                ):
                    self._finish_polygon_draft()
                    event.accept()
                    return
            self._polygon_draft.append([int(round(image_point.x())), int(round(image_point.y()))])
            self._polygon_hover = image_point
        else:
            # Ctrl 可在已有标注上强制开始绘制；其他情况优先检查已选标注的控制点。
            force_draw = self.tool in ("box", "polygon") and bool(event.modifiers() & Qt.ControlModifier)
            handle = None if force_draw else self._handle_at(point)
            if handle and image_point and self.selected_index >= 0:
                self._begin_shape_drag(self.selected_index, image_point, point, handle)
                self._set_hover_cursor(point)
            else:
                index = self._shape_index_at(image_point) if image_point and not force_draw else -1
                if index >= 0 and image_point:
                    if index == self.selected_index:
                        self._begin_shape_drag(index, image_point, point)
                        self._set_hover_cursor(point)
                    else:
                        # 未选中的框按下后先进入平移：松开且没有拖动才将它选中。
                        self._begin_pan_drag(point, index)
                elif self.tool == "pan":
                    self._begin_pan_drag(point, -1)
                elif self.tool == "box" and image_point:
                    self._draft_start = self._draft_end = image_point
                elif self.tool == "polygon" and image_point:
                    self._polygon_draft.append([int(round(image_point.x())), int(round(image_point.y()))])
                    self._polygon_hover = image_point
                elif self.tool == "select":
                    self._set_selected(-1)
        event.accept()

    def mouseMoveEvent(self, event):
        point = event.position()
        if self._drag_point is not None and self._drag_shape is None and self._drag_origin is not None:
            if self._pan_dragged or (point - self._drag_point).manhattanLength() >= 3:
                self._pan_dragged = True
                self.pan = self._drag_origin + (point - self._drag_point)
                self.update()
        elif self._drag_point is not None and self._drag_shape is not None and self._drag_index >= 0:
            if self._drag_canvas_start is not None and (point - self._drag_canvas_start).manhattanLength() < 3:
                return
            current = (
                self._to_image_clamped(point) if self._drag_mode in ("resize", "vertex") else self._to_image(point)
            )
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
                    if "w" in handle:
                        left = min(current.x(), right - 3)
                    if "e" in handle:
                        right = max(current.x(), left + 3)
                    if "n" in handle:
                        top = min(current.y(), bottom - 3)
                    if "s" in handle:
                        bottom = max(current.y(), top + 3)
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
        elif self.tool == "box" and self._draft_start:
            self._draft_end = self._to_image(point) or self._draft_end
            self.update()
        elif self.tool == "polygon" and self._polygon_draft:
            self._polygon_hover = self._to_image(point)
            self.update()
        else:
            self._set_hover_cursor(point)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        """结束手势；仅真正修改几何或新增标注时提交撤销记录。"""
        if event.button() != Qt.LeftButton:
            return super().mouseReleaseEvent(event)
        if self._drag_shape is not None and self._drag_index >= 0:
            before = self._copy_shapes(self.shapes)
            before[self._drag_index] = self._drag_shape
            self._drag_point = self._drag_shape = self._drag_canvas_start = None
            self._drag_index = -1
            self._drag_mode = self._resize_handle = None
            self._commit(before)
            self._set_hover_cursor(event.position())
        elif self._drag_point is not None and self._drag_origin is not None:
            delta = event.position() - self._drag_point
            if not self._pan_dragged and delta.manhattanLength() >= 3:
                self._pan_dragged = True
                self.pan = self._drag_origin + delta
                self.update()
            if not self._pan_dragged:
                # 点击框选中，长按拖动仍然只移动画布。
                self._set_selected(self._pan_click_index)
            self._drag_point = self._drag_origin = None
            self._pan_click_index = -1
            self._pan_dragged = False
            self._set_hover_cursor(event.position())
        elif self.tool == "box" and self._draft_start and self._draft_end:
            x1, y1 = self._draft_start.x(), self._draft_start.y()
            x2, y2 = self._draft_end.x(), self._draft_end.y()
            self._draft_start = self._draft_end = None
            if abs(x2 - x1) >= 3 and abs(y2 - y1) >= 3:
                before = self._copy_shapes(self.shapes)
                self.shapes.append(
                    {
                        "name": self.default_label,
                        "origin": "manual",
                        "bbox": [int(min(x1, x2)), int(min(y1, y2)), int(max(x1, x2)), int(max(y1, y2))],
                    }
                )
                self._set_selected(len(self.shapes) - 1)
                self._commit(before)
                self.box_created.emit(self.selected_index)
            self.update()
            self._set_hover_cursor(event.position())
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
        self.update()
        event.accept()

    def mouseDoubleClickEvent(self, event):
        if self.tool == "polygon" and event.button() == Qt.LeftButton and self._polygon_draft:
            self._finish_polygon_draft()
            event.accept()
            return
        if event.button() == Qt.LeftButton:
            image_point = self._to_image(event.position())
            index = self._shape_index_at(image_point) if image_point else -1
            if index >= 0:
                self._set_selected(index)
                event.accept()
                return
            self.zoom = 1.0
            self.pan = QPointF()
            self.update()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)
