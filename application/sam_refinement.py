"""Optional SAM 2.1 mask evidence for ambiguous preannotation boxes."""
from __future__ import annotations

from dataclasses import dataclass, replace

import cv2
import numpy as np
from PIL import Image
import config
from engine.image_io import _oriented_pil_size

from .class_verification import ClassVerificationOperation, VerifiedObject
from .contracts import ImagePrediction
from .high_recall import tile_windows
from .quick_operations import CandidateRuleRefinementOperation, _iou


@dataclass(frozen=True)
class SamRefinementResult:
    predictions: tuple[ImagePrediction, ...]
    blocked_pairs: frozenset[tuple[int, int]] = frozenset()
    analyzed: int = 0
    merged: int = 0
    prompted: int = 0
    resolved_conflicts: int = 0
    unresolved_conflicts: int = 0
    verifier_used: bool = False
    verifier_failed: bool = False


class SamMaskRefinementOperation:
    """Segment only overlapping candidates; keep all boxes if masks are uncertain."""

    MAX_PROMPTS = 32
    MIN_BOX_IOU = 0.25
    MIN_BOX_CONTAINMENT = 0.55
    MERGE_MASK_IOU = 0.80
    MERGE_MASK_CONTAINMENT = 0.90
    MIN_CONTAINED_MASK_IOU = 0.35
    DISTINCT_MASK_IOU = 0.20
    DISTINCT_MASK_CONTAINMENT = 0.40
    MAX_MASK_EDGE = 1024
    MASK_BATCH = 8
    MAX_VERIFIER_ANCHORS = 8
    MAX_UNION_ANCHORS = 4
    ANCHOR_MASK_COVERAGE = 0.80
    ANCHOR_MIN_MASK_IOU = 0.15
    ANCHOR_ASSIGNMENT_MARGIN = 0.08
    CROSS_PAIR_MASK_IOU = 0.15
    CROSS_PAIR_CONTAINMENT = 0.50
    WEAK_BOX_IOU = 0.68
    UNION_ANCHOR_COVERAGE = 0.85
    UNION_PAIR_MASK_IOU = 0.20
    UNION_PAIR_CONTAINMENT = 0.50
    FULL_ANCHOR_SCORE = 0.45
    FULL_ANCHOR_BOX_CONTAINMENT = 0.70
    FULL_ANCHOR_SAME_COVERAGE = 0.80
    FULL_ANCHOR_CROSS_COVERAGE = 0.92
    FULL_ANCHOR_MARGIN = 0.08
    GEOMETRY_MIN_ANCHOR_SCORE = 0.65
    GEOMETRY_MAX_FRAGMENT_SCORE = 0.55
    GEOMETRY_SCORE_RATIO = 0.75
    GEOMETRY_MIN_AREA_RATIO = 8.0
    GEOMETRY_BOX_CONTAINMENT = 0.82
    GEOMETRY_MAX_MASK_BOX_FILL = 0.18
    TRUNCATED_MIN_AREA_RATIO = 4.0
    TRUNCATED_MIN_FRAGMENT_SCORE = 0.55
    TRUNCATED_BOX_CONTAINMENT = 0.95
    TRUNCATED_EDGE_TOLERANCE = 5
    TRUNCATED_ANCHOR_EXTENSION = 0.12
    UNMASKED_MIN_AREA_RATIO = 20.0
    UNMASKED_MAX_SIDE_RATIO = 0.15

    def __init__(self):
        self._predictor = None
        self._weights_path = ""

    @staticmethod
    def _mask_iou(first: np.ndarray, second: np.ndarray) -> float:
        overlap = np.count_nonzero(first & second)
        union = np.count_nonzero(first | second)
        return overlap / union if union else 0.0

    @staticmethod
    def _box_containment(first: tuple[int, int, int, int],
                         second: tuple[int, int, int, int]) -> float:
        overlap = max(0, min(first[2], second[2]) - max(first[0], second[0])) * \
                  max(0, min(first[3], second[3]) - max(first[1], second[1]))
        first_area = max(0, first[2] - first[0]) * max(0, first[3] - first[1])
        second_area = max(0, second[2] - second[0]) * max(0, second[3] - second[1])
        smaller = min(first_area, second_area)
        return overlap / smaller if smaller else 0.0

    def _related_boxes(self, first: tuple[int, int, int, int],
                       second: tuple[int, int, int, int]) -> bool:
        return (_iou(first, second) >= self.MIN_BOX_IOU or
                self._box_containment(first, second) >= self.MIN_BOX_CONTAINMENT)

    @classmethod
    def _tile_truncates_anchor(cls, fragment: tuple[int, int, int, int],
                               anchor: tuple[int, int, int, int],
                               windows: tuple[tuple[int, int, int, int], ...]) -> bool:
        tolerance = cls.TRUNCATED_EDGE_TOLERANCE
        width, height = fragment[2] - fragment[0], fragment[3] - fragment[1]
        for left, top, right, bottom in windows:
            if (fragment[0] < left - tolerance or fragment[1] < top - tolerance or
                    fragment[2] > right + tolerance or fragment[3] > bottom + tolerance):
                continue
            edges = ((fragment[0], left, anchor[0], width, -1),
                     (fragment[2], right, anchor[2], width, 1),
                     (fragment[1], top, anchor[1], height, -1),
                     (fragment[3], bottom, anchor[3], height, 1))
            for coordinate, tile_edge, anchor_edge, span, direction in edges:
                extension = max(40, span * cls.TRUNCATED_ANCHOR_EXTENSION)
                if (abs(coordinate - tile_edge) <= tolerance and
                        direction * (anchor_edge - tile_edge) >= extension):
                    return True
        return False

    def _candidate_indices(self, predictions: tuple[ImagePrediction, ...],
                           capacity: int | None = None) -> tuple[int, ...]:
        pairs = []
        for first in range(len(predictions)):
            for second in range(first + 1, len(predictions)):
                overlap = _iou(predictions[first].bbox, predictions[second].bbox)
                containment = self._box_containment(predictions[first].bbox, predictions[second].bbox)
                if overlap >= self.MIN_BOX_IOU or containment >= self.MIN_BOX_CONTAINMENT:
                    cross_class = predictions[first].label != predictions[second].label
                    pairs.append((cross_class, overlap, containment, first, second))
        selected: set[int] = set()
        limit = self.MAX_PROMPTS if capacity is None else capacity
        for _, _, _, first, second in sorted(pairs, key=lambda item: (-item[0], -item[1], -item[2], item[3], item[4])):
            if len(selected | {first, second}) <= limit:
                selected.update((first, second))
        return tuple(sorted(selected))

    def _same_class_union_anchors(self, predictions: tuple[ImagePrediction, ...],
                                  indices: tuple[int, ...]) -> tuple[tuple[tuple[int, ...], tuple[int, int, int, int]], ...]:
        remaining = set(indices)
        components = []
        while remaining:
            seed = min(remaining)
            remaining.remove(seed)
            component = {seed}
            frontier = [seed]
            while frontier:
                current = frontier.pop()
                neighbors = [other for other in sorted(remaining)
                             if predictions[current].label == predictions[other].label and
                             self._related_boxes(predictions[current].bbox, predictions[other].bbox)]
                for other in neighbors:
                    remaining.remove(other)
                    component.add(other)
                    frontier.append(other)
            ordered = tuple(sorted(component))
            if len(ordered) < 2 or not any(
                _iou(predictions[first].bbox, predictions[second].bbox) < self.WEAK_BOX_IOU
                for position, first in enumerate(ordered) for second in ordered[position + 1:]
                if self._related_boxes(predictions[first].bbox, predictions[second].bbox)
            ):
                continue
            bbox = (min(predictions[index].bbox[0] for index in ordered),
                    min(predictions[index].bbox[1] for index in ordered),
                    max(predictions[index].bbox[2] for index in ordered),
                    max(predictions[index].bbox[3] for index in ordered))
            components.append((ordered, bbox))
        components.sort(key=lambda item: (-len(item[0]), item[0]))
        return tuple(components[:self.MAX_UNION_ANCHORS])

    def _predict_masks(self, image_path: str, boxes: list[list[int]], weights_path: str) -> list[np.ndarray | None]:
        if self._predictor is None or self._weights_path != weights_path:
            from ultralytics.models.sam import SAM2Predictor

            self._predictor = SAM2Predictor(overrides={
                "model": weights_path, "task": "segment", "mode": "predict",
                "imgsz": 1024, "conf": 0.01, "save": False, "verbose": False,
            })
            self._weights_path = weights_path
        predictor = self._predictor
        predictor.set_image(image_path)
        try:
            def decode(batch: list[list[int]]) -> list[np.ndarray | None]:
                try:
                    results = predictor(bboxes=batch)
                    data = (results[0].masks.data.detach().cpu().numpy()
                            if results and results[0].masks is not None else None)
                except Exception:
                    data = None
                if data is None or len(data) != len(batch):
                    if len(batch) == 1:
                        return [None]
                    midpoint = len(batch) // 2
                    return decode(batch[:midpoint]) + decode(batch[midpoint:])
                masks = []
                for mask in data:
                    height, width = mask.shape
                    scale = min(1.0, self.MAX_MASK_EDGE / max(height, width))
                    size = (max(1, round(width * scale)), max(1, round(height * scale)))
                    compact = (cv2.resize(mask.astype(np.uint8), size, interpolation=cv2.INTER_NEAREST) > 0
                               if scale < 1 else mask > 0.5)
                    masks.append(compact if np.count_nonzero(compact) >= 8 else None)
                return masks

            masks = []
            for offset in range(0, len(boxes), self.MASK_BATCH):
                masks.extend(decode(boxes[offset:offset + self.MASK_BATCH]))
            return masks
        finally:
            predictor.reset_image()

    @staticmethod
    def _combine_verified(predictions: tuple[ImagePrediction, ...], group: list[int],
                          anchor: VerifiedObject) -> ImagePrediction:
        items = [predictions[index] for index in group]
        representative = max((item for item in items if item.label == anchor.label),
                             key=lambda item: item.score if item.score is not None else -1.0)
        sources = tuple(dict.fromkeys(source for item in items
                                      for source in (item.sources or (item.source,)) if source))
        sources = tuple(dict.fromkeys((*sources, "yolo11n_verify")))
        source_scores: dict[str, float] = {}
        for item in items:
            pairs = item.source_scores or (((item.source, item.score),)
                                           if item.source and item.score is not None else ())
            for source, score in pairs:
                source_scores[source] = max(source_scores.get(source, float("-inf")), float(score))
        source_scores["yolo11n_verify"] = anchor.score
        flags = {flag for item in items for flag in item.risk_flags}
        flags.add("class_conflict")
        flags.discard("single_model")
        if any("/tile" not in source for source in sources):
            flags.discard("tile_only")
        alternatives = tuple(sorted({item.label for item in items if item.label != anchor.label}))
        return replace(representative, label=anchor.label, bbox=anchor.bbox,
                       score=anchor.score, source="yolo11n_verify", model_version="yolo11n.pt",
                       sources=sources, source_scores=tuple(source_scores.items()),
                       risk_flags=tuple(sorted(flags)), alternate_labels=alternatives)

    @staticmethod
    def _sources(prediction: ImagePrediction) -> tuple[str, ...]:
        return prediction.sources or ((prediction.source,) if prediction.source else ())

    @classmethod
    def _tile_only(cls, prediction: ImagePrediction) -> bool:
        sources = cls._sources(prediction)
        return bool(sources) and all(source.endswith("/tile") for source in sources)

    @classmethod
    def _combine_full_anchor(cls, predictions: tuple[ImagePrediction, ...],
                             group: list[int], anchor_index: int) -> ImagePrediction:
        anchor = predictions[anchor_index]
        combined = CandidateRuleRefinementOperation._combine([predictions[index] for index in group])
        alternatives = tuple(sorted({label for index in group
                                     for label in (predictions[index].label,
                                                   *predictions[index].alternate_labels)
                                     if label != anchor.label}))
        flags = set(combined.risk_flags)
        if alternatives:
            flags.add("class_conflict")
        return replace(combined, label=anchor.label, bbox=anchor.bbox,
                       score=anchor.score, source=anchor.source,
                       model_version=anchor.model_version,
                       risk_flags=tuple(sorted(flags)), alternate_labels=alternatives)

    def execute(self, image_path: str, predictions: tuple[ImagePrediction, ...],
                weights_path: str, verifier: ClassVerificationOperation | None = None) -> SamRefinementResult:
        indices = self._candidate_indices(predictions)
        if len(indices) < 2:
            return SamRefinementResult(predictions)

        cross_class = any(predictions[first].label != predictions[second].label and
                          self._related_boxes(predictions[first].bbox, predictions[second].bbox)
                          for position, first in enumerate(indices) for second in indices[position + 1:])
        anchors: tuple[VerifiedObject, ...] = ()
        verifier_used = verifier_failed = False
        if verifier is not None and cross_class:
            verifier_used = True
            try:
                labels = tuple(dict.fromkeys(predictions[index].label for index in indices))
                candidates = verifier.execute(image_path, labels)
                anchors = tuple(sorted((candidate for candidate in candidates
                                        if candidate.label in labels and
                                        any(self._related_boxes(candidate.bbox, predictions[index].bbox)
                                            for index in indices)),
                                       key=lambda item: -item.score)[:self.MAX_VERIFIER_ANCHORS])
            except Exception:
                verifier_failed = True
        if anchors:
            indices = self._candidate_indices(predictions, self.MAX_PROMPTS - len(anchors))
        union_anchors = self._same_class_union_anchors(predictions, indices)
        prompt_boxes = [list(predictions[index].bbox) for index in indices]
        prompt_boxes.extend(list(anchor.bbox) for anchor in anchors)
        prompt_boxes.extend(list(bbox) for _, bbox in union_anchors)
        masks = self._predict_masks(image_path, prompt_boxes, weights_path)
        if len(masks) != len(prompt_boxes):
            return SamRefinementResult(predictions, prompted=len(prompt_boxes),
                                       verifier_used=verifier_used, verifier_failed=verifier_failed)
        evidence = {index: mask for index, mask in zip(indices, masks) if mask is not None}
        anchor_masks = masks[len(indices):len(indices) + len(anchors)]
        union_masks = masks[len(indices) + len(anchors):]
        if not evidence:
            return SamRefinementResult(predictions, analyzed=len(evidence), prompted=len(prompt_boxes),
                                       verifier_used=verifier_used, verifier_failed=verifier_failed)
        mask_areas = {index: int(np.count_nonzero(mask)) for index, mask in evidence.items()}
        overlaps: dict[tuple[int, int], tuple[float, float]] = {}
        image_size: tuple[int, int] | None = None
        windows: tuple[tuple[int, int, int, int], ...] | None = None

        def oriented_size() -> tuple[int, int]:
            nonlocal image_size
            if image_size is None:
                with Image.open(image_path) as image:
                    image_size = _oriented_pil_size(image)
            return image_size

        def mask_box_fill(index: int) -> float:
            image_width, image_height = oriented_size()
            mask = evidence[index]
            mask_height, mask_width = mask.shape
            x1, y1, x2, y2 = predictions[index].bbox
            left = max(0, min(mask_width, round(x1 * mask_width / image_width)))
            right = max(0, min(mask_width, round(x2 * mask_width / image_width)))
            top = max(0, min(mask_height, round(y1 * mask_height / image_height)))
            bottom = max(0, min(mask_height, round(y2 * mask_height / image_height)))
            box_area = (right - left) * (bottom - top)
            return float(np.count_nonzero(mask[top:bottom, left:right])) / box_area if box_area > 0 else 1.0

        def mask_overlap(first: int, second: int) -> tuple[float, float]:
            key = (min(first, second), max(first, second))
            if key not in overlaps:
                intersection = int(np.count_nonzero(evidence[first] & evidence[second]))
                union = mask_areas[first] + mask_areas[second] - intersection
                overlaps[key] = (intersection / union if union else 0.0,
                                 intersection / min(mask_areas[first], mask_areas[second]))
            return overlaps[key]

        groups: list[tuple[list[int], VerifiedObject | int | None]] = []
        claimed: set[int] = set()
        if anchors and verifier is not None:
            assigned: dict[int, list[int]] = {index: [] for index in range(len(anchors))}
            for candidate_index, candidate_mask in evidence.items():
                if not verifier.supports(predictions[candidate_index].label):
                    continue
                choices = []
                for anchor_index, (anchor, anchor_mask) in enumerate(zip(anchors, anchor_masks)):
                    if anchor_mask is None or not self._related_boxes(predictions[candidate_index].bbox, anchor.bbox):
                        continue
                    intersection = int(np.count_nonzero(candidate_mask & anchor_mask))
                    coverage = intersection / mask_areas[candidate_index]
                    union = mask_areas[candidate_index] + int(np.count_nonzero(anchor_mask)) - intersection
                    mask_iou = intersection / union if union else 0.0
                    if coverage >= self.ANCHOR_MASK_COVERAGE and mask_iou >= self.ANCHOR_MIN_MASK_IOU:
                        choices.append((coverage * 0.7 + mask_iou * 0.3, anchor_index))
                choices.sort(reverse=True)
                if choices and (len(choices) == 1 or
                                choices[0][0] - choices[1][0] >= self.ANCHOR_ASSIGNMENT_MARGIN):
                    assigned[choices[0][1]].append(candidate_index)
            for anchor_index, assigned_indices in assigned.items():
                clusters: list[list[int]] = []
                remaining = set(assigned_indices)
                while remaining:
                    seed = min(remaining)
                    remaining.remove(seed)
                    cluster = [seed]
                    frontier = [seed]
                    while frontier:
                        current = frontier.pop()
                        neighbors = [index for index in sorted(remaining)
                                     if self._related_boxes(predictions[current].bbox, predictions[index].bbox) and
                                     (mask_overlap(current, index)[0] >= self.CROSS_PAIR_MASK_IOU or
                                      mask_overlap(current, index)[1] >= self.CROSS_PAIR_CONTAINMENT)]
                        for index in neighbors:
                            remaining.remove(index)
                            cluster.append(index)
                            frontier.append(index)
                    clusters.append(cluster)
                for group in clusters:
                    labels = {predictions[index].label for index in group}
                    if (len(group) >= 2 and len(labels) > 1 and anchors[anchor_index].label in labels and
                            all(verifier.supports(label) for label in labels)):
                        groups.append((group, anchors[anchor_index]))
                        claimed.update(group)

        verified_members = {index for group, anchor in groups
                            if isinstance(anchor, VerifiedObject) for index in group}
        full_anchors = [index for index in evidence
                        if not self._tile_only(predictions[index]) and
                        self._sources(predictions[index]) and
                        predictions[index].score is not None and
                        (predictions[index].score >= self.FULL_ANCHOR_SCORE or
                         index in verified_members)]
        competing_full_boxes = [index for index, prediction in enumerate(predictions)
                                if not self._tile_only(prediction) and self._sources(prediction) and
                                prediction.score is not None and prediction.score >= self.FULL_ANCHOR_SCORE]
        full_assignments: dict[int, list[int]] = {index: [] for index in full_anchors}
        geometry_absorbed: set[int] = set()
        truncated_absorbed: set[int] = set()
        for candidate_index in evidence:
            if candidate_index in claimed or not self._tile_only(predictions[candidate_index]):
                continue
            choices = []
            geometry_choices = []
            truncated_choices = []
            for anchor_index in full_anchors:
                anchor = predictions[anchor_index]
                candidate = predictions[candidate_index]
                anchor_area = ((anchor.bbox[2] - anchor.bbox[0]) *
                               (anchor.bbox[3] - anchor.bbox[1]))
                candidate_area = ((candidate.bbox[2] - candidate.bbox[0]) *
                                  (candidate.bbox[3] - candidate.bbox[1]))
                if candidate_area <= 0 or anchor_area < candidate_area * 1.2:
                    continue
                box_containment = self._box_containment(anchor.bbox, candidate.bbox)
                if box_containment < self.FULL_ANCHOR_BOX_CONTAINMENT:
                    continue
                if (anchor.label == candidate.label and
                        anchor_index not in verified_members and
                        anchor.score >= self.GEOMETRY_MIN_ANCHOR_SCORE and
                        candidate.score is not None and
                        candidate.score >= self.TRUNCATED_MIN_FRAGMENT_SCORE and
                        candidate.score <= anchor.score * 1.05 and
                        anchor_area >= candidate_area * self.TRUNCATED_MIN_AREA_RATIO and
                        box_containment >= self.TRUNCATED_BOX_CONTAINMENT and
                        {source.split("/", 1)[0] for source in self._sources(anchor)} &
                        {source.split("/", 1)[0] for source in self._sources(candidate)} and
                        not any(other_index != anchor_index and
                                self._box_containment(predictions[other_index].bbox, candidate.bbox) >= 0.50
                                for other_index in competing_full_boxes)):
                    if windows is None:
                        windows = tile_windows(*oriented_size(), config.HIGH_RECALL_TILE_SIZE,
                                               config.HIGH_RECALL_TILE_OVERLAP)
                    if self._tile_truncates_anchor(candidate.bbox, anchor.bbox, windows):
                        truncated_choices.append(anchor_index)
                if mask_areas[anchor_index] < mask_areas[candidate_index] * 1.1:
                    continue
                coverage = mask_overlap(anchor_index, candidate_index)[1]
                required = (self.FULL_ANCHOR_SAME_COVERAGE if anchor.label == candidate.label
                            else self.FULL_ANCHOR_CROSS_COVERAGE)
                if coverage < required:
                    if (anchor.label == candidate.label and
                            anchor.score >= self.GEOMETRY_MIN_ANCHOR_SCORE and
                            candidate.score is not None and
                            candidate.score <= min(self.GEOMETRY_MAX_FRAGMENT_SCORE,
                                                   anchor.score * self.GEOMETRY_SCORE_RATIO) and
                            anchor_area >= candidate_area * self.GEOMETRY_MIN_AREA_RATIO and
                            box_containment >= self.GEOMETRY_BOX_CONTAINMENT and
                            CandidateRuleRefinementOperation._tile_seam_clipped(candidate.bbox) and
                            {source.split("/", 1)[0] for source in self._sources(anchor)} &
                            {source.split("/", 1)[0] for source in self._sources(candidate)} and
                            mask_box_fill(candidate_index) <= self.GEOMETRY_MAX_MASK_BOX_FILL):
                        geometry_choices.append(anchor_index)
                    continue
                score = coverage * 0.8 + box_containment * 0.2
                choices.append((score, anchor_index))
            choices.sort(reverse=True)
            if choices and (len(choices) == 1 or
                            choices[0][0] - choices[1][0] >= self.FULL_ANCHOR_MARGIN):
                full_assignments[choices[0][1]].append(candidate_index)
            elif not choices and len(geometry_choices) == 1:
                full_assignments[geometry_choices[0]].append(candidate_index)
                geometry_absorbed.add(candidate_index)
            elif not choices and not geometry_choices and len(truncated_choices) == 1:
                full_assignments[truncated_choices[0]].append(candidate_index)
                truncated_absorbed.add(candidate_index)

        # SAM can fail on a narrow prompt while its full-image anchor remains
        # valid. Only a uniquely contained, tile-clipped sliver may fall back
        # to geometry without a fragment mask.
        for candidate_index in indices:
            if candidate_index in evidence or candidate_index in claimed or not self._tile_only(predictions[candidate_index]):
                continue
            candidate = predictions[candidate_index]
            if candidate.score is None or candidate.score > self.GEOMETRY_MAX_FRAGMENT_SCORE:
                continue
            candidate_area = ((candidate.bbox[2] - candidate.bbox[0]) *
                              (candidate.bbox[3] - candidate.bbox[1]))
            if candidate_area <= 0:
                continue
            matches = []
            for anchor_index in full_anchors:
                anchor = predictions[anchor_index]
                anchor_width = anchor.bbox[2] - anchor.bbox[0]
                anchor_height = anchor.bbox[3] - anchor.bbox[1]
                anchor_area = anchor_width * anchor_height
                if (anchor_index in verified_members or
                        anchor_width <= 0 or anchor_height <= 0 or
                        anchor.label != candidate.label or
                        anchor.score < self.GEOMETRY_MIN_ANCHOR_SCORE or
                        candidate.score > anchor.score * self.GEOMETRY_SCORE_RATIO or
                        anchor_area < candidate_area * self.UNMASKED_MIN_AREA_RATIO or
                        self._box_containment(anchor.bbox, candidate.bbox) < 0.97 or
                        min((candidate.bbox[2] - candidate.bbox[0]) / anchor_width,
                            (candidate.bbox[3] - candidate.bbox[1]) / anchor_height) >
                        self.UNMASKED_MAX_SIDE_RATIO or
                        not ({source.split("/", 1)[0] for source in self._sources(anchor)} &
                             {source.split("/", 1)[0] for source in self._sources(candidate)}) or
                        any(other_index != anchor_index and
                            self._box_containment(predictions[other_index].bbox, candidate.bbox) >= 0.50
                            for other_index in competing_full_boxes)):
                    continue
                if windows is None:
                    windows = tile_windows(*oriented_size(), config.HIGH_RECALL_TILE_SIZE,
                                           config.HIGH_RECALL_TILE_OVERLAP)
                if self._tile_truncates_anchor(candidate.bbox, anchor.bbox, windows):
                    matches.append(anchor_index)
            if len(matches) == 1:
                full_assignments[matches[0]].append(candidate_index)
                truncated_absorbed.add(candidate_index)
        for anchor_index, assigned_indices in full_assignments.items():
            if not assigned_indices:
                continue
            existing = next((group for group, _ in groups if anchor_index in group), None)
            if existing is not None:
                existing.extend(assigned_indices)
            else:
                groups.append(([anchor_index, *assigned_indices], anchor_index))
            claimed.update((anchor_index, *assigned_indices))

        for (component, _), union_mask in zip(union_anchors, union_masks):
            if union_mask is None:
                continue
            union_area = int(np.count_nonzero(union_mask))
            eligible = []
            for index in component:
                if index in claimed or index not in evidence:
                    continue
                intersection = int(np.count_nonzero(evidence[index] & union_mask))
                coverage = intersection / mask_areas[index]
                mask_iou = intersection / (mask_areas[index] + union_area - intersection)
                if coverage >= self.UNION_ANCHOR_COVERAGE and mask_iou >= self.ANCHOR_MIN_MASK_IOU:
                    eligible.append(index)
            local_groups: list[list[int]] = []
            for index in eligible:
                matches = [group for group in local_groups if all(
                    mask_overlap(index, member)[0] >= self.UNION_PAIR_MASK_IOU or
                    mask_overlap(index, member)[1] >= self.UNION_PAIR_CONTAINMENT
                    for member in group)]
                if len(matches) == 1:
                    matches[0].append(index)
                else:
                    local_groups.append([index])
            for group in local_groups:
                if len(group) >= 2:
                    groups.append((group, None))
                    claimed.update(group)

        def same_object(first: int, second: int) -> bool:
            if first not in evidence or second not in evidence:
                return False
            if predictions[first].label != predictions[second].label:
                return False
            if not self._related_boxes(predictions[first].bbox, predictions[second].bbox):
                return False
            mask_iou, containment = mask_overlap(first, second)
            return (mask_iou >= self.MERGE_MASK_IOU or
                    (mask_iou >= self.MIN_CONTAINED_MASK_IOU and
                     containment >= self.MERGE_MASK_CONTAINMENT))

        for index in range(len(predictions)):
            if index in claimed:
                continue
            matches = [group for group, anchor in groups if anchor is None and
                       all(same_object(index, member) for member in group)]
            if len(matches) == 1:
                matches[0].append(index)
            else:
                groups.append(([index], None))
        groups.sort(key=lambda item: min(item[0]))

        refined = []
        resolved_conflicts = 0
        for group, anchor in groups:
            if isinstance(anchor, VerifiedObject):
                item = self._combine_verified(predictions, group, anchor)
                resolved_conflicts += 1
            elif isinstance(anchor, int):
                item = self._combine_full_anchor(predictions, group, anchor)
                resolved_conflicts += len({predictions[index].label for index in group}) > 1
            else:
                item = (CandidateRuleRefinementOperation._combine([predictions[index] for index in group])
                        if len(group) > 1 else predictions[group[0]])
                if len(group) > 1:
                    # A partial mask must not shrink a valid larger box.
                    item = replace(item, bbox=(min(predictions[index].bbox[0] for index in group),
                                               min(predictions[index].bbox[1] for index in group),
                                               max(predictions[index].bbox[2] for index in group),
                                               max(predictions[index].bbox[3] for index in group)))
            if any(index in geometry_absorbed for index in group):
                item = replace(item, risk_flags=tuple(sorted({*item.risk_flags, "weak_mask_merge"})))
            if any(index in truncated_absorbed for index in group):
                item = replace(item, risk_flags=tuple(sorted({*item.risk_flags, "tile_fragment_merge"})))
            refined.append(item)
        blocked = set()
        unresolved = 0
        for first_group in range(len(groups)):
            for second_group in range(first_group + 1, len(groups)):
                first_indices, second_indices = groups[first_group][0], groups[second_group][0]
                if self._related_boxes(refined[first_group].bbox, refined[second_group].bbox):
                    unresolved += refined[first_group].label != refined[second_group].label
                if any(predictions[first].label == predictions[second].label and
                       mask_overlap(first, second)[0] <= self.DISTINCT_MASK_IOU and
                       mask_overlap(first, second)[1] <= self.DISTINCT_MASK_CONTAINMENT
                       for first in first_indices for second in second_indices
                       if first in evidence and second in evidence):
                    blocked.add((first_group, second_group))
        return SamRefinementResult(tuple(refined), frozenset(blocked), len(evidence),
                                   len(predictions) - len(refined), len(prompt_boxes),
                                   resolved_conflicts, unresolved, verifier_used, verifier_failed)
