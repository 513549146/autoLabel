"""审核页右侧覆盖式标注清单：搜索、显隐与定位。"""

from __future__ import annotations

from ..ui import *
from ..canvas import ImageCanvas


class _ElidedLabel(QLabel):
    """长文本按实际宽度省略，完整名称保存在条目悬浮提示中。"""

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self._full_text = text
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.setMinimumWidth(0)
        self.setToolTip(text)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.setText(self.fontMetrics().elidedText(self._full_text, Qt.ElideRight, self.contentsRect().width()))


class ReviewDrawerMixin:
    """由 ReviewPage 提供 canvas、canvas_card、类别筛选和清单状态。"""

    def _build_annotation_drawer(self):
        """清单覆盖在画布右侧，不改变检查器和画布的布局宽度。"""
        self.annotation_drawer = QFrame(self.canvas_card)
        self.annotation_drawer.setObjectName("annotationDrawer")
        self.annotation_drawer.setFixedWidth(360)
        self.annotation_drawer.setVisible(False)
        drawer = QVBoxLayout(self.annotation_drawer)
        drawer.setContentsMargins(16, 16, 16, 16)
        drawer.setSpacing(10)
        header = QHBoxLayout()
        header.addWidget(label("标注清单", "section"))
        self.drawer_count = label("0 个框", "muted")
        header.addWidget(self.drawer_count)
        header.addStretch()
        close = QToolButton()
        close.setText("×")
        close.setToolTip("关闭标注清单（L）")
        close.setFixedSize(28, 28)
        close.setCursor(Qt.PointingHandCursor)
        close.setStyleSheet("border:0;border-radius:8px;font-size:20px;color:#555B66;")
        close.clicked.connect(lambda: self.toggle_annotation_drawer(False))
        header.addWidget(close)
        drawer.addLayout(header)
        self.drawer_search = QLineEdit()
        self.drawer_search.setPlaceholderText("搜索类别或框编号")
        self.drawer_search.textChanged.connect(self._refresh_annotation_drawer)
        drawer.addWidget(self.drawer_search)
        self.drawer_category = FlatComboBox()
        self.drawer_category.currentIndexChanged.connect(self._refresh_annotation_drawer)
        drawer.addWidget(self.drawer_category)
        self.drawer_scroll = QScrollArea()
        self.drawer_scroll.setWidgetResizable(True)
        self.drawer_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.drawer_scroll.setStyleSheet("""
            QScrollArea, QScrollArea > QWidget > QWidget { border:0; background:transparent; }
            QScrollBar:vertical { background:transparent; width:10px; margin:5px 1px; border:0; }
            QScrollBar::handle:vertical { background:rgba(60, 60, 67, 72); min-height:34px; border-radius:4px; margin:1px 2px; }
            QScrollBar::handle:vertical:hover { background:rgba(60, 60, 67, 118); }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; background:transparent; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background:transparent; }
        """)
        self.drawer_rows = QWidget()
        self.drawer_rows.setStyleSheet("background:transparent;")
        self.drawer_rows_layout = QVBoxLayout(self.drawer_rows)
        self.drawer_rows_layout.setContentsMargins(0, 0, 0, 0)
        self.drawer_rows_layout.setSpacing(7)
        self.drawer_scroll.setWidget(self.drawer_rows)
        drawer.addWidget(self.drawer_scroll, 1)
        self.canvas_card.installEventFilter(self)

    def eventFilter(self, watched, event):
        if watched is getattr(self, "canvas_card", None) and event.type() == QEvent.Resize:
            self._position_annotation_drawer()
            self._position_save_toast()
        return super().eventFilter(watched, event)

    def _position_annotation_drawer(self):
        if not self.annotation_drawer.isVisible():
            return
        margin = 12
        width = min(360, max(260, self.canvas_card.width() - margin * 2))
        height = max(180, self.canvas_card.height() - margin * 2)
        self.annotation_drawer.setGeometry(self.canvas_card.width() - width - margin, margin, width, height)
        self.annotation_drawer.raise_()

    def _position_save_toast(self):
        if not self.save_toast.isVisible():
            return
        self.save_toast.adjustSize()
        self.save_toast.move(
            max(10, (self.canvas_card.width() - self.save_toast.width()) // 2),
            max(10, (self.canvas_card.height() - self.save_toast.height()) // 2),
        )
        self.save_toast.raise_()

    def _show_save_toast(self, message):
        self.save_toast.setText("✓ " + message)
        self.save_toast.adjustSize()
        self.save_toast.show()
        self._position_save_toast()
        QTimer.singleShot(1600, self.save_toast.hide)

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
        self.drawer_category.blockSignals(True)
        self.drawer_category.clear()
        self.drawer_category.addItem("全部类别", "")
        for name in categories:
            self.drawer_category.addItem(self._category_display_name(name), name)
        target = self.drawer_category.findData(current)
        self.drawer_category.setCurrentIndex(target if target >= 0 else 0)
        self.drawer_category.blockSignals(False)

    def _refresh_annotation_drawer(self):
        if not hasattr(self, "annotation_drawer"):
            return
        self._drawer_categories()
        query = self.drawer_search.text().strip().lower()
        category = self.drawer_category.currentData() or ""
        while self.drawer_rows_layout.count():
            entry = self.drawer_rows_layout.takeAt(0)
            widget = entry.widget()
            widget.deleteLater() if widget else None
        shown = 0
        for index, shape in enumerate(self.canvas.shapes):
            name = str(shape.get("name", "object"))
            display_name = str(shape.get("display_name") or self._category_display_name(name))
            if category and name != category:
                continue
            x1, y1, x2, y2 = self.canvas._shape_rect(index)
            width, height = int(x2 - x1), int(y2 - y1)
            searchable = f"{name} {display_name} {index + 1} {x1} {y1} {width} {height}".lower()
            if query and query not in searchable:
                continue
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(6)
            shape_type = "多边形" if shape.get("type") == "polygon" else "矩形"
            provenance = ImageCanvas.provenance_text(shape)
            item = QToolButton()
            item.setObjectName("annotationRow")
            item.setCheckable(True)
            item.setChecked(index == self.canvas.selected_index)
            item.setFixedHeight(58)
            item.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            item_layout = QVBoxLayout(item)
            item_layout.setContentsMargins(12, 5, 10, 5)
            item_layout.setSpacing(1)
            title_row = QHBoxLayout()
            title_row.setContentsMargins(0, 0, 0, 0)
            title_row.setSpacing(8)
            title = _ElidedLabel(display_name, item)
            title.setObjectName("annotationRowTitle")
            title.setStyleSheet("font-weight:600;color:#2E3138;")
            number = QLabel(f"#{index + 1} · {shape_type}", item)
            number.setObjectName("annotationRowNumber")
            number.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            number.setAttribute(Qt.WA_TransparentForMouseEvents)
            title_row.addWidget(title, 1)
            title_row.addWidget(number, 0)
            item_layout.addLayout(title_row)
            detail_row = QHBoxLayout()
            detail_row.setContentsMargins(0, 0, 0, 0)
            detail_row.setSpacing(8)
            source = _ElidedLabel(provenance, item)
            source.setObjectName("annotationRowSource")
            source.setStyleSheet("color:#747A85;")
            dimensions = QLabel(f"{width} × {height}", item)
            dimensions.setObjectName("annotationRowDimensions")
            dimensions.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            dimensions.setAttribute(Qt.WA_TransparentForMouseEvents)
            dimensions.setStyleSheet("color:#747A85;")
            detail_row.addWidget(source, 1)
            detail_row.addWidget(dimensions, 0)
            item_layout.addLayout(detail_row)
            color = self.canvas.color_for_label(name).name()
            item.setStyleSheet(
                f"QToolButton {{background:#FFFFFF; border:1px solid #E7E9ED; border-left:4px solid {color}; border-radius:10px; text-align:left; padding:7px 10px; color:#2E3138;}} QToolButton:hover {{background:#F6F9FE; border-color:#CFE1FF; border-left:4px solid {color};}} QToolButton:checked {{background:#EAF3FF; border-color:#0A74FF; border-left:4px solid {color}; font-weight:700;}}"
            )
            item.setToolTip(
                f"{display_name} · #{index + 1} · {shape_type}\n{provenance} · {width} × {height}\n点击定位并选中此标注框"
            )
            item.setAccessibleName(f"{display_name}，第 {index + 1} 个{shape_type}，{provenance}，{width} × {height}")
            item.clicked.connect(lambda checked=False, target=index: self._select_annotation_from_list(target))
            row_layout.addWidget(item, 1)
            visible = QToolButton()
            visible.setObjectName("visibilityCheck")
            visible.setCheckable(True)
            visible.setFixedSize(18, 18)
            visible.setChecked(index not in self.canvas.hidden_indices)
            visible.setText("✓" if visible.isChecked() else "")
            visible.setToolTip("在画布中显示此标注")
            visible.toggled.connect(
                lambda checked, target=index, control=visible: self._toggle_annotation_visibility(
                    control, target, checked
                )
            )
            row_layout.addWidget(visible, 0, Qt.AlignVCenter)
            self.drawer_rows_layout.addWidget(row)
            shown += 1
        if not shown:
            empty = label("没有匹配的标注框", "muted")
            empty.setAlignment(Qt.AlignCenter)
            self.drawer_rows_layout.addWidget(empty)
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
        hidden = set(self.canvas.hidden_indices)
        hidden.discard(index)
        self.canvas.set_hidden_indices(hidden)
        self.canvas.focus_shape(index)
        self._refresh_annotation_drawer()
