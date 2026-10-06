import math
import os
import hashlib
import json
import shutil
import threading
import uuid
from contextlib import contextmanager
from xml.etree import ElementTree as ET

import cv2
from PIL import Image

import config


_WORKING_DIRECTORY_LOCK = threading.RLock()


@contextmanager
def _working_directory(path):
    """Temporarily use an explicit working directory for third-party model assets."""
    with _WORKING_DIRECTORY_LOCK:
        previous = os.getcwd()
        os.chdir(path)
        try:
            yield
        finally:
            os.chdir(previous)


def get_device():
    import torch
    return "cuda" if torch.cuda.is_available() else "cpu"


def _imread_unicode(path):
    """读取图片，兼容含中文等非 ASCII 字符的路径（cv2.imread 在 Windows 上无法处理）"""
    import numpy as np
    with open(path, "rb") as f:
        data = np.frombuffer(f.read(), dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def _cxcywh_to_xyxy(box, width, height):
    x_c, y_c, w, h = box
    x1 = (x_c - w / 2) * width
    y1 = (y_c - h / 2) * height
    x2 = (x_c + w / 2) * width
    y2 = (y_c + h / 2) * height
    return int(x1), int(y1), int(x2), int(y2)


# ---------------------------------------------------------------------------
# 检测器抽象：支持多种后端（YOLOE / 旧版 GD / GD 1.5 / 微调 YOLO）
# ---------------------------------------------------------------------------
class Detector:
    def __init__(self, backend, model, processor=None, device=None):
        self.backend = backend
        self.model = model
        self.processor = processor
        self.device = device

    def detect(self, image_path, prompt, box_threshold, text_threshold):
        if self.backend == "gd15":
            return _detect_gd15(self, image_path, prompt, box_threshold, text_threshold)
        if self.backend == "yoloe":
            return _detect_yoloe(self, image_path, prompt, box_threshold, text_threshold)
        if self.backend == "yolo":
            return _detect_yolo(self, image_path, prompt, box_threshold, text_threshold)
        return _detect_gd_ogc(self, image_path, prompt, box_threshold, text_threshold)


def load_models(backend=None, weights_path=None):
    """加载检测模型，返回 Detector"""
    backend = backend or config.DETECTOR
    if backend == "gd15":
        model, processor, device = _load_gd15()
        return Detector("gd15", model, processor, device)
    if backend == "yoloe":
        return Detector("yoloe", _load_yoloe(weights_path), None, None)
    if backend in ("yolo", "custom_yolo"):
        return Detector("yolo", _load_yolo(weights_path), None, None)
    return Detector("gd_ogc", _load_gd_ogc(), None, get_device())


def auto_label(image_path, text_prompt, detector, box_threshold=config.BOX_THRESHOLD, text_threshold=config.TEXT_THRESHOLD):
    """检测单张图片，返回目标列表 [{name, bbox, score}]"""
    return detector.detect(image_path, text_prompt, box_threshold, text_threshold)


# ---------------------------------------------------------------------------
# 后端 1：旧版 Grounding DINO（groundingdino 包，SwinT-OGC）
# ---------------------------------------------------------------------------
def _load_gd_ogc():
    from groundingdino.util.slconfig import SLConfig
    from groundingdino.models import build_model
    from groundingdino.util.misc import clean_state_dict
    import torch

    if not os.path.isfile(config.GROUNDINGDINO_CONFIG):
        raise FileNotFoundError(f"未找到模型配置: {config.GROUNDINGDINO_CONFIG}")
    if not os.path.isfile(config.GROUNDINGDINO_WEIGHTS):
        raise FileNotFoundError(f"未找到权重文件: {config.GROUNDINGDINO_WEIGHTS}\n请将 weights 文件夹放到程序同目录下")
    if not os.path.isdir(config.BERT_ENCODER_DIR):
        raise FileNotFoundError(f"未找到文本编码器: {config.BERT_ENCODER_DIR}\n请将 weights 文件夹放到程序同目录下")

    args = SLConfig.fromfile(config.GROUNDINGDINO_CONFIG)
    args.device = get_device()
    args.text_encoder_type = config.BERT_ENCODER_DIR
    model = build_model(args)
    checkpoint = torch.load(config.GROUNDINGDINO_WEIGHTS, map_location="cpu")
    model.load_state_dict(clean_state_dict(checkpoint["model"]), strict=False)
    model.eval()
    return model


def _detect_gd_ogc(detector, image_path, prompt, box_threshold, text_threshold):
    from groundingdino.util.inference import predict
    from groundingdino.datasets import transforms as T

    image = _imread_unicode(image_path)
    if image is None:
        raise FileNotFoundError(f"无法读取图片: {image_path}")
    height, width = image.shape[:2]

    transform = T.Compose(
        [
            T.RandomResize([800], max_size=1333),
            T.ToTensor(),
            T.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
        ]
    )
    image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    image_pil = Image.fromarray(image_rgb)
    image_tensor, _ = transform(image_pil, None)

    boxes, logits, phrases = predict(
        model=detector.model,
        image=image_tensor,
        caption=prompt,
        box_threshold=box_threshold,
        text_threshold=text_threshold,
        remove_combined=True,
    )

    annotations = []
    for i, box in enumerate(boxes):
        x1, y1, x2, y2 = _cxcywh_to_xyxy(box.tolist(), width, height)
        annotations.append({"name": phrases[i], "bbox": [x1, y1, x2, y2], "score": float(logits[i])})
    return annotations


# ---------------------------------------------------------------------------
# 后端 2：Grounding DINO 1.5（transformers 实现，更强）
# ---------------------------------------------------------------------------
def _load_gd15():
    import torch
    from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor

    if not os.path.isdir(config.GD15_DIR):
        raise FileNotFoundError(f"未找到模型目录: {config.GD15_DIR}\n请将 weights 文件夹放到程序同目录下")
    device = get_device()
    model = AutoModelForZeroShotObjectDetection.from_pretrained(config.GD15_DIR).to(device)
    processor = AutoProcessor.from_pretrained(config.GD15_DIR)
    model.eval()
    return model, processor, device


def _detect_gd15(detector, image_path, prompt, box_threshold, text_threshold):
    import bisect
    import torch

    model = detector.model
    processor = detector.processor
    device = detector.device

    image = Image.open(image_path).convert("RGB")
    w, h = image.size
    inputs = processor(images=image, text=prompt, return_tensors="pt").to(device)

    with torch.no_grad():
        outputs = model(**inputs)

    probs = torch.sigmoid(outputs.logits)[0].cpu()          # [num_queries, 256]
    scores, max_tok = probs.max(dim=-1)
    boxes_cxcywh = outputs.pred_boxes[0].cpu()              # [num_queries, 4] 归一化 cxcywh

    input_ids = inputs.input_ids[0].cpu().tolist()
    tokenizer = processor.tokenizer
    special = {tokenizer.cls_token_id, tokenizer.sep_token_id, tokenizer.pad_token_id}
    period = tokenizer.convert_tokens_to_ids(".")
    separators = [i for i, t in enumerate(input_ids) if t == period or t in special]

    annotations = []
    for q in range(scores.shape[0]):
        score = float(scores[q])
        if score < box_threshold:
            continue

        cx, cy, bw, bh = boxes_cxcywh[q].tolist()
        x1, y1, x2, y2 = _cxcywh_to_xyxy((cx, cy, bw, bh), w, h)

        m = int(max_tok[q])
        pos = bisect.bisect_left(separators, m)
        right = separators[pos] if pos < len(separators) else len(input_ids) - 1
        left = separators[pos - 1] if pos > 0 else 0
        seg = [input_ids[i] for i in range(left + 1, right) if float(probs[q][i]) > text_threshold]
        label = tokenizer.decode(seg).replace(".", "").strip()

        annotations.append({"name": label, "bbox": [x1, y1, x2, y2], "score": score})
    return annotations


# ---------------------------------------------------------------------------
# 后端 3：YOLOE（Ultralytics，开放词表检测）
# ---------------------------------------------------------------------------
def _load_yoloe(weights_path=None):
    """加载 YOLOE；官方裸文件名在首次使用时由 Ultralytics 自动下载并缓存。"""
    from ultralytics import YOLOE

    path = weights_path or config.YOLOE_WEIGHTS
    if os.path.dirname(str(path)) and not os.path.isfile(path):
        raise FileNotFoundError(
            f"未找到 YOLOE 权重: {path}\n"
            "请将权重放到该路径，或把 config.YOLOE_WEIGHTS 设为官方权重文件名。"
        )
    return YOLOE(path)


def _parse_yoloe_classes(prompt):
    """将现有的“类别 . 类别”提示词转换为 YOLOE 所需的类别列表。"""
    classes = [part.strip() for part in prompt.replace("，", ",").replace("。", ".").replace(",", ".").split(".")]
    classes = [name for name in classes if name]
    if not classes:
        raise ValueError("YOLOE 需要至少一个类别提示词，例如：person . helmet . forklift")
    return classes


def _detect_yoloe(detector, image_path, prompt, box_threshold, text_threshold):
    """以文本类别提示运行 YOLOE，并输出与现有 VOC 管线一致的检测框。"""
    model = detector.model
    classes = _parse_yoloe_classes(prompt)
    # Ultralytics MobileCLIP resolves its auxiliary checkpoint from the process
    # working directory. Keep it alongside the primary YOLOE weights instead of
    # downloading another copy beside the executable.
    with _working_directory(config.WEIGHTS_DIR):
        model.set_classes(classes)
    results = model.predict(
        image_path,
        conf=box_threshold,
        iou=config.YOLO_IOU,
        max_det=config.YOLO_MAX_DET,
        verbose=False,
    )

    annotations = []
    if results and results[0].boxes is not None:
        for box in results[0].boxes:
            x1, y1, x2, y2 = [int(v) for v in box.xyxy[0].tolist()]
            cls_id = int(box.cls[0])
            name = model.names.get(cls_id, str(cls_id)) if isinstance(model.names, dict) else model.names[cls_id]
            annotations.append({"name": name, "bbox": [x1, y1, x2, y2], "score": float(box.conf[0])})
    return annotations


# ---------------------------------------------------------------------------
# 后端 4：微调后的 YOLO（Ultralytics，闭集检测）
# ---------------------------------------------------------------------------
def _load_yolo(weights_path=None):
    from ultralytics import YOLO
    path = os.path.abspath(weights_path or config.YOLO_WEIGHTS)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"未找到 YOLO 权重: {path}\n请先完成微调训练，或在自动标注页导入自定义 .pt 模型")
    return YOLO(path)


