"""Dataset export and model fine-tuning page."""
from __future__ import annotations

import multiprocessing
import threading
import time

import psutil

from application import ImageTrainingService
from application.training_worker import run_training_process
from ..ui import *

class ExportPage(QWidget):
    """模型微调页：左侧导出已通过数据，右侧可选地在独立进程中训练。"""
    log_message = Signal(str)
    training_progress = Signal(int, int, str)
    training_stage = Signal(str)
    training_batch = Signal(int, int, int, int)
    operation_finished = Signal(str, object)
    operation_failed = Signal(str)
    operation_cancelled = Signal(str)

    def __init__(self, window):
        """构建导出与训练两张卡片、日志区和训练轮询定时器，并连接全部信号。"""
        super().__init__(); self.window = window; self._worker_thread = None; self._data_yaml = ""; self._last_export_id = 0
        self._active_operation = None
        self._stop_event = threading.Event()
        self._training_process = None
        self._training_connection = None
        self._training_terminal = None
        self._training_terminal_received_at = 0.0
        self._training_stop_requested_at = 0.0
        self._training_force_sent = False
        self._training_polling = False
        self._training_started = 0.0
        self._training_stage = ""
        self._training_last_activity = 0.0
        self._training_last_heartbeat = 0.0
        self._progress_epoch = 0
        self._last_batch_log_bucket = -1
        self._training_project = None
        self._training_export_id = 0
        self._training_timer = QTimer(self)
        self._training_timer.setInterval(1000)
        self._training_timer.timeout.connect(self._refresh_training_status)
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._shutdown_training_process)
        self._training_service = ImageTrainingService()
        layout = QVBoxLayout(self); layout.setContentsMargins(24, 24, 24, 24); layout.setSpacing(14)
        header = QHBoxLayout(); header.addWidget(label("模型微调", "title")); header.addStretch(); layout.addLayout(header); layout.addWidget(label("闭环流程：已通过 → 导出标准 YOLO 数据集 → 后台微调 → 项目 models/ 保存版本化权重。", "muted"))
        stages = QHBoxLayout(); stages.setSpacing(14); stages.addWidget(self._export_card(), 1); stages.addWidget(self._train_card(), 1); layout.addLayout(stages)
        log_card = card(); log_layout = QVBoxLayout(log_card); log_layout.setContentsMargins(20, 20, 20, 14); log_layout.addWidget(label("运行记录", "section")); self.log = QPlainTextEdit(); self.log.setReadOnly(True); self.log.setPlaceholderText("导出与训练的运行记录会显示在这里"); self.log.document().setMaximumBlockCount(500); log_layout.addWidget(self.log); log_actions = QHBoxLayout(); log_actions.addStretch(); clear_log = button("清空日志"); clear_log.setToolTip("清空当前导出与训练运行记录"); clear_log.clicked.connect(self._clear_log); log_actions.addWidget(clear_log); log_layout.addLayout(log_actions); layout.addWidget(log_card, 1)
        self.log_message.connect(self._append_log)
        self.training_progress.connect(self._on_training_progress)
        self.training_stage.connect(self._on_training_stage)
        self.training_batch.connect(self._on_training_batch)
        self.operation_finished.connect(self._operation_complete)
        self.operation_failed.connect(self._operation_error)
        self.operation_cancelled.connect(self._operation_cancelled)

    def _export_card(self):
        """构建“1 · 导出数据集”卡片：输出目录、验证集比例与导出按钮。"""
        surface = card(); grid = QGridLayout(surface); grid.setContentsMargins(20, 20, 20, 20); grid.setSpacing(12); grid.addWidget(label("1 · 导出数据集", "section"), 0, 0, 1, 2); grid.addWidget(label("仅导出“已通过 · 训练候选”的图片与 XML 标注。", "muted"), 1, 0, 1, 2); grid.addWidget(label("输出目录", "muted"), 2, 0); self.out = QLineEdit(); self.out.setReadOnly(True); self.out.setPlaceholderText("打开项目后自动设为项目内 yolo_dataset 文件夹"); grid.addWidget(self.out, 2, 1); grid.addWidget(label("验证集比例", "muted"), 3, 0); self.ratio = QLineEdit("0.2"); grid.addWidget(self.ratio, 3, 1); self.export_status = label("尚未导出", "muted"); grid.addWidget(self.export_status, 4, 0, 1, 2); self.export_run = button("导出数据集", primary=True); self.export_run.clicked.connect(self._start_export); grid.addWidget(self.export_run, 5, 0, 1, 2); return surface

    def sync_project(self, project_root):
        """跟随项目刷新输出目录与 data.yaml，并据其存在与否启用训练按钮。"""
        self.out.setText(os.path.join(project_root, "yolo_dataset") if project_root else "")
        self._data_yaml = os.path.join(project_root, "yolo_dataset", "data.yaml") if project_root else ""
        ready = bool(self._data_yaml and os.path.isfile(self._data_yaml))
        exports = self.window.project.recent_dataset_exports(1) if self.window.project else []
        self._last_export_id = exports[0]["id"] if exports else 0
        if self._active_operation != "train":
            self.export_status.setText("已找到 data.yaml，可开始训练" if ready else "尚未导出")
        self.train_run.setEnabled(ready and not self._is_busy())
        self.export_run.setEnabled(bool(project_root) and not self._is_busy())

    def _train_card(self):
        """构建“2 · 微调训练”卡片：预训练模型、轮数、进度与启停按钮。"""
        surface = card(); grid = QGridLayout(surface); grid.setContentsMargins(20, 20, 20, 20); grid.setSpacing(12); grid.addWidget(label("2 · 微调训练", "section"), 0, 0, 1, 2); grid.addWidget(label("训练在后台运行；每次成功训练都会保留独立的模型版本。", "muted"), 1, 0, 1, 2); grid.addWidget(label("预训练模型", "muted"), 2, 0); self.train_model = FlatComboBox(); self.train_model.addItems(["yolov8n.pt", "yolov8s.pt", "yolo11n.pt"]); grid.addWidget(self.train_model, 2, 1); grid.addWidget(label("训练轮数", "muted"), 3, 0); self.epochs = QLineEdit("100"); grid.addWidget(self.epochs, 3, 1); self.train_status = label("请先导出数据集", "muted"); self.train_status.setWordWrap(True); grid.addWidget(self.train_status, 4, 0, 1, 2)
        self.train_progress = QProgressBar(); self.train_progress.setTextVisible(False); self.train_progress.setFixedHeight(6); self.train_progress.setToolTip("当前轮训练批次进度；数据扫描和验证期间会显示等待状态"); self.train_progress.setRange(0, 100); self.train_progress.setValue(0); grid.addWidget(self.train_progress, 5, 0, 1, 2)
        self.train_run = button("开始训练", primary=True); self.train_run.clicked.connect(self._start_training); self.train_run.setEnabled(False); grid.addWidget(self.train_run, 6, 0, 1, 2)
        self.train_stop = button("停止训练"); self.train_stop.clicked.connect(self._stop_training); self.train_stop.setEnabled(False); grid.addWidget(self.train_stop, 7, 0, 1, 2)
        return surface

    def _is_busy(self):
        """任一导出线程或训练子进程仍在运行时视为忙，避免并发操作。"""
        return bool(self._active_operation or (self._worker_thread and self._worker_thread.is_alive())
                    or (self._training_process and self._training_process.is_alive()))

    def _start_export(self):
        """校验训练候选并确认覆盖后，在工作线程导出数据集。"""
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
        self._active_operation = "export"
        self._set_busy(True)
        categories = list(self.window.project.categories)
        self.log_message.emit(f"开始导出 {len(files)} 张训练候选图片…")
        self._worker_thread = threading.Thread(target=self._run_export, args=(files, categories, ratio, output), daemon=True)
        self._worker_thread.start()

    def _run_export(self, files, categories, ratio, output):
        """工作线程入口：调用导出服务并把结果或错误发回主线程。"""
        try:
            summary = self._training_service.export_dataset(self.window.images_dir, self.window.annotations_dir, files, output, categories, ratio)
            self.operation_finished.emit("export", summary)
        except Exception as error:
            self.operation_failed.emit(f"导出失败：{error}")

    def _start_training(self):
        """用 spawn 上下文创建训练子进程与停止事件，并以 Pipe 回传进度。"""
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
        project_root = self.window.project_root
        self._training_project = self.window.project
        self._training_export_id = self._last_export_id
        self._active_operation = "train"
        context = multiprocessing.get_context("spawn")
        self._stop_event = context.Event()
        self._training_terminal = None
        self._training_terminal_received_at = 0.0
        self._training_stop_requested_at = 0.0
        self._training_force_sent = False
        self._training_started = time.monotonic()
        self._training_last_activity = self._training_started
        self._training_last_heartbeat = self._training_started
        self._progress_epoch = 0
        self._last_batch_log_bucket = -1
        self._training_stage = "等待训练进程启动"
        self.train_progress.setRange(0, 0)
        self._training_timer.start()
        self._set_busy(True)
        model, runs_dir = self.train_model.currentText(), os.path.join(project_root, "training_runs")
        self.log_message.emit(f"开始微调：{model} · {epochs} 轮。训练可持续数分钟，请保持应用打开。")
        receiver, sender = context.Pipe(duplex=False)
        self._training_connection = receiver
        self._training_process = context.Process(
            target=run_training_process,
            args=(data_yaml, model, epochs, runs_dir, self._stop_event, sender),
            daemon=False,  # PyTorch's DataLoader must be allowed to spawn workers.
        )
        try:
            self._training_process.start()
        except Exception as error:
            self._training_process = None
            receiver.close()
            self._training_connection = None
            self._operation_error(f"无法启动训练进程：{error}")
        finally:
            sender.close()

    def _stop_training(self):
        """先请求协作停止；若 5 秒内无响应则由轮询逻辑强制终止进程树。"""
        if self._active_operation != "train" or self._stop_event.is_set():
            return
        self._stop_event.set()
        self._training_stop_requested_at = time.monotonic()
        self.train_stop.setEnabled(False)
        self._training_stage = "正在停止训练"
        self._append_log("已请求停止训练；若当前步骤无法响应，5 秒后会终止训练进程，不会启用未完成的模型。")
        self._refresh_training_status()

    def _poll_training_process(self):
        """排空管道消息、处理超时强制终止，并在子进程退出后分派最终结果。"""
        process, connection = self._training_process, self._training_connection
        if process is None or connection is None:
            return
        try:
            for _ in range(200):
                if not connection.poll():
                    break
                try:
                    kind, *payload = connection.recv()
                except EOFError:
                    break
                if kind == "stage":
                    self._on_training_stage(*payload)
                elif kind == "batch":
                    self._on_training_batch(*payload)
                elif kind == "epoch":
                    self._on_training_progress(*payload)
                elif kind in {"complete", "cancelled", "error"}:
                    self._training_terminal = (kind, payload)
                    self._training_terminal_received_at = time.monotonic()
        except (EOFError, OSError):
            pass
        if process.is_alive():
            now = time.monotonic()
            if (self._training_terminal is not None and not self._training_force_sent
                    and now - self._training_terminal_received_at >= 2):
                self._training_force_sent = True
                self._append_log("训练计算已结束，正在清理残留的数据加载进程。")
                self._terminate_training_tree()
            elif (self._stop_event.is_set() and not self._training_force_sent
                  and now - self._training_stop_requested_at >= 5):
                self._training_force_sent = True
                self._append_log("训练进程未及时响应，正在终止训练及其数据加载进程。")
                self._terminate_training_tree()
            return

        process.join(timeout=0)
        exit_code = process.exitcode
        connection.close()
        self._training_process = None
        self._training_connection = None
        terminal = self._training_terminal
        self._training_terminal = None
        if self._stop_event.is_set():
            self._operation_cancelled("训练已停止；未将此次结果设为默认模型。")
        elif terminal and terminal[0] == "complete":
            self._finish_training_result(terminal[1][0])
        elif terminal and terminal[0] == "error":
            message, details = terminal[1]
            self._append_log(details)
            self._operation_error(message)
        elif terminal and terminal[0] == "cancelled":
            self._operation_cancelled(terminal[1][0])
        else:
            self._operation_error(f"训练进程意外退出（退出码 {exit_code}）；请查看运行记录。")

    def _terminate_training_tree(self):
        """用 psutil 终止训练进程及其 DataLoader 子进程，先 terminate 再 kill。"""
        process = self._training_process
        if process is None or not process.is_alive():
            return
        try:
            parent = psutil.Process(process.pid)
            targets = parent.children(recursive=True) + [parent]
            for target in targets:
                try:
                    target.terminate()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            _, alive = psutil.wait_procs(targets, timeout=1)
            for target in alive:
                try:
                    target.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        if process.is_alive():
            process.terminate()

    def _shutdown_training_process(self):
        """应用退出时收尾未结束的训练进程，避免残留子进程。"""
        if self._training_process is not None and self._training_process.is_alive():
            self._stop_event.set()
            self._terminate_training_tree()

    def _finish_training_result(self, result):
        """把 best.pt 复制进项目 models/ 并附上运行信息后走完成流程。"""
        try:
            project_root = self._training_project.project_dir
            models_dir = os.path.join(project_root, "models")
            os.makedirs(models_dir, exist_ok=True)
            target = os.path.join(models_dir, f"{Path(result['run_dir']).name}-best.pt")
            shutil.copy2(result["best_weights"], target)
            result["project_root"] = project_root
            result["project_weights"] = target
            result["model"] = self.train_model.currentText()
            result["epochs"] = int(self.epochs.text().strip())
            self._operation_complete("train", result)
        except Exception as error:
            self._operation_error(f"训练完成，但保存模型失败：{error}")

    def _operation_complete(self, operation, result):
        """统一收尾导出与训练：登记记录、刷新状态；被停止的训练不登记模型。"""
        if operation == "train" and self._stop_event.is_set():
            self._operation_cancelled("训练已停止；未将此次结果设为默认模型。")
            return
        self._worker_thread = None
        self._active_operation = None
        self._training_timer.stop()
        if operation == "export":
            self._data_yaml = result["data_yaml"]
            if self.window.project:
                self._last_export_id = self.window.project.record_dataset_export(result["root"], result["data_yaml"], result["train_images"], result["val_images"], result["objects"], result["classes"], result["validation_ratio"])
            self.export_status.setText(f"已导出：训练 {result['train_images']} 张 · 验证 {result['val_images']} 张 · {len(result['classes'])} 类")
            self.train_status.setText("数据集已就绪，可开始训练")
            self.log.appendPlainText(f"导出完成：{result['objects']} 个目标，data.yaml 已写入项目 yolo_dataset/。")
        else:
            self.train_progress.setRange(0, 100)
            self.train_progress.setValue(100)
            if self._training_project:
                self._training_project.record_training_run(self._training_export_id or None, result["run_dir"], result["project_weights"], result["model"], result["epochs"])
            if self.window.project and self.window.project_root == result["project_root"]:
                self.window.auto.activate_trained_model(result["project_weights"])
                self.train_status.setText("训练完成；最近版本已设为自动标注默认模型")
                self._append_log(f"训练完成：{result['project_weights']}（已保留为独立历史版本，并设为自动标注默认模型）")
            else:
                self.train_status.setText("训练完成；当前已切换到其他项目")
                self._append_log(f"训练完成：{result['project_weights']}。当前已切换项目，请重新打开原项目查看训练结果。")
            self._training_project = None
        self._set_busy(False)

    def _operation_error(self, message):
        """统一处理失败：复位进度、恢复按钮并弹出提示。"""
        self._worker_thread = None
        was_training = self._active_operation == "train"
        self._active_operation = None
        self._training_timer.stop()
        self._training_project = None
        if was_training:
            self.train_progress.setRange(0, 100)
            self.train_progress.setValue(0)
            self.train_status.setText("训练失败；请查看运行记录")
        self._set_busy(False)
        self.log.appendPlainText(message)
        ios_alert(self, "任务未完成", message, confirm_text="知道了", destructive=True)

    def _operation_cancelled(self, message):
        """统一处理取消：复位进度并说明结果未启用。"""
        self._worker_thread = None
        self._active_operation = None
        self._training_timer.stop()
        self._training_project = None
        self.train_progress.setRange(0, 100)
        self.train_progress.setValue(0)
        self.train_status.setText("训练已停止")
        self._append_log(message)
        self._set_busy(False)

    def _set_busy(self, busy):
        """按忙碌状态统一切换导出/训练按钮与输入控件可用性。"""
        self.export_run.setEnabled(bool(self.window.project_root) and not busy)
        self.train_run.setEnabled(bool(self._data_yaml and os.path.isfile(self._data_yaml)) and not busy)
        self.train_stop.setEnabled(self._active_operation == "train" and not self._stop_event.is_set())
        self.ratio.setEnabled(not busy); self.train_model.setEnabled(not busy); self.epochs.setEnabled(not busy)

    def _append_log(self, message):
        """追加一行运行记录并自动滚动到底部。"""
        self.log.appendPlainText(str(message))
        scrollbar = self.log.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _clear_log(self):
        """清空导出与训练的运行记录。"""
        self.log.clear()
        self.window.status_message("已清空导出与训练运行记录")

    def _on_training_progress(self, current, total, detail):
        """每轮结束时记录进度行并刷新状态区。"""
        if self._active_operation != "train":
            return
        line = f"第 {current}/{total} 轮完成" + (f" · {detail}" if detail else "")
        self._training_stage = f"第 {current}/{total} 轮完成，准备下一轮"
        self._training_last_activity = time.monotonic()
        self._append_log(line)
        self._refresh_training_status()

    def _on_training_stage(self, message):
        """记录训练阶段；扫描/加载阶段显示不确定进度条。"""
        if self._active_operation != "train" or self._stop_event.is_set():
            return
        if message != self._training_stage:
            self._append_log(message)
        self._training_stage = message
        self._training_last_activity = time.monotonic()
        if "扫描" in message or "加载" in message or "准备" in message:
            self.train_progress.setRange(0, 0)
        self._refresh_training_status()

    def _on_training_batch(self, epoch, total_epochs, batch, total_batches):
        """按批次更新进度条，并按 10% 分桶写日志以免刷屏。"""
        if self._active_operation != "train" or self._stop_event.is_set():
            return
        if epoch != self._progress_epoch:
            self._progress_epoch = epoch
            self._last_batch_log_bucket = -1
        self.train_progress.setRange(0, max(total_batches, 1))
        self.train_progress.setValue(batch)
        self._training_stage = f"第 {epoch}/{total_epochs} 轮 · 批次 {batch}/{total_batches}"
        self._training_last_activity = time.monotonic()
        bucket = (batch * 10 // max(total_batches, 1))
        if self._last_batch_log_bucket < 0 or bucket > self._last_batch_log_bucket or batch == total_batches:
            self._append_log(self._training_stage)
            self._last_batch_log_bucket = bucket
        self._refresh_training_status()

    def _refresh_training_status(self):
        """轮询子进程、刷新已运行时长，并在长时间无进展时输出等待提示。"""
        if self._active_operation != "train":
            return
        if not self._training_polling:
            self._training_polling = True
            try:
                self._poll_training_process()
            finally:
                self._training_polling = False
        if self._active_operation != "train":
            return
        now = time.monotonic()
        elapsed = int(now - self._training_started)
        duration = f"{elapsed // 60:02d}:{elapsed % 60:02d}"
        self.train_status.setText(f"{self._training_stage} · 已运行 {duration}")
        if now - self._training_last_activity >= 30 and now - self._training_last_heartbeat >= 30:
            self._append_log(f"{self._training_stage} · 已运行 {duration}，仍在等待当前阶段完成")
            self._training_last_heartbeat = now
