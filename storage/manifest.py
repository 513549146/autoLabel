"""可携带的项目清单文件，以及项目内路径的安全解析。"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from .constants import ANNOTATION_VERSION_DIR, WORKBENCH_FILE


def _now():
    # 最近项目可能在同一秒内多次切换；微秒精度保证 SQLite 排序仍代表真实顺序。
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _clean_categories(categories):
    """保留类别原顺序，并去掉空名称和重复项。"""
    result, seen = [], set()
    for category in categories:
        name = str(category).strip()
        if name and name not in seen:
            result.append(name)
            seen.add(name)
    return result


def project_manifest_path(project_dir):
    """返回项目根目录中的清单文件路径。"""
    return os.path.join(os.path.abspath(project_dir), WORKBENCH_FILE)


def create_project_manifest(project_dir, name=None):
    """创建项目清单；已有有效清单时直接沿用。"""
    root = os.path.abspath(project_dir)
    os.makedirs(root, exist_ok=True)
    data = {
        "format": "autolabel-workbench-project",
        "version": 1,
        "name": (name or os.path.basename(root)).strip() or os.path.basename(root),
        "directories": {
            "images": "images",
            "annotations": "annotations",
            "models": "models",
            "annotation_versions": ANNOTATION_VERSION_DIR,
            "dataset": "yolo_dataset",
        },
        "created_at": _now(),
    }
    path = project_manifest_path(root)
    if os.path.isfile(path):
        loaded = load_project_manifest(root)
        if loaded:
            return loaded
    temp_path = path + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
    os.replace(temp_path, path)
    return data


def load_project_manifest(project_dir):
    """读取项目清单；目录不是有效项目时返回 None。"""
    try:
        with open(project_manifest_path(project_dir), encoding="utf-8") as file:
            data = json.load(file)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict) or data.get("format") != "autolabel-workbench-project":
        return None
    directories = data.get("directories")
    if not isinstance(directories, dict):
        return None
    return data


def resolve_project_directory(project_dir, relative_path):
    """解析清单里的相对目录，并阻止路径越出项目根目录。"""
    root = os.path.abspath(project_dir)
    candidate = os.path.abspath(os.path.join(root, str(relative_path)))
    if os.path.commonpath((root, candidate)) != root:
        raise ValueError("项目目录配置不能指向项目文件夹之外")
    return candidate
