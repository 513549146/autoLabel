"""Minimal local model library, inspired by a version-management list."""
from __future__ import annotations

import threading
from pathlib import Path

from application.model_library import DOWNLOADABLE_MODELS, ModelLibrary
from ..ui import *


def _size_text(path: str) -> str:
    try:
        size = Path(path).stat().st_size
    except OSError:
        return ""
    return f"{size / 1024**3:.1f} GB" if size >= 1024**3 else f"{size / 1024**2:.0f} MB"


class ModelSwitch(QToolButton):
    """Enabled is blue on the left; disabled is gray on the right."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(46, 26)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("启用后可在自动标注页选择此模型")

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#E8F2FF" if self.isEnabled() and self.isChecked() else "#E9EDF2"))
        painter.drawRoundedRect(QRectF(1, 2, 44, 22), 11, 11)
        painter.setBrush(QColor("#0A74FF" if self.isChecked() else "#AAB6C6"))
        painter.drawEllipse(QRectF(4 if self.isChecked() else 25, 4, 18, 18))


class ModelManagerPage(QWidget):
    task_progress = Signal(int)
    task_done = Signal(str, str)
    task_error = Signal(str)

    def __init__(self, window):
        super().__init__()
        self.window = window
        self.library = ModelLibrary()
        self._worker_thread = None
        self._cancel_event = threading.Event()
        self._action_buttons = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 24)
        layout.setSpacing(14)
        header = QHBoxLayout()
        back = button("‹ 返回自动标注")
        back.clicked.connect(lambda: window.show_page("auto"))
        header.addWidget(back)
        header.addSpacing(10)
        header.addWidget(label("模型管理", "title"))
        header.addStretch()
        self.location = label("模型保存在本机", "muted")
        header.addWidget(self.location)
        layout.addLayout(header)

        lead = label("查看本机模型版本；启用状态按项目保存，可按需联网下载或导入权重。", "muted")
        layout.addWidget(lead)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea { background:transparent; border:0; }")
        content = QWidget()
        sections = QVBoxLayout(content)
        sections.setSizeConstraint(QLayout.SetMinAndMaxSize)
        sections.setContentsMargins(0, 0, 8, 0)
        sections.setSpacing(14)

        installed_card = card()
        installed_layout = QVBoxLayout(installed_card)
        installed_layout.setContentsMargins(18, 16, 18, 16)
        installed_layout.setSpacing(8)
        self.installed_title = label("已安装", "section")
        installed_layout.addWidget(self.installed_title)
        protected_note = label("官方预置模型可删除并重新下载；项目训练及未知来源权重受保护。", "muted")
        installed_layout.addWidget(protected_note)
        columns = QHBoxLayout()
        columns.addStretch()
        for title, width in (("来源", 86), ("启用", 46), ("操作", 62)):
            heading = label(title, "muted")
            heading.setFixedWidth(width)
            heading.setAlignment(Qt.AlignCenter)
            columns.addWidget(heading)
            columns.addSpacing(12)
        columns.setContentsMargins(0, 0, 2, 0)
        installed_layout.addLayout(columns)
        self.installed_rows = QVBoxLayout()
        self.installed_rows.setSpacing(7)
        installed_layout.addLayout(self.installed_rows)
        sections.addWidget(installed_card)

        download_card = card()
        download_layout = QVBoxLayout(download_card)
        download_layout.setContentsMargins(18, 16, 18, 16)
        download_layout.setSpacing(8)
        download_layout.addWidget(label("支持安装", "section"))
        note = label("仅点击下载时联网；模型文件来自 Ultralytics 官方发布页。", "muted")
        note.setWordWrap(True)
        download_layout.addWidget(note)
        self.download_rows = QVBoxLayout()
        self.download_rows.setSpacing(7)
        download_layout.addLayout(self.download_rows)
        sections.addWidget(download_card)

        import_card = card()
        import_layout = QHBoxLayout(import_card)
        import_layout.setContentsMargins(18, 16, 18, 16)
        import_layout.setSpacing(14)
        copy = QVBoxLayout()
        copy.setSpacing(5)
        copy.addWidget(label("导入自定义模型", "section"))
        detail = label("仅支持可信来源的 Ultralytics YOLO 目标检测 .pt 权重；分割、姿态、分类和 ONNX 暂不支持。", "muted")
        detail.setWordWrap(True)
        copy.addWidget(detail)
        import_layout.addLayout(copy, 1)
        self.import_button = button("导入 .pt 模型")
        self.import_button.clicked.connect(self._choose_import)
        import_layout.addWidget(self.import_button)
        sections.addWidget(import_card)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

        progress_row = QHBoxLayout()
        self.status = label("", "muted")
        progress_row.addWidget(self.status, 1)
        self.cancel_button = button("取消下载")
        self.cancel_button.clicked.connect(self._cancel_download)
        self.cancel_button.setVisible(False)
        progress_row.addWidget(self.cancel_button)
        layout.addLayout(progress_row)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.task_progress.connect(self.progress.setValue)
        self.task_done.connect(self._on_task_done)
        self.task_error.connect(self._on_task_error)
        self.refresh()

    @staticmethod
    def _clear_rows(rows):
        while rows.count():
            item = rows.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    def _base_row(self, title, detail):
        row = QFrame()
        row.setObjectName("modelRow")
        row.setFixedHeight(68)
        line = QHBoxLayout(row)
        line.setContentsMargins(14, 11, 14, 11)
        line.setSpacing(12)
        copy = QVBoxLayout()
        copy.setSpacing(3)
        name = label(title)
        name.setStyleSheet("font-size:13px;font-weight:700;")
        copy.addWidget(name)
        subtitle = label(detail, "muted")
        subtitle.setWordWrap(True)
        copy.addWidget(subtitle)
        line.addLayout(copy, 1)
        return row, line

    def _download_row(self, title, detail, origin, action_text, action, *, enabled=True):
        row, line = self._base_row(title, detail)
        badge = label(origin, "modelBadge")
        badge.setAlignment(Qt.AlignCenter)
        badge.setFixedWidth(86)
        line.addWidget(badge)
        action_button = button(action_text)
        action_button.setFixedWidth(62)
        action_button.setEnabled(enabled and not self._busy())
        action_button.clicked.connect(action)
        line.addWidget(action_button)
        self._action_buttons.append(action_button)
        return row

    def _installed_row(self, model, enabled):
        detail = f"{model.detail}  ·  {_size_text(model.path)}  ·  {Path(model.path).name}"
        row, line = self._base_row(model.title, detail)
        badge = label(model.origin, "modelBadge")
        badge.setFixedWidth(86)
        badge.setAlignment(Qt.AlignCenter)
        line.addWidget(badge)
        switch = ModelSwitch(row)
        switch.setChecked(enabled)
        switch.setEnabled(bool(self.window.project) and not self._busy())
        if not self.window.project:
            switch.setToolTip("打开项目后可设置此模型是否出现在自动标注页")
        switch.toggled.connect(lambda checked, item=model: self._toggle(item, checked))
        line.addWidget(switch)
        self._action_buttons.append(switch)
        delete = destructive_button("删除")
        delete.setFixedWidth(62)
        managed = self.library.can_remove(model)
        delete.setEnabled(managed and not self._busy())
        delete.setToolTip("删除本地权重；官方版本仍可在下方重新下载" if managed else "此权重没有可确认的安全恢复路径，暂不支持删除")
        delete.clicked.connect(lambda checked=False, item=model: self._delete(item))
        line.addWidget(delete)
        self._action_buttons.append(delete)
        return row

    def _busy(self):
        return bool(self._worker_thread and self._worker_thread.is_alive())

    def refresh(self):
        self._clear_rows(self.installed_rows)
        self._clear_rows(self.download_rows)
        self._action_buttons = []
        models = self.library.installed(self.window.project)
        self.installed_title.setText(f"已安装 · {len(models)}")
        self.location.setToolTip(str(self.library.root))
        if not models:
            self.installed_rows.addWidget(label("尚无可用模型；可从下方下载或导入。", "muted"))
        for model in models:
            self.installed_rows.addWidget(self._installed_row(model, self.library.is_enabled(self.window.project, model)))
        available = [offer for offer in DOWNLOADABLE_MODELS
                     if not any(Path(item.path).name == offer.filename and item.provider_id == offer.provider_id
                                for item in models)]
        if not available:
            self.download_rows.addWidget(label("所有支持的官方模型均已安装。", "muted"))
        for offer in available:
            row = self._download_row(
                offer.title, offer.detail, "官方版本", "下载",
                lambda checked=False, item=offer: self._start_download(item),
            )
            self.download_rows.addWidget(row)
        self.import_button.setEnabled(not self._busy())

    def _toggle(self, model, enabled):
        if not self.window.project:
            return
        if self.window.auto._worker_thread and self.window.auto._worker_thread.is_alive():
            self.status.setText("请等待自动标注完成后再切换模型。")
            self.refresh()
            return
        self.library.set_enabled(self.window.project, model, enabled)
        self.window.auto._refresh_model_choices()
        self.window.auto.persist_selected_model()
        self.window.auto.set_active_workflow(self.window.active_workflow_id)
        self.status.setText(f"{model.title} 已{'启用' if enabled else '停用'}")
        self.refresh()

    def _delete(self, model):
        if self._busy() or (self.window.auto._worker_thread and self.window.auto._worker_thread.is_alive()):
            self.status.setText("请等待模型任务或自动标注完成后再删除。")
            return
        if not ios_alert(self, "删除本地模型", f"将从本机删除“{model.title}”的权重文件。\n{model.path}\n\n项目图片和标注不会删除；其他项目若使用此模型，需重新选择。", "删除模型", "取消", destructive=True):
            return
        try:
            self.library.remove(model)
            if self.window.project:
                self.library.set_enabled(self.window.project, model, True)
            self.window.auto._refresh_model_choices()
            self.window.auto.persist_selected_model()
            self.window.auto.set_active_workflow(self.window.active_workflow_id)
            self.status.setText(f"已删除本地模型：{model.title}")
            self.refresh()
        except (OSError, ValueError) as error:
            ios_alert(self, "删除失败", str(error), confirm_text="知道了", destructive=True)

    def _choose_import(self):
        source, _ = QFileDialog.getOpenFileName(self, "导入 YOLO 检测模型", str(Path.home()), "YOLO 检测权重 (*.pt)")
        if source:
            self._start_task("正在校验并导入模型…", lambda: self.library.import_checkpoint(source), download=False)

    def _start_download(self, offer):
        self._start_task(
            f"正在下载 {offer.title}…", 
            lambda: self.library.download(offer, self.task_progress.emit, self._cancel_event.is_set),
            download=True,
        )

    def _start_task(self, message, task, *, download):
        if self._busy():
            return
        self._cancel_event.clear()
        self.status.setText(message)
        self.progress.setValue(0)
        self.progress.setVisible(download)
        self.cancel_button.setVisible(download)
        for action in self._action_buttons:
            action.setEnabled(False)
        self.import_button.setEnabled(False)

        def run():
            try:
                path = task()
                self.task_done.emit("模型已就绪", str(path))
            except Exception as error:
                self.task_error.emit(str(error))

        self._worker_thread = threading.Thread(target=run, daemon=True)
        self._worker_thread.start()

    def _cancel_download(self):
        self._cancel_event.set()
        self.cancel_button.setEnabled(False)
        self.status.setText("正在取消下载…")

    def _on_task_done(self, _message, path):
        self._worker_thread = None
        self.progress.setVisible(False)
        self.cancel_button.setVisible(False)
        self.cancel_button.setEnabled(True)
        self.status.setText(f"模型已就绪：{Path(path).name}")
        self.window.auto._refresh_model_choices()
        self.refresh()

    def _on_task_error(self, error):
        self._worker_thread = None
        self.progress.setVisible(False)
        self.cancel_button.setVisible(False)
        self.cancel_button.setEnabled(True)
        self.status.setText("操作未完成")
        self.refresh()
        ios_alert(self, "模型操作失败", error, confirm_text="知道了", destructive=True)
