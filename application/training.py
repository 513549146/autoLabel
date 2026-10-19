"""Image dataset export and model-training application service."""
from __future__ import annotations

import core


class ImageTrainingService:
    """Thin service boundary around the existing core training routines."""

    @staticmethod
    def export_dataset(images_dir, annotations_dir, filenames, output_dir, categories, validation_ratio):
        return core.export_yolo_dataset(
            images_dir, annotations_dir, filenames, output_dir, categories, validation_ratio
        )

    @staticmethod
    def train(data_yaml, base_model, runs_dir, epochs, on_progress=None,
              on_stage=None, on_batch=None, should_stop=None):
        return core.train_yolo_dataset(
            data_yaml, base_model, epochs, runs_dir,
            on_progress=on_progress, on_stage=on_stage, on_batch=on_batch,
            should_stop=should_stop,
        )
