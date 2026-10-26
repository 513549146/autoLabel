"""Image dataset export and model-training application service."""
from __future__ import annotations

import core


class ImageTrainingService:
    """Thin service boundary around the existing core training routines."""

    @staticmethod
    def export_dataset(images_dir, annotations_dir, filenames, output_dir, categories, validation_ratio):
        """导出已通过图片为 YOLO 数据集，转发到 core 实现。"""
        return core.export_yolo_dataset(
            images_dir, annotations_dir, filenames, output_dir, categories, validation_ratio
        )

    @staticmethod
    def train(data_yaml, base_model, runs_dir, epochs, on_progress=None,
              on_stage=None, on_batch=None, should_stop=None):
        """启动 Ultralytics 训练，转发 core 实现并透传进度与停止回调。"""
        return core.train_yolo_dataset(
            data_yaml, base_model, epochs, runs_dir,
            on_progress=on_progress, on_stage=on_stage, on_batch=on_batch,
            should_stop=should_stop,
        )
