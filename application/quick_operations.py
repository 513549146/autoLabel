"""Small, composable operations used by the quick image-labeling workflow."""
from __future__ import annotations

import os
import math
from dataclasses import dataclass, field, replace
from pathlib import Path

from PIL import Image
from engine.image_io import _oriented_pil_size

import config
import core
from project import ProjectStore

from .contracts import ImagePrediction, PredictionBundle
from .model_registry import ImageModelRegistry
from .preannotation import ImagePreannotationService
from .review_queue import ImageReviewQueue
from .workflow_contracts import QuickAnnotationRequest, RecoveryPolicy


def _iou(first: tuple[int, int, int, int], second: tuple[int, int, int, int]) -> float:
    ax1, ay1, ax2, ay2 = first; bx1, by1, bx2, by2 = second
    left, top, right, bottom = max(ax1, bx1), max(ay1, by1), min(ax2, bx2), min(ay2, by2)
    overlap = max(0, right - left) * max(0, bottom - top)
    if not overlap:
        return 0.0
    union = max(1, abs(ax2 - ax1) * abs(ay2 - ay1) + abs(bx2 - bx1) * abs(by2 - by1) - overlap)
    return overlap / union


@dataclass
class QuickOperationContext:
    request: QuickAnnotationRequest
    project: ProjectStore
    registry: ImageModelRegistry
    predictors: dict[str, object] = field(default_factory=dict)
    applied_steps: tuple[str, ...] = ()


class ValidateQuickTaskOperation:
    """Validate stable task input before a worker starts loading model weights."""

    def execute(self, context: QuickOperationContext) -> QuickAnnotationRequest:
        request = context.request
        if not os.path.isdir(request.images_dir) or not os.path.isdir(request.annotations_dir):
            raise ValueError("项目图片或标注目录不可用")
        normalized = request.config.normalized()
        primary = context.registry.descriptor(normalized.primary_provider_id)
        if "detect" not in primary.capabilities:
            raise ValueError("所选模型不支持图像检测")
        if normalized.primary_provider_id == "custom_yolo" or normalized.model_weights_path:
            if not normalized.model_weights_path or not os.path.isfile(normalized.model_weights_path):
                raise ValueError("所选模型权重不存在，请在模型管理页面选择一个可用版本")
        if normalized.recovery_policy != RecoveryPolicy.OFF:
            recovery = context.registry.descriptor(normalized.recovery_provider_id)
            if "open_vocab" not in recovery.capabilities:
                raise ValueError("快速标注的补扫模型必须支持开放词表检测")
        return QuickAnnotationRequest(
            request.project_root, request.images_dir, request.annotations_dir, normalized, request.filenames
        )


class SelectImageTargetsOperation:
    """Turn the selected processing scope into a deterministic project queue."""

    def execute(self, context: QuickOperationContext) -> tuple[str, ...]:
        request = context.request
        return tuple(ImageReviewQueue.target_files(
            request.images_dir, request.annotations_dir, context.project, request.config.scope
        ))


class ImageReadinessOperation:
    """Reject missing or unreadable inputs before model code sees them."""

    def execute(self, image_path: str) -> None:
        if not os.path.isfile(image_path):
            raise FileNotFoundError("图片不存在")
        try:
            with Image.open(image_path) as image:
                image.verify()
        except Exception as error:
            raise ValueError(f"图片无法读取：{error}") from error


class CandidatePredictionOperation:
    """Produce normalized proposals from one provider, caching its loaded model."""

    def execute(self, context: QuickOperationContext, provider_id: str, image_path: str) -> PredictionBundle:
        predictor = context.predictors.get(provider_id)
        if predictor is None:
            weights_path = (context.request.config.model_weights_path
                            if provider_id == context.request.config.primary_provider_id else "")
            predictor = context.registry.create(provider_id, weights_path)
            context.predictors[provider_id] = predictor
        return predictor.predict(image_path, context.request.config.prompt, context.request.config.confidence)


