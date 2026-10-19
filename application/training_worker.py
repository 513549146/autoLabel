"""Isolated training process entry point for the desktop workbench."""
from __future__ import annotations

import traceback


def run_training_process(data_yaml, model, epochs, runs_dir, stop_event, connection):
    """Run training outside Qt so a blocked setup can be terminated safely."""
    from core import TrainingCancelled, train_yolo_dataset

    def report(kind, *values):
        connection.send((kind, *values))

    try:
        result = train_yolo_dataset(
            data_yaml, model, epochs, runs_dir,
            on_progress=lambda current, total, detail: report("epoch", current, total, detail),
            on_stage=lambda message: report("stage", message),
            on_batch=lambda epoch, total, batch, count: report("batch", epoch, total, batch, count),
            should_stop=stop_event.is_set,
        )
        if stop_event.is_set():
            raise TrainingCancelled("训练已按请求停止")
        report("complete", result)
    except TrainingCancelled:
        report("cancelled", "训练已停止；未将此次结果设为默认模型。")
    except Exception as error:
        report("error", f"训练失败：{error}", traceback.format_exc())
    finally:
        connection.close()
