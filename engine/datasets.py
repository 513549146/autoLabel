"""把已通过的 VOC 标注导出为 YOLO 目标检测训练集。"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from xml.etree import ElementTree as ET

from PIL import Image

from .annotations import load_voc_xml


def export_yolo_dataset(images_dir, annotations_dir, filenames, output_dir, categories=None, validation_ratio=0.2):
    """原子地导出训练集；多边形取外接框参与目标检测训练。

    先写入临时目录，完成后再替换正式目录，避免留下半成品。
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
            file.write(
                f"path: {json.dumps(root.replace(os.sep, '/'), ensure_ascii=False)}\ntrain: images/train\nval: images/val\n\nnames:\n"
            )
            for index, category in enumerate(labels):
                file.write(f"  {index}: {json.dumps(category, ensure_ascii=False)}\n")
        if os.path.isdir(root):
            shutil.rmtree(root)
        os.replace(staging, root)
        return {
            "root": root,
            "data_yaml": os.path.join(root, "data.yaml"),
            "classes": labels,
            "train_images": image_counts["train"],
            "val_images": image_counts["val"],
            "objects": object_count,
            "validation_ratio": validation_ratio,
        }
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def training_readiness(annotations_dir, filenames, categories=None):
    """统计已通过图片的类别覆盖率，供训练前检查。"""
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
    return {
        "class_counts": class_counts,
        "labels_by_file": labels_by_file,
        "empty": empty,
        "missing": missing,
        "sparse": sparse,
    }
