"""Executable quick-labeling workflow for users without their own model."""
from __future__ import annotations

import os
from dataclasses import replace
from typing import Callable

import config
from project import ProjectStore

from .model_registry import ImageModelRegistry
from .model_library import ModelLibrary
from .class_verification import ClassVerificationOperation
from .high_recall import TiledPredictionOperation
from .sam_refinement import SamMaskRefinementOperation, SamRefinementResult
from .quick_operations import (
    AnnotationPersistenceOperation,
    CandidateBoundsOperation,
    CandidateFusionOperation,
    CandidateRuleRefinementOperation,
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
        self.sam_refine = SamMaskRefinementOperation()
        self.refine = CandidateRuleRefinementOperation()
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
        request = replace(request, config=replace(request.config,
                                                 tiled_inference="tile_scan" in state.applied_graph.nodes))
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
        model_library = ModelLibrary()
        installed_models = model_library.installed()
        recovery_paths = {
            "yoloe": config.YOLOE_WEIGHTS,
            "gd15": os.path.join(config.GD15_DIR, "model.safetensors"),
            "gd_ogc": config.GROUNDINGDINO_WEIGHTS,
        }
        recovery_id = context.request.config.recovery_provider_id
        recovery_path = recovery_paths.get(recovery_id)
        if context.request.config.recovery_policy != RecoveryPolicy.OFF and recovery_path:
            active_recovery = any(
                model.provider_id == recovery_id and
                os.path.normcase(os.path.abspath(model.path)) == os.path.normcase(os.path.abspath(recovery_path)) and
                model_library.is_enabled(context.project, model)
                for model in installed_models
            )
            if not active_recovery:
                context.request = replace(context.request, config=replace(
                    context.request.config, recovery_policy=RecoveryPolicy.OFF))
                on_log("[模型管理] 第二模型未安装或已停用，本次跳过召回补扫。")
        optional = []
        if context.request.config.recovery_policy != RecoveryPolicy.OFF:
            optional.append("召回补扫")
        if "bounds_check" in enabled:
            optional.append("候选边界整理")
        if "tile_scan" in enabled:
            optional.append("主模型大图切片补扫")
        sam_model = next((model for model in installed_models
                          if model.provider_id == "sam2" and model_library.is_enabled(context.project, model)), None)
        verifier_model = next((model for model in installed_models
                               if os.path.basename(model.path).lower() == "yolo11n.pt"
                               and model_library.is_enabled(context.project, model)
                               and os.path.normcase(os.path.abspath(model.path)) !=
                               os.path.normcase(os.path.abspath(context.request.config.model_weights_path or ""))), None)
        verifier = ClassVerificationOperation(verifier_model.path) if sam_model and verifier_model else None
        if "sam_mask_refinement" in enabled:
            if sam_model:
                optional.append("SAM 实例融合")
            else:
                on_log("[SAM] 未安装或未启用 SAM 2.1 Tiny，本次跳过掩码去重；可在模型管理下载并启用。")
        on_log("图像标注工作流已开始：" + ("、".join(optional) if optional else "仅主模型检测") + "。")
        completed: list[QuickImageResult] = []
        failed: list[str] = []
        sam_available = sam_model is not None and "sam_mask_refinement" in enabled
        sam_pairs = sam_no_pairs = sam_invalid = sam_failures = 0
        sam_masks = sam_merged = sam_class_resolved = sam_class_unresolved = sam_verifier_failures = 0
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
                tiles = (self.predict_tiles.execute(context, (primary.provider_id,), image_path, should_stop)
                         if "tile_scan" in enabled else ())
                project_categories = getattr(context.project, "categories", ())
                categories = (tuple(project_categories) if isinstance(project_categories, (list, tuple)) and project_categories
                              else tuple(part.strip() for part in context.request.config.prompt.split(".") if part.strip()))
                predicted_count = sum(len(bundle.predictions) for bundle in (primary, recovery, *tiles) if bundle)
                bundles = self.align_classes.execute((primary, recovery, *tiles), categories)
                raw_count = sum(len(bundle.predictions) for bundle in bundles)
                excluded_count = predicted_count - raw_count
                if excluded_count:
                    on_log(f"[类别过滤] {filename}：已排除类别表外的 {excluded_count} 个候选")
                fused = self.fuse.execute(bundles)
                merged_count = raw_count - len(fused)
                predictions = self.triage.execute(fused, bundles)
                if "bounds_check" in enabled:
                    predictions = self.check_bounds.execute(image_path, predictions)
                sam_result = SamRefinementResult(predictions)
                if "sam_mask_refinement" in enabled and sam_model:
                    try:
                        sam_result = self.sam_refine.execute(image_path, predictions, sam_model.path, verifier)
                        predictions = sam_result.predictions
                        if sam_result.prompted:
                            sam_pairs += 1
                            sam_masks += sam_result.analyzed
                            sam_merged += sam_result.merged
                            sam_class_resolved += sam_result.resolved_conflicts
                            sam_class_unresolved += sam_result.unresolved_conflicts
                            sam_verifier_failures += sam_result.verifier_failed
                            if sam_result.analyzed < 2:
                                sam_invalid += 1
                        else:
                            sam_no_pairs += 1
                    except Exception as error:
                        sam_model = None
                        sam_failures += 1
                        on_log(f"[SAM] {filename} 掩码推理失败，已保留全部候选并跳过后续图片：{error}")
                before_rules = len(predictions)
                if "rule_refinement" in enabled:
                    predictions = self.refine.execute(predictions, sam_result.blocked_pairs)
                rule_merged_count = before_rules - len(predictions)
                providers = provider_ids
                risk, rules = self.persist.execute(context, filename, predictions, providers)
                completed.append(QuickImageResult(filename, len(predictions), risk, rules, providers))
                risk_note = f" · 风险 {risk}" if risk else " · 风险低"
                tiled_note = f" · 切片补扫 {len(tiles)} 路" if tiles else ""
                merged_note = f" · 合并重复框 {merged_count} 个" if merged_count else ""
                rule_note = f" · 规则精简 {rule_merged_count} 个" if "rule_refinement" in enabled else ""
                provider_names = {"yoloe": "YOLOE", "gd15": "Grounding DINO Base",
                                  "gd_ogc": "Grounding DINO 旧版", "custom_yolo": "YOLO"}
                shown_providers = " + ".join(provider_names.get(item, item) for item in providers)
                on_log(f"[完成] {filename}：{len(predictions)} 个候选 · {shown_providers}{tiled_note}{merged_note}{rule_note}{risk_note}")
            except Exception as error:
                failed.append(filename)
                on_log(f"[失败] {filename}：{error}")
            if total:
                on_progress(int((index + 1) * 100 / total))

        if sam_available:
            on_log(f"[SAM] 本批实际送入分割：{sam_pairs} 张，得到有效掩码 {sam_masks} 框，"
                   f"合并 {sam_merged} 框，辅助选类 {sam_class_resolved} 处，"
                   f"仍有跨类别重叠 {sam_class_unresolved} 对；无需分割 {sam_no_pairs} 张，"
                   f"掩码无效 {sam_invalid} 张，失败 {sam_failures} 张，辅助模型失败 {sam_verifier_failures} 张。")

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
