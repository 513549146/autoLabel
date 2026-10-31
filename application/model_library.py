"""Local model inventory and explicit, validated model installation."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import config


@dataclass(frozen=True)
class DownloadableModel:
    id: str
    title: str
    filename: str
    provider_id: str
    url: str
    detail: str


@dataclass(frozen=True)
class InstalledModel:
    title: str
    provider_id: str
    path: str
    origin: str
    detail: str


DOWNLOADABLE_MODELS = (
    DownloadableModel(
        "sam2.1-tiny", "SAM 2.1 Tiny", "sam2.1_t.pt", "sam2",
        "https://github.com/ultralytics/assets/releases/download/v8.3.0/sam2.1_t.pt",
        "辅助分割 · 仅对重叠候选生成掩码，供预标注去重",
    ),
    DownloadableModel(
        "yoloe-11s", "YOLOE-11s", "yoloe-11s-seg.pt", "yoloe",
        "https://github.com/ultralytics/assets/releases/download/v8.3.0/yoloe-11s-seg.pt",
        "开放词表 · 较轻量 · 按项目类别提示检测",
    ),
    DownloadableModel(
        "yolo11s", "YOLO11s", "yolo11s.pt", "custom_yolo",
        "https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11s.pt",
        "目标检测 · COCO 预训练类别；项目专用类别需先微调",
    ),
    DownloadableModel(
        "yoloe-11l", "YOLOE-11l", "yoloe-11l-seg.pt", "yoloe",
        "https://github.com/ultralytics/assets/releases/download/v8.3.0/yoloe-11l-seg.pt",
        "开放词表 · 按项目类别提示检测",
    ),
    DownloadableModel(
        "yolo11n", "YOLO11n", "yolo11n.pt", "custom_yolo",
        "https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n.pt",
        "目标检测 · COCO 预训练类别；项目专用类别需先微调",
    ),
    DownloadableModel(
        "yolo26n", "YOLO26n", "yolo26n.pt", "custom_yolo",
        "https://github.com/ultralytics/assets/releases/download/v8.4.0/yolo26n.pt",
        "目标检测 · COCO 预训练类别；项目专用类别需先微调",
    ),
    DownloadableModel(
        "yolov8n", "YOLOv8n", "yolov8n.pt", "custom_yolo",
        "https://github.com/ultralytics/assets/releases/download/v8.1.0/yolov8n.pt",
        "目标检测 · COCO 预训练类别；项目专用类别需先微调",
    ),
    DownloadableModel(
        "yolov8s", "YOLOv8s", "yolov8s.pt", "custom_yolo",
        "https://github.com/ultralytics/assets/releases/download/v8.1.0/yolov8s.pt",
        "目标检测 · COCO 预训练类别；项目专用类别需先微调",
    ),
)


class ModelLibrary:
    """Models downloaded or imported here can be reused by every local project."""

    DISABLED_KEY = "disabled_models_v1"

    def __init__(self, root: str | Path | None = None):
        base = Path(os.environ.get("LOCALAPPDATA") or config.BASE_DIR)
        self.root = Path(root) if root is not None else base / "autoLabel" / "models"

    def download_path(self, offer: DownloadableModel) -> Path:
        return self.root / "downloads" / offer.filename

    @staticmethod
    def model_key(model: InstalledModel) -> str:
        return f"{model.provider_id}:{os.path.normcase(str(Path(model.path).resolve()))}"

    def _disabled_keys(self, project) -> set[str]:
        if not project or not hasattr(project, "metadata_value"):
            return set()
        try:
            value = json.loads(project.metadata_value(self.DISABLED_KEY, "[]"))
            return {str(item) for item in value} if isinstance(value, list) else set()
        except (TypeError, ValueError):
            return set()

    def is_enabled(self, project, model: InstalledModel) -> bool:
        return self.model_key(model) not in self._disabled_keys(project)

    def set_enabled(self, project, model: InstalledModel, enabled: bool) -> None:
        if not project:
            raise ValueError("请先打开项目")
        disabled = self._disabled_keys(project)
        key = self.model_key(model)
        if enabled:
            disabled.discard(key)
        else:
            disabled.add(key)
        project.set_metadata_value(self.DISABLED_KEY, json.dumps(sorted(disabled), ensure_ascii=False))

    def can_remove(self, model: InstalledModel) -> bool:
        """Only files with a known recovery path may be removed here."""
        raw_path = Path(model.path)
        if raw_path.is_symlink():
            return False
        path = raw_path.resolve()
        if model.origin == "已下载":
            return any(not self.download_path(offer).is_symlink()
                       and path == self.download_path(offer).resolve() and model.provider_id == offer.provider_id
                       for offer in DOWNLOADABLE_MODELS)
        if model.origin == "自定义导入":
            return path.parent == (self.root / "imports").resolve() and path.suffix.lower() == ".pt"
        if model.origin == "应用自带":
            return path.parent == Path(config.WEIGHTS_DIR).resolve() and any(
                path.name == offer.filename and model.provider_id == offer.provider_id
                for offer in DOWNLOADABLE_MODELS
            )
        return False

    def remove(self, model: InstalledModel) -> None:
        """Remove a known local copy; official presets remain in the install catalog."""
        if not self.can_remove(model):
            raise ValueError("此模型没有可确认的安全删除与恢复路径")
        path = Path(model.path).resolve()
        path.unlink()

    def installed(self, project=None) -> tuple[InstalledModel, ...]:
        found: list[InstalledModel] = []
        seen: set[str] = set()

        def add(title: str, provider: str, path: Path, origin: str, detail: str):
            if not path.is_file():
                return
            key = os.path.normcase(str(path.resolve()))
            if key in seen:
                return
            seen.add(key)
            found.append(InstalledModel(title, provider, str(path), origin, detail))

        add("YOLOE-11l", "yoloe", Path(config.YOLOE_WEIGHTS), "应用自带", "开放词表 · 按项目类别提示检测")
        add("SAM 2.1 Tiny", "sam2", Path(config.WEIGHTS_DIR) / "sam2.1_t.pt",
            "应用自带", "辅助分割 · 仅用于掩码去重")
        gd15 = Path(config.GD15_DIR) / "model.safetensors"
        add("Grounding DINO Base", "gd15", gd15, "应用自带", "开放词表 · 兼容模型")
        add("Grounding DINO SwinT", "gd_ogc", Path(config.GROUNDINGDINO_WEIGHTS), "应用自带", "开放词表 · 旧版")
        for path in sorted(Path(config.WEIGHTS_DIR).glob("*.pt")):
            name = path.name.lower()
            if name.startswith(("yoloe-", "sam2.")) or any(part in name for part in ("-seg", "-pose", "-obb", "-cls")):
                continue
            add(path.stem, "custom_yolo", path, "应用自带", "YOLO 检测权重 · 使用权重自带类别")
        for offer in DOWNLOADABLE_MODELS:
            add(offer.title, offer.provider_id, self.download_path(offer), "已下载", offer.detail)
        imports = self.root / "imports"
        if imports.is_dir():
            for path in sorted(imports.glob("*.pt")):
                add(path.stem, "custom_yolo", path, "自定义导入", "YOLO 检测权重 · 使用权重自带类别")
        if project:
            for run in getattr(project, "recent_training_runs", lambda _limit: [])(200):
                path = Path(run.get("weights_path", ""))
                stamp = str(run.get("created_at", ""))[:16].replace("T", " ")
                add(path.name, "custom_yolo", path, "项目训练", f"本项目训练 · {stamp}")
        return tuple(found)

    @staticmethod
    def validate_checkpoint(path: Path, provider_id: str) -> None:
        """Load a user-selected checkpoint using the same backend as inference."""
        if path.suffix.lower() != ".pt" or not path.is_file() or path.stat().st_size == 0:
            raise ValueError("仅支持非空的兼容 .pt 权重")
        if provider_id == "yoloe":
            from ultralytics import YOLOE
            model = YOLOE(str(path))
            if model.task not in {"detect", "segment"}:
                raise ValueError("此 YOLOE 权重不支持目标检测")
        elif provider_id == "custom_yolo":
            from ultralytics import YOLO
            model = YOLO(str(path))
            if model.task != "detect":
                raise ValueError("仅支持目标检测权重；分割、姿态、分类和旋转框暂不能导入")
            if not model.names:
                raise ValueError("模型没有可读取的类别名称")
        elif provider_id == "sam2":
            from ultralytics import SAM
            model = SAM(str(path))
            if model.task != "segment":
                raise ValueError("此权重不是 SAM 分割模型")
        else:
            raise ValueError("不支持的模型类型")

    def import_checkpoint(self, source: str | Path) -> Path:
        source = Path(source)
        self.validate_checkpoint(source, "custom_yolo")
        digest = hashlib.sha256()
        with source.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        destination_dir = self.root / "imports"
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = destination_dir / f"{source.stem}-{digest.hexdigest()[:10]}.pt"
        if destination.exists():
            return destination
        with tempfile.NamedTemporaryFile(prefix=".import-", suffix=".pt", dir=destination_dir, delete=False) as temporary:
            temporary_path = Path(temporary.name)
        try:
            shutil.copyfile(source, temporary_path)
            os.replace(temporary_path, destination)
            return destination
        finally:
            temporary_path.unlink(missing_ok=True)

    def download(
        self, offer: DownloadableModel, progress: Callable[[int], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> Path:
        if offer not in DOWNLOADABLE_MODELS:
            raise ValueError("未知下载模型")
        destination = self.download_path(offer)
        if destination.is_file():
            return destination
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=".download-", suffix="-" + offer.filename,
                                         dir=destination.parent, delete=False) as temporary:
            temporary_path = Path(temporary.name)
        try:
            request = urllib.request.Request(offer.url, headers={"User-Agent": "autoLabel-model-manager/1.0"})
            with urllib.request.urlopen(request, timeout=30) as response, temporary_path.open("wb") as output:
                if response.url.split(":", 1)[0].lower() != "https":
                    raise ValueError("模型下载必须使用 HTTPS")
                total = int(response.headers.get("Content-Length", "0") or 0)
                if total > 2 * 1024**3:
                    raise ValueError("模型文件超过 2 GiB 上限")
                received = 0
                while True:
                    if cancelled and cancelled():
                        raise InterruptedError("下载已取消")
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > 2 * 1024**3:
                        raise ValueError("模型文件超过 2 GiB 上限")
                    output.write(chunk)
                    if progress and total:
                        progress(min(99, int(received * 100 / total)))
            if cancelled and cancelled():
                raise InterruptedError("下载已取消")
            self.validate_checkpoint(temporary_path, offer.provider_id)
            os.replace(temporary_path, destination)
            if progress:
                progress(100)
            return destination
        finally:
            temporary_path.unlink(missing_ok=True)
