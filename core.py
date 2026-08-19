import os
from xml.etree import ElementTree as ET

import cv2
from PIL import Image

import config


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
# 检测器抽象：支持多种后端（旧版 GD / GD 1.5 / 微调 YOLO）
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
        if self.backend == "yolo":
            return _detect_yolo(self, image_path, prompt, box_threshold, text_threshold)
        return _detect_gd_ogc(self, image_path, prompt, box_threshold, text_threshold)


def load_models(backend=None):
    """加载检测模型，返回 Detector"""
    backend = backend or config.DETECTOR
    if backend == "gd15":
        model, processor, device = _load_gd15()
        return Detector("gd15", model, processor, device)
    if backend == "yolo":
        return Detector("yolo", _load_yolo(), None, None)
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
# 后端 3：微调后的 YOLO（Ultralytics，闭集检测）
# ---------------------------------------------------------------------------
def _load_yolo():
    from ultralytics import YOLO
    if not os.path.isfile(config.YOLO_WEIGHTS):
        raise FileNotFoundError(f"未找到 YOLO 权重: {config.YOLO_WEIGHTS}\n请先完成微调训练，或将权重放到 weights/yolo_best.pt")
    return YOLO(config.YOLO_WEIGHTS)


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
    """读取 VOC 格式标注，返回目标列表 [{name, bbox}]"""
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
        annotations.append({"name": name, "bbox": [x1, y1, x2, y2]})
    return annotations


def parse_categories(prompt):
    """从提示词中解析类别列表（以 . 分隔）"""
    return [p.strip() for p in prompt.split(".") if p.strip()]


def list_images(input_dir):
    """返回目录下所有支持的图片文件名（排序后）"""
    return [f for f in sorted(os.listdir(input_dir)) if f.lower().endswith(config.IMG_EXT)]


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
