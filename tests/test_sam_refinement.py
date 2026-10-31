"""Mask evidence must reduce duplicates without merging adjacent objects."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from application.class_verification import ClassVerificationOperation, VerifiedObject
from application.contracts import ImagePrediction
from application.quick_operations import CandidateRuleRefinementOperation
from application.sam_refinement import SamMaskRefinementOperation


class SamRefinementTests(unittest.TestCase):
    @staticmethod
    def _box(left: int, score: float) -> ImagePrediction:
        return ImagePrediction("person", (left, 10, left + 100, 110), score, "yoloe")

    def test_same_mask_merges_and_keeps_best_score(self):
        first, second = self._box(10, .8), self._box(14, .3)
        mask = np.zeros((256, 256), dtype=bool)
        mask[20:100, 20:100] = True
        operation = SamMaskRefinementOperation()
        operation._predict_masks = Mock(return_value=[mask, mask.copy()])
        result = operation.execute("image.jpg", (first, second), "sam2.1_t.pt")
        self.assertEqual(result.analyzed, 2)
        self.assertEqual(result.merged, 1)
        self.assertEqual(len(result.predictions), 1)
        self.assertEqual(result.predictions[0].score, .8)
        self.assertEqual(result.blocked_pairs, frozenset())
        operation._predict_masks.assert_called_once()

    def test_contained_partial_mask_merges_same_object(self):
        first, second = self._box(10, .8), self._box(14, .3)
        whole = np.zeros((256, 256), dtype=bool)
        part = np.zeros((256, 256), dtype=bool)
        whole[20:120, 20:120] = True
        part[40:110, 40:110] = True
        operation = SamMaskRefinementOperation()
        operation._predict_masks = Mock(return_value=[whole, part])
        result = operation.execute("image.jpg", (first, second), "sam2.1_t.pt")
        self.assertEqual(result.merged, 1)
        self.assertEqual(result.predictions[0].bbox, (10, 10, 114, 110))

    def test_union_prompt_merges_weakly_overlapping_parts_of_one_instance(self):
        first, second = self._box(10, .8), self._box(50, .3)
        left = np.zeros((256, 256), dtype=bool)
        right = np.zeros((256, 256), dtype=bool)
        union = np.zeros((256, 256), dtype=bool)
        left[20:120, 20:100] = True
        right[20:120, 60:140] = True
        union[20:120, 20:140] = True
        operation = SamMaskRefinementOperation()
        operation._predict_masks = Mock(return_value=[left, right, union])
        result = operation.execute("image.jpg", (first, second), "sam2.1_t.pt")
        self.assertEqual(result.merged, 1)
        self.assertEqual(result.predictions[0].bbox, (10, 10, 150, 110))
        self.assertEqual(len(operation._predict_masks.call_args.args[1]), 3)

    def test_union_prompt_does_not_merge_two_disjoint_instances(self):
        first, second = self._box(10, .8), self._box(50, .3)
        left = np.zeros((256, 256), dtype=bool)
        right = np.zeros((256, 256), dtype=bool)
        left[20:120, 20:80] = True
        right[20:120, 100:160] = True
        operation = SamMaskRefinementOperation()
        operation._predict_masks = Mock(return_value=[left, right, left | right])
        result = operation.execute("image.jpg", (first, second), "sam2.1_t.pt")
        self.assertEqual(result.merged, 0)
        self.assertEqual(result.blocked_pairs, frozenset({(0, 1)}))

    def test_distinct_masks_protect_nearby_objects_from_final_rules(self):
        first, second = self._box(10, .8), self._box(14, .3)
        left = np.zeros((256, 256), dtype=bool)
        right = np.zeros((256, 256), dtype=bool)
        left[20:100, 20:80] = True
        right[20:100, 100:160] = True
        operation = SamMaskRefinementOperation()
        operation._predict_masks = Mock(return_value=[left, right])
        result = operation.execute("image.jpg", (first, second), "sam2.1_t.pt")
        self.assertEqual(result.merged, 0)
        self.assertEqual(result.blocked_pairs, frozenset({(0, 1)}))
        self.assertEqual(len(CandidateRuleRefinementOperation().execute(
            result.predictions, result.blocked_pairs)), 2)

    def test_ambiguous_masks_leave_the_rule_stage_available(self):
        first, second = self._box(10, .8), self._box(14, .3)
        left = np.zeros((256, 256), dtype=bool)
        right = np.zeros((256, 256), dtype=bool)
        left[20:120, 20:120] = True
        right[40:140, 20:120] = True
        operation = SamMaskRefinementOperation()
        operation._predict_masks = Mock(return_value=[left, right])
        result = operation.execute("image.jpg", (first, second), "sam2.1_t.pt")
        self.assertEqual(result.merged, 0)
        self.assertEqual(result.blocked_pairs, frozenset())
        self.assertEqual(len(CandidateRuleRefinementOperation().execute(
            result.predictions, result.blocked_pairs)), 1)

    def test_unrelated_boxes_skip_sam_entirely(self):
        operation = SamMaskRefinementOperation()
        operation._predict_masks = Mock()
        boxes = (self._box(0, .5), self._box(300, .5))
        self.assertEqual(operation.execute("image.jpg", boxes, "sam2.1_t.pt").predictions, boxes)
        operation._predict_masks.assert_not_called()

    def test_missing_masks_leave_candidates_intact(self):
        operation = SamMaskRefinementOperation()
        operation._predict_masks = Mock(return_value=[])
        boxes = (self._box(10, .5), self._box(14, .3))
        result = operation.execute("image.jpg", boxes, "sam2.1_t.pt")
        self.assertEqual(result.predictions, boxes)
        self.assertEqual(result.blocked_pairs, frozenset())

    def test_cross_class_fragments_follow_mask_and_corroborated_verifier_label(self):
        proposals = (
            ImagePrediction("cat", (10, 10, 110, 110), .71, "yoloe"),
            ImagePrediction("dog", (20, 20, 100, 100), .62, "yoloe"),
            ImagePrediction("dog", (25, 25, 105, 105), .54, "gd15"),
        )
        whole = np.zeros((256, 256), dtype=bool)
        part = np.zeros((256, 256), dtype=bool)
        whole[20:120, 20:120] = True
        part[40:100, 40:100] = True
        operation = SamMaskRefinementOperation()
        operation._predict_masks = Mock(return_value=[whole, part, part.copy(), whole.copy()])
        verifier = Mock()
        verifier.execute.return_value = (VerifiedObject("dog", (12, 12, 108, 112), .56),)
        verifier.supports.return_value = True
        result = operation.execute("image.jpg", proposals, "sam2.1_t.pt", verifier)
        self.assertEqual(result.merged, 2)
        self.assertEqual(result.resolved_conflicts, 1)
        self.assertEqual(len(result.predictions), 1)
        output = result.predictions[0]
        self.assertEqual((output.label, output.bbox, output.score), ("dog", (12, 12, 108, 112), .56))
        self.assertEqual(output.alternate_labels, ("cat",))
        self.assertIn("class_conflict", output.risk_flags)
        self.assertIn("yolo11n_verify", output.sources)

    def test_verifier_does_not_absorb_a_distinct_nearby_instance(self):
        proposals = (
            ImagePrediction("cat", (10, 10, 110, 110), .71, "yoloe"),
            ImagePrediction("dog", (20, 20, 100, 100), .62, "yoloe"),
            ImagePrediction("dog", (30, 20, 120, 110), .65, "gd15"),
        )
        target = np.zeros((256, 256), dtype=bool)
        fragment = np.zeros((256, 256), dtype=bool)
        other = np.zeros((256, 256), dtype=bool)
        target[20:120, 20:110] = True
        fragment[40:100, 40:90] = True
        other[20:120, 130:210] = True
        operation = SamMaskRefinementOperation()
        broad_anchor = target | other
        operation._predict_masks = Mock(return_value=[target, fragment, other, broad_anchor, broad_anchor.copy()])
        verifier = Mock()
        verifier.execute.return_value = (VerifiedObject("dog", (12, 12, 108, 112), .56),)
        verifier.supports.return_value = True
        result = operation.execute("image.jpg", proposals, "sam2.1_t.pt", verifier)
        self.assertEqual(len(result.predictions), 2)
        self.assertEqual(result.resolved_conflicts, 1)
        self.assertEqual(result.predictions[1], proposals[2])

    def test_cross_class_boxes_abstain_without_verifier(self):
        cat = ImagePrediction("cat", (10, 10, 110, 110), .8, "yoloe")
        dog = ImagePrediction("dog", (14, 10, 114, 110), .6, "gd15")
        mask = np.zeros((256, 256), dtype=bool)
        mask[20:120, 20:120] = True
        operation = SamMaskRefinementOperation()
        operation._predict_masks = Mock(return_value=[mask, mask.copy()])
        result = operation.execute("image.jpg", (cat, dog), "sam2.1_t.pt")
        self.assertEqual(result.predictions, (cat, dog))
        self.assertEqual(result.resolved_conflicts, 0)
        self.assertEqual(result.unresolved_conflicts, 1)

    def test_full_image_anchor_absorbs_wrongly_labeled_tile_fragment(self):
        whole = ImagePrediction("dog", (10, 10, 200, 200), .65, "yoloe")
        fragment = ImagePrediction("cat", (40, 40, 120, 120), .91, "yoloe/tile")
        whole_mask = np.zeros((256, 256), dtype=bool)
        part_mask = np.zeros((256, 256), dtype=bool)
        whole_mask[20:190, 20:190] = True
        part_mask[50:110, 50:110] = True
        operation = SamMaskRefinementOperation()
        operation._predict_masks = Mock(return_value=[whole_mask, part_mask])
        result = operation.execute("image.jpg", (whole, fragment), "sam2.1_t.pt")
        self.assertEqual(len(result.predictions), 1)
        self.assertEqual(result.predictions[0].label, "dog")
        self.assertEqual(result.predictions[0].bbox, whole.bbox)
        self.assertEqual(result.predictions[0].alternate_labels, ("cat",))
        self.assertIn("class_conflict", result.predictions[0].risk_flags)

    def test_full_image_anchor_keeps_tile_fragment_on_distinct_mask(self):
        whole = ImagePrediction("dog", (10, 10, 200, 200), .65, "yoloe")
        fragment = ImagePrediction("cat", (110, 110, 180, 180), .91, "yoloe/tile")
        whole_mask = np.zeros((256, 256), dtype=bool)
        other_mask = np.zeros((256, 256), dtype=bool)
        whole_mask[20:105, 20:105] = True
        other_mask[120:175, 120:175] = True
        operation = SamMaskRefinementOperation()
        operation._predict_masks = Mock(return_value=[whole_mask, other_mask])
        result = operation.execute("image.jpg", (whole, fragment), "sam2.1_t.pt")
        self.assertEqual(result.predictions, (whole, fragment))

    def test_full_image_anchor_abstains_when_two_instances_match_equally(self):
        dog = ImagePrediction("dog", (10, 10, 190, 190), .65, "yoloe")
        person = ImagePrediction("person", (20, 10, 200, 190), .75, "gd15")
        fragment = ImagePrediction("cat", (60, 60, 120, 120), .91, "yoloe/tile")
        whole_mask = np.zeros((256, 256), dtype=bool)
        part_mask = np.zeros((256, 256), dtype=bool)
        whole_mask[20:185, 20:185] = True
        part_mask[65:115, 65:115] = True
        operation = SamMaskRefinementOperation()
        operation._predict_masks = Mock(return_value=[whole_mask, whole_mask.copy(), part_mask])
        result = operation.execute("image.jpg", (dog, person, fragment), "sam2.1_t.pt")
        self.assertEqual(result.predictions, (dog, person, fragment))

    def test_low_score_tile_sliver_with_bad_mask_is_absorbed_into_same_class(self):
        whole = ImagePrediction("person", (100, 100, 1900, 1900), .9, "yoloe")
        sliver = ImagePrediction("person", (1650, 500, 1728, 800), .3, "yoloe/tile")
        whole_mask = np.zeros((256, 256), dtype=bool)
        sliver_mask = np.zeros((256, 256), dtype=bool)
        whole_mask[20:180, 20:180] = True
        sliver_mask[65:68, 209:212] = True
        operation = SamMaskRefinementOperation()
        operation._predict_masks = Mock(return_value=[whole_mask, sliver_mask, None])
        with patch("application.sam_refinement.Image.open") as opened:
            opened.return_value.__enter__.return_value.size = (2048, 2048)
            result = operation.execute("image.jpg", (whole, sliver), "sam2.1_t.pt")
        self.assertEqual(len(result.predictions), 1)
        self.assertEqual(result.predictions[0].bbox, whole.bbox)
        self.assertEqual(result.predictions[0].score, whole.score)
        self.assertIn("weak_mask_merge", result.predictions[0].risk_flags)

    def test_distinct_well_formed_small_instance_is_not_geometry_merged(self):
        whole = ImagePrediction("person", (100, 100, 1900, 1900), .9, "yoloe")
        small = ImagePrediction("person", (1650, 500, 1728, 800), .3, "yoloe/tile")
        whole_mask = np.zeros((256, 256), dtype=bool)
        small_mask = np.zeros((256, 256), dtype=bool)
        whole_mask[20:180, 20:180] = True
        small_mask[63:100, 206:216] = True
        operation = SamMaskRefinementOperation()
        operation._predict_masks = Mock(return_value=[whole_mask, small_mask, None])
        with patch("application.sam_refinement.Image.open") as opened:
            opened.return_value.__enter__.return_value.size = (2048, 2048)
            result = operation.execute("image.jpg", (whole, small), "sam2.1_t.pt")
        self.assertEqual(result.predictions, (whole, small))

    def test_high_score_tile_fragment_with_disjoint_sam_mask_uses_unique_full_anchor(self):
        whole = ImagePrediction("person", (100, 100, 1900, 1900), .95, "yoloe")
        fragment = ImagePrediction("person", (1536, 768, 1800, 1500), .85, "yoloe/tile")
        whole_mask = np.zeros((256, 256), dtype=bool)
        fragment_mask = np.zeros((256, 256), dtype=bool)
        whole_mask[20:100, 20:100] = True
        fragment_mask[120:180, 120:180] = True
        operation = SamMaskRefinementOperation()
        operation._predict_masks = Mock(return_value=[whole_mask, fragment_mask, None])
        with patch("application.sam_refinement.Image.open") as opened:
            opened.return_value.__enter__.return_value.size = (2048, 2048)
            result = operation.execute("image.jpg", (whole, fragment), "sam2.1_t.pt")
        self.assertEqual(len(result.predictions), 1)
        self.assertEqual(result.predictions[0].bbox, whole.bbox)
        self.assertEqual(result.predictions[0].score, whole.score)
        self.assertIn("tile_fragment_merge", result.predictions[0].risk_flags)

    def test_high_score_tile_fragment_abstains_with_multiple_full_anchors(self):
        whole = ImagePrediction("person", (100, 100, 1900, 1900), .95, "yoloe")
        second = ImagePrediction("person", (1200, 100, 1950, 1900), .90, "gd15")
        fragment = ImagePrediction("person", (1536, 768, 1800, 1500), .85, "yoloe/tile")
        masks = [np.zeros((256, 256), dtype=bool) for _ in range(3)]
        masks[0][20:100, 20:100] = True
        masks[1][100:160, 30:100] = True
        masks[2][170:240, 170:240] = True
        for competing_mask in (masks[1], None):
            operation = SamMaskRefinementOperation()
            evidence = [masks[0], competing_mask, masks[2]]
            operation._predict_masks = Mock(side_effect=lambda _image, boxes, _weights:
                                            evidence + [None] * (len(boxes) - len(evidence)))
            with patch("application.sam_refinement.Image.open") as opened:
                opened.return_value.__enter__.return_value.size = (2048, 2048)
                result = operation.execute("image.jpg", (whole, second, fragment), "sam2.1_t.pt")
            self.assertEqual(result.predictions, (whole, second, fragment))

    def test_unmasked_narrow_tile_sliver_uses_strict_geometry_fallback(self):
        whole = ImagePrediction("person", (100, 100, 1900, 2050), .95, "yoloe")
        sliver = ImagePrediction("person", (1536, 1176, 1646, 1990), .43, "yoloe/tile")
        whole_mask = np.zeros((256, 256), dtype=bool)
        whole_mask[20:200, 20:200] = True
        operation = SamMaskRefinementOperation()
        operation._predict_masks = Mock(return_value=[whole_mask, None, None])
        with patch("application.sam_refinement.Image.open") as opened:
            opened.return_value.__enter__.return_value.size = (3216, 2136)
            result = operation.execute("image.jpg", (whole, sliver), "sam2.1_t.pt")
        self.assertEqual(len(result.predictions), 1)
        self.assertEqual(result.predictions[0].bbox, whole.bbox)
        self.assertIn("tile_fragment_merge", result.predictions[0].risk_flags)

    def test_final_rules_preserve_verifier_label_and_original_alternative(self):
        reviewed = ImagePrediction("dog", (10, 10, 110, 110), .56, "yolo11n_verify",
                                   risk_flags=("class_conflict",), alternate_labels=("cat",),
                                   sources=("yoloe", "yolo11n_verify"))
        duplicate = ImagePrediction("dog", (12, 11, 112, 111), .83, "yoloe")
        merged, = CandidateRuleRefinementOperation().execute((reviewed, duplicate))
        self.assertEqual(merged.label, "dog")
        self.assertEqual(merged.score, .56)
        self.assertEqual(merged.alternate_labels, ("cat",))
        self.assertIn("class_conflict", merged.risk_flags)

    def test_one_image_embedding_is_reused_across_mask_batches(self):
        # Real SAM results use bool masks; OpenCV cannot resize bool arrays.
        data = np.ones((8, 1100, 1100), dtype=bool)
        predictor = Mock()
        predictor.return_value = [Mock(masks=Mock(data=Mock(
            detach=Mock(return_value=Mock(cpu=Mock(return_value=Mock(
                numpy=Mock(return_value=data))))))))]
        operation = SamMaskRefinementOperation()
        boxes = [[index, 0, index + 10, 10] for index in range(16)]
        with patch("ultralytics.models.sam.SAM2Predictor", return_value=predictor):
            result = operation._predict_masks("image.jpg", boxes, "sam2.1_t.pt")
        self.assertEqual(len(result), 16)
        self.assertEqual(result[0].shape, (1024, 1024))
        predictor.set_image.assert_called_once_with("image.jpg")
        self.assertEqual(predictor.call_count, 2)
        predictor.reset_image.assert_called_once()

    def test_failed_mask_batch_is_split_without_discarding_other_masks(self):
        import torch

        predictor = Mock()

        def predict(*, bboxes):
            # A bad prompt makes SAM omit a mask from the batch. Splitting
            # recovers the other three without encoding the image again.
            if any(box[0] == 30 for box in bboxes):
                count = len(bboxes) - 1
            else:
                count = len(bboxes)
            masks = None if count == 0 else SimpleNamespace(data=torch.ones((count, 32, 32)))
            return [SimpleNamespace(masks=masks)]

        predictor.side_effect = predict
        operation = SamMaskRefinementOperation()
        with patch("ultralytics.models.sam.SAM2Predictor", return_value=predictor):
            masks = operation._predict_masks("image.jpg", [[0, 0, 10, 10], [10, 0, 20, 10],
                                                              [20, 0, 30, 10], [30, 0, 40, 10]],
                                             "sam2.1_t.pt")
        self.assertEqual(len(masks), 4)
        self.assertEqual(sum(mask is not None for mask in masks), 3)
        predictor.set_image.assert_called_once_with("image.jpg")
        predictor.reset_image.assert_called_once()

    def test_auxiliary_classifier_only_requests_project_classes(self):
        box = SimpleNamespace(cls=[2], conf=[.56], xyxy=[Mock(tolist=Mock(return_value=[1, 2, 40, 50]))])
        model = Mock(names={0: "person", 1: "cat", 2: "dog", 3: "banana"})
        model.predict.return_value = [SimpleNamespace(boxes=[box])]
        with patch("ultralytics.YOLO", return_value=model):
            operation = ClassVerificationOperation("yolo11n.pt")
            result = operation.execute("image.jpg", ("cat", "dog"))
        self.assertEqual(result, (VerifiedObject("dog", (1, 2, 40, 50), .56),))
        self.assertTrue(operation.supports("cat"))
        self.assertFalse(operation.supports("forklift"))
        model.predict.assert_called_once_with("image.jpg", classes=[1, 2], conf=.45,
                                              iou=.6, verbose=False)

    def test_auxiliary_classifier_preserves_project_class_spelling(self):
        box = SimpleNamespace(cls=[2], conf=[.56], xyxy=[Mock(tolist=Mock(return_value=[1, 2, 40, 50]))])
        model = Mock(names={0: "person", 1: "cat", 2: "dog"})
        model.predict.return_value = [SimpleNamespace(boxes=[box])]
        with patch("ultralytics.YOLO", return_value=model):
            result = ClassVerificationOperation("yolo11n.pt").execute("image.jpg", ("Cat", "Dog"))
        self.assertEqual(result, (VerifiedObject("Dog", (1, 2, 40, 50), .56),))


if __name__ == "__main__":
    unittest.main()
