"""Small, composable operations used by the quick image-labeling workflow."""
from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path

from PIL import Image

import config
import core
from project import ProjectStore

from .contracts import ImagePrediction, PredictionBundle
from .model_registry import ImageModelRegistry
from .preannotation import ImagePreannotationService
from .review_queue import ImageReviewQueue
from .workflow_contracts import QuickAnnotationRequest, RecoveryPolicy


def _iou(first: tuple[int, int, int, int], second: tuple[int, int, int, int]) -> float:
    """计算两个 xyxy 框的交并比，用于同类重叠候选的合并判定。"""
    ax1, ay1, ax2, ay2 = first; bx1, by1, bx2, by2 = second
    left, top, right, bottom = max(ax1, bx1), max(ay1, by1), min(ax2, bx2), min(ay2, by2)
    overlap = max(0, right - left) * max(0, bottom - top)
    if not overlap:
        return 0.0
    union = max(1, abs(ax2 - ax1) * abs(ay2 - ay1) + abs(bx2 - bx1) * abs(by2 - by1) - overlap)
    return overlap / union


@dataclass
class QuickOperationContext:
    """一次快速标注运行的共享上下文：请求、项目库、模型注册表与已缓存预测器。"""
    request: QuickAnnotationRequest
    project: ProjectStore
    registry: ImageModelRegistry
    predictors: dict[str, object] = field(default_factory=dict)
    applied_steps: tuple[str, ...] = ()


class ValidateQuickTaskOperation:
    """Validate stable task input before a worker starts loading model weights."""

    def execute(self, context: QuickOperationContext) -> QuickAnnotationRequest:
        """在加载权重前校验目录、提示词、模型能力与自定义权重是否存在。"""
        request = context.request
        if not os.path.isdir(request.images_dir) or not os.path.isdir(request.annotations_dir):
            raise ValueError("项目图片或标注目录不可用")
        normalized = request.config.normalized()
        primary = context.registry.descriptor(normalized.primary_provider_id)
        if "detect" not in primary.capabilities:
            raise ValueError("所选模型不支持图像检测")
        if normalized.primary_provider_id == "custom_yolo":
            if not normalized.model_weights_path or not os.path.isfile(normalized.model_weights_path):
                raise ValueError("项目训练模型不存在，请在自动标注页重新选择一个可用模型")
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
        """按处理范围把项目图片解析成本次要处理的文件队列。"""
        request = context.request
        return tuple(ImageReviewQueue.target_files(
            request.images_dir, request.annotations_dir, context.project, request.config.scope
        ))


class ImageReadinessOperation:
    """Reject missing or unreadable inputs before model code sees them."""

    def execute(self, image_path: str) -> None:
        """先用 Pillow 校验图片可读，避免坏文件让模型代码崩溃。"""
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
        """按 provider 调用模型并缓存已加载实例，避免逐图重复加载。"""
        predictor = context.predictors.get(provider_id)
        if predictor is None:
            weights_path = context.request.config.model_weights_path if provider_id == "custom_yolo" else ""
            predictor = context.registry.create(provider_id, weights_path)
            context.predictors[provider_id] = predictor
        return predictor.predict(image_path, context.request.config.prompt, context.request.config.confidence)


class RecoveryPredictionOperation:
    """Optionally add a second generic detector to improve recall."""

    def execute(
        self, context: QuickOperationContext, primary: PredictionBundle, image_path: str
    ) -> PredictionBundle | None:
        """按补扫策略决定是否追加第二个开放词表模型来提升召回。"""
        settings = context.request.config
        if settings.recovery_policy == RecoveryPolicy.OFF:
            return None
        if settings.recovery_policy == RecoveryPolicy.EMPTY_ONLY and primary.predictions:
            return None
        if settings.recovery_provider_id == primary.provider_id:
            return None
        return CandidatePredictionOperation().execute(context, settings.recovery_provider_id, image_path)


class CandidateFusionOperation:
    """Class-aware NMS keeps the highest-confidence proposal from overlapping models."""

    def execute(self, bundles: tuple[PredictionBundle | None, ...]) -> tuple[ImagePrediction, ...]:
        """按类别做 NMS，合并多模型产生的重叠候选并保留高分框。"""
        candidates = [prediction for bundle in bundles if bundle for prediction in bundle.predictions]
        candidates.sort(key=lambda item: item.score if item.score is not None else -1.0, reverse=True)
        accepted: list[ImagePrediction] = []
        for candidate in candidates:
            if any(
                candidate.label == selected.label and _iou(candidate.bbox, selected.bbox) >= config.YOLO_IOU
                for selected in accepted
            ):
                continue
            accepted.append(candidate)
        return tuple(accepted)


class CandidateBoundsOperation:
    """Clip candidate boxes to the image and drop boxes without visible area."""

    def execute(self, image_path: str, predictions: tuple[ImagePrediction, ...]) -> tuple[ImagePrediction, ...]:
        """把候选框裁剪到图片范围内，并丢弃没有可见面积的框。"""
        with Image.open(image_path) as image:
            width, height = image.size
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
        """先快照旧 XML，再原子写入新候选并计算风险分。"""
        request = context.request
        image_path = os.path.join(request.images_dir, filename)
        xml_path = os.path.join(request.annotations_dir, Path(filename).stem + ".xml")
        if os.path.isfile(xml_path):
            ImagePreannotationService.snapshot_annotation(
                context.project, filename, image_path, xml_path, "重跑前结果", "+".join(providers), request.config.prompt,
                deduplicate=True,
            )
        annotations = [prediction.as_annotation() for prediction in predictions]
        core.save_as_voc_xml(annotations, image_path, request.annotations_dir)
        risk, rules = core.annotation_risk(annotations, image_path)
        ImagePreannotationService.snapshot_annotation(
            context.project, filename, image_path, xml_path, "快速预标注", "+".join(providers), request.config.prompt
        )
        return risk, tuple(sorted(rules))


class ReviewRoutingOperation:
    """Move completed quick-labeling work into the existing human-review queue."""

    def execute(self, project: ProjectStore, filenames: tuple[str, ...], reopen_approved=False) -> None:
        """把本次完成的图片送入待审核队列，必要时重开已通过的图片。"""
        if filenames:
            project.mark_batch_for_review(filenames, include_approved=reopen_approved, include_skipped=True)
