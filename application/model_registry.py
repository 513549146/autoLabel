"""Image-model provider boundary for the current local inference backends."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import core

from .contracts import PredictionBundle, normalize_annotations


@dataclass(frozen=True)
class ImageModelDescriptor:
    id: str
    title: str
    capabilities: frozenset[str]


class ImagePredictor(Protocol):
    """Minimal provider contract used by the pre-annotation service."""

    descriptor: ImageModelDescriptor

    def predict(self, image_path: str, prompt: str, confidence: float) -> PredictionBundle:
        ...


class CoreImagePredictor:
    """Adapter around the existing, tested ``core`` detector implementations."""

    def __init__(self, descriptor: ImageModelDescriptor, weights_path: str = ""):
        self.descriptor = descriptor
        self.weights_path = weights_path
        self._detector = core.load_models(
            descriptor.id, weights_path if descriptor.id in {"custom_yolo", "yoloe"} else None
        )

    def predict(self, image_path: str, prompt: str, confidence: float) -> PredictionBundle:
        annotations = core.auto_label(
            image_path, prompt, self._detector, box_threshold=confidence
        )
        version = Path(self.weights_path).name if self.weights_path else self.descriptor.id
        return normalize_annotations(annotations, self.descriptor.id, version)


class ImageModelRegistry:
    """Maps stable UI choices to local image-provider implementations.

    Future image backends add one descriptor and provider factory here.  Pages
    do not need to import Ultralytics, Transformers, or Grounding DINO.
    """

    _DESCRIPTORS = {
        "yoloe": ImageModelDescriptor("yoloe", "YOLOE（开放词表，推荐）", frozenset({"detect", "open_vocab"})),
        "gd15": ImageModelDescriptor("gd15", "Grounding DINO（兼容旧模型）", frozenset({"detect", "open_vocab"})),
        "gd_ogc": ImageModelDescriptor("gd_ogc", "Grounding DINO（旧版）", frozenset({"detect", "open_vocab"})),
        "yolo": ImageModelDescriptor("yolo", "YOLO（微调）", frozenset({"detect", "closed_set"})),
        "custom_yolo": ImageModelDescriptor("custom_yolo", "自定义 YOLO", frozenset({"detect", "closed_set"})),
    }

    def descriptors(self) -> tuple[ImageModelDescriptor, ...]:
        return tuple(self._DESCRIPTORS.values())

    def descriptor(self, provider_id: str) -> ImageModelDescriptor:
        try:
            return self._DESCRIPTORS[provider_id]
        except KeyError as error:
            raise ValueError(f"未知图像模型：{provider_id}") from error

    def create(self, provider_id: str, weights_path: str = "") -> ImagePredictor:
        return CoreImagePredictor(self.descriptor(provider_id), weights_path)
