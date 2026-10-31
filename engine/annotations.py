"""VOC XML 读取与确定性的审核质量规则；写入入口位于 core.py。"""

from __future__ import annotations

import math
import os
from xml.etree import ElementTree as ET

import config
from .image_io import _imread_unicode


def load_voc_xml(xml_path):
    """读取 VOC 框及分数、人工状态、候选来源和多边形扩展字段。"""
    tree = ET.parse(xml_path)
    root = tree.getroot()
    annotations = []
    for obj in root.findall("object"):
        name = obj.findtext("name", "")
        bndbox = obj.find("bndbox")
        if bndbox is None:
            continue
        x1 = int(float(bndbox.findtext("xmin", "0")))
        y1 = int(float(bndbox.findtext("ymin", "0")))
        x2 = int(float(bndbox.findtext("xmax", "0")))
        y2 = int(float(bndbox.findtext("ymax", "0")))
        annotation = {"name": name, "bbox": [x1, y1, x2, y2]}
        try:
            score = obj.findtext("score")
            if score is not None:
                annotation["score"] = float(score)
        except ValueError:
            pass
        origin = obj.findtext("review_state")
        if origin in {"manual", "revised", "verified"}:
            annotation["origin"] = origin
        sources = [str(node.text).strip() for node in obj.findall("model_sources/model") if node.text]
        if sources:
            annotation["sources"] = sources
        source_scores = []
        for node in obj.findall("model_scores/model"):
            try:
                source_scores.append([node.attrib["name"], float(node.attrib["score"])])
            except (KeyError, ValueError):
                continue
        if source_scores:
            annotation["source_scores"] = source_scores
        flags = [str(node.text).strip() for node in obj.findall("review_flags/flag") if node.text]
        if flags:
            annotation["risk_flags"] = flags
        alternate_labels = [str(node.text).strip() for node in obj.findall("alternate_labels/label") if node.text]
        if alternate_labels:
            annotation["alternate_labels"] = alternate_labels
        points = []
        for point in obj.findall("polygon/pt"):
            try:
                points.append([int(float(point.findtext("x", "0"))), int(float(point.findtext("y", "0")))])
            except ValueError:
                continue
        if len(points) >= 3:
            annotation["type"] = "polygon"
            annotation["points"] = points
        annotations.append(annotation)
    return annotations


def _bbox_iou(first, second):
    ax1, ay1, ax2, ay2 = first
    bx1, by1, bx2, by2 = second
    left, top, right, bottom = max(ax1, bx1), max(ay1, by1), min(ax2, bx2), min(ay2, by2)
    overlap = max(0, right - left) * max(0, bottom - top)
    if not overlap:
        return 0.0
    union = max(1, (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - overlap)
    return overlap / union


def annotation_issues(annotations, image_path=None):
    """按固定规则识别空标注、异常框、低置信度及高召回风险。"""
    if not annotations:
        return {"empty"}
    image_width = image_height = 0
    if image_path:
        image = _imread_unicode(image_path)
        if image is not None:
            image_height, image_width = image.shape[:2]
    issues = set()
    for index, annotation in enumerate(annotations):
        issues.update(
            flag
            for flag in (annotation.get("risk_flags") or ())
            if flag in {"single_model", "tile_only", "unknown_label", "class_conflict",
                       "weak_mask_merge", "tile_fragment_merge"}
        )
        try:
            x1, y1, x2, y2 = [float(value) for value in annotation.get("bbox", (0, 0, 0, 0))]
        except (TypeError, ValueError):
            issues.add("invalid")
            continue
        if not all(math.isfinite(value) for value in (x1, y1, x2, y2)):
            issues.add("invalid")
            continue
        width, height = abs(x2 - x1), abs(y2 - y1)
        if width * height < config.REVIEW_MIN_BOX_AREA:
            issues.add("tiny")
        if image_width > 0 and (x1 < 0 or y1 < 0 or x2 > image_width or y2 > image_height):
            issues.add("out_of_bounds")
        try:
            if annotation.get("score") is not None and float(annotation["score"]) < config.REVIEW_LOW_CONFIDENCE:
                issues.add("low_confidence")
        except (TypeError, ValueError):
            issues.add("invalid")
        current = (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
        for other in annotations[:index]:
            try:
                ox1, oy1, ox2, oy2 = [float(value) for value in other.get("bbox", (0, 0, 0, 0))]
            except (TypeError, ValueError):
                continue
            compared = (min(ox1, ox2), min(oy1, oy2), max(ox1, ox2), max(oy1, oy2))
            if (
                annotation.get("name") == other.get("name")
                and _bbox_iou(current, compared) >= config.REVIEW_DUPLICATE_IOU
            ):
                issues.add("duplicate")
    return issues


def annotation_risk(annotations, image_path=None):
    """汇总质量规则与模型不确定性，给出审核优先级分数。"""
    issues = annotation_issues(annotations, image_path)
    weights = {
        "invalid": 100,
        "empty": 90,
        "out_of_bounds": 55,
        "duplicate": 35,
        "tiny": 25,
        "low_confidence": 30,
        "single_model": 35,
        "tile_only": 25,
        "unknown_label": 55,
        "class_conflict": 60,
        "weak_mask_merge": 45,
        "tile_fragment_merge": 45,
    }
    score = sum(weights.get(issue, 0) for issue in issues)
    scores = []
    for annotation in annotations:
        try:
            if annotation.get("score") is not None:
                scores.append(float(annotation["score"]))
        except (TypeError, ValueError):
            pass
    if scores:
        score += round(max(0.0, config.REVIEW_LOW_CONFIDENCE - min(scores)) * 100)
    return min(100, score), issues


def parse_categories(prompt):
    """从提示词中解析类别列表（以 . 分隔）"""
    return [p.strip() for p in prompt.split(".") if p.strip()]


def list_images(input_dir):
    """返回目录下所有支持的图片文件名（排序后）"""
    return [f for f in sorted(os.listdir(input_dir)) if f.lower().endswith(config.IMG_EXT)]
