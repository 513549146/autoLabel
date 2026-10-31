"""On-demand, independent label evidence for disputed SAM instances."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VerifiedObject:
    label: str
    bbox: tuple[int, int, int, int]
    score: float


class ClassVerificationOperation:
    """Run a small closed-set detector once, only for labels it knows."""

    MIN_SCORE = 0.45

    def __init__(self, weights_path: str):
        self.weights_path = weights_path
        self._model = None
        self._known_labels: frozenset[str] = frozenset()

    def supports(self, label: str) -> bool:
        return label.casefold() in self._known_labels

    def execute(self, image_path: str, labels: tuple[str, ...]) -> tuple[VerifiedObject, ...]:
        if self._model is None:
            from ultralytics import YOLO

            self._model = YOLO(self.weights_path)
            names = self._model.names
            values = names.values() if isinstance(names, dict) else names
            self._known_labels = frozenset(str(name).strip().casefold() for name in values)
        requested = {label.strip().casefold(): label for label in labels}
        names = self._model.names
        indexed = names.items() if isinstance(names, dict) else enumerate(names)
        class_ids = [int(index) for index, name in indexed if str(name).strip().casefold() in requested]
        if not class_ids:
            return ()
        results = self._model.predict(image_path, classes=class_ids, conf=self.MIN_SCORE,
                                      iou=0.6, verbose=False)
        if not results or results[0].boxes is None:
            return ()
        verified = []
        for box in results[0].boxes:
            label = requested[str(names[int(box.cls[0])]).strip().casefold()]
            bbox = tuple(round(float(value)) for value in box.xyxy[0].tolist())
            if len(bbox) == 4 and bbox[2] > bbox[0] and bbox[3] > bbox[1]:
                verified.append(VerifiedObject(label, bbox, float(box.conf[0])))
        return tuple(verified)
