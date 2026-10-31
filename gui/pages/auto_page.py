"""Automatic image pre-annotation page."""
from __future__ import annotations

from application import (
    QuickAnnotationConfig,
    QuickAnnotationRequest,
    QuickAnnotationSettings,
    QuickAnnotationWorkflow,
    RecoveryPolicy,
)
from application.workflow_templates import WorkflowComposerSettings, WorkflowTemplateRegistry
from application.model_library import ModelLibrary
from PySide6.QtWidgets import QStyledItemDelegate
from ..ui import *


class CategoryCellDelegate(QStyledItemDelegate):
    """Use a full-cell editor so the model text never shows through it."""

    enter_pressed = Signal(int, int)

    def createEditor(self, parent, option, index):
        editor = QLineEdit(parent)
        editor.setFrame(False)
        editor.setStyleSheet("background:#FFFFFF; border:0; border-radius:0; margin:0; padding:0 10px; color:#1D1D1F;")
        editor.returnPressed.connect(lambda: self.enter_pressed.emit(index.row(), index.column()))
        return editor

    def updateEditorGeometry(self, editor, option, _index):
        editor.setGeometry(option.rect)


class CategoryManagerDialog(QDialog):
    """Project-local category table: English model labels plus review display names."""

    def __init__(self, project, parent=None):
        super().__init__(parent)
        self.project = project
        self.setWindowTitle("类别管理")
        # This document-style editor should not inherit the generic Python/
        # application glyph in its native title bar.
        self.setWindowIcon(QIcon())
        self.setMinimumSize(650, 420)
        layout = QVBoxLayout(self); layout.setContentsMargins(24, 22, 24, 22); layout.setSpacing(14)
        layout.addWidget(label("类别管理", "title"))
        hint = label("英文标签用于模型检测、XML 和训练；中文名称只在审核界面展示。", "muted"); hint.setWordWrap(True); layout.addWidget(hint)
        self.table = QTableWidget(0, 3, self)
        self.table.setHorizontalHeaderLabels(["序号", "英文标签", "中文显示名"])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(44)
        self.table.verticalHeader().setMinimumSectionSize(44)
        self.table.setColumnWidth(0, 62)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(1, 220)
        self.table.setStyleSheet("QTableWidget { border: 1px solid #E3E5E9; border-radius: 10px; gridline-color: #ECEEF2; background: #FFFFFF; selection-background-color:#F1F6FE; selection-color:#1D1D1F; } QHeaderView::section { background:#F6F7F9; border:0; border-bottom:1px solid #E3E5E9; padding:9px; color:#646873; font-weight:600; } QTableWidget::item { padding: 0 10px; } QTableWidget::item:selected { background:#F1F6FE; color:#1D1D1F; }")
        self._delegate = CategoryCellDelegate(self.table)
        self._delegate.enter_pressed.connect(self._advance_after_enter)
        self.table.setItemDelegateForColumn(1, self._delegate)
        self.table.setItemDelegateForColumn(2, self._delegate)
        layout.addWidget(self.table, 1)
        actions = QHBoxLayout(); add = button("添加类别"); add.clicked.connect(self._add_row); remove = button("删除选中"); remove.clicked.connect(self._remove_selected); actions.addWidget(add); actions.addWidget(remove); actions.addStretch(); cancel = button("取消"); cancel.clicked.connect(self.reject); save = button("保存类别", primary=True); save.clicked.connect(self._save); actions.addWidget(cancel); actions.addWidget(save); layout.addLayout(actions)
        for record in project.category_records():
            self._add_row(record.get("name", ""), record.get("display_name", ""), focus=False)
        self._ensure_blank_row()

    def _row_is_empty(self, row):
        return all(not (self.table.item(row, column).text().strip() if self.table.item(row, column) else "") for column in (1, 2))

    def _add_row(self, name="", display_name="", focus=True):
        use_blank_row = not name and not display_name and self.table.rowCount() and self._row_is_empty(self.table.rowCount() - 1)
        row = self.table.rowCount() - 1 if use_blank_row else self.table.rowCount()
        if not use_blank_row:
            self.table.insertRow(row)
        self.table.setRowHeight(row, 44)
        number = QTableWidgetItem(str(row + 1)); number.setFlags(Qt.ItemIsEnabled)
        self.table.setItem(row, 0, number)
        self.table.setItem(row, 1, QTableWidgetItem(str(name)))
        self.table.setItem(row, 2, QTableWidgetItem(str(display_name)))
        if focus:
            self._edit_cell(row, 1)

    def _ensure_blank_row(self):
        if not self.table.rowCount() or not self._row_is_empty(self.table.rowCount() - 1):
            self._add_row(focus=False)

    def _edit_cell(self, row, column):
        self.table.setCurrentCell(row, column)
        item = self.table.item(row, column)
        if item:
            self.table.editItem(item)

    def _advance_after_enter(self, row, column):
        """Enter moves through the grid and grows the trailing blank row."""
        def move_to_next_cell():
            if column == 1:
                self._edit_cell(row, 2)
                return
            if row == self.table.rowCount() - 1:
                self._ensure_blank_row()
            self._edit_cell(min(row + 1, self.table.rowCount() - 1), 1)
        QTimer.singleShot(0, move_to_next_cell)

    def _remove_selected(self):
        rows = sorted({index.row() for index in self.table.selectedIndexes()}, reverse=True)
        for row in rows:
            self.table.removeRow(row)
        for row in range(self.table.rowCount()):
            self.table.item(row, 0).setText(str(row + 1))
        self._ensure_blank_row()

    def _save(self):
        records = []
        seen = set(); display_seen = set()
        for row in range(self.table.rowCount()):
            name_item, display_item = self.table.item(row, 1), self.table.item(row, 2)
            name = name_item.text().strip() if name_item else ""
            display_name = display_item.text().strip() if display_item else ""
            if not name:
                continue
            if name.casefold() in seen:
                ios_alert(self, "英文标签重复", f"“{name}”已存在。每个英文标签只能保留一行。", confirm_text="知道了", destructive=True)
                return
            display_name = display_name or name
            if display_name.casefold() in display_seen:
                ios_alert(self, "中文显示名重复", f"“{display_name}”已存在。每个中文显示名只能保留一行。", confirm_text="知道了", destructive=True)
                return
            seen.add(name.casefold())
            display_seen.add(display_name.casefold())
            records.append({"name": name, "display_name": display_name})
        self.project.set_category_records(records)
        self.accept()


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
        self._quick_workflow = QuickAnnotationWorkflow()
        self._active_template_id = "quick"
        layout = QVBoxLayout(self); layout.setContentsMargins(24, 24, 24, 24); layout.setSpacing(14)
        header = QHBoxLayout(); header.addWidget(label("自动标注", "title")); header.addStretch()
        self.model_manager_button = button("模型管理")
        self.model_manager_button.setObjectName("modelManagerLink")
        self.model_manager_button.setToolTip("查看本机模型、下载官方模型或导入兼容权重")
        self.model_manager_button.clicked.connect(lambda: self.window.show_page("model_manager"))
        header.addWidget(self.model_manager_button)
        layout.addLayout(header)
        # Kept as a non-visual compatibility value for workflow integrations;
        # the user-facing state now lives in the lower-right footer.
        self.project_summary = label("", "muted")
        data = card(); grid = QGridLayout(data); grid.setContentsMargins(22, 20, 22, 20); grid.setHorizontalSpacing(14); grid.setVerticalSpacing(12)
        grid.addWidget(label("项目数据", "section"), 0, 0, 1, 3); grid.addWidget(label("项目内图片", "muted"), 1, 0)
        self.images = QLineEdit(); self.images.setReadOnly(True); self.images.setPlaceholderText("新建项目后，图片将保存在这里"); grid.addWidget(self.images, 1, 1); self.import_images = button("导入图片"); self.import_images.clicked.connect(self._import_images); grid.addWidget(self.import_images, 1, 2)
        grid.addWidget(label("项目内标注", "muted"), 2, 0); self.annotations = QLineEdit(); self.annotations.setReadOnly(True); self.annotations.setPlaceholderText("与图片一起保存的 XML 标注"); grid.addWidget(self.annotations, 2, 1); self.import_annotations = button("导入标注"); self.import_annotations.clicked.connect(self._import_annotations); grid.addWidget(self.import_annotations, 2, 2); layout.addWidget(data)
        self.quick_setup = card(); grid = QGridLayout(self.quick_setup); grid.setContentsMargins(22, 20, 22, 20); grid.setHorizontalSpacing(14); grid.setVerticalSpacing(12)
        self.setup_title = label("图像标注配置", "section"); grid.addWidget(self.setup_title, 0, 0, 1, 3); grid.addWidget(label("主检测模型", "muted"), 1, 0); self.model = FlatComboBox(); self._refresh_model_choices(); self.model.setToolTip("项目训练模型会保留历史版本；最近一次成功训练的模型会自动设为默认。") ; grid.addWidget(self.model, 1, 1, 1, 2)
        grid.addWidget(label("类别表", "muted"), 2, 0); self.category_summary = label("打开项目后管理英文标签与中文审核显示名", "muted"); self.category_summary.setWordWrap(True); grid.addWidget(self.category_summary, 2, 1); categories = button("管理类别"); categories.clicked.connect(self._categories); grid.addWidget(categories, 2, 2)
        grid.addWidget(label("候选最低置信度", "muted"), 3, 0); confidence_host = QWidget(); confidence_layout = QHBoxLayout(confidence_host); confidence_layout.setContentsMargins(0, 0, 0, 0); confidence_layout.setSpacing(10); self.confidence = QSlider(Qt.Horizontal); self.confidence.setRange(10, 95); self.confidence.setValue(round(config.HIGH_RECALL_THRESHOLD * 100)); self.confidence.setToolTip("高召回默认 10%；降低阈值会保留更多待人工审核的候选框，不能保证零漏检。"); self.confidence_value = label("", "muted"); self.confidence_value.setFixedWidth(38); self.confidence_value.setAlignment(Qt.AlignRight | Qt.AlignVCenter); confidence_layout.addWidget(self.confidence, 1); confidence_layout.addWidget(self.confidence_value); grid.addWidget(confidence_host, 3, 1, 1, 2); self.confidence.valueChanged.connect(self._update_confidence_label); self._update_confidence_label(self.confidence.value())
        grid.addWidget(label("处理范围", "muted"), 4, 0); self.run_scope = FlatComboBox(); self.run_scope.addItem("仅未标注图片（安全）", "new"); self.run_scope.addItem("风险队列（高风险优先）", "risk"); self.run_scope.addItem("所有待审核图片", "review"); self.run_scope.addItem("全部图片（重新预标注）", "all"); self.run_scope.setToolTip("重新预标注会先保留当前 XML 版本；“全部图片”完成后会将已通过图片退回待审核"); grid.addWidget(self.run_scope, 4, 1, 1, 2); layout.addWidget(self.quick_setup)
        actions = QHBoxLayout(); self.start = button("开始快速标注", primary=True); self.start.clicked.connect(self._start_labeling); actions.addWidget(self.start); self.stop = button("停止"); self.stop.clicked.connect(self._stop_labeling); self.stop.setEnabled(False); actions.addWidget(self.stop); self.strategy_hint = label("默认全图双模型补扫；大图切片补扫。", "muted"); actions.addWidget(self.strategy_hint); actions.addStretch(); layout.addLayout(actions)
        self.progress = QProgressBar(); self.progress.setTextVisible(False); self.progress.setValue(0); layout.addWidget(self.progress)
        log_card = card(); log_layout = QVBoxLayout(log_card); log_layout.setContentsMargins(16, 14, 16, 12); log_layout.setSpacing(8)
        self.log = QPlainTextEdit(); self.log.setReadOnly(True); self.log.setPlaceholderText("运行记录会显示在这里"); self.log.setMinimumHeight(140); self.log.document().setMaximumBlockCount(500); log_layout.addWidget(self.log, 1)
        log_actions = QHBoxLayout(); log_actions.addStretch(); clear_log = button("清空日志"); clear_log.setToolTip("清空当前自动标注运行记录"); clear_log.clicked.connect(self._clear_log); log_actions.addWidget(clear_log); log_layout.addLayout(log_actions); layout.addWidget(log_card, 1)
        status_footer = QHBoxLayout(); status_footer.setContentsMargins(0, 0, 0, 0); status_footer.addStretch(); self.workflow_status = label("", "muted"); self.workflow_status.setFixedWidth(460); self.workflow_status.setAlignment(Qt.AlignRight | Qt.AlignVCenter); self.workflow_status.setWordWrap(False); status_footer.addWidget(self.workflow_status); layout.addLayout(status_footer)
        self.log_message.connect(self._append_log)
        self.progress_changed.connect(self.progress.setValue)
        self.batch_finished.connect(self._on_label_finished)
        self.batch_failed.connect(self._on_label_failed)

    def _update_confidence_label(self, value):
        self.confidence_value.setText(f"{int(value)}%")

    def _append_log(self, message):
        self.log.appendPlainText(str(message))
        scrollbar = self.log.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _clear_log(self):
        self.log.clear()
        self.window.status_message("已清空自动标注运行记录")

    def set_active_workflow(self, template_id: str):
        """Reflect the applied project workflow."""
        self._active_template_id = template_id
        template = WorkflowTemplateRegistry().get(template_id)
        executable = template.executable
        busy = bool(self._worker_thread and self._worker_thread.is_alive())
        self.start.setText("开始" + template.title if executable else "自有模型流程待接入")
        self.setup_title.setText(f"{template.title}配置" if executable else "图像标注配置")
        self.start.setEnabled(bool(self.window.project_root) and executable and not busy and self.model.currentData() is not None)
        if self.window.project_root:
            state = WorkflowComposerSettings.load(self.window.project)
            enabled = set(state.applied_graph.nodes)
            options = [step.title for step in template.steps if step.optional and step.id in enabled]
            status = (
                f"当前工作流：{template.title} · " + ("已启用：" + "、".join(options) if options else "可选步骤均已屏蔽")
                if executable else "仅可编辑草稿 · 自有模型执行器尚未接入"
            )
            self.project_summary.setText(f"当前工作流：{template.title}。可从项目卡片打开配置。")
            self.workflow_status.setText(status)
            self.workflow_status.setToolTip(status)
            settings = self._quick_workflow.load_settings(self.window.project)
            dual = "recovery_model" in enabled and settings.recovery_policy == RecoveryPolicy.ALL_IMAGES
            tile_text = "；主模型大图切片补扫" if "tile_scan" in enabled else ""
            self.strategy_hint.setText(
                "全图双模型补扫" + tile_text + "；保留单模型候选供审核。" if dual else
                "当前未启用全图双模型；如需高召回，请在工作流页启用每张图补扫。"
            )
        else:
            self.project_summary.setText("先从左侧三点菜单新建项目或打开项目。")
            self.workflow_status.setText("")
            self.workflow_status.setToolTip("")
            self.strategy_hint.setText("默认全图双模型补扫；切片补扫可在工作流页启用。")

    def _apply_quick_settings(self, settings):
        model_value = ((settings.primary_provider_id, settings.model_weights_path)
                       if settings.model_weights_path else settings.primary_provider_id)
        model_index = self._model_index(model_value)
        self.model.setCurrentIndex(model_index if model_index >= 0 else 0)
        scope_index = self.run_scope.findData(settings.scope)
        self.run_scope.setCurrentIndex(scope_index if scope_index >= 0 else 0)
        self.confidence.blockSignals(True)
        self.confidence.setValue(round(settings.confidence * 100))
        self.confidence.blockSignals(False)
        self._update_confidence_label(self.confidence.value())

    def sync_project(self, root: str | None = None, name: str | None = None):
        active = bool(root and self.window.project_root)
        self.images.setText(self.window.images_dir if active else "")
        self.annotations.setText(self.window.annotations_dir if active else "")
        self.import_images.setEnabled(active); self.import_annotations.setEnabled(active)
        self._refresh_model_choices()
        if active and self.window.project:
            settings = self._quick_workflow.load_settings(self.window.project)
            if not self.window.project.categories and settings.prompt:
                self.window.project.set_categories(core.parse_categories(settings.prompt))
            self._apply_quick_settings(settings)
            self._update_category_summary()
        self.set_active_workflow(getattr(self.window, "active_workflow_id", "quick"))
        if not active:
            self.category_summary.setText("打开项目后管理英文标签与中文审核显示名")

    def _update_category_summary(self):
        records = self.window.project.category_records() if self.window.project else []
        if not records:
            self.category_summary.setText("尚未配置类别；点击“管理类别”添加英文标签和中文显示名")
            return
        preview = "、".join(f"{item['name']} → {item['display_name']}" for item in records[:3])
        more = f" 等 {len(records)} 类" if len(records) > 3 else ""
        self.category_summary.setText(preview + more)

    def _refresh_model_choices(self):
        """Only enabled, installed models are selectable for quick annotation."""
        selected = self.model.currentData() if hasattr(self, "model") else "yoloe"
        self.model.blockSignals(True)
        self.model.clear()
        library = ModelLibrary()
        project = getattr(self.window, "project", None)
        for entry in library.installed(project):
            if entry.provider_id == "sam2":
                continue  # SAM refines masks; it is never a primary detector.
            if not library.is_enabled(project, entry):
                continue
            if entry.provider_id == "yoloe" and os.path.normcase(entry.path) == os.path.normcase(config.YOLOE_WEIGHTS):
                choice = "yoloe"
            elif entry.provider_id in {"gd15", "gd_ogc"}:
                choice = entry.provider_id
            else:
                choice = (entry.provider_id, entry.path)
            source = f"{entry.origin} · " if entry.origin not in {"应用自带"} else ""
            self.model.addItem(f"{source}{entry.title}", choice)
        if self.model.count() == 0:
            self.model.addItem("请在模型管理中启用模型", None)
        index = self._model_index(selected)
        self.model.setCurrentIndex(index if index >= 0 else 0)
        self.model.blockSignals(False)

    def _model_index(self, choice):
        return next((index for index in range(self.model.count()) if self.model.itemData(index) == choice), -1)

    def persist_selected_model(self):
        choice = self.model.currentData()
        if choice is not None and self.window.project:
            self.select_model(choice)

    def select_model(self, choice):
        """Select a managed model and remember it for the current project."""
        if not self.window.project:
            return
        self._refresh_model_choices()
        index = self._model_index(choice)
        if index < 0:
            self.window.status_message("所选模型已不可用，请刷新模型管理页面")
            return
        self.model.setCurrentIndex(index)
        provider_id, weights_path = choice if isinstance(choice, tuple) else (choice, "")
        current = self._quick_workflow.load_settings(self.window.project)
        settings = QuickAnnotationConfig(
            prompt=current.prompt, primary_provider_id=provider_id, model_weights_path=weights_path,
            recovery_provider_id=current.recovery_provider_id, recovery_policy=current.recovery_policy,
            confidence=current.confidence, scope=current.scope, tiled_inference=current.tiled_inference,
        )
        self._quick_workflow.save_settings(self.window.project, settings)
        self.window.status_message(f"当前模型：{self.model.currentText()}")

    def activate_trained_model(self, weights_path):
        """Make a completed project checkpoint the persisted pre-label default."""
        if not self.window.project or not os.path.isfile(weights_path):
            return
        self._refresh_model_choices()
        choice = ("custom_yolo", weights_path)
        index = self._model_index(choice)
        if index >= 0:
            self.model.setCurrentIndex(index)
        current = self._quick_workflow.load_settings(self.window.project)
        settings = QuickAnnotationConfig(
            prompt=current.prompt, primary_provider_id="custom_yolo", model_weights_path=weights_path,
            recovery_provider_id=current.recovery_provider_id, recovery_policy=current.recovery_policy,
            confidence=current.confidence, scope=current.scope,
            tiled_inference=current.tiled_inference,
        )
        self._quick_workflow.save_settings(self.window.project, settings)

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
        if not self.window.project:
            self.window.status_message("请先新建或打开项目")
            return
        dialog = CategoryManagerDialog(self.window.project, self)
        if dialog.exec():
            self._update_category_summary()
            self.window.review.refresh()

    def _start_labeling(self):
        if not WorkflowTemplateRegistry().get(self._active_template_id).executable:
            ios_alert(self, "流程尚未可执行", "自有模型预设目前用于查看与编排。请选择快速标注或自定义流程后开始任务。", confirm_text="知道了")
            return
        if not self.window.project_root:
            self.log.appendPlainText("请先从左侧项目菜单新建、导入或选择最近项目。"); return
        if self._worker_thread and self._worker_thread.is_alive():
            return
        prompt = " . ".join(self.window.project.categories) if self.window.project else ""
        if not prompt:
            self.log.appendPlainText("提示词不能为空。"); return
        saved = QuickAnnotationSettings.load(self.window.project) if self.window.project else QuickAnnotationConfig()
        selected_model = self.model.currentData()
        if selected_model is None:
            self.log.appendPlainText("请先在模型管理中启用一个模型。")
            return
        provider_id, weights_path = selected_model if isinstance(selected_model, tuple) else (selected_model, "")
        settings = QuickAnnotationConfig(
            prompt=prompt,
            primary_provider_id=provider_id,
            model_weights_path=weights_path,
            recovery_provider_id="gd15" if provider_id != "gd15" else "yoloe",
            recovery_policy=saved.recovery_policy,
            confidence=self.confidence.value() / 100,
            scope=self.run_scope.currentData() or "new",
            tiled_inference="tile_scan" in WorkflowComposerSettings.load(self.window.project).applied_graph.nodes,
        )
        request = QuickAnnotationRequest(
            project_root=self.window.project_root,
            images_dir=self.window.images_dir,
            annotations_dir=self.window.annotations_dir,
            config=settings,
        )
        try:
            pending = self._quick_workflow.plan(request)
            settings = settings.normalized()
        except ValueError as error:
            self.log.appendPlainText(f"{WorkflowTemplateRegistry().get(self._active_template_id).title}配置无效：{error}"); return
        if not pending:
            self.log.appendPlainText("当前范围没有需要处理的图片。可切换到“风险队列”或“所有待审核图片”重新预标注。")
            self.window.refresh_project_state(); self.window.show_page("review"); return
        if settings.scope != "new":
            all_images_notice = "\n\n其中原本“已通过”的图片，会在成功写入新结果后退回待审核。" if settings.scope == "all" else ""
            if not ios_alert(self, "确认重新预标注", f"将重新预标注 {len(pending)} 张图片。当前 XML 会先保存为可恢复版本，再写入新的模型结果。{all_images_notice}\n\n是否继续？", "继续", "取消"):
                return
        if self.window.project:
            self._quick_workflow.save_settings(self.window.project, settings)
        self._stop_event.clear(); self.start.setEnabled(False); self.stop.setEnabled(True); self.progress.setValue(0)
        self.log.clear()
        recovery_text = {RecoveryPolicy.EMPTY_ONLY: "空标注自动补扫", RecoveryPolicy.ALL_IMAGES: "每张图片双模型补扫", RecoveryPolicy.OFF: "关闭补扫"}[settings.recovery_policy]
        self.log.appendPlainText(f"准备运行{WorkflowTemplateRegistry().get(self._active_template_id).title}：{len(pending)} 张图片（{self.run_scope.currentText()} · {recovery_text} · 最低置信度 {settings.confidence:.0%}）。")
        self._worker_thread = threading.Thread(
            target=self._run_quick_labeling,
            args=(request.with_filenames(pending),),
            daemon=True,
        )
        self._worker_thread.start()

    def _stop_labeling(self):
        if self._worker_thread and self._worker_thread.is_alive():
            self._stop_event.set(); self.stop.setEnabled(False); self.log.appendPlainText("将在当前图片处理完成后停止。")

    def _run_quick_labeling(self, request: QuickAnnotationRequest):
        try:
            result = self._quick_workflow.run(
                request,
                should_stop=self._stop_event.is_set,
                on_log=self.log_message.emit,
                on_progress=self.progress_changed.emit,
            )
            self.batch_finished.emit(request.project_root, result, result.stopped)
        except ModuleNotFoundError as exc:
            self.batch_failed.emit(f"缺少模型依赖：{exc.name}。请安装 requirements.txt 中的依赖。")
        except Exception as exc:
            self.batch_failed.emit(f"快速标注失败：{exc}")

    def _on_label_finished(self, project_root: str, result, stopped: bool):
        self.start.setEnabled(bool(self.window.project_root) and WorkflowTemplateRegistry().get(self._active_template_id).executable and self.model.currentData() is not None); self.stop.setEnabled(False)
        if project_root != self.window.project_root:
            self.log.appendPlainText("任务完成，但当前已切换到其他项目；结果仍已保存到原项目。")
            return
        completed = result.filenames
        self.window.refresh_all()
        risk_note = f"其中 {result.high_risk_count} 张进入高风险审核队列。" if result.high_risk_count else "均已完成风险分流。"
        failure_note = f"另有 {len(result.failed)} 张失败，原 XML 未被替换；请查看上方 [失败] 日志后重试。" if result.failed else ""
        self.log.appendPlainText(f"图像标注结束：{len(completed)} 张已写入项目并进入待审核；{risk_note}{failure_note}")
        if result.failed:
            self.window.status_message(f"快速标注有 {len(result.failed)} 张失败；请查看运行记录")
        if completed:
            self.window.show_page("review")

    def _on_label_failed(self, message: str):
        self.start.setEnabled(bool(self.window.project_root) and WorkflowTemplateRegistry().get(self._active_template_id).executable and self.model.currentData() is not None); self.stop.setEnabled(False)
        self.log.appendPlainText(message)
        ios_alert(self, "自动标注失败", message, confirm_text="知道了", destructive=True)
