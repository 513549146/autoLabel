"""Image review-queue selection independent from Qt widgets."""
from __future__ import annotations

import os
from pathlib import Path

import config
import core
from project import STATUS_NEEDS_REVIEW


class ImageReviewQueue:
    """Builds the existing new/review/risk queues from project state."""

    @staticmethod
    def target_files(images_dir: str, annotations_dir: str, project, scope: str) -> list[str]:
        """按处理范围筛选图片；XML 损坏的图片会进入队列而不是被静默丢弃。"""
        filenames = core.list_images(images_dir)
        statuses = project.statuses_for(filenames) if project else {}
        result: list[str] = []
        for filename in filenames:
            image_path = os.path.join(images_dir, filename)
            xml_path = os.path.join(annotations_dir, Path(filename).stem + ".xml")
            if scope == "new" and not os.path.isfile(xml_path):
                result.append(filename)
            elif scope == "all":
                result.append(filename)
            elif scope == "review" and statuses.get(filename) == STATUS_NEEDS_REVIEW:
                result.append(filename)
            elif scope == "risk" and statuses.get(filename) == STATUS_NEEDS_REVIEW and os.path.isfile(xml_path):
                try:
                    risk, _ = core.annotation_risk(core.load_voc_xml(xml_path), image_path)
                    if risk >= config.REVIEW_HIGH_RISK:
                        result.append(filename)
                except Exception:
                    # A damaged annotation belongs in the queue rather than
                    # silently disappearing from the reviewer’s workload.
                    result.append(filename)
        return result
