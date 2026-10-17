"""Project-scoped workflow editor with validated draft graphs."""
from __future__ import annotations

from dataclasses import replace

from application import QuickAnnotationSettings, RecoveryPolicy, WorkflowComposerSettings, WorkflowTemplateRegistry
from application.workflow_templates import WorkflowGraph
from ..ui import *
from ..workflow_composer import WorkflowComposer


class WorkflowPage(QWidget):
    workflow_changed = Signal(str)

    def __init__(self, window):
        super().__init__()
        self.window = window
        self.registry = WorkflowTemplateRegistry()
        self.project_state = WorkflowComposerSettings.load(None)
        self.active_template_id = "quick"
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._save_state)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 24)
        layout.setSpacing(14)
        header = QHBoxLayout()
        back = button("‹ 返回项目")
        back.clicked.connect(lambda: self.window.show_page("auto"))
        header.addWidget(back)
        header.addSpacing(10)
        header.addWidget(label("项目工作流", "title"))
        header.addStretch()
        self.applied_badge = label("已应用：快速标注", "muted")
        header.addWidget(self.applied_badge)
        layout.addLayout(header)
        self.summary = label("选择预设或自定义流程；校验通过后应用到当前项目。", "muted")
        layout.addWidget(self.summary)

        content = QHBoxLayout()
        content.setSpacing(14)
        content.addWidget(self._build_library())
        canvas = card()
        canvas_layout = QVBoxLayout(canvas)
        canvas_layout.setContentsMargins(18, 16, 18, 16)
        canvas_layout.setSpacing(10)
        canvas_header = QHBoxLayout()
        canvas_header.addWidget(label("流程画布", "section"))
        canvas_header.addStretch()
        zoom_out = button("−")
        zoom_out.setToolTip("缩小画布")
        zoom_out.clicked.connect(lambda: self.composer.board.zoom_out())
        canvas_header.addWidget(zoom_out)
        self.zoom_value = label("100%", "muted")
        self.zoom_value.setFixedWidth(42)
        self.zoom_value.setAlignment(Qt.AlignCenter)
        canvas_header.addWidget(self.zoom_value)
        zoom_in = button("+")
        zoom_in.setToolTip("放大画布")
        zoom_in.clicked.connect(lambda: self.composer.board.zoom_in())
        canvas_header.addWidget(zoom_in)
        fit = button("适配")
        fit.setToolTip("适配全部流程节点")
        fit.clicked.connect(lambda: self.composer.board.fit_graph())
        canvas_header.addWidget(fit)
        reset = button("重置草稿")
        reset.clicked.connect(self._reset_draft)
        canvas_header.addWidget(reset)
        validate = button("校验流程")
        validate.clicked.connect(self._validate)
        canvas_header.addWidget(validate)
        canvas_layout.addLayout(canvas_header)
        self.composer = WorkflowComposer()
        self.composer.set_preset_chooser_visible(False)
        self.composer.template_selected.connect(self._select_template)
        self.composer.layout_changed.connect(self._layout_changed)
        self.composer.graph_changed.connect(self._graph_changed)
        self.composer.connection_rejected.connect(self._show_errors)
        self.composer.step_selected.connect(self._select_step)
        self.composer.zoom_changed.connect(lambda percent: self.zoom_value.setText(f"{percent}%"))
        canvas_layout.addWidget(self.composer, 1)
        content.addWidget(canvas, 1)
        content.addWidget(self._build_inspector())
        layout.addLayout(content, 1)

    def _build_library(self):
        panel = card()
        panel.setFixedWidth(202)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(9)
        layout.addWidget(label("预设流程", "section"))
        self.preset_buttons = {}
        for template in self.registry.templates():
            if template.id == "custom":
                continue
            item = QToolButton()
            item.setCheckable(True)
            item.setText(template.title)
            item.setToolTip(template.subtitle)
            item.setMinimumHeight(52)
            item.clicked.connect(lambda checked=False, template_id=template.id:
                                 self.composer.select_template(template_id))
            self.preset_buttons[template.id] = item
            layout.addWidget(item)
        layout.addSpacing(9)
        layout.addWidget(label("自定义流程", "section"))
        custom = self.registry.get("custom")
        item = QToolButton()
        item.setCheckable(True)
        item.setText("自定义 · 图像标注")
        item.setToolTip("基于可执行原子操作，自选补扫与边界整理步骤")
        item.setMinimumHeight(52)
        item.clicked.connect(lambda checked=False: self.composer.select_template("custom"))
        self.preset_buttons[custom.id] = item
        layout.addWidget(item)
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet("background:#E7EAF0;")
        layout.addWidget(line)
        layout.addWidget(label("步骤库", "section"))
        self.optional_buttons = {}
        for template in self.registry.templates():
            for step in template.steps:
                if step.optional and step.id not in self.optional_buttons:
                    toggle = QCheckBox(f"启用{step.title}")
                    toggle.setChecked(True)
                    toggle.toggled.connect(lambda checked, step_id=step.id:
                                           self._toggle_optional(step_id, checked))
                    self.optional_buttons[step.id] = toggle
                    layout.addWidget(toggle)
        layout.addStretch()
        tip = label("勾选或屏蔽可选操作；拖动节点与连线编辑受约束的执行图。必经操作不可移除。", "muted")
        tip.setWordWrap(True)
        layout.addWidget(tip)
        return panel

    def _build_inspector(self):
        panel = card()
        panel.setFixedWidth(245)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        layout.addWidget(label("属性", "section"))
        self.template_title = label("快速标注", "section")
        layout.addWidget(self.template_title)
        self.template_detail = label("", "muted")
        self.template_detail.setWordWrap(True)
        layout.addWidget(self.template_detail)
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet("background:#E7EAF0;")
        layout.addWidget(line)
        layout.addWidget(label("选中步骤", "muted"))
        self.step_title = label("未选择", "section")
        layout.addWidget(self.step_title)
        self.step_detail = label("点击卡片查看操作职责。", "muted")
        self.step_detail.setWordWrap(True)
        layout.addWidget(self.step_detail)

        layout.addWidget(label("连线", "section"))
        self.edge_list = FlatComboBox()
        layout.addWidget(self.edge_list)
        unlink = button("删除选中连线")
        unlink.clicked.connect(self._remove_edge)
        layout.addWidget(unlink)
        edge_hint = label("从卡片右侧蓝色端口拖向目标卡片以添加连线。", "muted")
        edge_hint.setWordWrap(True)
        layout.addWidget(edge_hint)

        self.quick_options = QFrame()
        option_layout = QVBoxLayout(self.quick_options)
        option_layout.setContentsMargins(0, 8, 0, 0)
        option_layout.setSpacing(7)
        option_layout.addWidget(label("快速标注选项", "section"))
        option_layout.addWidget(label("召回补扫", "muted"))
        self.recovery_policy = FlatComboBox()
        self.recovery_policy.addItem("空标注自动补扫（推荐）", RecoveryPolicy.EMPTY_ONLY.value)
        self.recovery_policy.addItem("每张图片双模型补扫", RecoveryPolicy.ALL_IMAGES.value)
        self.recovery_policy.currentIndexChanged.connect(self._draft_changed)
        option_layout.addWidget(self.recovery_policy)
        layout.addWidget(self.quick_options)
        layout.addStretch()
        self.apply = button("应用当前流程", primary=True)
        self.apply.clicked.connect(self._apply)
        layout.addWidget(self.apply)
        self.state = label("未保存到项目", "muted")
        self.state.setWordWrap(True)
        layout.addWidget(self.state)
        return panel

    def sync_project(self, root: str | None = None):
        self._save_timer.stop()
        self.project_state = WorkflowComposerSettings.load(self.window.project)
        self.active_template_id = self.project_state.draft_template_id
        self.composer.set_state(self.active_template_id, self.project_state.layouts,
                                self.project_state.drafts)
        settings = QuickAnnotationSettings.load(self.window.project)
        policy = self.project_state.quick_recovery_policy or settings.recovery_policy.value
        index = self.recovery_policy.findData(policy)
        self.recovery_policy.blockSignals(True)
        self.recovery_policy.setCurrentIndex(index if index >= 0 else 0)
        self.recovery_policy.blockSignals(False)
        self._render_template()
        self._render_applied()
        self.state.setText("草稿已加载" if self.window.project else "打开项目后可配置工作流")
        self.workflow_changed.emit(self.project_state.applied_template_id)

    def _select_template(self, template_id: str):
        self.active_template_id = template_id
        self.project_state.draft_template_id = template_id
        self._render_template()
        self._draft_changed()

    def _render_template(self):
        template = self.registry.get(self.active_template_id)
        graph = self.composer.board.graph
        for key, item in self.preset_buttons.items():
            selected = key == template.id
            item.blockSignals(True)
            item.setChecked(selected)
            item.blockSignals(False)
            item.setStyleSheet(
                "QToolButton { background:#EAF3FF; border:1px solid #9EC7FF; border-radius:10px; color:#0A74FF; font-weight:700; padding:7px; }"
                if selected else
                "QToolButton { background:#FFFFFF; border:1px solid #DDE2E8; border-radius:10px; color:#4E5560; padding:7px; }"
            )
        self.template_title.setText(template.title)
        self.template_detail.setText(template.subtitle)
        self.quick_options.setVisible(template.id in ("quick", "custom"))
        self.apply.setEnabled(template.executable and bool(self.window.project))
        self.summary.setText(
            "正在编辑可执行图像标注草稿；校验并应用后才会影响自动标注。"
            if template.executable else "自有模型预设保留编排接口；执行器尚未接入，暂不能应用。"
        )
        available = {step.id for step in template.steps if step.optional}
        for key, toggle in self.optional_buttons.items():
            toggle.setVisible(key in available)
            toggle.blockSignals(True)
            toggle.setChecked(key in graph.nodes)
            toggle.blockSignals(False)
        self.recovery_policy.setEnabled("recovery_model" in graph.nodes)
        self._render_edges()

    def _render_applied(self):
        title = self.registry.get(self.project_state.applied_template_id).title
        self.applied_badge.setText(f"已应用：{title}")
        if hasattr(self.window, "project_workflow"):
            self.window.project_workflow.setText(f"当前工作流 · {title}  ›")

    def _render_edges(self):
        graph = self.composer.board.graph
        names = {step.id: step.title for step in self.registry.get(self.active_template_id).steps}
        self.edge_list.clear()
        for source, target in graph.edges:
            self.edge_list.addItem(f"{names[source]} → {names[target]}", (source, target))

    def _layout_changed(self, template_id, layouts):
        self.project_state.layouts = layouts
        self._draft_changed()

    def _graph_changed(self, template_id: str, graph: WorkflowGraph):
        self.project_state.drafts[template_id] = graph
        self._render_template()
        self._draft_changed()

    def _select_step(self, step_id: str):
        step = next((item for item in self.registry.get(self.active_template_id).steps
                     if item.id == step_id), None)
        if step:
            self.step_title.setText(step.title + (" · 可选" if step.optional else ""))
            self.step_detail.setText(step.detail)

    def _toggle_optional(self, step_id: str, enabled: bool):
        self.composer.set_optional_enabled(step_id, enabled)

    def _remove_edge(self):
        edge = self.edge_list.currentData()
        if edge:
            errors = self.composer.remove_edge(*edge)
            if errors:
                self._show_errors("；".join(errors))

    def _reset_draft(self):
        template = self.registry.get(self.active_template_id)
        self.project_state.layouts.pop(template.id, None)
        self.project_state.drafts.pop(template.id, None)
        self.composer.set_state(template.id, self.project_state.layouts,
                                self.project_state.drafts)
        self._render_template()
        self._draft_changed()

    def _validate(self):
        errors = self.composer.board.graph.validate(self.registry.get(self.active_template_id))
        if errors:
            self._show_errors("；".join(errors))
        else:
            self.state.setText("校验通过：无循环依赖，必经步骤与可选分支完整")
        return not errors

    def _show_errors(self, message: str):
        self.state.setText(f"校验失败：{message}")
        self.window.status_message(message)

    def _draft_changed(self, *_):
        self.project_state.quick_recovery_policy = self.recovery_policy.currentData()
        self.state.setText("草稿已修改，尚未应用")
        if self.window.project:
            self._save_timer.start(450)

    def _apply(self):
        if not self.window.project or not self.registry.get(self.active_template_id).executable:
            return
        worker = self.window.auto._worker_thread
        if worker and worker.is_alive():
            self.state.setText("当前批次仍在运行，请在任务结束后应用新流程")
            return
        if not self._validate():
            return
        graph = self.composer.board.graph
        self.project_state.drafts[self.active_template_id] = graph
        self.project_state.applied_template_id = self.active_template_id
        self.project_state.applied_graph = graph
        settings = QuickAnnotationSettings.load(self.window.project)
        policy = (RecoveryPolicy(self.recovery_policy.currentData() or RecoveryPolicy.EMPTY_ONLY.value)
                  if "recovery_model" in graph.nodes else RecoveryPolicy.OFF)
        QuickAnnotationSettings.save(self.window.project, replace(settings, recovery_policy=policy))
        self._save_timer.stop()
        self._save_state()
        self._render_applied()
        self.workflow_changed.emit(self.active_template_id)
        self.state.setText("流程已应用到当前项目")
        self.window.status_message(f"已应用{self.registry.get(self.active_template_id).title}工作流")

    def _save_state(self):
        if self.window.project:
            WorkflowComposerSettings.save(self.window.project, self.project_state)

    def flush_draft(self):
        if self._save_timer.isActive():
            self._save_timer.stop()
            self._save_state()
