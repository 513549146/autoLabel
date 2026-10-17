"""Image review canvas page and review interactions."""
from __future__ import annotations

from collections import Counter

from ..ui import *
from ..canvas import ImageCanvas
from application.preannotation import ImagePreannotationService
from project import QUALITY_RULES_VERSION

class ReviewPage(QWidget):
    quality_index_finished = Signal(int, str, object)

    def __init__(self, window):
        super().__init__(); self.window = window; self.files: list[str] = []; self.index = 0; self._risk_cache = {}; self._risk_project_root = ""; self._spot_check_files = None; self._thumbnail_buttons = {}; self._thumbnail_signature = (); self._thumbnail_generation = 0; self._thumbnail_scroll_anchor = 0; self._thumbnail_scroll_guard = False; self._thumbnail_icons = {}; self._thumbnail_render_queue = []; self._thumbnail_render_pending = set(); self._thumbnail_risk_queue = []; self._thumbnail_risk_pending = set(); self._quality_filename = ""; self._current_dirty = False
        self._drawing_category: str | None = None
        self._quality_scan_token = 0
        self._quality_scan_thread = None
        self._quality_known_files: set[str] = set()
        self._quality_index_error = ""
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
        self.issue_filter = FlatComboBox(); self.issue_filter.addItem("全部待审核", "all"); self.issue_filter.addItem("风险队列", "risk"); self.issue_filter.addItem("低置信度", "low_confidence"); self.issue_filter.addItem("空标注", "empty"); self.issue_filter.addItem("已跳过", "skipped"); self.issue_filter.setToolTip("风险队列包含全部问题项，按风险降序；跳过的图片暂不参与训练。"); self.issue_filter.currentIndexChanged.connect(self._on_issue_filter_changed); review_actions.addWidget(self.issue_filter)
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
        self.save_button = button("保存", primary=True); self.save_button.setToolTip("保存标注（Ctrl+S）"); self.save_button.clicked.connect(self.save); review_actions.addWidget(self.save_button); tools.addLayout(review_actions); bar_layout.addLayout(tools); self.queue_summary = label("", "muted"); self.queue_summary.setObjectName("queueSummary"); bar_layout.addWidget(self.queue_summary); root.addWidget(bar)
        work = QHBoxLayout(); work.setSpacing(14); self.canvas_card = card(); canvas_layout = QVBoxLayout(self.canvas_card); canvas_layout.setContentsMargins(10, 10, 10, 10); self.canvas = ImageCanvas(); self.canvas.shapes_changed.connect(self._canvas_changed); self.canvas.selection_changed.connect(self._selection_changed); self.canvas.box_created.connect(self._new_box_created); self.canvas.polygon_created.connect(self._new_box_created); self.pan_tool.setChecked(True); canvas_layout.addWidget(self.canvas); work.addWidget(self.canvas_card, 1)
        self.save_toast = QLabel(self.canvas_card); self.save_toast.setObjectName("saveToast"); self.save_toast.setAttribute(Qt.WA_TransparentForMouseEvents); self.save_toast.setVisible(False)
        self._build_annotation_drawer()
        inspector = card(); inspector.setFixedWidth(280); inspector_layout = QVBoxLayout(inspector); inspector_layout.setContentsMargins(20, 20, 20, 20); inspector_layout.setSpacing(0); form_host = QWidget(); form = QVBoxLayout(form_host); form.setContentsMargins(0, 0, 0, 0); form.setSpacing(10); form.addWidget(label("标签", "section")); form.addSpacing(8)
        form.addWidget(label("显示类别（可多选）", "muted")); self.filter_collapsed = False; self.filter_host = QWidget(); self.filter_layout = QVBoxLayout(self.filter_host); self.filter_layout.setContentsMargins(0, 0, 0, 0); self.filter_layout.setSpacing(4); self.filter_layout.setAlignment(Qt.AlignTop); self.filter_scroll = QScrollArea(); self.filter_scroll.setWidgetResizable(True); self.filter_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff); self.filter_scroll.setFixedHeight(104); self.filter_scroll.setWidget(self.filter_host)
        filter_section = QWidget(); filter_section_layout = QVBoxLayout(filter_section); filter_section_layout.setContentsMargins(0, 0, 0, 0); filter_section_layout.setSpacing(0); filter_section_layout.addWidget(self.filter_scroll)
        collapse_row = QHBoxLayout(); collapse_row.setContentsMargins(0, 0, 0, 0); collapse_row.setSpacing(7); left_line = QFrame(); left_line.setFixedHeight(1); left_line.setStyleSheet("background:#E7EAF0;"); right_line = QFrame(); right_line.setFixedHeight(1); right_line.setStyleSheet("background:#E7EAF0;"); self.filter_collapse = DoubleChevronButton(); self.filter_collapse.clicked.connect(self.toggle_category_filters); collapse_row.addWidget(left_line, 1); collapse_row.addWidget(self.filter_collapse); collapse_row.addWidget(right_line, 1); filter_section_layout.addLayout(collapse_row); form.addWidget(filter_section)
        form.addSpacing(8); form.addWidget(label("选中框标签", "muted")); self.category = FlatComboBox(); self.category.setToolTip("为当前选中的标注框修改类别"); self.category.setEnabled(False); form.addWidget(self.category); self.annotation_state_hint = label("", "muted"); self.annotation_state_hint.setWordWrap(True); self.annotation_state_hint.hide(); form.addWidget(self.annotation_state_hint)
        segments = QHBoxLayout(); segments.setSpacing(8); self.status_buttons = {}
        self.review_state_badge = label("待审核", "reviewStateBadge"); self.review_state_badge.setToolTip("当前审核状态"); self.review_state_badge.setFixedHeight(40); segments.addWidget(self.review_state_badge)
        self.return_to_review = button("移回待审核"); self.return_to_review.setToolTip("将已跳过图片放回审核队列"); self.return_to_review.clicked.connect(lambda: self.set_status(STATUS_NEEDS_REVIEW)); self.return_to_review.setVisible(False); segments.addWidget(self.return_to_review)
        for caption, state, tip in (("通过", STATUS_APPROVED, "确认标注正确，加入训练候选"), ("跳过", STATUS_SKIPPED, "暂不处理并移出当前队列，不会加入训练候选")):
            item = button(caption); item.setToolTip(tip); item.clicked.connect(lambda checked=False, s=state: self.set_status(s)); self.status_buttons[state] = item; segments.addWidget(item)
        form.addLayout(segments); self.review_status_hint = label("待审核是当前状态；通过会加入训练候选；跳过可在“已跳过”筛选中移回。", "muted"); self.review_status_hint.setWordWrap(True); form.addWidget(self.review_status_hint); self.quality_hint = label("", "muted"); self.quality_hint.setWordWrap(True); form.addWidget(self.quality_hint)
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
        footer = QHBoxLayout(); footer.addWidget(label("审核进度", "muted")); self.progress = QProgressBar(); self.progress.setObjectName("reviewProgress"); self.progress.setTextVisible(False); self.progress.setFixedHeight(4); self.progress.setToolTip("全项目已通过 / 已进入审核的图片；跳过属于暂缓，不计入完成"); footer.addWidget(self.progress, 1, Qt.AlignVCenter); self.progress_text = label("0%", "muted"); footer.addWidget(self.progress_text); root.addLayout(footer)
        QShortcut(QKeySequence("Left"), self, activated=lambda: self.move(-1))
        QShortcut(QKeySequence("Right"), self, activated=lambda: self.move(1))
        QShortcut(QKeySequence("H"), self, activated=lambda: self.pan_tool.setChecked(True))
        QShortcut(QKeySequence("V"), self, activated=lambda: self.select_tool.setChecked(True))
        QShortcut(QKeySequence("R"), self, activated=lambda: self.box_tool.setChecked(True))
        QShortcut(QKeySequence("P"), self, activated=lambda: self.polygon_tool.setChecked(True))
        QShortcut(QKeySequence("L"), self, activated=self.toggle_annotation_drawer)
        QShortcut(QKeySequence("Ctrl+S"), self, activated=self.save)
        self.category.currentIndexChanged.connect(lambda _index: self._apply_selected_category())
        QShortcut(QKeySequence("Ctrl+Z"), self, activated=self.undo)
        QShortcut(QKeySequence("Ctrl+Shift+Z"), self, activated=self.redo)
        QShortcut(QKeySequence("Delete"), self, activated=self.delete_selected)
        QShortcut(QKeySequence("A"), self, activated=lambda: self._quick_review(STATUS_APPROVED))
        QShortcut(QKeySequence("S"), self, activated=lambda: self._quick_review(STATUS_SKIPPED))
        self.notes.textChanged.connect(self._schedule_note_save)
        self.quality_index_finished.connect(self._on_quality_index_finished)

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
        self.drawer_category.blockSignals(True); self.drawer_category.clear(); self.drawer_category.addItem("全部类别", "")
        for name in categories:
            self.drawer_category.addItem(self._category_display_name(name), name)
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
            display_name = str(shape.get("display_name") or self._category_display_name(name))
            if category and name != category:
                continue
            x1, y1, x2, y2 = self.canvas._shape_rect(index)
            width, height = int(x2 - x1), int(y2 - y1)
            searchable = f"{name} {display_name} {index + 1} {x1} {y1} {width} {height}".lower()
            if query and query not in searchable:
                continue
            row = QWidget(); row_layout = QHBoxLayout(row); row_layout.setContentsMargins(0, 0, 0, 0); row_layout.setSpacing(6)
            shape_type = "多边形" if shape.get("type") == "polygon" else "矩形"
            provenance = ImageCanvas.provenance_text(shape)
            item = QToolButton(); item.setObjectName("annotationRow"); item.setCheckable(True); item.setChecked(index == self.canvas.selected_index); item.setToolButtonStyle(Qt.ToolButtonTextOnly); item.setText(f"{display_name}   #{index + 1}  ·  {shape_type}\n{provenance}   ·   {width} × {height}"); item.setFixedHeight(54)
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

    def _risk_for(self, filename):
        cached = self._risk_cache.get(filename)
        if cached is not None:
            return cached
        if not self.window.project:
            return None
        record = self.window.project.quality_records_for([filename]).get(filename)
        if record is None or record[3] != QUALITY_RULES_VERSION:
            return None
        result = record[0], set(record[1])
        self._risk_cache[filename] = result
        return result

    def _scan_quality_index(self, token, root, project, filenames):
        """Validate file stamps and calculate only missing/stale risks off the UI thread."""
        processed = set()
        error = ""
        try:
            cached = project.quality_records_for(filenames)
            updates = []
            for filename in filenames:
                if token != self._quality_scan_token:
                    return
                stamp = project.quality_stamp(filename)
                record = cached.get(filename)
                if record is not None and record[2] == stamp and record[3] == QUALITY_RULES_VERSION:
                    processed.add(filename)
                    continue
                xml_path = os.path.join(project.annotations_dir, Path(filename).stem + ".xml")
                try:
                    shapes = core.load_voc_xml(xml_path) if os.path.isfile(xml_path) else []
                    risk, rules = core.annotation_risk(shapes, os.path.join(project.images_dir, filename))
                except Exception:
                    risk, rules = 100, {"invalid"}
                if project.quality_stamp(filename) != stamp:
                    continue
                updates.append((filename, risk, rules, stamp))
                if len(updates) >= 32:
                    processed.update(project.upsert_quality_records(updates))
                    updates.clear()
            processed.update(project.upsert_quality_records(updates))
        except Exception as exc:
            error = str(exc)
        try:
            self.quality_index_finished.emit(token, root, (processed, error))
        except RuntimeError:
            pass

    def _ensure_quality_index(self, filenames):
        if not self.window.project or self._quality_index_error:
            return
        missing = [filename for filename in filenames if filename not in self._quality_known_files]
        if not missing or (self._quality_scan_thread and self._quality_scan_thread.is_alive()):
            return
        project = self.window.project
        token = self._quality_scan_token
        root = os.path.abspath(self.window.project_root)
        self._quality_scan_thread = threading.Thread(
            target=self._scan_quality_index, args=(token, root, project, missing), daemon=True,
        )
        self._quality_scan_thread.start()

    def _on_quality_index_finished(self, token, root, result):
        if token != self._quality_scan_token or root != os.path.abspath(self.window.project_root or ""):
            return
        processed, error = result
        self._quality_scan_thread = None
        self._quality_known_files.update(processed)
        self._quality_index_error = error
        self.refresh()

    def _on_issue_filter_changed(self):
        # Save the old image before resetting its position in the new result set.
        self._save_current(silent=True)
        self.index = 0
        self._quality_index_error = ""
        # A filter switch is also a cheap opportunity to notice XML/image edits
        # made outside the workbench; validation runs in the background.
        self._quality_known_files.clear()
        self.refresh(save_current=False)

    def _update_review_progress(self):
        if not self.window.project:
            percent = 0
        else:
            summary = self.window.project.summary(self.window.project_images())
            total = sum(summary.get(status, 0) for status in (STATUS_NEEDS_REVIEW, STATUS_APPROVED, STATUS_SKIPPED))
            percent = round(summary.get(STATUS_APPROVED, 0) * 100 / total) if total else 0
        self.progress.setValue(percent)
        self.progress_text.setText(f"{percent}%")

    def _update_queue_summary(self, files, quality):
        if self._spot_check_files is not None:
            records = self.window.project.spot_checks_for(self._spot_check_files) if self.window.project else {}
            pending = sum(record.get("state") == "sampled" for record in records.values())
            self.queue_summary.setText(f"最终抽检队列 · 待核验 {pending} 张；通过后保留为训练候选，需修改则移回待审核")
            return
        mode = self.issue_filter.currentData()
        if mode not in ("all", "skipped") and len(quality) < len(files):
            self.queue_summary.setText(
                f"正在建立质量索引 {len(quality)} / {len(files)} 张；可继续使用其他页面"
                if not self._quality_index_error else f"质量索引暂不可用：{self._quality_index_error}"
            )
            return
        if mode == "risk":
            scores = [quality[filename][0] for filename in files]
            high = sum(score >= config.REVIEW_HIGH_RISK for score in scores)
            attention = sum(0 < score < config.REVIEW_HIGH_RISK for score in scores)
            safe = sum(score == 0 for score in scores)
            self.queue_summary.setText(f"风险队列：高风险 {high} 张 · 待关注 {attention} 张 · 已隐藏无风险 {safe} 张")
        else:
            if mode == "skipped":
                self.queue_summary.setText(f"已跳过 {len(files)} 张 · 选择“待审核”可将图片移回审核队列")
            elif mode in ("low_confidence", "empty"):
                caption = "低置信度" if mode == "low_confidence" else "空标注"
                count = sum(mode in quality[filename][1] for filename in files)
                self.queue_summary.setText(f"{caption} {count} 张 · 按风险从高到低排序")
            else:
                self.queue_summary.setText(f"待审核 {len(files)} 张 · 可切换“风险队列”按质量规则筛选")

    def _filtered_files(self, files, quality):
        if self._spot_check_files is not None:
            records = self.window.project.spot_checks_for(self._spot_check_files) if self.window.project else {}
            return [filename for filename in self._spot_check_files if records.get(filename, {}).get("state") == "sampled"]
        mode = self.issue_filter.currentData() if hasattr(self, "issue_filter") else "all"
        if mode in ("all", "skipped"):
            return files
        if len(quality) < len(files):
            return []
        if mode == "risk":
            result = [filename for filename in files if quality[filename][0] > 0]
        else:
            result = [filename for filename in files if mode in quality[filename][1]]
        return sorted(result, key=lambda filename: (-quality[filename][0], filename.lower()))

    def _update_quality_hint(self, filename):
        labels = {
            "empty": "空标注", "low_confidence": "低置信度", "tiny": "极小框",
            "out_of_bounds": "越界框", "duplicate": "疑似重复框", "invalid": "标注文件异常",
        }
        result = self._risk_for(filename)
        if result is None:
            self.quality_hint.setText("正在检查标注质量…")
            self.quality_hint.setStyleSheet("color:#737780;")
            return
        risk, issues = result
        level = "高" if risk >= config.REVIEW_HIGH_RISK else "中" if risk else "低"
        detail = "、".join(labels[item] for item in sorted(issues)) if issues else "未发现明显问题"
        self.quality_hint.setText(f"风险{level} · {risk}分\n质量规则：{detail}")
        if "out_of_bounds" in issues:
            self.quality_hint.setToolTip("“越界框”指原始标注坐标存在小于 0 或大于图片宽高的值。画布会按图片边缘裁切显示，因此只越界几个像素时不容易从视觉上察觉；选择该框后将边缘拖回图片内并保存即可。")
        else:
            self.quality_hint.setToolTip("")
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

    def refresh(self, *, save_current=True):
        if save_current:
            self._save_current(silent=True)
        project_root = os.path.abspath(self.window.project_root) if self.window.project_root else ""
        if project_root != self._risk_project_root:
            self._risk_cache = {}
            self._risk_project_root = project_root
            self._quality_scan_token += 1
            self._quality_scan_thread = None
            self._quality_known_files = set()
            self._quality_index_error = ""
        self._update_review_progress()
        mode = self.issue_filter.currentData()
        source_files = self.window.project_images(STATUS_SKIPPED if mode == "skipped" else STATUS_NEEDS_REVIEW)
        if self._spot_check_files is not None:
            source_files = list(self._spot_check_files)
        self._ensure_quality_index(source_files)
        quality = self.window.project.quality_records_for(source_files) if self.window.project else {}
        quality = {name: record for name, record in quality.items() if record[3] == QUALITY_RULES_VERSION}
        for name, record in quality.items():
            self._risk_cache[name] = record[0], set(record[1])
        self._update_queue_summary(source_files, quality)
        files = self._filtered_files(source_files, quality)
        if files != self.files:
            self._thumbnail_generation += 1
        self.files = files; self.index = min(self.index, max(0, len(self.files) - 1)); self._load()
        for index, item in self._thumbnail_buttons.items():
            if index < len(self.files):
                record = self._risk_cache.get(self.files[index])
                self._apply_thumbnail_risk(item, record[0] if record else None)

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
            cached = self._risk_cache.get(filename)
            self._apply_thumbnail_risk(item, cached[0] if cached else None)
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
        self.position.setText(f"{current} / {total}")
        self._update_thumbnail_selection()
        self._schedule_thumbnail_fill(100)
        if not total:
            self.notes.blockSignals(True); self.notes.clear(); self.notes.blockSignals(False)
            self.quality_hint.setText("")
            self._sync_status_controls()
            self._update_annotation_state_hint(-1)
            self.canvas.set_content(None, []); self._rebuild_category_filters(); self._refresh_annotation_drawer(); return
        filename = self.files[self.index]; image_path = os.path.join(self.window.images_dir, filename); xml_path = os.path.join(self.window.annotations_dir, Path(filename).stem + ".xml")
        try: shapes = core.load_voc_xml(xml_path) if os.path.isfile(xml_path) else []
        except Exception: shapes = []
        display_names = self.window.project.category_display_names() if self.window.project else {}
        for shape in shapes:
            shape["display_name"] = display_names.get(shape.get("name", ""), shape.get("name", ""))
        self.canvas.set_content(image_path, shapes)
        self._current_dirty = False
        self.notes.blockSignals(True); self.notes.setPlainText(self.window.project.note_for(filename) if self.window.project else ""); self.notes.blockSignals(False)
        self._schedule_quality_hint(filename)
        self._prefetch_timer.start(90)
        self.category.blockSignals(True); self.category.clear(); self.category.blockSignals(False); self.category.setEnabled(False)
        self._sync_status_controls()
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

    def _sync_status_controls(self):
        """Show the current state separately from the available actions."""
        current = None
        if self.files and self.window.project:
            current = self.window.project.status_for(self.files[self.index])
        labels = {
            STATUS_NEEDS_REVIEW: ("● 待审核", "#0A74FF"),
            STATUS_APPROVED: ("● 已通过", "#16794C"),
            STATUS_SKIPPED: ("● 已跳过", "#9A6700"),
        }
        text, foreground = labels.get(current, ("未选择图片", "#737780"))
        self.review_state_badge.setText(text)
        self.review_state_badge.setStyleSheet(f"background:transparent; color:{foreground}; border:0; padding:0; font-size:12px; font-weight:700;")
        self.return_to_review.setVisible(current == STATUS_SKIPPED)
        self.return_to_review.setEnabled(current == STATUS_SKIPPED)
        for state, control in self.status_buttons.items():
            control.setEnabled(bool(current) and state != current)

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

    def _category_display_name(self, name):
        if self.window.project:
            return self.window.project.category_display_names().get(name, name)
        return name

    def _sync_selected_category_editor(self, selected_name=None):
        """Populate the inspector with configured display names and raw label data."""
        current_name = selected_name if selected_name is not None else self.category.currentData()
        categories = self._available_categories()
        self.category.blockSignals(True)
        self.category.clear()
        for name in categories:
            self.category.addItem(self._category_display_name(name), name)
        index = self.category.findData(current_name)
        self.category.setCurrentIndex(index if index >= 0 else -1)
        self.category.blockSignals(False)
        self.category.setEnabled(index >= 0)

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
        display_names = self.window.project.category_display_names() if self.window.project else {}
        for name in categories:
            item = QCheckBox(display_names.get(name, name)); item.setChecked(select_all or name in selected); item.toggled.connect(self._apply_category_filters)
            self.filter_layout.addWidget(item); self._filter_checks[name] = item
        self.filter_layout.addStretch()
        self._refresh_category_counts()
        self._apply_category_filters()

    def _refresh_category_counts(self):
        if not hasattr(self, "_filter_checks"):
            return
        counts = Counter(str(shape.get("name", "")) for shape in self.canvas.shapes)
        display_names = self.window.project.category_display_names() if self.window.project else {}
        for name, checkbox in self._filter_checks.items():
            count = counts[name]
            checkbox.setText(f"{display_names.get(name, name)}  ({count})")

    def _apply_category_filters(self):
        self.canvas.set_visible_categories({name for name, item in self._filter_checks.items() if item.isChecked()})

    def _apply_selected_category(self):
        name = str(self.category.currentData() or "").strip()
        if not name or not 0 <= self.canvas.selected_index < len(self.canvas.shapes):
            return
        self._set_drawing_category(name)
        shape = self.canvas.shapes[self.canvas.selected_index]
        if shape.get("name") == name and shape.get("display_name") == self._category_display_name(name):
            return
        self.canvas.apply_category(name, self._category_display_name(name))
        self._update_annotation_state_hint(self.canvas.selected_index)
        self._rebuild_category_filters({name for name, item in self._filter_checks.items() if item.isChecked()} | {name})
        self._refresh_annotation_drawer()

    def _set_drawing_category(self, name: str):
        self._drawing_category = name
        self.canvas.set_default_label(name)
        display_name = self._category_display_name(name)
        self.box_tool.setToolTip(f"绘制矩形标注框（R）\n当前类别：{display_name}；新框将沿用此类别")
        self.polygon_tool.setToolTip(f"绘制多边形标注（P）\n当前类别：{display_name}；新框将沿用此类别")

    def _selection_changed(self, index: int):
        if 0 <= index < len(self.canvas.shapes):
            name = self.canvas.shapes[index].get("name", "")
            self._sync_selected_category_editor(name)
        else:
            self.category.blockSignals(True); self.category.clear(); self.category.blockSignals(False); self.category.setEnabled(False)
        self._update_annotation_state_hint(index)
        self._refresh_annotation_drawer()

    def _update_annotation_state_hint(self, index):
        if 0 <= index < len(self.canvas.shapes):
            self.annotation_state_hint.setText("标注来源：" + ImageCanvas.provenance_text(self.canvas.shapes[index]))
        else:
            self.annotation_state_hint.setText("")
        self.annotation_state_hint.setVisible(bool(self.annotation_state_hint.text()))

    def _new_box_created(self, index: int):
        records = self.window.project.category_records() if self.window.project else []
        if not records:
            self.canvas.delete_selected()
            ios_alert(self, "请先配置类别", "请先在“自动标注”页的“管理类别”中添加英文标签和中文显示名。", confirm_text="知道了")
            return
        display_to_name = {record["display_name"]: record["name"] for record in records}
        annotation_type = "多边形" if 0 <= index < len(self.canvas.shapes) and self.canvas.shapes[index].get("type") == "polygon" else "矩形"
        category = self._drawing_category
        if category not in {record["name"] for record in records}:
            # The canvas has already committed the provisional shape. Do not
            # autosave it with the placeholder label while the dialog is open.
            self._autosave_timer.stop()
            value, ok = ios_item_input(self, f"新增{annotation_type}标注", "选择类别：", list(display_to_name), 0, False, "创建")
            if not ok or not value.strip():
                self.canvas.delete_selected()
                self.window.status_message("已取消新增标注")
                return
            category = display_to_name[value.strip()]
            self._set_drawing_category(category)
        # The canvas committed the new shape before emitting this signal.
        # Complete that same undo step instead of adding a second "relabel"
        # step for every newly drawn box or polygon.
        shape = self.canvas.shapes[index]
        shape["name"] = category
        shape["display_name"] = self._category_display_name(category)
        self.canvas.update()
        self._sync_selected_category_editor(category)
        selected = {name for name, item in self._filter_checks.items() if item.isChecked()} if hasattr(self, "_filter_checks") else None
        self._rebuild_category_filters(selected | {category} if selected is not None else None)
        self._refresh_annotation_drawer()
        if self._current_dirty:
            self._autosave_timer.start(650)

    def _canvas_changed(self, _shapes):
        self._current_dirty = True
        self._autosave_timer.start(650)
        self._update_annotation_state_hint(self.canvas.selected_index)
        self.window.status_message("标注已修改，正在自动保存…")
        self._refresh_category_counts()
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
        self._quality_scan_token += 1
        self._quality_scan_thread = None
        self._quality_known_files = set()
        self._quality_index_error = ""
        self._thumbnail_signature = ()
        self._thumbnail_generation += 1
        self._thumbnail_icons = {}
        self._thumbnail_render_queue = []
        self._thumbnail_render_pending = set()
        self._thumbnail_risk_queue = []
        self._thumbnail_risk_pending = set()
        self._current_dirty = False
        self._drawing_category = None
        self.canvas.set_default_label("object")
        self.box_tool.setToolTip("绘制矩形标注框（R）")
        self.polygon_tool.setToolTip("绘制多边形标注（P）")
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

    def _index_current_quality(self, filename, shapes=None):
        if not self.window.project:
            return
        image_path = os.path.join(self.window.images_dir, filename)
        xml_path = os.path.join(self.window.annotations_dir, Path(filename).stem + ".xml")
        try:
            annotations = shapes if shapes is not None else core.load_voc_xml(xml_path)
            risk, rules = core.annotation_risk(annotations, image_path)
        except Exception:
            risk, rules = 100, {"invalid"}
        self.window.project.upsert_quality(filename, risk, rules)
        self._risk_cache[filename] = risk, set(rules)
        self._quality_known_files.add(filename)

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
        self._index_current_quality(filename, self.canvas.shapes)
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
        changed = self._save_current(silent=True)
        message = "已保存" if changed else "已是最新保存"
        self.window.status_message(message)
        self._show_save_toast(message)

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
        ImagePreannotationService.snapshot_annotation(
            self.window.project, filename, image_path, xml_path, "恢复前当前结果", "", "", deduplicate=True,
        )
        shutil.copy2(version["path"], xml_path)
        self._index_current_quality(filename)
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
        self.window.status_message("已通过并进入下一张" if status == STATUS_APPROVED else "已跳过并进入下一张" if status == STATUS_SKIPPED else "已移回待审核")
