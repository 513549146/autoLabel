"""Deterministic object-level evaluation against a separate human gold set."""
from __future__ import annotations

from collections import defaultdict


def box_iou(first, second) -> float:
    ax1, ay1, ax2, ay2 = (float(value) for value in first)
    bx1, by1, bx2, by2 = (float(value) for value in second)
    left, top = max(ax1, bx1), max(ay1, by1)
    right, bottom = min(ax2, bx2), min(ay2, by2)
    intersection = max(0.0, right - left) * max(0.0, bottom - top)
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - intersection
    return intersection / union if union > 0 else 0.0


def evaluate_recall(ground_truth: dict, predictions: dict, iou_threshold: float = 0.5) -> dict:
    """One-to-one class-aware matching; report every missed gold object."""
    if not 0 < iou_threshold <= 1:
        raise ValueError("IoU 阈值必须在 0 到 1 之间")
    stats = defaultdict(lambda: {"ground_truth": 0, "predicted": 0, "tp": 0, "fp": 0, "fn": 0})
    misses = []
    zero_candidate_images = 0
    images_with_misses = 0
    all_files = sorted(set(ground_truth) | set(predictions))
    for filename in all_files:
        truth = list(ground_truth.get(filename, ()))
        guessed = list(predictions.get(filename, ()))
        if truth and not guessed:
            zero_candidate_images += 1
        for item in truth:
            stats[item["name"]]["ground_truth"] += 1
        for item in guessed:
            stats[item["name"]]["predicted"] += 1
        pairs = sorted(
            ((box_iou(actual["bbox"], proposed["bbox"]), actual_index, proposed_index)
             for actual_index, actual in enumerate(truth)
             for proposed_index, proposed in enumerate(guessed)
             if actual["name"] == proposed["name"]),
            reverse=True,
        )
        matched_truth, matched_predictions = set(), set()
        for overlap, actual_index, proposed_index in pairs:
            if overlap < iou_threshold:
                break
            if actual_index in matched_truth or proposed_index in matched_predictions:
                continue
            matched_truth.add(actual_index)
            matched_predictions.add(proposed_index)
            stats[truth[actual_index]["name"]]["tp"] += 1
        for index, item in enumerate(truth):
            if index not in matched_truth:
                stats[item["name"]]["fn"] += 1
                misses.append({"image": filename, "name": item["name"], "bbox": item["bbox"]})
        if len(matched_truth) < len(truth):
            images_with_misses += 1
        for index, item in enumerate(guessed):
            if index not in matched_predictions:
                stats[item["name"]]["fp"] += 1
    classes = {}
    for name, values in sorted(stats.items()):
        gt_count, predicted_count = values["ground_truth"], values["predicted"]
        classes[name] = {**values,
                         "recall": values["tp"] / gt_count if gt_count else None,
                         "precision": values["tp"] / predicted_count if predicted_count else None}
    totals = {key: sum(values[key] for values in stats.values())
              for key in ("ground_truth", "predicted", "tp", "fp", "fn")}
    totals["recall"] = totals["tp"] / totals["ground_truth"] if totals["ground_truth"] else None
    totals["precision"] = totals["tp"] / totals["predicted"] if totals["predicted"] else None
    totals["misses_per_1000_objects"] = (1000 * totals["fn"] / totals["ground_truth"]
                                          if totals["ground_truth"] else None)
    return {"iou_threshold": iou_threshold, "images": len(all_files),
            "zero_candidate_images": zero_candidate_images,
            "images_with_misses": images_with_misses,
            "overall": totals, "classes": classes, "misses": misses}