class RecoveryPredictionOperation:
    """Optionally add a second generic detector to improve recall."""

    def execute(
        self, context: QuickOperationContext, primary: PredictionBundle, image_path: str
    ) -> PredictionBundle | None:
        settings = context.request.config
        if settings.recovery_policy == RecoveryPolicy.OFF:
            return None
        if settings.recovery_policy == RecoveryPolicy.EMPTY_ONLY and primary.predictions:
            return None
        if settings.recovery_provider_id == primary.provider_id:
            return None
        return CandidatePredictionOperation().execute(context, settings.recovery_provider_id, image_path)


class CandidateFusionOperation:
    """Merge same-object duplicates while retaining every isolated candidate."""

    @staticmethod
    def _same_object(first: ImagePrediction, second: ImagePrediction) -> bool:
        if first.label != second.label:
            return False
        a, b = first.bbox, second.bbox
        aw, ah, bw, bh = a[2] - a[0], a[3] - a[1], b[2] - b[0], b[3] - b[1]
        if min(aw, ah, bw, bh) <= 0:
            return False
        area_ratio = max(aw * ah, bw * bh) / min(aw * ah, bw * bh)
        if area_ratio > config.FUSION_MAX_AREA_RATIO:
            return False
        center_shift = max(abs(a[0] + a[2] - b[0] - b[2]) / (2 * max(aw, bw)),
                           abs(a[1] + a[3] - b[1] - b[3]) / (2 * max(ah, bh)))
        if center_shift > config.FUSION_MAX_CENTER_SHIFT:
            return False
        first_sources = set(first.sources or ((first.source,) if first.source else ()))
        second_sources = set(second.sources or ((second.source,) if second.source else ()))
        shared_sources = first_sources & second_sources
        if any("/tile" not in source for source in shared_sources):
            threshold = config.FUSION_SAME_FULL_IOU
        elif shared_sources:
            threshold = config.FUSION_SAME_TILE_IOU
        else:
            threshold = config.FUSION_CROSS_STREAM_IOU
        return _iou(a, b) >= threshold

    def execute(self, bundles: tuple[PredictionBundle | None, ...]) -> tuple[ImagePrediction, ...]:
        candidates = [replace(prediction,
                              source=prediction.source or bundle.provider_id,
                              sources=prediction.sources or (prediction.source or bundle.provider_id,))
                      for bundle in bundles if bundle for prediction in bundle.predictions]
        # Raw scores from heterogeneous models are not calibrated against one another.
        # Only pairwise geometry and class decide duplication; score is review context.
        groups: list[list[ImagePrediction]] = []
        for candidate in candidates:
            matches = [group for group in groups if all(self._same_object(candidate, member) for member in group)]
            group = max(matches, key=lambda members: min(_iou(candidate.bbox, member.bbox)
                                                        for member in members), default=None)
            if group is None:
                groups.append([candidate])
            else:
                group.append(candidate)
        accepted = []
        for group in groups:
            sources = tuple(dict.fromkeys(source for item in group
                                          for source in (item.sources or (item.source,)) if source))
            flags = set(flag for item in group for flag in item.risk_flags)
            bbox = (min(item.bbox[0] for item in group), min(item.bbox[1] for item in group),
                    max(item.bbox[2] for item in group), max(item.bbox[3] for item in group))
            scores = [item.score for item in group if item.score is not None]
            source_scores = {}
            for item in group:
                for source, score in item.source_scores or (((item.source, item.score),) if item.score is not None and item.source else ()):
                    source_scores[source] = max(source_scores.get(source, float("-inf")), float(score))
            accepted.append(replace(group[0], bbox=bbox, score=max(scores) if scores else None,
                                    sources=sources, source_scores=tuple(source_scores.items()),
                                    risk_flags=tuple(sorted(flags))))
        return tuple(accepted)


