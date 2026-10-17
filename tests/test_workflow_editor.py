"""Contract tests for project workflow drafts and constrained execution."""
from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from application.contracts import ImagePrediction, PredictionBundle
from application.quick_operations import CandidateBoundsOperation, QuickOperationContext
from application.quick_workflow import QuickAnnotationWorkflow
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
        without_recovery = preset.without_optional(QUICK_WORKFLOW, "recovery_model")
        self.assertEqual(without_recovery.validate(QUICK_WORKFLOW), ())
        broken = WorkflowGraph("quick", preset.nodes,
                               tuple(edge for edge in preset.edges if edge != ("primary_model", "candidate_fusion")))
        self.assertIn("必经步骤之间的依赖不能断开", broken.validate(QUICK_WORKFLOW))
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
        workflow.check_bounds.execute = Mock(return_value=())
        workflow.persist.execute = Mock(return_value=(0, ()))
        workflow.route_review.execute = Mock()
        with patch.object(workflow, "_context", return_value=context):
            result = workflow.run(request, lambda: False, lambda message: None, lambda percent: None)
        self.assertEqual(result.filenames, ("a.png",))
        workflow.check_bounds.execute.assert_called_once()
        workflow.route_review.execute.assert_called_once_with(project, ("a.png",))


if __name__ == "__main__":
    unittest.main()
