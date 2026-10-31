"""Atomic high-recall operations shared by the quick annotation runner.

Tiling is conditional on image size. Every emitted proposal retains its model
and full-image/tile origin so review can distinguish agreement from uncertainty.
"""
from __future__ import annotations

import os
import tempfile
from dataclasses import replace

from PIL import Image, ImageOps

import config

from .contracts import PredictionBundle


def tile_windows(width: int, height: int, size: int, overlap: float) -> tuple[tuple[int, int, int, int], ...]:
    """Cover the image with overlapping windows, including its right/bottom edges."""
    if width <= 0 or height <= 0 or size <= 0 or not 0 <= overlap < 1:
        raise ValueError("无效的切片参数")

    def starts(length: int) -> list[int]:
        if length <= size:
            return [0]
        stride = max(1, round(size * (1 - overlap)))
        positions = list(range(0, length - size + 1, stride))
        if positions[-1] != length - size:
            positions.append(length - size)
        return positions

    return tuple((x, y, min(width, x + size), min(height, y + size))
                 for y in starts(height) for x in starts(width))


class TileCropOperation:
    """Materialize one crop for path-based detector adapters."""

    def execute(self, image, window, destination):
        # Temporary crops need exact pixels but no compression effort.
        image.crop(window).save(destination, compress_level=0)
        return destination


class TileCoordinateRestoreOperation:
    """Translate one tile's detections into original-image coordinates."""

    def execute(self, bundle, left, top, provider_id):
        source = f"{provider_id}/tile"
        restored = []
        for prediction in bundle.predictions:
            x1, y1, x2, y2 = prediction.bbox
            restored.append(replace(
                prediction, bbox=(x1 + left, y1 + top, x2 + left, y2 + top),
                source=source, sources=(source,),
                source_scores=((source, prediction.score),) if prediction.score is not None else (),
            ))
        return tuple(restored)


class TiledPredictionOperation:
    """Run each enabled detector on overlapping crops and restore global boxes."""

    def execute(self, context, provider_ids, image_path, should_stop=lambda: False):
        if not context.request.config.tiled_inference or not provider_ids:
            return ()
        with Image.open(image_path) as opened:
            image = ImageOps.exif_transpose(opened)
            width, height = image.size
            if max(width, height) < config.HIGH_RECALL_TILE_MIN_DIMENSION:
                return ()
            image = image.convert("RGB")
        windows = tile_windows(width, height, config.HIGH_RECALL_TILE_SIZE,
                               config.HIGH_RECALL_TILE_OVERLAP)
        collected = {provider_id: [] for provider_id in provider_ids}
        crop = TileCropOperation()
        restore = TileCoordinateRestoreOperation()
        # Crop files are disposable inputs for the existing path-based adapters.
        with tempfile.TemporaryDirectory(prefix=".autolabel-tiles-", dir=context.request.project_root) as temporary:
            for index, (left, top, right, bottom) in enumerate(windows):
                if should_stop():
                    raise InterruptedError("切片补扫已取消，当前图片未覆盖原标注")
                crop_path = os.path.join(temporary, f"tile-{index:04d}.png")
                crop.execute(image, (left, top, right, bottom), crop_path)
                for provider_id in provider_ids:
                    # Import here to keep the atomic operations usable independently.
                    from .quick_operations import CandidatePredictionOperation
                    bundle = CandidatePredictionOperation().execute(context, provider_id, crop_path)
                    collected[provider_id].extend(restore.execute(bundle, left, top, provider_id))
                os.remove(crop_path)
        return tuple(PredictionBundle(provider_id, "tiled", tuple(collected[provider_id]))
                     for provider_id in provider_ids)
