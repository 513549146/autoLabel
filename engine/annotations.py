"""VOC XML 读写与确定性的审核质量规则。"""

from __future__ import annotations

import math
import os
from xml.etree import ElementTree as ET

import config
from .image_io import _imread_unicode


def save_as_voc_xml(annotations, image_path, output_dir):
    """将审核画布中的框保存为 VOC XML，并保留本产品的扩展字段。"""
    image = _imread_unicode(image_path)
    height, width = image.shape[:2]
    filename = os.path.basename(image_path)

    root = ET.Element("annotation")
    ET.SubElement(root, "folder").text = "images"
    ET.SubElement(root, "filename").text = filename
    ET.SubElement(root, "path").text = os.path.abspath(image_path)

    source = ET.SubElement(root, "source")
    ET.SubElement(source, "database").text = "Unknown"

    size = ET.SubElement(root, "size")
    ET.SubElement(size, "width").text = str(width)
    ET.SubElement(size, "height").text = str(height)
    ET.SubElement(size, "depth").text = "3"

    ET.SubElement(root, "segmented").text = "0"

    for ann in annotations:
        obj = ET.SubElement(root, "object")
        ET.SubElement(obj, "name").text = ann["name"]
        ET.SubElement(obj, "pose").text = "Unspecified"
        ET.SubElement(obj, "truncated").text = "0"
        ET.SubElement(obj, "difficult").text = "0"

        # 这三个字段是兼容 VOC 的小扩展：只读标准框的工具仍能使用 XML，
        # 本程序则可跨审核会话保留分数、人工状态和多边形顶点。
        score = ann.get("score")
        if score is not None:
            try:
                ET.SubElement(obj, "score").text = f"{float(score):.6f}"
            except (TypeError, ValueError):
                pass
        if ann.get("origin") in {"manual", "revised", "verified"}:
            ET.SubElement(obj, "review_state").text = ann["origin"]

        points = ann.get("points") or []
        if ann.get("type") == "polygon" and len(points) >= 3:
            polygon = ET.SubElement(obj, "polygon")
            for point in points:
                try:
                    x, y = point[:2]
                except (TypeError, ValueError, IndexError):
                    continue
                node = ET.SubElement(polygon, "pt")
                try:
                    ET.SubElement(node, "x").text = str(int(round(float(x))))
                    ET.SubElement(node, "y").text = str(int(round(float(y))))
                except (TypeError, ValueError, IndexError):
                    polygon.remove(node)

        bndbox = ET.SubElement(obj, "bndbox")
        x1, y1, x2, y2 = ann["bbox"]
        ET.SubElement(bndbox, "xmin").text = str(x1)
        ET.SubElement(bndbox, "ymin").text = str(y1)
        ET.SubElement(bndbox, "xmax").text = str(x2)
        ET.SubElement(bndbox, "ymax").text = str(y2)

    out_name = os.path.splitext(filename)[0] + ".xml"
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(os.path.join(output_dir, out_name), encoding="utf-8", xml_declaration=True)


def load_voc_xml(xml_path):
    """读取 VOC 框及分数、人工审核状态和多边形扩展字段。"""
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
    """返回命中的质量规则名；同一份标注始终得到同一结果。"""
    if not annotations:
        return {"empty"}
    image_width = image_height = 0
    if image_path:
        image = _imread_unicode(image_path)
        if image is not None:
            image_height, image_width = image.shape[:2]
    issues = set()
    for index, annotation in enumerate(annotations):
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
    """根据质量规则和模型置信度计算审核优先级。"""
    issues = annotation_issues(annotations, image_path)
    weights = {
        "invalid": 100,
        "empty": 90,
        "out_of_bounds": 55,
        "duplicate": 35,
        "tiny": 25,
        "low_confidence": 30,
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