class CandidateRuleRefinementOperation:
    """Collapse remaining same-object proposals using geometry and comparable scores.

    A weak score never removes a spatially isolated candidate. Raw scores from
    different providers are deliberately not compared.
    """

    @staticmethod
    def _models(prediction: ImagePrediction) -> set[str]:
        sources = prediction.sources or ((prediction.source,) if prediction.source else ())
        return {source.split("/", 1)[0] for source in sources if source}

    @staticmethod
    def _score_for_models(prediction: ImagePrediction, models: set[str]) -> float | None:
        scores = [float(score) for source, score in prediction.source_scores
                  if source.split("/", 1)[0] in models and math.isfinite(float(score))]
        if scores:
            return max(scores)
        if prediction.score is not None and CandidateRuleRefinementOperation._models(prediction) <= models:
            score = float(prediction.score)
            return score if math.isfinite(score) else None
        return None

    @staticmethod
    def _tile_seam_clipped(bbox: tuple[int, int, int, int]) -> bool:
        stride = round(config.HIGH_RECALL_TILE_SIZE * (1 - config.HIGH_RECALL_TILE_OVERLAP))
        if stride <= 0:
            return False
        for value, offset in zip(bbox, (0, 0, config.HIGH_RECALL_TILE_SIZE, config.HIGH_RECALL_TILE_SIZE)):
            if value <= 0:
                continue
            distance = (value - offset) % stride
            if min(distance, stride - distance) <= config.RULE_TILE_EDGE_TOLERANCE:
                return True
        return False

    @classmethod
    def _tile_partial_pair(cls, first: ImagePrediction, second: ImagePrediction,
                           shared_models: set[str], first_score: float | None,
                           second_score: float | None) -> ImagePrediction | None:
        a, b = first.bbox, second.bbox
        area_a = (a[2] - a[0]) * (a[3] - a[1])
        area_b = (b[2] - b[0]) * (b[3] - b[1])
        if min(area_a, area_b) <= 0 or not shared_models:
            return None
        small, big = (first, second) if area_a < area_b else (second, first)
        small_score, big_score = (first_score, second_score) if small is first else (second_score, first_score)
        if small_score is None or big_score is None or big_score <= 0:
            return None
        small_sources = small.sources or ((small.source,) if small.source else ())
        big_sources = big.sources or ((big.source,) if big.source else ())
        if (not small_sources or not all(source.endswith("/tile") for source in small_sources) or
                not any("/tile" not in source for source in big_sources) or
                small_score > big_score * config.RULE_TILE_WEAKER_RATIO or
                not cls._tile_seam_clipped(small.bbox)):
            return None
        s, g = small.bbox, big.bbox
        sw, sh, gw, gh = s[2] - s[0], s[3] - s[1], g[2] - g[0], g[3] - g[1]
        overlap = max(0, min(s[2], g[2]) - max(s[0], g[0])) * max(0, min(s[3], g[3]) - max(s[1], g[1]))
        containment = overlap / (sw * sh)
        area_ratio = (gw * gh) / (sw * sh)
        center_shift = max(abs(s[0] + s[2] - g[0] - g[2]) / (2 * max(sw, gw)),
                           abs(s[1] + s[3] - g[1] - g[3]) / (2 * max(sh, gh)))
        if (containment >= config.RULE_TILE_CONTAINMENT and
                area_ratio <= config.RULE_TILE_AREA_RATIO and
                center_shift <= config.RULE_TILE_CENTER_SHIFT):
            return big
        return None

    @classmethod
    def _same_object(cls, first: ImagePrediction, second: ImagePrediction) -> bool:
        if first.label != second.label:
            return False
        a, b = first.bbox, second.bbox
        aw, ah, bw, bh = a[2] - a[0], a[3] - a[1], b[2] - b[0], b[3] - b[1]
        if min(aw, ah, bw, bh) <= 0:
            return False
        overlap = _iou(a, b)
        area_ratio = max(aw * ah, bw * bh) / min(aw * ah, bw * bh)
        center_shift = max(abs(a[0] + a[2] - b[0] - b[2]) / (2 * max(aw, bw)),
                           abs(a[1] + a[3] - b[1] - b[3]) / (2 * max(ah, bh)))
        if (overlap >= config.RULE_STRONG_IOU and
                area_ratio <= config.RULE_STRONG_AREA_RATIO and
                center_shift <= config.RULE_STRONG_CENTER_SHIFT):
            return True
        shared_models = cls._models(first) & cls._models(second)
        if not shared_models:
            return False
        first_score = cls._score_for_models(first, shared_models)
        second_score = cls._score_for_models(second, shared_models)
        if first_score is None or second_score is None or max(first_score, second_score) <= 0:
            return False
        if (overlap >= config.RULE_SCORED_IOU and
                area_ratio <= config.RULE_SCORED_AREA_RATIO and
                center_shift <= config.RULE_SCORED_CENTER_SHIFT and
                min(first_score, second_score) / max(first_score, second_score) <= config.RULE_SCORED_WEAKER_RATIO):
            return True
        return cls._tile_partial_pair(first, second, shared_models, first_score, second_score) is not None

    @classmethod
    def _combine(cls, group: list[ImagePrediction]) -> ImagePrediction:
        common_models = set.intersection(*(cls._models(item) for item in group))
        weights = [cls._score_for_models(item, common_models) if common_models else None for item in group]
        if not common_models or any(weight is None for weight in weights):
            weights = [1.0] * len(group)
        weights = [max(0.05, weight) for weight in weights]
        total = sum(weights)
        bbox = tuple(round(sum(item.bbox[axis] * weight for item, weight in zip(group, weights)) / total)
                     for axis in range(4))
        for first in group:
            for second in group:
                if first is second:
                    continue
                shared = cls._models(first) & cls._models(second)
                big = cls._tile_partial_pair(first, second, shared,
                                             cls._score_for_models(first, shared),
                                             cls._score_for_models(second, shared))
                if big is not None:
                    bbox = big.bbox
                    break
            else:
                continue
            break
        sources = tuple(dict.fromkeys(source for item in group
                                      for source in (item.sources or (item.source,)) if source))
        source_scores: dict[str, float] = {}
        for item in group:
            pairs = item.source_scores or (((item.source, item.score),)
                                           if item.source and item.score is not None else ())
            for source, score in pairs:
                source_scores[source] = max(source_scores.get(source, float("-inf")), float(score))
        flags = {flag for item in group for flag in item.risk_flags}
        if len({source.split("/", 1)[0] for source in sources}) > 1:
            flags.discard("single_model")
        if any("/tile" not in source for source in sources):
            flags.discard("tile_only")
        scores = [item.score for item in group if item.score is not None]
        verified = next((item for item in group if item.source == "yolo11n_verify" and
                         "class_conflict" in item.risk_flags), None)
        representative = verified or group[0]
        alternatives = tuple(dict.fromkeys(label for item in group for label in item.alternate_labels
                                           if label != representative.label))
        return replace(representative, bbox=bbox,
                       score=verified.score if verified else max(scores) if scores else None,
                       sources=sources, source_scores=tuple(source_scores.items()),
                       risk_flags=tuple(sorted(flags)), alternate_labels=alternatives)

    def execute(self, predictions: tuple[ImagePrediction, ...],
                blocked_pairs: frozenset[tuple[int, int]] = frozenset()) -> tuple[ImagePrediction, ...]:
        groups: list[list[tuple[int, ImagePrediction]]] = []
        for index, candidate in sorted(enumerate(predictions),
                                       key=lambda item: (item[1].label, item[1].bbox, item[1].source)):
            matches = [group for group in groups
                       if all((min(index, member_index), max(index, member_index)) not in blocked_pairs
                              and self._same_object(candidate, member)
                              for member_index, member in group)]
            # Two plausible groups signal a crowded scene; retain the candidate.
            if len(matches) == 1:
                matches[0].append((index, candidate))
            else:
                groups.append([(index, candidate)])
        result = [(min(index for index, _ in group), self._combine([item for _, item in group])
                   if len(group) > 1 else group[0][1]) for group in groups]
        return tuple(item for _, item in sorted(result, key=lambda pair: pair[0]))


