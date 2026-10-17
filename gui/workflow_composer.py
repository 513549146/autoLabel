"""Zoomable, pannable workflow canvas with editable constrained connections."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, QRectF, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (
    QFrame, QGraphicsDropShadowEffect, QGraphicsItem, QGraphicsObject,
    QGraphicsScene, QGraphicsView, QHBoxLayout, QLabel, QToolButton, QVBoxLayout, QWidget,
)

from application.workflow_templates import QUICK_WORKFLOW, WorkflowGraph, WorkflowTemplate, WorkflowTemplateRegistry


class _WorkflowNode(QGraphicsObject):
    """A large operation card; dragging the output dot starts a connection."""

    selected_step = Signal(str)
    moved = Signal(str)
    released = Signal(str)
    connection_preview = Signal(str, QPointF)
    connection_finished = Signal(str, QPointF)

    WIDTH = 208
    HEIGHT = 100

    def __init__(self, step):
        super().__init__()
        self.step = step
        self._connecting = False
        self.setFlags(
            QGraphicsItem.ItemIsMovable
            | QGraphicsItem.ItemIsSelectable
            | QGraphicsItem.ItemSendsGeometryChanges
        )
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.OpenHandCursor)
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(15)
        shadow.setOffset(0, 3)
        shadow.setColor(QColor(31, 49, 72, 22))
        self.setGraphicsEffect(shadow)

    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, self.WIDTH, self.HEIGHT)

    def paint(self, painter, option, widget=None):
        del option, widget
        painter.setRenderHint(QPainter.Antialiasing)
        selected = self.isSelected()
        optional = self.step.optional
        border = QColor("#0A74FF" if selected else "#E1E7EF")
        fill = QColor("#FFFFFF")
        painter.setPen(QPen(border, 2 if selected else 1))
        painter.setBrush(fill)
        painter.drawRoundedRect(QRectF(1, 1, self.WIDTH - 2, self.HEIGHT - 2), 15, 15)

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#D79A33" if optional else "#0A74FF"))
        painter.drawRoundedRect(QRectF(1, 18, 4, 64), 2, 2)

        badge_rect = QRectF(self.WIDTH - 64, 15, 47, 21)
        painter.setBrush(QColor("#FFF4DD" if optional else "#EAF3FF"))
        painter.drawRoundedRect(badge_rect, 9, 9)
        badge_font = QFont("Microsoft YaHei UI")
        badge_font.setPointSizeF(8)
        badge_font.setWeight(QFont.DemiBold)
        painter.setFont(badge_font)
        painter.setPen(QColor("#95611A" if optional else "#2765AA"))
        painter.drawText(badge_rect, Qt.AlignCenter, "可选" if optional else "必经")

        title_font = QFont("Microsoft YaHei UI")
        title_font.setPointSizeF(12)
        title_font.setWeight(QFont.DemiBold)
        painter.setFont(title_font)
        painter.setPen(QColor("#202A36"))
        painter.drawText(QRectF(18, 14, self.WIDTH - 89, 27),
                         Qt.AlignLeft | Qt.AlignVCenter, self.step.title)

        detail_font = QFont("Microsoft YaHei UI")
        detail_font.setPointSizeF(9.5)
        painter.setFont(detail_font)
        painter.setPen(QColor("#697586"))
        painter.drawText(QRectF(18, 46, self.WIDTH - 36, 41),
                         Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap, self.step.detail)

        painter.setPen(QPen(QColor("#C7D5E5"), 1.5))
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawEllipse(QPointF(8, self.HEIGHT / 2), 4, 4)
        painter.setPen(QPen(QColor("#FFFFFF"), 1.5))
        painter.setBrush(QColor("#0A74FF"))
        painter.drawEllipse(QPointF(self.WIDTH - 8, self.HEIGHT / 2), 6, 6)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            self.moved.emit(self.step.id)
        return super().itemChange(change, value)

    def mousePressEvent(self, event):
        point = event.pos()
        self._connecting = (
            event.button() == Qt.LeftButton
            and point.x() >= self.WIDTH - 25
            and abs(point.y() - self.HEIGHT / 2) <= 21
        )
        if self._connecting:
            self.setCursor(Qt.CrossCursor)
            event.accept()
            return
        self.setCursor(Qt.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._connecting:
            self.connection_preview.emit(self.step.id, event.scenePos())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._connecting:
            self._connecting = False
            self.connection_finished.emit(self.step.id, event.scenePos())
            event.accept()
        else:
            super().mouseReleaseEvent(event)
            self.selected_step.emit(self.step.id)
            self.released.emit(self.step.id)
        self.setCursor(Qt.OpenHandCursor)


class _WorkflowScene(QGraphicsScene):
    """Draw a quiet dot grid and dependency arrows beneath operation cards."""

    def __init__(self, board):
        super().__init__(board)
        self.board = board

    def drawBackground(self, painter, rect):
        painter.fillRect(rect, QColor("#FBFCFE"))
        painter.setPen(QPen(QColor("#E9EEF4"), 1))
        spacing = 24
        start_x = int(rect.left() // spacing) * spacing
        start_y = int(rect.top() // spacing) * spacing
        for x in range(start_x, int(rect.right()) + spacing, spacing):
            for y in range(start_y, int(rect.bottom()) + spacing, spacing):
                painter.drawPoint(x, y)

        for source, target in self.board.graph.edges:
            first = self.board.nodes.get(source)
            second = self.board.nodes.get(target)
            if not first or not second:
                continue
            path, end, arrow = self._edge_path(source, target, first, second)
            painter.setPen(QPen(QColor("#AABFD5"), 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(path)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("#AABFD5"))
            painter.drawPolygon(QPolygonF(arrow))
        if self.board._preview:
            source, end = self.board._preview
            first = self.board.nodes.get(source)
            if first:
                start = first.pos() + QPointF(first.WIDTH - 8, first.HEIGHT / 2)
                painter.setPen(QPen(QColor("#0A74FF"), 2, Qt.DashLine))
                painter.drawLine(start, end)

    def _edge_path(self, source, target, first, second):
        delta = second.pos() - first.pos()
        path = QPainterPath()
        if source == "primary_model" and target == "candidate_fusion" and "recovery_model" in self.board.nodes:
            start = first.pos() + QPointF(first.WIDTH / 2, first.HEIGHT)
            end = second.pos() + QPointF(second.WIDTH / 2, second.HEIGHT)
            below = max(start.y(), end.y()) + 93
            path.moveTo(start)
            path.cubicTo(QPointF(start.x(), below), QPointF(end.x(), below), end)
            arrow = [end, end + QPointF(-6, 9), end + QPointF(6, 9)]
        elif abs(delta.x()) >= abs(delta.y()):
            rightward = delta.x() >= 0
            start = first.pos() + QPointF(first.WIDTH if rightward else 0, first.HEIGHT / 2)
            end = second.pos() + QPointF(0 if rightward else second.WIDTH, second.HEIGHT / 2)
            bend = max(45.0, abs(end.x() - start.x()) * 0.43)
            direction = 1 if rightward else -1
            path.moveTo(start)
            path.cubicTo(start + QPointF(bend * direction, 0),
                         end - QPointF(bend * direction, 0), end)
            arrow = [end, end + QPointF(-8 * direction, -5), end + QPointF(-8 * direction, 5)]
        else:
            downward = delta.y() >= 0
            start = first.pos() + QPointF(first.WIDTH / 2, first.HEIGHT if downward else 0)
            end = second.pos() + QPointF(second.WIDTH / 2, 0 if downward else second.HEIGHT)
            bend = max(45.0, abs(end.y() - start.y()) * 0.43)
            direction = 1 if downward else -1
            path.moveTo(start)
            path.cubicTo(start + QPointF(0, bend * direction),
                         end - QPointF(0, bend * direction), end)
            arrow = [end, end + QPointF(-5, -8 * direction), end + QPointF(5, -8 * direction)]
        return path, end, arrow


class _WorkflowBoard(QGraphicsView):
    layout_changed = Signal(dict)
    step_selected = Signal(str)
    graph_changed = Signal(object)
    connection_rejected = Signal(str)
    zoom_changed = Signal(int)

    _default_positions = (
        (44, 55), (310, 55), (576, 55),
        (576, 235), (310, 235), (44, 235),
        (44, 415), (310, 415), (576, 415),
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.template: WorkflowTemplate = QUICK_WORKFLOW
        self.graph = WorkflowGraph.preset(QUICK_WORKFLOW)
        self.nodes: dict[str, _WorkflowNode] = {}
        self.positions: dict[str, tuple[int, int]] = {}
        self._preview: tuple[str, QPointF] | None = None
        self._zoom = 1.0
        self._auto_fit = True
        self._middle_pan: QPoint | None = None
        self._scene = _WorkflowScene(self)
        self.setScene(self._scene)
        self.setMinimumHeight(340)
        self.setFrameShape(QFrame.NoFrame)
        self.setStyleSheet("QGraphicsView { background:#FBFCFE; border:1px solid #E4E9F0; border-radius:12px; }")
        self.setRenderHint(QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

    def set_template(self, template: WorkflowTemplate,
                     positions: dict[str, tuple[int, int]] | None = None,
                     graph: WorkflowGraph | None = None):
        self.template = template
        self.graph = graph or WorkflowGraph.preset(template)
        self._preview = None
        self._scene.clear()
        self.nodes = {}
        self.positions = dict(positions or {})
        # Old 124x58 card coordinates overlap with the new 208x100 cards.
        legacy_layout = (
            len(self.positions) > 1
            and max((point[1] for point in self.positions.values()), default=0) < 210
        )
        if legacy_layout:
            self.positions.clear()
        for index, step in enumerate(template.steps):
            if step.id not in self.graph.nodes:
                continue
            node = _WorkflowNode(step)
            node.selected_step.connect(self.step_selected.emit)
            node.moved.connect(self._node_moved)
            node.released.connect(self._node_released)
            node.connection_preview.connect(self._preview_connection)
            node.connection_finished.connect(self._finish_connection)
            x, y = self.positions.get(step.id, self._default_positions[index])
            self._scene.addItem(node)
            node.setPos(x, y)
            self.nodes[step.id] = node
            self.positions[step.id] = (x, y)
        self._auto_fit = True
        self._scene.update()
        QTimer.singleShot(0, self.fit_graph)

    def set_graph(self, graph: WorkflowGraph):
        self.set_template(self.template, self.positions, graph)
        self.graph_changed.emit(graph)

    def add_edge(self, source: str, target: str) -> tuple[str, ...]:
        edge = (source, target)
        if edge in self.graph.edges:
            return ("该连线已经存在",)
        candidate = WorkflowGraph(self.graph.template_id, self.graph.nodes, (*self.graph.edges, edge))
        errors = candidate.validate(self.template)
        if not errors:
            self.graph = candidate
            self.graph_changed.emit(candidate)
            self._scene.update()
        return errors

    def remove_edge(self, source: str, target: str) -> tuple[str, ...]:
        candidate = WorkflowGraph(
            self.graph.template_id, self.graph.nodes,
            tuple(edge for edge in self.graph.edges if edge != (source, target)),
        )
        errors = candidate.validate(self.template)
        if not errors:
            self.graph = candidate
            self.graph_changed.emit(candidate)
            self._scene.update()
        return errors

    def _node_moved(self, step_id: str):
        node = self.nodes.get(step_id)
        if node:
            self.positions[step_id] = (round(node.pos().x()), round(node.pos().y()))
        self._scene.update()

    def _node_released(self, step_id: str):
        self._node_moved(step_id)
        self.layout_changed.emit(dict(self.positions))

    def _preview_connection(self, source: str, point: QPointF):
        self._preview = (source, point)
        self._scene.update()

    def _finish_connection(self, source: str, point: QPointF):
        self._preview = None
        target = next(
            (item.step.id for item in self._scene.items(point)
             if isinstance(item, _WorkflowNode) and item.step.id != source),
            None,
        )
        if target:
            errors = self.add_edge(source, target)
            if errors:
                self.connection_rejected.emit("；".join(errors))
        self._scene.update()

    def fit_graph(self):
        if not self.nodes or self.viewport().width() < 30:
            return
        bounds = self._scene.itemsBoundingRect().adjusted(-54, -64, 54, 64)
        self._scene.setSceneRect(bounds.adjusted(-300, -240, 300, 240))
        self.resetTransform()
        self.fitInView(bounds, Qt.KeepAspectRatio)
        self._zoom = self.transform().m11()
        self._auto_fit = True
        self.zoom_changed.emit(round(self._zoom * 100))

    def zoom_by(self, factor: float):
        desired = max(0.55, min(2.5, self._zoom * factor))
        self.scale(desired / self._zoom, desired / self._zoom)
        self._zoom = desired
        self._auto_fit = False
        self.zoom_changed.emit(round(self._zoom * 100))

    def zoom_in(self):
        self.zoom_by(1.18)

    def zoom_out(self):
        self.zoom_by(1 / 1.18)

    def wheelEvent(self, event):
        self.zoom_by(1.12 if event.angleDelta().y() > 0 else 1 / 1.12)
        event.accept()

    def mousePressEvent(self, event):
        if event.button() == Qt.MiddleButton:
            self._middle_pan = event.position().toPoint()
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._middle_pan is not None:
            point = event.position().toPoint()
            delta = point - self._middle_pan
            self._middle_pan = point
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MiddleButton and self._middle_pan is not None:
            self._middle_pan = None
            self.unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._auto_fit:
            QTimer.singleShot(0, self.fit_graph)


class WorkflowComposer(QWidget):
    """Select a preset and edit its operation graph in a scalable canvas."""

    template_selected = Signal(str)
    layout_changed = Signal(str, dict)
    step_selected = Signal(str)
    graph_changed = Signal(str, object)
    connection_rejected = Signal(str)
    zoom_changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.registry = WorkflowTemplateRegistry()
        self.active_template_id = QUICK_WORKFLOW.id
        self.layouts: dict[str, dict[str, tuple[int, int]]] = {}
        self.graphs: dict[str, WorkflowGraph] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(9)
        self.chooser_host = QWidget()
        chooser = QHBoxLayout(self.chooser_host)
        chooser.setContentsMargins(0, 0, 0, 0)
        chooser.setSpacing(8)
        self.buttons: dict[str, QToolButton] = {}
        for template in self.registry.templates():
            item = QToolButton()
            item.setCheckable(True)
            item.setText(template.title)
            item.setToolTip(template.subtitle)
            item.setMinimumHeight(30)
            item.clicked.connect(lambda checked=False, template_id=template.id:
                                 self.select_template(template_id))
            self.buttons[template.id] = item
            chooser.addWidget(item)
        chooser.addStretch()
        self.hint = QLabel()
        self.hint.setStyleSheet("color:#737780;font-size:11px;")
        chooser.addWidget(self.hint)
        layout.addWidget(self.chooser_host)
        self.board = _WorkflowBoard()
        self.board.layout_changed.connect(self._on_layout_changed)
        self.board.step_selected.connect(self._show_step)
        self.board.graph_changed.connect(self._on_graph_changed)
        self.board.connection_rejected.connect(self.connection_rejected.emit)
        self.board.zoom_changed.connect(self.zoom_changed.emit)
        layout.addWidget(self.board, 1)
        self.detail = QLabel("拖动卡片调整位置；从蓝色端口拖出连线。滚轮缩放，拖动空白处或按住鼠标中键平移。")
        self.detail.setStyleSheet("color:#737780;font-size:11px;")
        self.detail.setWordWrap(True)
        layout.addWidget(self.detail)
        self.select_template(QUICK_WORKFLOW.id, emit=False)

    def set_state(self, template_id: str, layouts: dict[str, dict[str, tuple[int, int]]],
                  graphs: dict[str, WorkflowGraph] | None = None):
        self.layouts = {key: dict(value) for key, value in layouts.items()}
        self.graphs = dict(graphs or {})
        self.select_template(template_id, emit=False)

    def set_preset_chooser_visible(self, visible: bool):
        self.chooser_host.setVisible(visible)

    def select_template(self, template_id: str, emit=True):
        template = self.registry.get(template_id)
        self.active_template_id = template.id
        for key, item in self.buttons.items():
            selected = key == template.id
            item.blockSignals(True)
            item.setChecked(selected)
            item.blockSignals(False)
            item.setStyleSheet(
                "QToolButton { background:#EAF3FF; border:1px solid #9EC7FF; border-radius:8px; color:#0A74FF; font-weight:700; padding:5px 12px; }"
                if selected else
                "QToolButton { background:#FFFFFF; border:1px solid #DDE2E8; border-radius:8px; color:#4E5560; padding:5px 12px; }"
            )
        self.hint.setText("可执行" if template.executable else "预设接口 · 尚未接入执行器")
        self.board.set_template(
            template, self.layouts.get(template.id, {}),
            self.graphs.get(template.id, WorkflowGraph.preset(template)),
        )
        if emit:
            self.template_selected.emit(template.id)

    def _on_layout_changed(self, positions):
        self.layouts[self.active_template_id] = positions
        self.layout_changed.emit(self.active_template_id, self.layouts)

    def _on_graph_changed(self, graph: WorkflowGraph):
        self.graphs[self.active_template_id] = graph
        self.graph_changed.emit(self.active_template_id, graph)

    def set_optional_enabled(self, step_id: str, enabled: bool):
        template = self.registry.get(self.active_template_id)
        graph = self.board.graph
        updated = graph.with_optional(template, step_id) if enabled else graph.without_optional(template, step_id)
        self.board.set_graph(updated)

    def add_edge(self, source: str, target: str) -> tuple[str, ...]:
        return self.board.add_edge(source, target)

    def remove_edge(self, source: str, target: str) -> tuple[str, ...]:
        return self.board.remove_edge(source, target)

    def _show_step(self, step_id: str):
        step = next(
            (item for item in self.registry.get(self.active_template_id).steps if item.id == step_id),
            None,
        )
        if step:
            suffix = "（可选）" if step.optional else ""
            self.detail.setText(f"{step.title}{suffix}：{step.detail}")
            self.step_selected.emit(step_id)
