"""Training progress and cooperative cancellation regression tests."""
from __future__ import annotations

import os
import multiprocessing
import sys
import time
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

import core  # noqa: E402
from application.training_worker import run_training_process  # noqa: E402
from gui.pages.export_page import ExportPage  # noqa: E402


def _sleep_forever():
    time.sleep(60)


class _Loader:
    def __init__(self, images):
        self.dataset = range(images)
        self.closed = False

    def __len__(self):
        return 2

    def close(self):
        self.closed = True


class _Trainer:
    def __init__(self, run_dir):
        self.epoch = 0
        self.epochs = 2
        self.train_loader = _Loader(4)
        self.test_loader = _Loader(2)
        self.save_dir = run_dir
        self.tloss = {"box": 0.25}
        self.metrics = {"metrics/mAP50(B)": 0.5}

    def label_loss_items(self, values, prefix="train"):
        return {f"{prefix}/{key}": value for key, value in values.items()}

    def get_dataloader(self, dataset_path, batch_size, rank, mode):
        return self.train_loader if mode == "train" else self.test_loader


class TrainingControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _fake_ultralytics(self, run_dir):
        models = []

        class YOLO:
            def __init__(self, base_model):
                self.callbacks = {}
                self.trainer = _Trainer(run_dir)
                models.append(self)

            def add_callback(self, name, callback):
                self.callbacks.setdefault(name, []).append(callback)

            def _fire(self, name):
                for callback in self.callbacks.get(name, []):
                    callback(self.trainer)

            def train(self, **kwargs):
                self.train_options = kwargs
                self._fire("on_pretrain_routine_start")
                self.trainer.get_dataloader("train", 16, -1, mode="train")
                self.trainer.get_dataloader("val", 32, -1, mode="val")
                self._fire("on_pretrain_routine_end")
                self._fire("on_train_start")
                for epoch in range(2):
                    self.trainer.epoch = epoch
                    self._fire("on_train_epoch_start")
                    for _ in range(2):
                        self._fire("on_train_batch_start")
                        self._fire("on_train_batch_end")
                    self._fire("on_train_epoch_end")
                    self._fire("on_fit_epoch_end")
                self.trainer.epoch = 2
                self._fire("on_fit_epoch_end")  # Final best-model validation is not another epoch.
                self._fire("on_train_end")

        fake = types.ModuleType("ultralytics")
        fake.YOLO = YOLO
        return fake, models

    def test_training_actions_are_stacked_without_redundant_shortcut(self):
        window = SimpleNamespace(project=None, project_root="", status_message=lambda message: None)
        page = ExportPage(window)
        export_grid = page.export_run.parentWidget().layout()
        self.assertEqual(export_grid.getItemPosition(export_grid.indexOf(page.export_run)), (5, 0, 1, 2))
        grid = page.train_run.parentWidget().layout()
        self.assertEqual(grid.getItemPosition(grid.indexOf(page.train_run)), (6, 0, 1, 2))
        self.assertEqual(grid.getItemPosition(grid.indexOf(page.train_stop)), (7, 0, 1, 2))
        self.assertFalse(hasattr(page, "use_latest"))
        page.close()

    def test_progress_reports_stages_batches_and_completed_epochs(self):
        fake, models = self._fake_ultralytics(Path("fake/runs/finetune"))
        stages, batches, epochs = [], [], []
        with patch.dict(sys.modules, {"ultralytics": fake}), \
             patch.object(core.os.path, "isfile", return_value=True), \
             patch.object(core.os, "makedirs"):
            result = core.train_yolo_dataset(
                "fake/data.yaml", "yolov8n.pt", 2, "fake/runs",
                on_stage=stages.append,
                on_batch=lambda *args: batches.append(args),
                on_progress=lambda *args: epochs.append(args),
            )
        self.assertTrue(result["best_weights"].endswith("best.pt"))
        self.assertEqual([item[0] for item in epochs], [1, 2])
        self.assertIn((1, 2, 1, 2), batches)
        self.assertIn((2, 2, 2, 2), batches)
        self.assertTrue(any("扫描" in stage for stage in stages))
        self.assertIn("训练集数据加载器已就绪", stages)
        self.assertIn("验证集数据加载器已就绪", stages)
        self.assertTrue(any("验证" in stage for stage in stages))
        if os.name == "nt":
            self.assertGreater(models[0].train_options["workers"], 0)
            self.assertLessEqual(models[0].train_options["workers"], 2)
        self.assertFalse(models[0].trainer.train_loader.closed)

    def test_cancel_between_batches_closes_loaders_and_skips_result(self):
        fake, models = self._fake_ultralytics(Path("fake/runs/finetune"))
        stopped = False

        def on_batch(*args):
            nonlocal stopped
            stopped = True

        with patch.dict(sys.modules, {"ultralytics": fake}), \
             patch.object(core.os.path, "isfile", return_value=True), \
             patch.object(core.os, "makedirs"):
            with self.assertRaises(core.TrainingCancelled):
                core.train_yolo_dataset(
                    "fake/data.yaml", "yolov8n.pt", 2, "fake/runs",
                    on_batch=on_batch, should_stop=lambda: stopped,
                )
        self.assertTrue(models[0].trainer.train_loader.closed)
        self.assertTrue(models[0].trainer.test_loader.closed)

    def test_stop_button_and_stall_heartbeat(self):
        project = SimpleNamespace(recent_training_runs=lambda limit: [])
        window = SimpleNamespace(project=project, project_root="missing-project", status_message=lambda message: None)
        page = ExportPage(window)
        page._active_operation = "train"
        page._training_started = time.monotonic() - 65
        page._training_last_activity = time.monotonic() - 35
        page._training_last_heartbeat = time.monotonic() - 35
        page._training_stage = "正在扫描图片与标签"
        page._set_busy(True)
        self.assertTrue(page.train_stop.isEnabled())
        page._refresh_training_status()
        self.assertIn("仍在等待当前阶段完成", page.log.toPlainText())
        page._stop_training()
        self.assertTrue(page._stop_event.is_set())
        self.assertFalse(page.train_stop.isEnabled())
        page._operation_complete("train", {"project_root": "missing-project"})
        self.assertIn("训练已停止", page.train_status.text())
        page.close()

    def test_worker_reports_progress_and_completion_over_connection(self):
        sent = []
        connection = SimpleNamespace(send=sent.append, close=lambda: None)
        stop_event = __import__("threading").Event()

        def fake_train(*args, **kwargs):
            kwargs["on_stage"]("正在加载训练集")
            kwargs["on_batch"](1, 2, 1, 10)
            kwargs["on_progress"](1, 2, "mAP=0.5")
            return {"run_dir": "fake-run", "best_weights": "fake-best.pt"}

        with patch.object(core, "train_yolo_dataset", side_effect=fake_train):
            run_training_process("fake.yaml", "yolov8n.pt", 2, "fake-runs", stop_event, connection)
        self.assertEqual([message[0] for message in sent], ["stage", "batch", "epoch", "complete"])

    def test_unresponsive_training_is_forced_to_stop(self):
        project = SimpleNamespace(recent_training_runs=lambda limit: [])
        window = SimpleNamespace(project=project, project_root="missing-project", status_message=lambda message: None)
        page = ExportPage(window)
        page._active_operation = "train"
        page._training_process = SimpleNamespace(is_alive=lambda: True)
        page._training_connection = SimpleNamespace(poll=lambda: False)
        page._stop_event.set()
        page._training_stop_requested_at = time.monotonic() - 6
        page._training_started = time.monotonic() - 30
        page._training_last_activity = time.monotonic()
        with patch.object(page, "_terminate_training_tree") as terminate:
            page._refresh_training_status()
        terminate.assert_called_once()
        self.assertTrue(page._training_force_sent)
        page._training_process = None
        page.close()

    def test_finished_training_cleans_up_lingering_loader_processes(self):
        project = SimpleNamespace(recent_training_runs=lambda limit: [])
        window = SimpleNamespace(project=project, project_root="missing-project", status_message=lambda message: None)
        page = ExportPage(window)
        page._active_operation = "train"
        page._training_process = SimpleNamespace(is_alive=lambda: True)
        page._training_connection = SimpleNamespace(poll=lambda: False)
        page._training_terminal = ("complete", [{"run_dir": "fake"}])
        page._training_terminal_received_at = time.monotonic() - 3
        with patch.object(page, "_terminate_training_tree") as terminate:
            page._poll_training_process()
        terminate.assert_called_once()
        page._training_process = None
        page.close()

    def test_stop_terminates_a_live_training_process(self):
        project = SimpleNamespace(recent_training_runs=lambda limit: [])
        window = SimpleNamespace(project=project, project_root="missing-project", status_message=lambda message: None)
        page = ExportPage(window)
        process = multiprocessing.get_context("spawn").Process(target=_sleep_forever)
        try:
            process.start()
            page._training_process = process
            self.assertTrue(process.is_alive())
            page._terminate_training_tree()
            process.join(timeout=5)
            self.assertFalse(process.is_alive())
        finally:
            if process.is_alive():
                process.kill()
                process.join(timeout=5)
            page._training_process = None
            page.close()

    def test_completed_process_records_and_activates_model(self):
        project = SimpleNamespace(
            project_dir="fake-project",
            recent_training_runs=lambda limit: [],
            record_training_run=Mock(),
        )
        auto = SimpleNamespace(activate_trained_model=Mock())
        window = SimpleNamespace(
            project=project, project_root="fake-project", auto=auto,
            status_message=lambda message: None,
        )
        page = ExportPage(window)
        page._training_project = project
        page._training_export_id = 7
        page._active_operation = "train"
        page.epochs.setText("1")
        result = {"run_dir": "fake-runs/finetune", "best_weights": "fake-runs/finetune/weights/best.pt"}
        with patch.object(os, "makedirs"), patch("gui.pages.export_page.shutil.copy2"):
            page._finish_training_result(result)
        project.record_training_run.assert_called_once()
        auto.activate_trained_model.assert_called_once_with(result["project_weights"])
        self.assertIn("训练完成", page.train_status.text())
        page.close()


if __name__ == "__main__":
    unittest.main()