class CandidateClassAlignmentOperation:
    """Keep only configured project classes in persisted preannotations."""

    def execute(self, bundles, category_names):
        canonical = {name.strip().casefold(): name.strip() for name in category_names if name.strip()}
        aligned = []
        for bundle in bundles:
            if bundle is None:
                continue
            proposals = []
            for proposal in bundle.predictions:
                label = proposal.label.strip().strip(" .")
                matched = canonical.get(label.casefold())
                if canonical and not matched:
                    continue
                proposals.append(replace(proposal, label=matched or label or "object",
                                         risk_flags=proposal.risk_flags))
            aligned.append(replace(bundle, predictions=tuple(proposals)))
        return tuple(aligned)


class CandidateReviewTriageOperation:
    """Mark proposals found by only one model or only on image tiles."""

    def execute(self, predictions, bundles):
        expected_models = {bundle.provider_id for bundle in bundles}
        result = []
        for prediction in predictions:
            sources = prediction.sources or ((prediction.source,) if prediction.source else ())
            models = {source.split("/", 1)[0] for source in sources}
            flags = set(prediction.risk_flags)
            if len(expected_models) > 1 and len(models) == 1:
                flags.add("single_model")
            if sources and all(source.endswith("/tile") for source in sources):
                flags.add("tile_only")
            result.append(replace(prediction, risk_flags=tuple(sorted(flags))))
        return tuple(result)