def _detect_yolo(detector, image_path, prompt, box_threshold, text_threshold):
    model = detector.model
    results = model.predict(
        image_path,
        conf=box_threshold,
        iou=config.YOLO_IOU,
        max_det=config.YOLO_MAX_DET,
        verbose=False,
    )
    annotations = []
    if results and results[0].boxes is not None:
        for box in results[0].boxes:
            x1, y1, x2, y2 = [int(v) for v in box.xyxy[0].tolist()]
            cls_id = int(box.cls[0])
            name = model.names.get(cls_id, str(cls_id))
            score = float(box.conf[0])
            annotations.append({"name": name, "bbox": [x1, y1, x2, y2], "score": score})
    return annotations


# ---------------------------------------------------------------------------
# VOC 读写
# ---------------------------------------------------------------------------
def save_as_voc_xml(annotations, image_path, output_dir):
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

        # ``score``, ``review_state`` and ``polygon`` are small VOC extensions. Readers that only
        # understand VOC bounding boxes keep working; autoLabel can retain model
        # confidence and editable polygon geometry across review sessions.
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
    """Read VOC boxes plus autoLabel's score, review-state and polygon extensions."""
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
    ax1, ay1, ax2, ay2 = first; bx1, by1, bx2, by2 = second
    left, top, right, bottom = max(ax1, bx1), max(ay1, by1), min(ax2, bx2), min(ay2, by2)
    overlap = max(0, right - left) * max(0, bottom - top)
    if not overlap:
        return 0.0
    union = max(1, (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - overlap)
    return overlap / union


def annotation_issues(annotations, image_path=None):
    """Return deterministic QA rule names for an annotation collection."""
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
            issues.add("invalid"); continue
        if not all(math.isfinite(value) for value in (x1, y1, x2, y2)):
            issues.add("invalid"); continue
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
            if annotation.get("name") == other.get("name") and _bbox_iou(current, compared) >= config.REVIEW_DUPLICATE_IOU:
                issues.add("duplicate")
    return issues


def annotation_risk(annotations, image_path=None):
    """Score review urgency from explicit quality rules and model uncertainty."""
    issues = annotation_issues(annotations, image_path)
    weights = {
        "invalid": 100, "empty": 90, "out_of_bounds": 55, "duplicate": 35,
        "tiny": 25, "low_confidence": 30,
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


def export_yolo_dataset(images_dir, annotations_dir, filenames, output_dir, categories=None, validation_ratio=0.2):
    """Export reviewed VOC annotations as an atomic YOLO detection dataset.

    Polygon annotations are exported through their enclosing boxes so rectangle
    and polygon review results can be trained together with a detection model.
    """
    names = [str(filename) for filename in filenames if os.path.isfile(os.path.join(images_dir, filename))]
    if not names:
        raise ValueError("没有可导出的已通过图片")
    try:
        validation_ratio = float(validation_ratio)
    except (TypeError, ValueError) as error:
        raise ValueError("验证集比例必须是 0 到 0.9 之间的数字") from error
    if not 0 < validation_ratio < 0.9:
        raise ValueError("验证集比例必须在 0 到 0.9 之间")

    annotations_by_file, discovered = {}, []
    for filename in names:
        xml_path = os.path.join(annotations_dir, os.path.splitext(filename)[0] + ".xml")
        try:
            shapes = load_voc_xml(xml_path) if os.path.isfile(xml_path) else []
        except (ET.ParseError, OSError, ValueError):
            shapes = []
        annotations_by_file[filename] = shapes
        for shape in shapes:
            name = str(shape.get("name", "")).strip()
            if name and name not in discovered:
                discovered.append(name)
    labels = []
    for category in categories or []:
        category = str(category).strip()
        if category and category not in labels:
            labels.append(category)
    labels.extend(name for name in discovered if name not in labels)
    if not labels:
        raise ValueError("已通过图片中没有有效类别，无法生成训练集")
    class_ids = {name: index for index, name in enumerate(labels)}

    ordered = sorted(names, key=lambda name: hashlib.sha1(name.encode("utf-8")).hexdigest())
    validation_count = max(1, round(len(ordered) * validation_ratio)) if len(ordered) > 1 else 0
    validation_names = set(ordered[:validation_count])
    root = os.path.abspath(output_dir)
    parent = os.path.dirname(root)
    os.makedirs(parent, exist_ok=True)
    staging = os.path.join(parent, f".{os.path.basename(root)}.autolabel-export-{uuid.uuid4().hex}")
    os.makedirs(staging, exist_ok=False)
    try:
        for split in ("train", "val"):
            os.makedirs(os.path.join(staging, "images", split), exist_ok=True)
            os.makedirs(os.path.join(staging, "labels", split), exist_ok=True)
        image_counts = {"train": 0, "val": 0}
        object_count = 0
        for filename in names:
            split = "val" if filename in validation_names else "train"
            source = os.path.join(images_dir, filename)
            destination = os.path.join(staging, "images", split, filename)
            shutil.copy2(source, destination)
            try:
                with Image.open(source) as image:
                    width, height = image.size
            except OSError as error:
                raise ValueError(f"无法读取图片尺寸：{filename}") from error
            lines = []
            for shape in annotations_by_file[filename]:
                class_id = class_ids.get(str(shape.get("name", "")).strip())
                if class_id is None or width <= 0 or height <= 0:
                    continue
                try:
                    x1, y1, x2, y2 = [float(value) for value in shape.get("bbox", ())]
                except (TypeError, ValueError):
                    continue
                left, top = max(0.0, min(x1, x2)), max(0.0, min(y1, y2))
                right, bottom = min(float(width), max(x1, x2)), min(float(height), max(y1, y2))
                if right <= left or bottom <= top:
                    continue
                center_x, center_y = (left + right) / 2 / width, (top + bottom) / 2 / height
                box_width, box_height = (right - left) / width, (bottom - top) / height
                lines.append(f"{class_id} {center_x:.6f} {center_y:.6f} {box_width:.6f} {box_height:.6f}")
            label_path = os.path.join(staging, "labels", split, os.path.splitext(filename)[0] + ".txt")
            with open(label_path, "w", encoding="utf-8", newline="\n") as file:
                file.write("\n".join(lines))
            image_counts[split] += 1
            object_count += len(lines)
        yaml_path = os.path.join(staging, "data.yaml")
        with open(yaml_path, "w", encoding="utf-8", newline="\n") as file:
            file.write(f"path: {json.dumps(root.replace(os.sep, '/'), ensure_ascii=False)}\ntrain: images/train\nval: images/val\n\nnames:\n")
            for index, category in enumerate(labels):
                file.write(f"  {index}: {json.dumps(category, ensure_ascii=False)}\n")
        if os.path.isdir(root):
            shutil.rmtree(root)
        os.replace(staging, root)
        return {
            "root": root, "data_yaml": os.path.join(root, "data.yaml"), "classes": labels,
            "train_images": image_counts["train"], "val_images": image_counts["val"], "objects": object_count, "validation_ratio": validation_ratio,
        }
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def training_readiness(annotations_dir, filenames, categories=None):
    """Summarize label coverage for the approved-set quality gate."""
    class_counts = {str(name): 0 for name in categories or [] if str(name).strip()}
    labels_by_file, empty = {}, []
    for filename in filenames:
        xml_path = os.path.join(annotations_dir, os.path.splitext(filename)[0] + ".xml")
        try:
            shapes = load_voc_xml(xml_path) if os.path.isfile(xml_path) else []
        except (ET.ParseError, OSError, ValueError):
            shapes = []
        labels = [str(shape.get("name", "")).strip() for shape in shapes if str(shape.get("name", "")).strip()]
        labels_by_file[filename] = sorted(set(labels))
        if not labels:
            empty.append(filename)
        for name in labels:
            class_counts[name] = class_counts.get(name, 0) + 1
    missing = [name for name, count in class_counts.items() if count == 0]
    sparse = [name for name, count in class_counts.items() if 0 < count < 10]
    return {"class_counts": class_counts, "labels_by_file": labels_by_file, "empty": empty, "missing": missing, "sparse": sparse}


def train_yolo_dataset(data_yaml, base_model, epochs, runs_dir):
    """Run Ultralytics training and return the resulting run and best weight paths."""
    from ultralytics import YOLO

    if not os.path.isfile(data_yaml):
        raise FileNotFoundError("请先导出训练集（未找到 data.yaml）")
    try:
        epochs = int(epochs)
    except (TypeError, ValueError) as error:
        raise ValueError("训练轮数必须是正整数") from error
    if epochs < 1:
        raise ValueError("训练轮数必须大于 0")
    os.makedirs(runs_dir, exist_ok=True)
    model = YOLO(base_model)
    model.train(data=data_yaml, epochs=epochs, project=runs_dir, name="finetune", exist_ok=False, verbose=True)
    run_dir = str(model.trainer.save_dir)
    best = os.path.join(run_dir, "weights", "best.pt")
    if not os.path.isfile(best):
        raise FileNotFoundError("训练结束但未找到 best.pt")
    return {"run_dir": run_dir, "best_weights": best}


def batch_process(input_dir=config.INPUT_DIR, output_dir=config.OUTPUT_DIR, prompt=config.PROMPT,
                  box_threshold=config.BOX_THRESHOLD, text_threshold=config.TEXT_THRESHOLD):
    os.makedirs(output_dir, exist_ok=True)
    detector = load_models()

    img_files = list_images(input_dir)
    print(f"共发现 {len(img_files)} 张图片")

    for img_file in img_files:
        image_path = os.path.join(input_dir, img_file)
        annotations = auto_label(image_path, prompt, detector, box_threshold, text_threshold)
        save_as_voc_xml(annotations, image_path, output_dir)
        print(f"[完成] {img_file}: {len(annotations)} 个目标")

    print("全部处理完毕")
