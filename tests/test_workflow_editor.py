"""Contract tests for project workflow drafts and constrained execution."""
from __future__ import annotations

import unittest
import os
from unittest.mock import Mock, patch

import config
from application.contracts import ImagePrediction, PredictionBundle
from application.quick_operations import CandidateBoundsOperation, QuickOperationContext
from application.quick_workflow import QuickAnnotationWorkflow
from application.model_library import InstalledModel
from application.class_verification import ClassVerificationOperation
from application.sam_refinement import SamRefinementResult
from application.workflow_contracts import QuickAnnotationConfig, QuickAnnotationRequest, RecoveryPolicy
from application.workflow_templates import (
    CUSTOM_WORKFLOW, QUICK_WORKFLOW, WorkflowComposerSettings, WorkflowGraph, WorkflowProjectState,
)


class _MetadataProject:
    def __init__(self):
        self.values = {}

    def metadata_value(self, key, default=""):
        return self.values.get(key, default)

    def set_metadata_value(self, key, value):
        self.values[key] = value


class WorkflowEditorTests(unittest.TestCase):
    def test_optional_branch_and_cycle_constraints(self):
        preset = WorkflowGraph.preset(QUICK_WORKFLOW)
        self.assertEqual(preset.validate(QUICK_WORKFLOW), ())
        self.assertNotIn("tile_scan", preset.nodes)
        without_recovery = preset.without_optional(QUICK_WORKFLOW, "recovery_model")
        self.assertEqual(without_recovery.validate(QUICK_WORKFLOW), ())
        broken = WorkflowGraph("quick", preset.nodes,
                               tuple(edge for edge in preset.edges if edge != ("primary_model", "class_alignment")))
        self.assertIn("必经步骤之间的依赖不能断开", broken.validate(QUICK_WORKFLOW))
        with_tile = without_recovery.with_optional(QUICK_WORKFLOW, "tile_scan")
        self.assertEqual(with_tile.validate(QUICK_WORKFLOW), ())
        self.assertIn(("tile_scan", "class_alignment"), with_tile.edges)
        self.assertNotIn(("recovery_model", "tile_scan"), with_tile.edges)
        cycle = WorkflowGraph("quick", preset.nodes, (*preset.edges, ("human_review", "primary_model")))
        self.assertIn("流程存在循环依赖", cycle.validate(QUICK_WORKFLOW))
        self.assertEqual(without_recovery.with_optional(QUICK_WORKFLOW, "recovery_model"),
                         preset)

    def test_draft_does_not_replace_applied_graph(self):
        project = _MetadataProject()
        state = WorkflowProjectState()
        state.drafts["quick"] = state.applied_graph.without_optional(QUICK_WORKFLOW, "recovery_model")
        state.quick_recovery_policy = "all_images"
        WorkflowComposerSettings.save(project, state)
        restored = WorkflowComposerSettings.load(project)
        self.assertIn("recovery_model", restored.applied_graph.nodes)
        self.assertNotIn("recovery_model", restored.draft(QUICK_WORKFLOW).nodes)
        self.assertEqual(restored.quick_recovery_policy, "all_images")

    def test_saved_old_graph_is_upgraded_without_reenabling_optional_recovery(self):
        project = _MetadataProject()
        old_nodes = ("project_images", "target_queue", "image_check", "primary_model",
                     "candidate_fusion", "risk_and_version", "human_review")
        old_edges = (("project_images", "target_queue"), ("target_queue", "image_check"),
                     ("image_check", "primary_model"), ("primary_model", "candidate_fusion"),
                     ("candidate_fusion", "risk_and_version"), ("risk_and_version", "human_review"))
        import json
        project.set_metadata_value(WorkflowComposerSettings.METADATA_KEY, json.dumps({
            "applied_template": "quick", "draft_template": "quick",
            "applied_graph": {"nodes": old_nodes, "edges": old_edges},
            "drafts": {"quick": {"nodes": old_nodes, "edges": old_edges}},
            "layouts": {"quick": {"primary_model": [99, 99]}},
        }))
        state = WorkflowComposerSettings.load(project)
        self.assertEqual(state.applied_graph.validate(QUICK_WORKFLOW), ())
        self.assertNotIn("recovery_model", state.applied_graph.nodes)
        self.assertNotIn("tile_scan", state.applied_graph.nodes)
        self.assertIn("class_alignment", state.applied_graph.nodes)
        self.assertIn("review_triage", state.applied_graph.nodes)
        self.assertIn("rule_refinement", state.applied_graph.nodes)
        self.assertIn("sam_mask_refinement", state.applied_graph.nodes)
        self.assertNotIn("quick", state.layouts)

    def test_saved_tile_graph_keeps_enabled_tile_and_adds_full_image_bypass(self):
        project = _MetadataProject()
        preset = WorkflowGraph.preset(QUICK_WORKFLOW).with_optional(QUICK_WORKFLOW, "tile_scan")
        old = WorkflowGraph("quick", preset.nodes,
                            tuple(edge for edge in preset.edges
                                  if edge not in {("primary_model", "class_alignment"),
                                                  ("recovery_model", "class_alignment")}))
        state = WorkflowProjectState(applied_graph=old, drafts={"quick": old})
        WorkflowComposerSettings.save(project, state)
        restored = WorkflowComposerSettings.load(project)
        self.assertIn("tile_scan", restored.applied_graph.nodes)
        self.assertIn(("primary_model", "class_alignment"), restored.applied_graph.edges)
        self.assertEqual(restored.applied_graph.validate(QUICK_WORKFLOW), ())

    def test_saved_graph_missing_only_final_rules_is_upgraded(self):
        project = _MetadataProject()
        preset = WorkflowGraph.preset(QUICK_WORKFLOW)
        old = WorkflowGraph("quick", tuple(node for node in preset.nodes if node != "rule_refinement"),
                            tuple(edge for edge in preset.edges if "rule_refinement" not in edge) +
                            (("review_triage", "risk_and_version"),))
        state = WorkflowProjectState(applied_graph=old)
        WorkflowComposerSettings.save(project, state)
        restored = WorkflowComposerSettings.load(project)
        self.assertIn("rule_refinement", restored.applied_graph.nodes)
        self.assertEqual(restored.applied_graph.validate(QUICK_WORKFLOW), ())

    def test_applied_graph_disables_recovery_in_runner(self):
        project = _MetadataProject()
        state = WorkflowProjectState(
            applied_graph=WorkflowGraph.preset(QUICK_WORKFLOW).without_optional(
                QUICK_WORKFLOW, "recovery_model"
            )
        )
        WorkflowComposerSettings.save(project, state)
        request = QuickAnnotationRequest(
            "project", "images", "annotations",
            QuickAnnotationConfig(recovery_policy=RecoveryPolicy.ALL_IMAGES),
        )
        with patch("application.quick_workflow.ProjectStore", return_value=project):
            context = QuickAnnotationWorkflow()._context(request)
        self.assertEqual(context.request.config.recovery_policy, RecoveryPolicy.OFF)
        self.assertFalse(context.request.config.tiled_inference)

    def test_applied_tile_node_controls_runner_setting(self):
        project = _MetadataProject()
        graph = WorkflowGraph.preset(QUICK_WORKFLOW).with_optional(QUICK_WORKFLOW, "tile_scan")
        WorkflowComposerSettings.save(project, WorkflowProjectState(applied_graph=graph))
        request = QuickAnnotationRequest("project", "images", "annotations", QuickAnnotationConfig())
        with patch("application.quick_workflow.ProjectStore", return_value=project):
            context = QuickAnnotationWorkflow()._context(request)
        self.assertTrue(context.request.config.tiled_inference)

    def test_custom_flow_persists_and_controls_runner(self):
        project = _MetadataProject()
        graph = WorkflowGraph.preset(CUSTOM_WORKFLOW).without_optional(CUSTOM_WORKFLOW, "recovery_model")
        state = WorkflowProjectState(applied_template_id="custom", draft_template_id="custom",
                                     applied_graph=graph, drafts={"custom": graph})
        WorkflowComposerSettings.save(project, state)
        restored = WorkflowComposerSettings.load(project)
        self.assertEqual(restored.applied_template_id, "custom")
        self.assertEqual(restored.applied_graph, graph)
        request = QuickAnnotationRequest(
            "project", "images", "annotations",
            QuickAnnotationConfig(recovery_policy=RecoveryPolicy.ALL_IMAGES),
        )
        with patch("application.quick_workflow.ProjectStore", return_value=project):
            context = QuickAnnotationWorkflow()._context(request)
        self.assertEqual(context.request.config.recovery_policy, RecoveryPolicy.OFF)
        self.assertIn("bounds_check", context.applied_steps)

    def test_candidate_bounds_operation_is_executable(self):
        image = type("Image", (), {"size": (100, 80), "__enter__": lambda self: self,
                                   "__exit__": lambda self, *_: None})()
        predictions = (
            ImagePrediction("fruit", (-5, 4, 120, 90)),
            ImagePrediction("fruit", (120, 20, 130, 40)),
        )
        with patch("application.quick_operations.Image.open", return_value=image):
            result = CandidateBoundsOperation().execute("image.png", predictions)
        self.assertEqual([item.bbox for item in result], [(0, 4, 100, 80)])

    def test_runner_dispatches_custom_optional_operation(self):
        request = QuickAnnotationRequest("project", "images", "annotations", QuickAnnotationConfig(), ("a.png",))
        workflow = QuickAnnotationWorkflow()
        project = Mock()
        context = QuickOperationContext(request, project, workflow.registry,
                                        applied_steps=WorkflowGraph.preset(CUSTOM_WORKFLOW).nodes)
        bundle = PredictionBundle("yoloe", "test", ())
        workflow.validate.execute = Mock(return_value=request)
        workflow.check_image.execute = Mock()
        workflow.predict.execute = Mock(return_value=bundle)
        workflow.recover.execute = Mock(return_value=None)
        workflow.fuse.execute = Mock(return_value=())
        workflow.triage.execute = Mock(return_value=())
        workflow.refine.execute = Mock(return_value=())
        workflow.predict_tiles.execute = Mock(return_value=())
        workflow.check_bounds.execute = Mock(return_value=())
        workflow.persist.execute = Mock(return_value=(0, ()))
        workflow.route_review.execute = Mock()
        logs = []
        with patch.object(workflow, "_context", return_value=context), \
             patch("application.quick_workflow.ModelLibrary") as library:
            library.return_value.installed.return_value = ()
            result = workflow.run(request, lambda: False, logs.append, lambda percent: None)
        self.assertEqual(result.filenames, ("a.png",))
        workflow.check_bounds.execute.assert_called_once()
        workflow.predict_tiles.execute.assert_not_called()
        workflow.refine.execute.assert_called_once_with((), frozenset())
        self.assertTrue(any("规则精简 0 个" in message for message in logs))
        self.assertTrue(any("未安装或未启用 SAM" in message for message in logs))
        self.assertFalse(any("用时" in message or "0.0s" in message for message in logs))
        workflow.route_review.execute.assert_called_once_with(project, ("a.png",))

    def test_enabled_tile_scans_primary_model_only(self):
        request = QuickAnnotationRequest("project", "images", "annotations",
                                         QuickAnnotationConfig(tiled_inference=True), ("a.png",))
        workflow = QuickAnnotationWorkflow()
        project = Mock()
        graph = WorkflowGraph.preset(QUICK_WORKFLOW).with_optional(QUICK_WORKFLOW, "tile_scan")
        context = QuickOperationContext(request, project, workflow.registry, applied_steps=graph.nodes)
        primary = PredictionBundle("yoloe", "test", ())
        recovery = PredictionBundle("gd15", "test", ())
        workflow.validate.execute = Mock(return_value=request)
        workflow.check_image.execute = Mock()
        workflow.predict.execute = Mock(return_value=primary)
        workflow.recover.execute = Mock(return_value=recovery)
        workflow.predict_tiles.execute = Mock(return_value=())
        workflow.fuse.execute = Mock(return_value=())
        workflow.triage.execute = Mock(return_value=())
        workflow.refine.execute = Mock(return_value=())
        workflow.persist.execute = Mock(return_value=(0, ()))
        workflow.route_review.execute = Mock()
        with patch.object(workflow, "_context", return_value=context), \
             patch("application.quick_workflow.ModelLibrary") as library:
            library.return_value.installed.return_value = ()
            workflow.run(request, lambda: False, lambda _message: None, lambda _percent: None)
        workflow.predict_tiles.execute.assert_called_once()
        self.assertEqual(workflow.predict_tiles.execute.call_args.args[1], ("yoloe",))

    def test_disabled_recovery_model_is_not_loaded(self):
        request = QuickAnnotationRequest("project", "images", "annotations", QuickAnnotationConfig(), ("a.png",))
        workflow = QuickAnnotationWorkflow()
        project = Mock()
        context = QuickOperationContext(request, project, workflow.registry,
                                        applied_steps=WorkflowGraph.preset(QUICK_WORKFLOW).nodes)
        primary = PredictionBundle("yoloe", "test", ())
        workflow.validate.execute = Mock(return_value=request)
        workflow.check_image.execute = Mock()
        workflow.predict.execute = Mock(return_value=primary)
        workflow.registry.create = Mock(side_effect=AssertionError("disabled recovery loaded"))
        workflow.fuse.execute = Mock(return_value=())
        workflow.triage.execute = Mock(return_value=())
        workflow.refine.execute = Mock(return_value=())
        workflow.persist.execute = Mock(return_value=(0, ()))
        workflow.route_review.execute = Mock()
        logs = []
        with patch.object(workflow, "_context", return_value=context), \
             patch("application.quick_workflow.ModelLibrary") as library:
            library.return_value.installed.return_value = (
                InstalledModel("Grounding DINO", "gd15", os.path.join(config.GD15_DIR,
                                                                    "model.safetensors"), "应用自带", ""),)
            library.return_value.is_enabled.return_value = False
            workflow.run(request, lambda: False, logs.append, lambda _percent: None)
        self.assertEqual(context.request.config.recovery_policy, RecoveryPolicy.OFF)
        self.assertTrue(any("第二模型未安装或已停用" in message for message in logs))

    def test_runner_passes_sam_protection_to_rules_before_persisting(self):
        request = QuickAnnotationRequest("project", "images", "annotations", QuickAnnotationConfig(), ("a.png",))
        workflow = QuickAnnotationWorkflow()
        project = Mock()
        context = QuickOperationContext(request, project, workflow.registry,
                                        applied_steps=WorkflowGraph.preset(QUICK_WORKFLOW).nodes)
        box = ImagePrediction("person", (10, 10, 110, 110), .5, "yoloe")
        bundle = PredictionBundle("yoloe", "test", (box,))
        workflow.validate.execute = Mock(return_value=request)
        workflow.check_image.execute = Mock()
        workflow.predict.execute = Mock(return_value=bundle)
        workflow.recover.execute = Mock(return_value=None)
        workflow.predict_tiles.execute = Mock(return_value=())
        workflow.fuse.execute = Mock(return_value=(box,))
        workflow.triage.execute = Mock(return_value=(box,))
        workflow.sam_refine.execute = Mock(return_value=SamRefinementResult((box,), frozenset(), 2, 1, 2))
        workflow.refine.execute = Mock(return_value=(box,))
        workflow.persist.execute = Mock(return_value=(0, ()))
        workflow.route_review.execute = Mock()
        model = InstalledModel("SAM", "sam2", "sam2.1_t.pt", "已下载", "")
        auxiliary = InstalledModel("YOLO11n", "custom_yolo", "weights/yolo11n.pt", "应用自带", "")
        library = Mock()
        library.installed.return_value = (model, auxiliary)
        library.is_enabled.return_value = True
        logs = []
        with patch.object(workflow, "_context", return_value=context), \
             patch("application.quick_workflow.ModelLibrary", return_value=library):
            workflow.run(request, lambda: False, logs.append, lambda _percent: None)
        self.assertEqual(workflow.sam_refine.execute.call_args.args[:3],
                         (os.path.join("images", "a.png"), (box,), model.path))
        verifier = workflow.sam_refine.execute.call_args.args[3]
        self.assertIsInstance(verifier, ClassVerificationOperation)
        self.assertEqual(verifier.weights_path, auxiliary.path)
        workflow.refine.execute.assert_called_once_with((box,), frozenset())
        self.assertEqual(workflow.persist.execute.call_args.args[2], (box,))
        self.assertTrue(any("实际送入分割：1 张" in message and "合并 1 框" in message for message in logs))

    def test_runner_persists_only_categories_configured_in_project(self):
        request = QuickAnnotationRequest("project", "images", "annotations", QuickAnnotationConfig(), ("a.png",))
        workflow = QuickAnnotationWorkflow()
        project = Mock()
        project.categories = ["person"]
        context = QuickOperationContext(request, project, workflow.registry,
                                        applied_steps=WorkflowGraph.preset(QUICK_WORKFLOW).nodes)
        bundle = PredictionBundle("custom_yolo", "test", (
            ImagePrediction("person", (0, 0, 20, 20), .5, "custom_yolo"),
            ImagePrediction("banana", (30, 0, 50, 20), .9, "custom_yolo"),
        ))
        workflow.validate.execute = Mock(return_value=request)
        workflow.check_image.execute = Mock()
        workflow.predict.execute = Mock(return_value=bundle)
        workflow.recover.execute = Mock(return_value=None)
        workflow.predict_tiles.execute = Mock(return_value=())
        workflow.persist.execute = Mock(return_value=(0, ()))
        workflow.route_review.execute = Mock()
        logs = []
        with patch.object(workflow, "_context", return_value=context):
            workflow.run(request, lambda: False, logs.append, lambda percent: None)
        persisted = workflow.persist.execute.call_args.args[2]
        self.assertEqual([item.label for item in persisted], ["person"])
        self.assertTrue(any("类别表外的 1 个候选" in message for message in logs))


if __name__ == "__main__":
    unittest.main()
