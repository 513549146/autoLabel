# -*- coding: utf-8 -*-
"""微调工作流核心逻辑：VOC -> YOLO 导出、YOLO 训练。可被 GUI 与命令行复用。"""
import os
import random
import shutil
from xml.etree import ElementTree as ET

import config

IMG_EXT = (".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff")


def _read_voc(xml_path):
    tree = ET.parse(xml_path)
    root = tree.getroot()
    size = root.find("size")
    width = int(float(size.findtext("width", "0")))
    height = int(float(size.findtext("height", "0")))
    objs = []
    for obj in root.findall("object"):
        name = obj.findtext("name", "")
        b = obj.find("bndbox")
        if b is None:
            continue
        x1 = float(b.findtext("xmin", "0"))
        y1 = float(b.findtext("ymin", "0"))
        x2 = float(b.findtext("xmax", "0"))
        y2 = float(b.findtext("ymax", "0"))
        objs.append((name, x1, y1, x2, y2))
    return width, height, objs


def export_voc_to_yolo(images_dir, ann_dir, out_dir, classes=None, val_ratio=0.2, seed=42, log=print):
    """将 VOC 标注导出为 YOLO 训练集。返回 (class_map, counts)。"""
    pairs = []
    for f in sorted(os.listdir(ann_dir)):
        if not f.lower().endswith(".xml"):
            continue
        stem = os.path.splitext(f)[0]
        img_path = None
        for ext in IMG_EXT:
            cand = os.path.join(images_dir, stem + ext)
            if os.path.isfile(cand):
                img_path = cand
                break
        if img_path is None:
            log(f"[跳过] 找不到图片: {stem}")
            continue
        pairs.append((img_path, os.path.join(ann_dir, f)))

    if not pairs:
        raise ValueError("没有找到任何 图片/标注 配对，请检查图片目录与标注目录")

    if classes:
        class_map = {c.strip(): i for i, c in enumerate(classes.split(",")) if c.strip()}
    else:
        names = set()
        for _, xml_path in pairs:
            _, _, objs = _read_voc(xml_path)
            names.update(o[0] for o in objs)
        class_map = {name: i for i, name in enumerate(sorted(names))}

    log(f"类别: {class_map}")

    random.seed(seed)
    random.shuffle(pairs)
    n_val = int(len(pairs) * val_ratio)
    splits = [("train", pairs[n_val:]), ("val", pairs[:n_val])]
    counts = {}
    for split, subset in splits:
        img_dir = os.path.join(out_dir, "images", split)
        lbl_dir = os.path.join(out_dir, "labels", split)
        os.makedirs(img_dir, exist_ok=True)
        os.makedirs(lbl_dir, exist_ok=True)
        for img_path, xml_path in subset:
            stem = os.path.splitext(os.path.basename(img_path))[0]
            ext = os.path.splitext(img_path)[1]
            shutil.copy2(img_path, os.path.join(img_dir, stem + ext))
            width, height, objs = _read_voc(xml_path)
            lines = []
            for name, x1, y1, x2, y2 in objs:
                if name not in class_map:
                    log(f"[警告] 未知类别 {name}，已忽略（{stem}）")
                    continue
                cls = class_map[name]
                xc = (x1 + x2) / 2 / width
                yc = (y1 + y2) / 2 / height
                w = (x2 - x1) / width
                h = (y2 - y1) / height
                lines.append(f"{cls} {xc:.6f} {yc:.6f} {w:.6f} {h:.6f}")
            with open(os.path.join(lbl_dir, stem + ".txt"), "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
        counts[split] = len(subset)
        log(f"[{split}] {len(subset)} 张")

    names_yaml = "\n".join(f"  {i}: {name}" for name, i in sorted(class_map.items(), key=lambda kv: kv[1]))
    yaml_path = os.path.join(out_dir, "dataset.yaml")
    with open(yaml_path, "w", encoding="utf-8") as f:
        f.write(f"path: {os.path.abspath(out_dir)}\n")
        f.write("train: images/train\n")
        f.write("val: images/val\n")
        f.write("names:\n")
        f.write(names_yaml + "\n")

    log(f"导出完成 -> {os.path.abspath(out_dir)}")
    log(f"数据配置: {yaml_path}")
    return class_map, counts, yaml_path


def train_yolo(data, model_name="yolov8s.pt", epochs=100, imgsz=640, batch=16, device=None,
               log=print, stop_event=None):
    """微调训练 YOLO。返回 best.pt 路径（若找到）。"""
    from ultralytics import YOLO

    # 项目内已预下载的权重优先使用，否则由 ultralytics 联网下载
    local_path = os.path.join(config.WEIGHTS_DIR, model_name)
    if os.path.isfile(local_path):
        model_name = local_path

    model = YOLO(model_name)
    log(f"加载预训练模型: {model_name}")

    def on_fit_epoch_end(trainer):
        if stop_event is not None and stop_event.is_set():
            trainer.stop_training = True
            return
        ep = getattr(trainer, "epoch", 0) + 1
        total_ep = getattr(trainer, "epochs", epochs)
        metrics = getattr(trainer, "metrics", {}) or {}
        m50 = metrics.get("metrics/mAP50(B)")
        m50_95 = metrics.get("metrics/mAP50-95(B)")
        parts = [f"Epoch {ep}/{total_ep}"]
        if m50 is not None:
            parts.append(f"mAP50={float(m50):.4f}")
        if m50_95 is not None:
            parts.append(f"mAP50-95={float(m50_95):.4f}")
        log("  ".join(parts))

    model.add_callback("on_fit_epoch_end", on_fit_epoch_end)
    model.train(
        data=data,
        epochs=epochs,
        imgsz=imgsz,
        batch=batch,
        device=device,
        verbose=False,
    )

    best = getattr(model.trainer, "best", None)
    if best and os.path.isfile(best):
        log(f"训练完成，最优权重: {best}")
    else:
        log("训练完成")
    return best
