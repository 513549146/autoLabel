"""Dataset export and model fine-tuning page."""
from __future__ import annotations

from application import ImageTrainingService
from ..ui import *

class ExportPage(QWidget):
    log_message = Signal(str)
    training_progress = Signal(int, int, str)
    operation_finished = Signal(str, object)
    operation_failed = Signal(str)

    def __init__(self, window):
        super().__init__(); self.window = window; self._worker_thread = None; self._data_yaml = ""; self._last_export_id = 0
        self._training_service = ImageTrainingService()
        layout = QVBoxLayout(self); layout.setContentsMargins(24, 24, 24, 24); layout.setSpacing(14)
        header = QHBoxLayout(); header.addWidget(label("模型微调", "title")); header.addStretch(); layout.addLayout(header); layout.addWidget(label("闭环流程：已通过 → 导出标准 YOLO 数据集 → 后台微调 → 项目 models/ 保存版本化权重。", "muted"))
        stages = QHBoxLayout(); stages.setSpacing(14); stages.addWidget(self._export_card(), 1); stages.addWidget(self._train_card(), 1); layout.addLayout(stages)
        log_card = card(); log_layout = QVBoxLayout(log_card); log_layout.setContentsMargins(20, 20, 20, 14); log_layout.addWidget(label("运行记录", "section")); self.log = QPlainTextEdit(); self.log.setReadOnly(True); self.log.setPlaceholderText("导出与训练的运行记录会显示在这里"); self.log.document().setMaximumBlockCount(500); log_layout.addWidget(self.log); log_actions = QHBoxLayout(); log_actions.addStretch(); clear_log = button("清空日志"); clear_log.setToolTip("清空当前导出与训练运行记录"); clear_log.clicked.connect(self._clear_log); log_actions.addWidget(clear_log); log_layout.addLayout(log_actions); layout.addWidget(log_card, 1)
        self.log_message.connect(self._append_log)
        self.training_progress.connect(self._on_training_progress)
        self.operation_finished.connect(self._operation_complete)
        self.operation_failed.connect(self._operation_error)

    def _export_card(self):
        surface = card(); grid = QGridLayout(surface); grid.setContentsMargins(20, 20, 20, 20); grid.setSpacing(12); grid.addWidget(label("1 · 导出数据集", "section"), 0, 0, 1, 2); grid.addWidget(label("仅导出“已通过 · 训练候选”的图片与 XML 标注。", "muted"), 1, 0, 1, 2); grid.addWidget(label("输出目录", "muted"), 2, 0); self.out = QLineEdit(); self.out.setReadOnly(True); self.out.setPlaceholderText("打开项目后自动设为项目内 yolo_dataset 文件夹"); grid.addWidget(self.out, 2, 1); grid.addWidget(label("验证集比例", "muted"), 3, 0); self.ratio = QLineEdit("0.2"); grid.addWidget(self.ratio, 3, 1); self.export_status = label("尚未导出", "muted"); grid.addWidget(self.export_status, 4, 0, 1, 2); self.export_run = button("导出数据集", primary=True); self.export_run.clicked.connect(self._start_export); grid.addWidget(self.export_run, 5, 1); return surface

    def sync_project(self, project_root):
        self.out.setText(os.path.join(project_root, "yolo_dataset") if project_root else "")
        self._data_yaml = os.path.join(project_root, "yolo_dataset", "data.yaml") if project_root else ""
        ready = bool(self._data_yaml and os.path.isfile(self._data_yaml))
        exports = self.window.project.recent_dataset_exports(1) if self.window.project else []
        self._last_export_id = exports[0]["id"] if exports else 0
        self.export_status.setText("已找到 data.yaml，可开始训练" if ready else "尚未导出")
        self.train_run.setEnabled(ready and not self._is_busy())
        self.export_run.setEnabled(bool(project_root) and not self._is_busy())
        runs = getattr(self.window.project, "recent_training_runs", lambda _limit: [])(1) if self.window.project else []
        self.use_latest.setEnabled(bool(runs and os.path.isfile(runs[0]["weights_path"]) and not self._is_busy()))

    def _train_card(self):
        surface = card(); grid = QGridLayout(surface); grid.setContentsMargins(20, 20, 20, 20); grid.setSpacing(12); grid.addWidget(label("2 · 微调训练", "section"), 0, 0, 1, 2); grid.addWidget(label("训练在后台运行；每次成功训练都会保留独立的模型版本。", "muted"), 1, 0, 1, 2); grid.addWidget(label("预训练模型", "muted"), 2, 0); self.train_model = FlatComboBox(); self.train_model.addItems(["yolov8n.pt", "yolov8s.pt", "yolo11n.pt"]); grid.addWidget(self.train_model, 2, 1); grid.addWidget(label("训练轮数", "muted"), 3, 0); self.epochs = QLineEdit("100"); grid.addWidget(self.epochs, 3, 1); self.train_status = label("请先导出数据集", "muted"); grid.addWidget(self.train_status, 4, 0, 1, 2); self.train_run = button("开始训练", primary=True); self.train_run.clicked.connect(self._start_training); self.train_run.setEnabled(False); grid.addWidget(self.train_run, 5, 1); self.use_latest = button("用最近模型预标注"); self.use_latest.clicked.connect(self._open_latest_model_in_auto); self.use_latest.setEnabled(False); grid.addWidget(self.use_latest, 6, 1); return surface

    def _is_busy(self):
        return bool(self._worker_thread and self._worker_thread.is_alive())

    def _start_export(self):
        if self._is_busy() or not self.window.project:
            return
        files = self.window.project_images(STATUS_APPROVED)
        if not files:
            ios_alert(self, "没有训练候选", "请先在审核页将至少一张图片标记为“通过”。")
            return
        try:
            ratio = float(self.ratio.text().strip())
        except ValueError:
            ios_alert(self, "验证集比例无效", "请输入 0 到 0.9 之间的数字，例如 0.2。", confirm_text="知道了", destructive=True)
            return
        output = self.out.text()
        if os.path.isdir(output) and os.listdir(output):
            if not ios_alert(self, "覆盖训练集", "项目内已有导出数据集。继续会用本次已通过结果完整替换它。", "覆盖并导出", "取消", destructive=True):
                return
        self._set_busy(True)
        categories = list(self.window.project.categories)
        self.log_message.emit(f"开始导出 {len(files)} 张训练候选图片…")
        self._worker_thread = threading.Thread(target=self._run_export, args=(files, categories, ratio, output), daemon=True)
        self._worker_thread.start()

    def _run_export(self, files, categories, ratio, output):
        try:
            summary = self._training_service.export_dataset(self.window.images_dir, self.window.annotations_dir, files, output, categories, ratio)
            self.operation_finished.emit("export", summary)
        except Exception as error:
            self.operation_failed.emit(f"导出失败：{error}")

    def _start_training(self):
        if self._is_busy() or not self.window.project:
            return
        data_yaml = self._data_yaml or os.path.join(self.out.text(), "data.yaml")
        if not os.path.isfile(data_yaml):
            ios_alert(self, "请先导出数据集", "当前项目中未找到 data.yaml。请先完成第 1 步。")
            return
        try:
            epochs = int(self.epochs.text().strip())
            if epochs < 1:
                raise ValueError
        except ValueError:
            ios_alert(self, "训练轮数无效", "请输入大于 0 的整数。", confirm_text="知道了", destructive=True)
            return
        self._set_busy(True)
        model, runs_dir = self.train_model.currentText(), os.path.join(self.window.project_root, "training_runs")
        self.log_message.emit(f"开始微调：{model} · {epochs} 轮。训练可持续数分钟，请保持应用打开。")
        self._worker_thread = threading.Thread(target=self._run_training, args=(data_yaml, model, epochs, runs_dir), daemon=True)
        self._worker_thread.start()

    def _run_training(self, data_yaml, model, epochs, runs_dir):
        try:
            result = self._training_service.train(
                data_yaml, model, runs_dir, epochs,
                on_progress=lambda current, total, detail: self.training_progress.emit(current, total, detail),
            )
            models_dir = os.path.join(self.window.project_root, "models")
            os.makedirs(models_dir, exist_ok=True)
            target = os.path.join(models_dir, f"{Path(result['run_dir']).name}-best.pt")
            shutil.copy2(result["best_weights"], target)
            result["project_weights"] = target; result["model"] = model; result["epochs"] = epochs
            self.operation_finished.emit("train", result)
        except Exception as error:
            self.operation_failed.emit(f"训练失败：{error}")

    def _operation_complete(self, operation, result):
        self._worker_thread = None
        if operation == "export":
            self._data_yaml = result["data_yaml"]
            if self.window.project:
                self._last_export_id = self.window.project.record_dataset_export(result["root"], result["data_yaml"], result["train_images"], result["val_images"], result["objects"], result["classes"], result["validation_ratio"])
            self.export_status.setText(f"已导出：训练 {result['train_images']} 张 · 验证 {result['val_images']} 张 · {len(result['classes'])} 类")
            self.train_status.setText("数据集已就绪，可开始训练")
            self.log.appendPlainText(f"导出完成：{result['objects']} 个目标，data.yaml 已写入项目 yolo_dataset/。")
        else:
            if self.window.project:
                self.window.project.record_training_run(self._last_export_id or None, result["run_dir"], result["project_weights"], result["model"], result["epochs"])
                self.window.auto.activate_trained_model(result["project_weights"])
            self.train_status.setText("训练完成；最近版本已设为自动标注默认模型")
            self._append_log(f"训练完成：{result['project_weights']}（已保留为独立历史版本，并设为自动标注默认模型）")
        self._set_busy(False)

    def _operation_error(self, message):
        self._worker_thread = None
        self._set_busy(False)
        self.log.appendPlainText(message)
        ios_alert(self, "任务未完成", message, confirm_text="知道了", destructive=True)

    def _set_busy(self, busy):
        self.export_run.setEnabled(bool(self.window.project_root) and not busy)
        self.train_run.setEnabled(bool(self._data_yaml and os.path.isfile(self._data_yaml)) and not busy)
        self.ratio.setEnabled(not busy); self.train_model.setEnabled(not busy); self.epochs.setEnabled(not busy)
        runs = getattr(self.window.project, "recent_training_runs", lambda _limit: [])(1) if self.window.project else []
        self.use_latest.setEnabled(bool(runs and os.path.isfile(runs[0]["weights_path"]) and not busy))

    def _append_log(self, message):
        self.log.appendPlainText(str(message))
        scrollbar = self.log.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _clear_log(self):
        self.log.clear()
        self.window.status_message("已清空导出与训练运行记录")

    def _on_training_progress(self, current, total, detail):
        line = f"Epoch {current}/{total}" + (f" · {detail}" if detail else "")
        self.train_status.setText(f"训练中 · {current} / {total} epochs")
        self._append_log(line)

    def _open_latest_model_in_auto(self):
        if not self.window.project:
            return
        runs = self.window.project.recent_training_runs(1)
        if not runs or not os.path.isfile(runs[0]["weights_path"]):
            ios_alert(self, "未找到训练模型", "当前项目没有可用的已训练模型。", confirm_text="知道了")
            return
        self.window.auto.activate_trained_model(runs[0]["weights_path"])
        self.window.show_page("auto")
