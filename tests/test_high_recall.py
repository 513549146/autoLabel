"""High-recall candidate preservation, tiling, and missed-object metrics."""
from __future__ import annotations

import unittest
import copy
import os
import tempfile
import xml.etree.ElementTree as ET
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image

import config
import core
from application.contracts import ImagePrediction, PredictionBundle
from application.high_recall import TiledPredictionOperation, tile_windows
from application.quick_operations import AnnotationPersistenceOperation, CandidateClassAlignmentOperation, CandidateFusionOperation, CandidateRuleRefinementOperation, CandidateReviewTriageOperation, RecoveryPredictionOperation
from application.recall_evaluation import evaluate_recall
from application.workflow_contracts import QuickAnnotationConfig, RecoveryPolicy
from gui.canvas import ImageCanvas


class HighRecallTests(unittest.TestCase):
    def test_yoloe_text_classes_are_encoded_once_per_prompt(self):
        model = Mock()
        model.predict.return_value = []
        detector = SimpleNamespace(model=model)
        core._detect_yoloe(detector, "full.jpg", "person . dog", .1, .25)
        core._detect_yoloe(detector, "tile.jpg", "person . dog", .1, .25)
        model.set_classes.assert_called_once_with(["person", "dog"])
        core._detect_yoloe(detector, "other.jpg", "cat", .1, .25)
        self.assertEqual(model.set_classes.call_count, 2)

    def test_fixed_class_yolo_receives_only_project_class_ids_at_predict_time(self):
        model = Mock()
        model.names = {0: "person", 1: "banana", 2: "dog"}
        model.predict.return_value = []
        detector = SimpleNamespace(model=model)
        self.assertEqual(core._detect_yolo(detector, "image.jpg", "dog . person", .1, .25), [])
        model.predict.assert_called_once_with(
            "image.jpg", conf=.1, iou=config.YOLO_IOU,
            max_det=config.YOLO_MAX_DET, classes=[0, 2], verbose=False,
        )

    def test_fixed_class_yolo_rejects_weights_without_any_project_class(self):
        model = Mock()
        model.names = {0: "banana", 1: "bird"}
        with self.assertRaisesRegex(ValueError, "不包含类别表中的类别"):
            core._detect_yolo(SimpleNamespace(model=model), "image.jpg", "cat . dog", .1, .25)
        model.predict.assert_not_called()

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

    def test_matching_tile_candidates_merge_without_dropping_isolated_objects(self):
        tiles = PredictionBundle("yoloe", "tiled", (
            ImagePrediction("person", (10, 10, 110, 110), .24, "yoloe/tile", sources=("yoloe/tile",)),
            ImagePrediction("person", (18, 12, 118, 112), .20, "yoloe/tile", sources=("yoloe/tile",)),
            ImagePrediction("person", (150, 10, 250, 110), .17, "yoloe/tile", sources=("yoloe/tile",)),
        ))
        result = CandidateFusionOperation().execute((tiles,))
        self.assertEqual(len(result), 2)
        truth = {"image": [{"name": "person", "bbox": [10, 10, 110, 110]},
                           {"name": "person", "bbox": [150, 10, 250, 110]}]}
        before = evaluate_recall(truth, {"image": tiles.annotations()})
        after = evaluate_recall(truth, {"image": [item.as_annotation() for item in result]})
        self.assertEqual(after["overall"]["fn"], before["overall"]["fn"])

    def test_two_full_image_detections_remain_separate_even_when_close(self):
        primary = PredictionBundle("yoloe", "", (
            ImagePrediction("person", (0, 0, 100, 100), .3, "yoloe"),
            ImagePrediction("person", (14, 0, 114, 100), .28, "yoloe"),
        ))
        result = CandidateFusionOperation().execute((primary,))
        self.assertEqual(len(result), 2)

    def test_cross_model_duplicate_merges_but_different_classes_do_not(self):
        primary = PredictionBundle("yoloe", "", (
            ImagePrediction("helmet", (20, 20, 120, 120), .3, "yoloe"),
        ))
        secondary = PredictionBundle("gd15", "", (
            ImagePrediction("helmet", (28, 23, 128, 123), .2, "gd15"),
            ImagePrediction("person", (22, 22, 122, 122), .1, "gd15"),
        ))
        result = CandidateFusionOperation().execute((primary, secondary))
        self.assertEqual(len(result), 2)
        helmet = next(item for item in result if item.label == "helmet")
        self.assertEqual(set(helmet.sources), {"yoloe", "gd15"})
        self.assertEqual(len(helmet.source_scores), 2)

    def test_final_rules_use_same_model_score_to_merge_near_duplicate(self):
        strong = ImagePrediction("person", (10, 10, 110, 110), .8, "yoloe",
                                 sources=("yoloe",), source_scores=(("yoloe", .8),))
        weak = ImagePrediction("person", (20, 11, 120, 111), .3, "yoloe/tile",
                               sources=("yoloe/tile",), source_scores=(("yoloe/tile", .3),),
                               risk_flags=("tile_only",))
        unique = ImagePrediction("person", (180, 10, 280, 110), .1, "yoloe/tile")
        result = CandidateRuleRefinementOperation().execute((strong, weak, unique))
        self.assertEqual(len(result), 2)
        merged = result[0]
        self.assertLess(merged.bbox[0], 15)
        self.assertEqual(set(merged.sources), {"yoloe", "yoloe/tile"})
        self.assertNotIn("tile_only", merged.risk_flags)
        self.assertEqual(result[1], unique)
        truth = {"image": [{"name": "person", "bbox": [10, 10, 110, 110]},
                           {"name": "person", "bbox": [180, 10, 280, 110]}]}
        before = evaluate_recall(truth, {"image": [item.as_annotation() for item in (strong, weak, unique)]})
        after = evaluate_recall(truth, {"image": [item.as_annotation() for item in result]})
        self.assertEqual(after["overall"]["fn"], before["overall"]["fn"])

    def test_final_rules_do_not_compare_scores_across_models_at_moderate_overlap(self):
        first = ImagePrediction("person", (10, 10, 110, 110), .9, "yoloe")
        second = ImagePrediction("person", (20, 10, 120, 110), .1, "gd15")
        result = CandidateRuleRefinementOperation().execute((first, second))
        self.assertEqual(len(result), 2)

    def test_final_rules_merge_near_identical_cross_model_boxes_and_preserve_evidence(self):
        first = ImagePrediction("person", (10, 10, 110, 110), .4, "yoloe",
                                risk_flags=("single_model",), sources=("yoloe",))
        second = ImagePrediction("person", (13, 11, 113, 111), .3, "gd15",
                                 risk_flags=("single_model", "unknown_label"), sources=("gd15",))
        result = CandidateRuleRefinementOperation().execute((first, second))
        self.assertEqual(len(result), 1)
        self.assertEqual(set(result[0].sources), {"yoloe", "gd15"})
        self.assertNotIn("single_model", result[0].risk_flags)
        self.assertIn("unknown_label", result[0].risk_flags)

    def test_final_rules_remove_weak_tile_edge_fragment_without_shrinking_full_box(self):
        full = ImagePrediction("person", (700, 100, 1200, 500), .7, "yoloe",
                               sources=("yoloe",), source_scores=(("yoloe", .7),))
        clipped = ImagePrediction("person", (768, 100, 1100, 500), .2, "yoloe/tile",
                                  sources=("yoloe/tile",), source_scores=(("yoloe/tile", .2),))
        result = CandidateRuleRefinementOperation().execute((full, clipped))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].bbox, full.bbox)
        self.assertEqual(set(result[0].sources), {"yoloe", "yoloe/tile"})
        unrelated = ImagePrediction("person", (820, 100, 1152, 500), .2, "yoloe/tile",
                                    sources=("yoloe/tile",), source_scores=(("yoloe/tile", .2),))
        self.assertEqual(len(CandidateRuleRefinementOperation().execute((full, unrelated))), 2)

    def test_model_source_name_is_human_readable(self):
        text = ImageCanvas.provenance_text({"score": .2, "sources": ["gd15/tile"]})
        self.assertIn("Grounding DINO", text)
        self.assertIn("切片", text)
        self.assertNotIn("gd15", text)

    def test_geometry_absorption_is_marked_for_review(self):
        shape = {"name": "person", "bbox": [10, 10, 100, 100], "score": .9,
                 "sources": ["yoloe", "yoloe/tile"], "risk_flags": ["weak_mask_merge"]}
        self.assertIn("待人工核验", ImageCanvas.provenance_text(shape))
        self.assertIn("weak_mask_merge", core.annotation_issues([shape]))
        shape["risk_flags"] = ["tile_fragment_merge"]
        self.assertIn("切片截断框", ImageCanvas.provenance_text(shape))
        self.assertIn("tile_fragment_merge", core.annotation_issues([shape]))

    def test_every_box_caption_keeps_label_and_optional_model_score(self):
        self.assertEqual(ImageCanvas.caption_text({"name": "person", "score": .42,
                                                   "sources": ["gd15"]}), "person 42%")
        self.assertEqual(ImageCanvas.caption_text({"name": "person", "origin": "manual"}),
                         "person · 人工标注")
        self.assertEqual(ImageCanvas.caption_text({"name": "person", "origin": "revised"}),
                         "person · 已修订")
        self.assertEqual(ImageCanvas.caption_text({"name": "person", "origin": "verified"}),
                         "person · 已核验")
        self.assertEqual(ImageCanvas.caption_text({"name": "person", "score": float("nan")}),
                         "person · 历史标注")

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

    def test_class_alignment_keeps_only_configured_project_categories(self):
        source = PredictionBundle("custom_yolo", "", (
            ImagePrediction(" Person ", (1, 2, 10, 12), .4, "custom_yolo"),
            ImagePrediction("banana", (20, 2, 30, 12), .8, "custom_yolo"),
            ImagePrediction("DOG", (40, 2, 50, 12), .3, "custom_yolo"),
        ))
        aligned, = CandidateClassAlignmentOperation().execute((source,), ("person", "dog"))
        self.assertEqual([item.label for item in aligned.predictions], ["person", "dog"])

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
             patch("application.high_recall.ImageOps.exif_transpose", return_value=image), \
             patch("application.high_recall.tempfile.TemporaryDirectory") as temporary, \
             patch("application.high_recall.os.remove"), \
             patch("application.quick_operations.CandidatePredictionOperation.execute", predictor):
            temporary.return_value.__enter__.return_value = "unused"
            result = TiledPredictionOperation().execute(context, ("yoloe",), "image.jpg")
        self.assertEqual(len(result), 1)
        self.assertTrue(any(item.bbox == (650, 20, 670, 40) for item in result[0].predictions))
        self.assertTrue(all(item.sources == ("yoloe/tile",) for item in result[0].predictions))

    def test_tiled_operation_uses_exif_oriented_dimensions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "rotated.jpg")
            image = Image.new("RGB", (1800, 900), "red")
            exif = Image.Exif()
            exif[274] = 6
            image.save(path, exif=exif)
            context = SimpleNamespace(request=SimpleNamespace(
                config=SimpleNamespace(tiled_inference=True), project_root=directory))
            with patch("application.high_recall.tile_windows", return_value=()) as windows:
                TiledPredictionOperation().execute(context, ("yoloe",), path)
            self.assertEqual(windows.call_args.args[:2], (900, 1800))

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
                                risk_flags=("single_model", "tile_only", "class_conflict"),
                                alternate_labels=("dog",)).as_annotation()

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
        self.assertEqual(set(copied["risk_flags"]), {"single_model", "tile_only", "class_conflict"})
        self.assertEqual(copied["alternate_labels"], ["dog"])
        self.assertIn("single_model", core.annotation_issues([copied]))
        self.assertIn("class_conflict", core.annotation_issues([copied]))

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
