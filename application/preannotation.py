"""Batch image pre-annotation service with version-safe persistence."""
from __future__ import annotations

import os
import shutil
import hashlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import core
from project import ANNOTATION_VERSION_DIR, ProjectStore

from .model_registry import ImageModelRegistry


@dataclass(frozen=True)
class ImagePreannotationRequest:
    project_root: str
    images_dir: str
    annotations_dir: str
    filenames: tuple[str, ...]
    prompt: str
    provider_id: str
    confidence: float
    weights_path: str = ""


@dataclass(frozen=True)
class ImagePreannotationResult:
    completed: tuple[str, ...]
    stopped: bool


class ImagePreannotationService:
    """Runs one provider over image files and records every prediction version."""

    def __init__(self, registry: ImageModelRegistry | None = None):
        self.registry = registry or ImageModelRegistry()

    def run(
        self,
        request: ImagePreannotationRequest,
        should_stop: Callable[[], bool],
        on_log: Callable[[str], None],
        on_progress: Callable[[int], None],
    ) -> ImagePreannotationResult:
        project = ProjectStore(request.annotations_dir, request.images_dir, request.project_root)
        on_log("正在加载检测模型，请稍候…")
        predictor = self.registry.create(request.provider_id, request.weights_path)
        on_log("模型加载完成，开始推理。")
        completed: list[str] = []
        total = len(request.filenames)

        for index, filename in enumerate(request.filenames):
            if should_stop():
                on_log("已按请求停止自动标注。")
                break
            image_path = os.path.join(request.images_dir, filename)
            xml_path = os.path.join(request.annotations_dir, Path(filename).stem + ".xml")
            try:
                if os.path.isfile(xml_path):
                    self.snapshot_annotation(project, filename, image_path, xml_path, "重跑前结果", request.provider_id, request.prompt, deduplicate=True)
                bundle = predictor.predict(image_path, request.prompt, request.confidence)
                core.save_as_voc_xml(bundle.annotations(), image_path, request.annotations_dir)
                self.snapshot_annotation(project, filename, image_path, xml_path, "自动预标注", bundle.model_version, request.prompt)
                completed.append(filename)
                on_log(f"[完成] {filename}：{len(bundle.predictions)} 个目标 · 已保存版本")
            except Exception as error:
                on_log(f"[失败] {filename}：{error}")
            if total:
                on_progress(int((index + 1) * 100 / total))

        return ImagePreannotationResult(tuple(completed), should_stop())

    @staticmethod
    def snapshot_annotation(project, filename, image_path, xml_path, source, model, prompt, *, deduplicate=False):
        """Copy the current XML into the project version store and index it."""
        if not os.path.isfile(xml_path):
            return
        if deduplicate:
            source_size = os.path.getsize(xml_path)
            source_digest = ImagePreannotationService._file_digest(xml_path)
            for version in project.annotation_versions_for(filename):
                path = version["path"]
                try:
                    if os.path.getsize(path) == source_size and ImagePreannotationService._file_digest(path) == source_digest:
                        return
                except OSError:
                    continue
        version_root = os.path.join(project.project_dir, ANNOTATION_VERSION_DIR, Path(filename).stem)
        os.makedirs(version_root, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        snapshot = os.path.join(version_root, f"{stamp}-{source}.xml")
        shutil.copy2(xml_path, snapshot)
        annotations = core.load_voc_xml(snapshot)
        risk, rules = core.annotation_risk(annotations, image_path)
        project.record_annotation_version(filename, snapshot, source, model, prompt, len(annotations), risk, rules)
        project.upsert_quality(filename, risk, rules)

    @staticmethod
    def _file_digest(path):
        digest = hashlib.sha256()
        with open(path, "rb") as stream:
            for chunk in iter(lambda: stream.read(65536), b""):
                digest.update(chunk)
        return digest.digest()
