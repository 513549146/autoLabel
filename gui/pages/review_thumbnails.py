"""审核缩略图：只创建当前视窗附近的条目，分批补齐图片与风险分。"""

from __future__ import annotations

from ..ui import *


class ReviewThumbnailMixin:
    """由 ReviewPage 提供图片队列、缩略图缓存和质量索引。"""

    def _ensure_thumbnail_strip(self, anchor_index=None):
        # 只保留当前窗口附近的按钮；滚动时重建小窗口，避免大项目创建海量控件。
        total = len(self.files)
        visible_slots = max(8, int(max(1, self.thumbnails.viewport().width()) / 126) + 2)
        window_size = min(total, max(30, visible_slots * 3))
        anchor = self.index if anchor_index is None else max(0, min(total - 1, anchor_index))
        current_start = (
            self._thumbnail_signature[1]
            if len(self._thumbnail_signature) == 3 and self._thumbnail_signature[0] == self._thumbnail_generation
            else -1
        )
        buffer = max(4, window_size // 3)
        if (
            current_start >= 0
            and current_start + buffer <= anchor < current_start + window_size - buffer
            and len(self._thumbnail_buttons) == window_size
        ):
            return
        start = max(0, min(anchor - window_size // 2, total - window_size))
        visible_indices = range(start, start + window_size)
        signature = (self._thumbnail_generation, start, window_size)
        if signature == self._thumbnail_signature and len(self._thumbnail_buttons) == window_size:
            return
        for item in self._thumbnail_buttons.values():
            item.setParent(None)
            item.deleteLater()
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
            item = QToolButton(self.thumb_host)
            item.move(index * 126, 0)
            item.setFixedSize(116, 74)
            item.setCheckable(True)
            item.setIcon(self._thumbnail_icons.get(filename, QIcon()))
            item.setIconSize(QSize(108, 66))
            item.setToolTip(f"#{index + 1} · {filename}")
            if filename not in self._thumbnail_icons:
                self._queue_thumbnail_render(index, filename)
            cached = self._risk_cache.get(filename)
            if cached is None:
                self._apply_thumbnail_risk(item, None)
                self._queue_thumbnail_risk(index, filename)
            else:
                self._apply_thumbnail_risk(item, cached[0])
            item.clicked.connect(lambda checked=False, target=index: self.select(target))
            item.show()
            self._thumbnail_buttons[index] = item
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
        pixmap = load_oriented_pixmap(os.path.join(self.window.images_dir, filename))
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
        item.setStyleSheet(
            "QToolButton {border:2px solid transparent; border-radius:8px; background:#FFFFFF;} QToolButton:hover {border-color:#BFD8FF; background:#F8FBFF;} QToolButton:checked {border-color:#0A74FF; background:#EEF6FF;}"
        )
        badge = item.findChild(QLabel, "thumbnailRiskBadge")
        if badge is None:
            badge = QLabel(item)
            badge.setObjectName("thumbnailRiskBadge")
            badge.setAttribute(Qt.WA_TransparentForMouseEvents)
            badge.setAlignment(Qt.AlignCenter)
            badge.setGeometry(6, 6, 30, 19)
            badge.show()
        badge.setText("…" if risk is None else str(risk))
        badge.setStyleSheet(
            f"background:#FFFFFF;color:{color};border:1px solid {color};border-radius:9px;font-size:10px;font-weight:700;"
        )

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
        self._thumbnail_scroll_anchor = max(
            0, min(len(self.files) - 1, int((value + self.thumbnails.viewport().width() / 2) / step))
        )
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
