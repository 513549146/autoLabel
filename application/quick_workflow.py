"""Executable quick-labeling workflow for users without their own model."""
from __future__ import annotations

import os
from dataclasses import replace
from typing import Callable

from project import ProjectStore

from .model_registry import ImageModelRegistry
from .high_recall import TiledPredictionOperation
from .quick_operations import (
    AnnotationPersistenceOperation,
    CandidateBoundsOperation,
    CandidateFusionOperation,
    CandidateClassAlignmentOperation,
    CandidateReviewTriageOperation,
    CandidatePredictionOperation,
    ImageReadinessOperation,
    QuickOperationContext,
    RecoveryPredictionOperation,
    ReviewRoutingOperation,
    SelectImageTargetsOperation,
    ValidateQuickTaskOperation,
)
from .workflow_contracts import (
    AnnotationWorkflowMode,
    QuickAnnotationRequest,
    QuickAnnotationResult,
    QuickAnnotationSettings,
    QuickImageResult,
    RecoveryPolicy,
)
from .workflow_templates import WorkflowComposerSettings, WorkflowTemplateRegistry


class QuickAnnotationWorkflow:
    """A composable generic pre-labeling route ending in human review.

    Atomic operations remain independently usable for later task orchestration
    or server execution; this class only orders them for today's desktop UI.
    """

    mode = AnnotationWorkflowMode.QUICK

    def __init__(self, registry: ImageModelRegistry | None = None):
        self.registry = registry or ImageModelRegistry()
        self.validate = ValidateQuickTaskOperation()
        self.select_targets = SelectImageTargetsOperation()
        self.check_image = ImageReadinessOperation()
        self.predict = CandidatePredictionOperation()
        self.recover = RecoveryPredictionOperation()
        self.fuse = CandidateFusionOperation()
        self.align_classes = CandidateClassAlignmentOperation()
        self.triage = CandidateReviewTriageOperation()
        self.predict_tiles = TiledPredictionOperation()
        self.check_bounds = CandidateBoundsOperation()
        self.persist = AnnotationPersistenceOperation()
        self.route_review = ReviewRoutingOperation()

    def _context(self, request: QuickAnnotationRequest) -> QuickOperationContext:
        project = ProjectStore(request.annotations_dir, request.images_dir, request.project_root)
        state = WorkflowComposerSettings.load(project)
        template = WorkflowTemplateRegistry().get(state.applied_template_id)
        if not template.executable:
            raise ValueError("当前项目尚未应用可执行的图像标注流程")
        errors = state.applied_graph.validate(template)
        if errors:
            raise ValueError("当前项目工作流无效：" + "；".join(errors))
        if "recovery_model" not in state.applied_graph.nodes:
            request = replace(request, config=replace(request.config, recovery_policy=RecoveryPolicy.OFF))
        return QuickOperationContext(request=request, project=project, registry=self.registry,
                                     applied_steps=state.applied_graph.nodes)

    def plan(self, request: QuickAnnotationRequest) -> tuple[str, ...]:
        context = self._context(request)
        context.request = self.validate.execute(context)
        return self.select_targets.execute(context)

    def save_settings(self, project, settings) -> None:
        QuickAnnotationSettings.save(project, settings)

    def load_settings(self, project):
        return QuickAnnotationSettings.load(project)

    def run(
        self,
        request: QuickAnnotationRequest,
        should_stop: Callable[[], bool],
        on_log: Callable[[str], None],
        on_progress: Callable[[int], None],
    ) -> QuickAnnotationResult:
        context = self._context(request)
        context.request = self.validate.execute(context)
        filenames = context.request.filenames or self.select_targets.execute(context)
        total = len(filenames)
        if not total:
            return QuickAnnotationResult((), False)

        enabled = set(context.applied_steps)
        optional = []
        if "recovery_model" in enabled:
            optional.append("召回补扫")
        if "bounds_check" in enabled:
            optional.append("候选边界整理")
        on_log("图像标注工作流已开始：" + ("、".join(optional) if optional else "仅主模型检测") + "。")
        completed: list[QuickImageResult] = []
        failed: list[str] = []
        for index, filename in enumerate(filenames):
            if should_stop():
                on_log("已按请求停止快速标注。")
                break
            image_path = os.path.join(context.request.images_dir, filename)
            try:
                self.check_image.execute(image_path)
                primary = self.predict.execute(context, context.request.config.primary_provider_id, image_path)
                recovery = self.recover.execute(context, primary, image_path)
                provider_ids = tuple(dict.fromkeys(bundle.provider_id for bundle in (primary, recovery) if bundle))
                tiles = self.predict_tiles.execute(context, provider_ids, image_path, should_stop)
                categories = tuple(part.strip() for part in context.request.config.prompt.split(".") if part.strip())
                bundles = self.align_classes.execute((primary, recovery, *tiles), categories)
                raw_count = sum(len(bundle.predictions) for bundle in bundles)
                fused = self.fuse.execute(bundles)
                merged_count = raw_count - len(fused)
                predictions = self.triage.execute(fused, bundles)
                if "bounds_check" in enabled:
                    predictions = self.check_bounds.execute(image_path, predictions)
                providers = provider_ids
                risk, rules = self.persist.execute(context, filename, predictions, providers)
                completed.append(QuickImageResult(filename, len(predictions), risk, rules, providers))
                risk_note = f" · 风险 {risk}" if risk else " · 风险低"
                tiled_note = f" · 切片补扫 {len(tiles)} 路" if tiles else ""
                merged_note = f" · 合并重复框 {merged_count} 个" if merged_count else ""
                provider_names = {"yoloe": "YOLOE", "gd15": "Grounding DINO Base",
                                  "gd_ogc": "Grounding DINO 旧版", "custom_yolo": "YOLO"}
                shown_providers = " + ".join(provider_names.get(item, item) for item in providers)
                on_log(f"[完成] {filename}：{len(predictions)} 个候选 · {shown_providers}{tiled_note}{merged_note}{risk_note}")
            except Exception as error:
                failed.append(filename)
                on_log(f"[失败] {filename}：{error}")
            if total:
                on_progress(int((index + 1) * 100 / total))

        result = QuickAnnotationResult(tuple(completed), should_stop(), tuple(failed))
        # A training completion does not change approval state.  Only the
        # explicit full-image re-labeling mode reopens approved images after a
        # successful new prediction has actually been written.
        if context.request.config.scope == "all":
            self.route_review.execute(context.project, result.filenames, reopen_approved=True)
        else:
            self.route_review.execute(context.project, result.filenames)
        if result.high_risk_count:
            on_log(f"已将 {result.high_risk_count} 张高风险图片标记到审核队列前列。")
        return result