class CandidateBoundsOperation:
    """Clip candidate boxes to the image and drop boxes without visible area."""

    def execute(self, image_path: str, predictions: tuple[ImagePrediction, ...]) -> tuple[ImagePrediction, ...]:
        with Image.open(image_path) as image:
            width, height = _oriented_pil_size(image)
        cleaned = []
        for prediction in predictions:
            left, top, right, bottom = prediction.bbox
            clipped = (
                max(0, min(width, left)), max(0, min(height, top)),
                max(0, min(width, right)), max(0, min(height, bottom)),
            )
            if clipped[2] > clipped[0] and clipped[3] > clipped[1]:
                cleaned.append(replace(prediction, bbox=clipped))
        return tuple(cleaned)


class AnnotationPersistenceOperation:
    """Version any previous XML, atomically write proposals, then calculate review risk."""

    def execute(
        self, context: QuickOperationContext, filename: str, predictions: tuple[ImagePrediction, ...], providers: tuple[str, ...]
    ) -> tuple[int, tuple[str, ...]]:
        request = context.request
        image_path = os.path.join(request.images_dir, filename)
        xml_path = os.path.join(request.annotations_dir, Path(filename).stem + ".xml")
        previous_xml = Path(xml_path).read_bytes() if os.path.isfile(xml_path) else None
        if os.path.isfile(xml_path):
            ImagePreannotationService.snapshot_annotation(
                context.project, filename, image_path, xml_path, "重跑前结果", "+".join(providers), request.config.prompt,
                deduplicate=True,
            )
        annotations = [prediction.as_annotation() for prediction in predictions]
        try:
            core.save_as_voc_xml(annotations, image_path, request.annotations_dir)
            risk, rules = core.annotation_risk(annotations, image_path)
            ImagePreannotationService.snapshot_annotation(
                context.project, filename, image_path, xml_path, "快速预标注", "+".join(providers), request.config.prompt
            )
        except Exception:
            if previous_xml is None:
                if os.path.exists(xml_path):
                    os.remove(xml_path)
            else:
                restore_path = xml_path + ".restore"
                try:
                    Path(restore_path).write_bytes(previous_xml)
                    os.replace(restore_path, xml_path)
                finally:
                    if os.path.exists(restore_path):
                        os.remove(restore_path)
                previous_annotations = core.load_voc_xml(xml_path)
                old_risk, old_rules = core.annotation_risk(previous_annotations, image_path)
                context.project.upsert_quality(filename, old_risk, old_rules)
            raise
        return risk, tuple(sorted(rules))


class ReviewRoutingOperation:
    """Move completed quick-labeling work into the existing human-review queue."""

    def execute(self, project: ProjectStore, filenames: tuple[str, ...], reopen_approved=False) -> None:
        if filenames:
            project.mark_batch_for_review(filenames, include_approved=reopen_approved, include_skipped=True)
