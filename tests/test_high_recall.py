"""High-recall candidate preservation, tiling, and missed-object metrics."""
from __future__ import annotations

import unittest
import copy
import os
import xml.etree.ElementTree as ET
from types import SimpleNamespace
from unittest.mock import Mock, patch

import config
import core
from application.contracts import ImagePrediction, PredictionBundle
from application.high_recall import TiledPredictionOperation, tile_windows
from application.quick_operations import AnnotationPersistenceOperation, CandidateClassAlignmentOperation, CandidateFusionOperation, CandidateReviewTriageOperation, RecoveryPredictionOperation
from application.recall_evaluation import evaluate_recall
from application.workflow_contracts import QuickAnnotationConfig, RecoveryPolicy
from gui.canvas import ImageCanvas


class HighRecallTests(unittest.TestCase):
    def test_default_runs_second_model_even_when_primary_found_a_box(self):
        settings = QuickAnnotationConfig(prompt="person")
        self.assertEqual(settings.recovery_policy, RecoveryPolicy.ALL_IMAGES)
        self.assertEqual(settings.confidence, config.HIGH_RECALL_THRESHOLD)
        primary = PredictionBundle("yoloe", "", (ImagePrediction("person", (0, 0, 10, 10)),))
        context = SimpleNamespace(request=SimpleNamespace(config=settings))
        with patch("application.quick_operations.CandidatePredictionOperation.execute",
                   return_value=PredictionBundle("gd15", "", ())) as predict:
            RecoveryPredictionOperation().execute(context, primary, "image.jpg")
        predict.assert_called_once_with(context, "gd15", "image.jpg")

    def test_partial_miss_from_primary_is_kept_from_second_model(self):
        primary = PredictionBundle("yoloe", "v1", (
            ImagePrediction("person", (10, 10, 60, 90), .3, "yoloe", sources=("yoloe",)),
        ))
        secondary = PredictionBundle("gd15", "v1", (
            ImagePrediction("person", (11, 11, 61, 91), .2, "gd15", sources=("gd15",)),
            ImagePrediction("helmet", (20, 10, 42, 29), .12, "gd15", sources=("gd15",)),
        ))
        candidates = CandidateReviewTriageOperation().execute(
            CandidateFusionOperation().execute((primary, secondary)), (primary, secondary))
        self.assertEqual(len(candidates), 2)
        person = next(item for item in candidates if item.label == "person")
        helmet = next(item for item in candidates if item.label == "helmet")
        self.assertEqual(set(person.sources), {"yoloe", "gd15"})
        self.assertEqual(set(source for source, _ in person.source_scores), {"yoloe", "gd15"})
        self.assertNotIn("single_model", person.risk_flags)
        self.assertIn("single_model", helmet.risk_flags)

    def test_nearby_same_class_objects_are_not_suppressed(self):
        first = ImagePrediction("person", (0, 0, 100, 100), .3, "yoloe", sources=("yoloe",))
        second = ImagePrediction("person", (25, 0, 125, 100), .4, "gd15", sources=("gd15",))
        self.assertEqual(len(CandidateFusionOperation().execute((
            PredictionBundle("yoloe", "", (first,)), PredictionBundle("gd15", "", (second,)),
        ))), 2)

    def test_windows_cover_edges_and_only_tile_detection_is_flagged(self):
        windows = tile_windows(2000, 1100, 960, .2)
        self.assertIn((1040, 140, 2000, 1100), windows)
        self.assertTrue(all(right <= 2000 and bottom <= 1100 for _, _, right, bottom in windows))
        only_tile = PredictionBundle("yoloe", "tiled", (
            ImagePrediction("bolt", (100, 100, 110, 110), .12, "yoloe/tile", sources=("yoloe/tile",)),
        ))
        other = PredictionBundle("gd15", "", ())
        proposal, = CandidateReviewTriageOperation().execute(
            CandidateFusionOperation().execute((only_tile, other)), (only_tile, other))
        self.assertIn("tile_only", proposal.risk_flags)
        self.assertIn("single_model", proposal.risk_flags)

    def test_unknown_label_is_kept_for_review(self):
        source = PredictionBundle("gd15", "", (
            ImagePrediction("A safety helmet", (1, 2, 10, 12), .1, "gd15"),
        ))
        aligned, = CandidateClassAlignmentOperation().execute((source,), ("helmet",))
        self.assertEqual(aligned.predictions[0].label, "A safety helmet")
        self.assertIn("unknown_label", aligned.predictions[0].risk_flags)

    def test_tiled_operation_restores_image_coordinates(self):
        image = Mock()
        image.size = (1600, 900)
        image.convert.return_value = image
        image.__enter__ = Mock(return_value=image)
        image.__exit__ = Mock(return_value=False)
        predictor = Mock(return_value=PredictionBundle("yoloe", "v", (
            ImagePrediction("bolt", (10, 20, 30, 40), .2, "yoloe", sources=("yoloe",)),
        )))
        context = SimpleNamespace(request=SimpleNamespace(
            config=SimpleNamespace(tiled_inference=True), project_root="unused"))
        with patch("application.high_recall.Image.open", return_value=image), \
             patch("application.high_recall.tempfile.TemporaryDirectory") as temporary, \
             patch("application.high_recall.os.remove"), \
             patch("application.quick_operations.CandidatePredictionOperation.execute", predictor):
            temporary.return_value.__enter__.return_value = "unused"
            result = TiledPredictionOperation().execute(context, ("yoloe",), "image.jpg")
        self.assertEqual(len(result), 1)
        self.assertTrue(any(item.bbox == (650, 20, 670, 40) for item in result[0].predictions))
        self.assertTrue(all(item.sources == ("yoloe/tile",) for item in result[0].predictions))

    def test_evaluation_reports_missed_object_and_class_gate_data(self):
        truth = {"a": [{"name": "helmet", "bbox": [0, 0, 20, 20]},
                       {"name": "person", "bbox": [20, 20, 70, 90]}]}
        predicted = {"a": [{"name": "person", "bbox": [20, 20, 70, 90]}]}
        report = evaluate_recall(truth, predicted)
        self.assertEqual(report["overall"]["fn"], 1)
        self.assertEqual(report["classes"]["helmet"]["recall"], 0)
        self.assertEqual(report["misses"][0]["name"], "helmet")
        self.assertEqual(report["images_with_misses"], 1)
        self.assertEqual(report["zero_candidate_images"], 0)
        self.assertEqual(report["overall"]["misses_per_1000_objects"], 500)

    def test_provenance_survives_xml_and_canvas_copy(self):
        saved = {}
        shape = ImagePrediction("helmet", (1, 2, 20, 22), .12, "gd15/tile",
                                sources=("gd15/tile",),
                                source_scores=(("gd15/tile", .12),),
                                risk_flags=("single_model", "tile_only")).as_annotation()

        def capture(tree, _path):
            saved["root"] = copy.deepcopy(tree.getroot())

        with patch("core._imread_unicode", return_value=SimpleNamespace(shape=(100, 100, 3))), \
             patch("core._write_voc_tree", new=capture):
            core.save_as_voc_xml([shape], "image.jpg", "annotations")
        with patch("core.ET.parse", return_value=ET.ElementTree(saved["root"])):
            loaded, = core.load_voc_xml("unused.xml")
        copied, = ImageCanvas._copy_shapes([loaded])
        self.assertEqual(copied["sources"], ["gd15/tile"])
        self.assertEqual(copied["source_scores"], [["gd15/tile", .12]])
        self.assertEqual(set(copied["risk_flags"]), {"single_model", "tile_only"})
        self.assertIn("single_model", core.annotation_issues([copied]))

    def test_xml_replace_happens_after_complete_write(self):
        tree = Mock()
        with patch("core.os.replace") as replace_file, patch("core.os.path.exists", return_value=False):
            core._write_voc_tree(tree, "annotations/image.xml")
        self.assertEqual(tree.write.call_count, 1)
        replace_file.assert_called_once()

    def test_failed_snapshot_does_not_leave_new_xml_as_success(self):
        context = SimpleNamespace(request=SimpleNamespace(
            images_dir="images", annotations_dir="annotations",
            config=SimpleNamespace(prompt="helmet")), project=Mock())
        candidate = ImagePrediction("helmet", (1, 2, 20, 22), .12, "yoloe")
        with patch("application.quick_operations.os.path.isfile", return_value=False), \
             patch("application.quick_operations.core.save_as_voc_xml"), \
             patch("application.quick_operations.core.annotation_risk", return_value=(0, set())), \
             patch("application.quick_operations.ImagePreannotationService.snapshot_annotation",
                   side_effect=RuntimeError("version store unavailable")), \
             patch("application.quick_operations.os.path.exists", return_value=True), \
             patch("application.quick_operations.os.remove") as remove_file:
            with self.assertRaisesRegex(RuntimeError, "version store unavailable"):
                AnnotationPersistenceOperation().execute(context, "image.jpg", (candidate,), ("yoloe",))
        remove_file.assert_called_once_with(os.path.join("annotations", "image.xml"))


if __name__ == "__main__":
    unittest.main()
