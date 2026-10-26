"""Approved-image library and spot-check page."""
from __future__ import annotations

from collections import OrderedDict

from PySide6.QtGui import QImageReader

from ..canvas import ImageCanvas
from ..ui import *

class LibraryPage(QWidget):
    """已通过 · 训练候选页：统计就绪度、做抽检、把图片移回待审核。"""
    readiness_finished = Signal(int, object)
    thumbnail_ready = Signal(int, str, object, object)
    PAGE_SIZE = 48
    THUMBNAIL_CACHE_SIZE = 192

    def __init__(self, window):
        """构建就绪看板、筛选栏与缩略图网格，并连接后台统计与缩略图信号。"""
        super().__init__(); self.window = window; self.files = []; self.selected = set(); self._analysis = {}; self._analysis_signature = (); self._analysis_token = 0; self._spot_checks = {}
        self._image_root = ""; self._visible = []; self._shown = 0; self._more_button = None; self._grid_initialized = False; self._page_scheduled = False
        self._thumbnail_token = 0; self._thumbnail_labels = {}; self._thumbnail_cache = OrderedDict()
        self._categories_signature = None; self._class_chip_signature = object()
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
        surface = card(); self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True); self.host = QWidget(); self.grid = QGridLayout(self.host); self.grid.setContentsMargins(18, 18, 18, 18); self.grid.setSpacing(14); self.grid.setAlignment(Qt.AlignTop | Qt.AlignLeft); self.scroll.setWidget(self.host); self.scroll.verticalScrollBar().valueChanged.connect(self._on_scroll); lay = QVBoxLayout(surface); lay.setContentsMargins(8, 8, 8, 8); lay.addWidget(self.scroll); layout.addWidget(surface, 1)
        self.readiness_finished.connect(self._apply_readiness)
        self.thumbnail_ready.connect(self._apply_thumbnail)

    def _metric(self, caption, value):
        """创建一个指标块（数值、标题、细进度条），返回三元组供后续更新。"""
        host = QWidget(); host.setFixedWidth(76); layout = QVBoxLayout(host); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(1); value_label = label(value, "section"); layout.addWidget(value_label); layout.addWidget(label(caption, "muted")); progress = QProgressBar(); progress.setRange(0, 100); progress.setTextVisible(False); progress.setFixedHeight(3); layout.addWidget(progress)
        return host, value_label, progress

    @staticmethod
    def _set_metric(metric, value, percent, color):
        """更新指标块的数值、进度条百分比与颜色。"""
        _, value_label, progress = metric; value_label.setText(value); progress.setValue(max(0, min(100, int(percent)))); progress.setStyleSheet(f"QProgressBar {{background:#E9EDF2;border:0;border-radius:3px;}} QProgressBar::chunk {{background:{color};border-radius:3px;}}")

    def _training_target(self):
        """读取项目级训练目标，缺省回退到 config.MIN_TRAIN_IMAGES。"""
        if not self.window.project:
            return config.MIN_TRAIN_IMAGES
        try:
            return max(1, int(self.window.project.metadata_value("training_target_images", config.MIN_TRAIN_IMAGES)))
        except (TypeError, ValueError):
            return config.MIN_TRAIN_IMAGES

    def edit_training_target(self):
        """弹窗修改项目训练目标并立即刷新就绪度。"""
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
        signature = None if counts is None else tuple(sorted(counts.items()))
        if signature == self._class_chip_signature:
            return
        self._class_chip_signature = signature
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
        """重读已通过图片与抽检状态，刷新看板、筛选与网格。"""
        files = self.window.project_images(STATUS_APPROVED)
        spot_checks = self.window.project.spot_checks_for(files) if self.window.project else {}
        image_root = self.window.images_dir
        changed = files != self.files or spot_checks != self._spot_checks or image_root != self._image_root
        if image_root != self._image_root:
            self._thumbnail_cache.clear()
        self._image_root = image_root
        self.files = files; self.selected.intersection_update(files); self._spot_checks = spot_checks
        target = self._training_target(); self.target_button.setText(f"目标 {target}")
        self.count.setText(f"{len(self.files)} 张已通过图片"); self.readiness_summary.setText("训练候选尚待检查" if self.files else "尚未形成训练候选")
        self._set_metric(self.size_metric, f"{len(self.files)} / {target}", len(self.files) * 100 / target, "#31A56C" if len(self.files) >= target else "#E6B800")
        previous_filter = self.category_filter.currentData()
        self._sync_categories(); self._queue_readiness(); self._render_class_chips(self._analysis.get("class_counts") if self._analysis else None); self._update_spot_progress()
        if changed or not self._grid_initialized or previous_filter != self.category_filter.currentData():
            self._render_grid()
        else:
            self._update_selection_info()

    def _sync_categories(self):
        """类别表变化时重建类别筛选，并尽量保留当前选项。"""
        categories = tuple(self.window.project.categories) if self.window.project else ()
        if categories == self._categories_signature:
            return
        self._categories_signature = categories
        current = self.category_filter.currentData() or "all"
        self.category_filter.blockSignals(True); self.category_filter.clear(); self.category_filter.addItem("全部类别", "all")
        for name in categories: self.category_filter.addItem(name, name)
        index = self.category_filter.findData(current); self.category_filter.setCurrentIndex(max(0, index)); self.category_filter.blockSignals(False)

    def _queue_readiness(self):
        """签名变化时在后台重算类别覆盖与标注完整度。"""
        categories = self._categories_signature or ()
        signature = (self.window.annotations_dir, tuple(self.files), categories)
        if signature == self._analysis_signature:
            return
        self._analysis_signature = signature; self._analysis = {}; self._analysis_token += 1; token = self._analysis_token
        self._set_metric(self.coverage_metric, "…", 0, "#0A74FF"); self._set_metric(self.quality_metric, "…", 0, "#0A74FF")
        categories = list(categories); files = list(self.files)
        annotations_dir = self.window.annotations_dir
        threading.Thread(target=lambda: self.readiness_finished.emit(token, core.training_readiness(annotations_dir, files, categories)), daemon=True).start()

    def _apply_readiness(self, token, analysis):
        """把后台统计结果写入指标与类别胶囊，并判定是否就绪。"""
        if token != self._analysis_token:
            return
        self._analysis = analysis
        counts = analysis.get("class_counts", {}); covered = sum(count > 0 for count in counts.values()); total_classes = len(counts); complete = len(self.files) - len(analysis.get("empty", []))
        self._set_metric(self.coverage_metric, f"{covered} / {total_classes} 类" if total_classes else "未设类别", covered * 100 / max(1, total_classes), "#31A56C" if total_classes and covered == total_classes else "#E6B800")
        self._set_metric(self.quality_metric, f"{complete} / {len(self.files)} 张", complete * 100 / max(1, len(self.files)), "#31A56C" if complete == len(self.files) else "#FF8A00")
        is_ready = len(self.files) >= self._training_target() and total_classes > 0 and covered == total_classes and complete == len(self.files)
        self.readiness.setText("就绪" if is_ready else "待补充"); self.readiness_summary.setText("可导出训练集" if is_ready else "训练候选尚待检查"); self.readiness_dot.setStyleSheet(f"background:{'#31A56C' if is_ready else '#E6B800'};border-radius:4px;")
        self._render_class_chips(counts)
        if (self.category_filter.currentData() or "all") != "all":
            self._render_grid()

    def _visible_files(self):
        """按搜索词、类别与抽检状态筛选候选文件。"""
        query, category, spot = self.search.text().strip().lower(), self.category_filter.currentData() or "all", self.spot_filter.currentData() or "all"
        labels = self._analysis.get("labels_by_file", {})
        result = []
        for filename in self.files:
            if query and query not in filename.lower(): continue
            if category != "all" and category not in labels.get(filename, ()): continue
            state = self._spot_checks.get(filename, {}).get("state")
            if spot != "all" and state != spot: continue
            result.append(filename)
        return result

    def _render_grid(self):
        """重建缩略图网格；缩略图按当前页懒加载。"""
        self._grid_initialized = True
        self._thumbnail_token += 1
        self._thumbnail_labels.clear()
        while self.grid.count():
            item = self.grid.takeAt(0); widget = item.widget(); widget.deleteLater() if widget else None
        self._more_button = None
        self._visible = self._visible_files()
        self._shown = 0
        self._update_selection_info()
        if not self._visible:
            message = "正在统计类别…" if self.files and self.category_filter.currentData() != "all" and not self._analysis else "当前筛选没有训练候选"
            empty = label(message, "muted"); empty.setAlignment(Qt.AlignCenter); self.grid.addWidget(empty, 0, 0); return
        self._append_page()

    def _update_selection_info(self):
        """刷新已选数量并切换批量按钮可用性。"""
        self.selection_info.setText(f"已选择 {len(self.selected)} 张" if self.selected else "未选择")
        self.batch_return.setEnabled(bool(self.selected))

    def _append_page(self):
        """追加一页候选卡片，命中缓存的缩略图直接复用，其余交给后台解码。"""
        if self._more_button:
            self.grid.removeWidget(self._more_button)
            self._more_button.deleteLater()
            self._more_button = None
        start = self._shown
        end = min(len(self._visible), start + self.PAGE_SIZE)
        pending = []
        for index in range(start, end):
            filename = self._visible[index]
            item = card(); item.setFixedSize(224, 226); lay = QVBoxLayout(item); lay.setContentsMargins(8, 8, 8, 8); image = QLabel(); image.setFixedSize(208, 124); image.setAlignment(Qt.AlignCenter); image.setText("载入中…"); lay.addWidget(image)
            path = os.path.join(self.window.images_dir, filename)
            try:
                stat = os.stat(path)
                key = (path, stat.st_mtime_ns, stat.st_size)
            except OSError:
                key = (path, 0, 0)
            cached = self._thumbnail_cache.get(key)
            if cached is not None:
                self._thumbnail_cache.move_to_end(key)
                image.setPixmap(cached)
            else:
                self._thumbnail_labels[filename] = (image, key)
                pending.append((filename, path, key))
            row = QHBoxLayout(); check = QCheckBox(); check.setChecked(filename in self.selected); check.toggled.connect(lambda checked=False, target=filename: self.toggle_selected(target, checked)); row.addWidget(check); file_label = label(filename); file_label.setFixedWidth(180); file_label.setWordWrap(False); file_label.setToolTip(filename); row.addWidget(file_label); lay.addLayout(row)
            bottom = QHBoxLayout(); state = self._spot_checks.get(filename, {}).get("state"); badge = label("待抽检" if state == "sampled" else "抽检通过" if state == "passed" else "训练候选"); badge.setStyleSheet("color:#B45309;background:#FFF7E6;border-radius:8px;padding:4px 7px;font-weight:700;" if state == "sampled" else "color:#17834C;background:#EEF9F3;border-radius:8px;padding:4px 7px;font-weight:700;" if state == "passed" else "color:#0A74FF;background:#EAF3FF;border-radius:8px;padding:4px 7px;font-weight:700;"); bottom.addWidget(badge); bottom.addStretch()
            remove = destructive_button("移回待审核"); remove.clicked.connect(lambda checked=False, target=filename: self.remove_record(target)); bottom.addWidget(remove); lay.addLayout(bottom); self.grid.addWidget(item, index // 4, index % 4, Qt.AlignTop | Qt.AlignLeft)
        self._shown = end
        if end < len(self._visible):
            self._more_button = button(f"显示更多 · 还有 {len(self._visible) - end} 张")
            self._more_button.clicked.connect(self._append_page)
            self.grid.addWidget(self._more_button, (end + 3) // 4, 0, 1, 4, Qt.AlignHCenter)
        if pending:
            token = self._thumbnail_token
            threading.Thread(target=self._load_thumbnails, args=(token, pending), daemon=True).start()

    def _on_scroll(self, value):
        """滚动接近底部时安排加载下一页，避免重复触发。"""
        bar = self.scroll.verticalScrollBar()
        if (not self._page_scheduled and self._shown < len(self._visible)
                and bar.maximum() > 0 and value >= bar.maximum() - 240):
            self._page_scheduled = True
            QTimer.singleShot(0, self._auto_append_page)

    def _auto_append_page(self):
        """仍在底部附近时真正追加下一页。"""
        self._page_scheduled = False
        bar = self.scroll.verticalScrollBar()
        if self._shown < len(self._visible) and bar.maximum() > 0 and bar.value() >= bar.maximum() - 240:
            self._append_page()

    def _load_thumbnails(self, token, pending):
        """Decode only visible-page thumbnails away from the Qt UI thread."""
        for filename, path, key in pending:
            if token != self._thumbnail_token:
                return
            reader = QImageReader(path)
            reader.setAutoTransform(True)
            original = reader.size()
            if original.isValid():
                reader.setScaledSize(original.scaled(QSize(208, 124), Qt.KeepAspectRatio))
            thumbnail = reader.read()
            try:
                self.thumbnail_ready.emit(token, filename, key, thumbnail)
            except RuntimeError:
                return

    def _apply_thumbnail(self, token, filename, key, thumbnail):
        """回到主线程后写入缩略图，并按 LRU 限制缓存大小。"""
        if token != self._thumbnail_token:
            return
        entry = self._thumbnail_labels.pop(filename, None)
        if not entry or entry[1] != key:
            return
        image_label = entry[0]
        if thumbnail.isNull():
            image_label.setText("无法预览")
            return
        pixmap = QPixmap.fromImage(thumbnail).scaled(image_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        image_label.setPixmap(pixmap)
        self._thumbnail_cache[key] = pixmap
        self._thumbnail_cache.move_to_end(key)
        while len(self._thumbnail_cache) > self.THUMBNAIL_CACHE_SIZE:
            self._thumbnail_cache.popitem(last=False)

    def toggle_selected(self, filename, checked):
        """记录或取消某张候选的选中状态。"""
        (self.selected.add(filename) if checked else self.selected.discard(filename)); self._update_selection_info()

    def start_spot_check(self):
        """从未抽取的候选里随机抽若干张并标记为待抽检。"""
        if not self.window.project or not self.files:
            return
        candidates = [name for name in self.files if name not in self._spot_checks]
        chosen = random.sample(candidates, min(int(self.sample_size.currentData()), len(candidates)))
        if not chosen:
            self.window.status_message("没有尚未抽取的训练候选"); return
        self.window.project.mark_spot_checks(chosen, "sampled"); self._spot_checks = self.window.project.spot_checks_for(self.files); self.spot_filter.blockSignals(True); self.spot_filter.setCurrentIndex(self.spot_filter.findData("sampled")); self.spot_filter.blockSignals(False); self._update_spot_progress(); self._render_grid(); self.window.status_message(f"已随机抽取 {len(chosen)} 张，点击“开始抽检”进入审核页")

    def _update_spot_progress(self):
        """刷新待抽检/已完成计数与“开始抽检”可用性。"""
        pending = sum(record.get("state") == "sampled" for record in self._spot_checks.values())
        passed = sum(record.get("state") == "passed" for record in self._spot_checks.values())
        self.spot_progress.setText(f"待抽检 {pending} · 已完成 {passed}")
        self.begin_spot_check.setEnabled(bool(pending))

    def begin_spot_check_review(self):
        """带着待抽检图片进入常规审核画布。"""
        sampled = [filename for filename, record in self._spot_checks.items() if record.get("state") == "sampled"]
        if sampled:
            self.window.open_spot_check_review(sampled)

    def return_selected(self):
        """确认后把所选候选批量移回待审核，不删除原图与 XML。"""
        names = sorted(self.selected)
        if not names or not self.window.project or not ios_alert(self, "批量移回待审核", f"将 {len(names)} 张训练候选移回待审核？原图和 XML 不会删除。", "移回待审核", "取消"):
            return
        self.window.project.set_statuses(names, STATUS_NEEDS_REVIEW); self.selected.clear(); self.window.refresh_all(); self.window.status_message(f"已移回 {len(names)} 张待审核")

    def remove_record(self, filename: str):
        """确认后把单张图片移回待审核。"""
        if not ios_alert(self, "移回待审核", f"要将“{filename}”移回待审核吗？\n\n原图和 XML 标注不会删除。", "移回待审核", "取消") or not self.window.project:
            return
        self.window.project.set_statuses([filename], STATUS_NEEDS_REVIEW); self.window.refresh_all(); self.window.status_message(f"已移回待审核：{filename}")
