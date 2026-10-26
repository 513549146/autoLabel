"""Stable image-workflow contracts shared by providers and services.

The application is intentionally image-only today.  These contracts keep model
outputs independent of a particular inference library without prematurely
adding video, text, or point-cloud abstractions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


@dataclass(frozen=True)
class ImagePrediction:
    """A normalized model proposal ready for XML persistence and review."""

    label: str
    bbox: tuple[int, int, int, int]
    score: float | None = None
    source: str = ""
    model_version: str = ""
    risk_flags: tuple[str, ...] = ()

    def as_annotation(self) -> dict:
        """转成 core.VOC 写入所用的标注字典，保留分数供风险规则使用。"""
        annotation = {"name": self.label, "bbox": list(self.bbox)}
        if self.score is not None:
            annotation["score"] = float(self.score)
        return annotation


@dataclass(frozen=True)
class PredictionBundle:
    """The result of one provider run against one image."""

    provider_id: str
    model_version: str
    predictions: tuple[ImagePrediction, ...] = ()

    def annotations(self) -> list[dict]:
        """把本次预测整体转成标注字典列表。"""
        return [prediction.as_annotation() for prediction in self.predictions]


def normalize_annotations(
    annotations: Iterable[dict], provider_id: str, model_version: str = ""
) -> PredictionBundle:
    """Convert existing core annotations into the normalized provider result."""

    predictions = []
    for annotation in annotations:
        try:
            bbox = tuple(int(value) for value in annotation["bbox"])
            if len(bbox) != 4:
                continue
        except (KeyError, TypeError, ValueError):
            continue
        score = annotation.get("score")
        predictions.append(
            ImagePrediction(
                label=str(annotation.get("name") or "object"),
                bbox=bbox,
                score=float(score) if score is not None else None,
                source=provider_id,
                model_version=model_version,
            )
        )
    return PredictionBundle(provider_id, model_version, tuple(predictions))
