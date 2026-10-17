"""Capture README screenshots from a disposable, synthetic Qt project.

Run from the repository root with ``python tools/capture_readme_screenshots.py``.
The script does not open the user's recent projects or run a detection model.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter, QPen  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import core  # noqa: E402
import gui.workbench as workbench_module  # noqa: E402
from gui.ui import apply_app_style  # noqa: E402
from gui.pages.auto_page import CategoryManagerDialog  # noqa: E402
from project import (  # noqa: E402
    ProjectStore, STATUS_APPROVED, STATUS_NEEDS_REVIEW, STATUS_SKIPPED,
    create_project_manifest,
)

OUTPUT = ROOT / "assets" / "docs"


class DemoWorkspace:
    """Keep documentation capture independent of the user's workspace.db."""

    def __init__(self):
        self._recent = []

    def recent_projects(self):
        return list(self._recent)

    def last_project(self):
        return None

    def remember_project(self, path, name):
        self._recent = [(path, name)]


def draw_sample_image(path: Path, offset: int):
    image = QImage(960, 600, QImage.Format_RGB32)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.fillRect(0, 0, 960, 600, QColor("#E9EEF2"))
    painter.fillRect(0, 394, 960, 206, QColor("#D7D9D4"))
    painter.fillRect(0, 385, 960, 9, QColor("#A8B4BA"))

    # Warehouse racks and parcels provide a recognizable, wholly synthetic
    # scene while keeping the project's review bounding boxes easy to read.
    for x in (60, 730):
        painter.fillRect(x, 86, 12, 310, QColor("#647684"))
        painter.fillRect(x + 145, 86, 12, 310, QColor("#647684"))
        for y in (184, 292, 390):
            painter.fillRect(x, y, 157, 9, QColor("#647684"))
    for x, y, width, height in ((83, 228, 80, 55), (91, 122, 92, 54), (767, 228, 86, 56), (782, 120, 77, 55)):
        painter.fillRect(x, y, width, height, QColor("#BD9B77"))
        painter.setPen(QPen(QColor("#987858"), 2))
        painter.drawRect(x, y, width, height)
    painter.setPen(Qt.NoPen)

    person_x = 263 + offset
    painter.setBrush(QColor("#E9B48A"))
    painter.drawEllipse(person_x + 27, 176, 54, 60)
    painter.setBrush(QColor("#F2B544"))
    painter.drawRoundedRect(person_x + 20, 164, 68, 24, 9, 9)
    painter.setBrush(QColor("#25718F"))
    painter.drawRoundedRect(person_x + 10, 239, 89, 132, 18, 18)
    painter.setPen(QPen(QColor("#273F51"), 15, Qt.SolidLine, Qt.RoundCap))
    painter.drawLine(person_x + 30, 368, person_x + 18, 445)
    painter.drawLine(person_x + 77, 368, person_x + 91, 445)
    painter.setPen(Qt.NoPen)

    forklift_x = 480 - offset // 2
    painter.setBrush(QColor("#E6A23A"))
    painter.drawRoundedRect(forklift_x, 309, 248, 95, 15, 15)
    painter.setBrush(QColor("#384E60"))
    painter.drawRect(forklift_x + 65, 224, 9, 111)
    painter.drawRect(forklift_x + 195, 224, 9, 111)
    painter.drawRect(forklift_x + 65, 220, 139, 10)
    painter.drawRect(forklift_x + 236, 171, 11, 230)
    painter.drawRect(forklift_x + 237, 393, 106, 9)
    painter.setBrush(QColor("#2B333A"))
    painter.drawEllipse(forklift_x + 35, 382, 57, 57)
    painter.drawEllipse(forklift_x + 177, 382, 57, 57)
    painter.end()
    if not image.save(str(path), "JPEG", 90):
        raise RuntimeError(f"Could not save synthetic image: {path}")


def make_demo_project(root: Path):
    images = root / "images"
    annotations = root / "annotations"
    for directory in (images, annotations, root / "models", root / "annotation_versions", root / "yolo_dataset"):
        directory.mkdir(parents=True, exist_ok=True)
    create_project_manifest(str(root), "示例 · 仓储安全标注")
    project = ProjectStore(str(annotations), str(images), str(root))
    project.set_category_records([
        {"name": "person", "display_name": "人员"},
        {"name": "forklift", "display_name": "叉车"},
        {"name": "helmet", "display_name": "安全帽"},
    ])
    project.set_metadata_value("training_target_images", "4")

    for number in range(1, 8):
        filename = f"warehouse-{number:02d}.jpg"
        image_path = images / filename
        offset = (number - 1) * 8
        draw_sample_image(image_path, offset)
        person_x = 263 + offset
        forklift_x = 480 - offset // 2
        shapes = [
            {"name": "person", "bbox": [person_x + 8, 163, person_x + 102, 445], "score": 0.18 if number == 1 else 0.91},
            {"name": "helmet", "bbox": [person_x + 18, 163, person_x + 90, 190], "score": 0.84},
            {"name": "forklift", "bbox": [forklift_x - 1, 169, forklift_x + 343, 441], "score": 0.79},
        ]
        if number == 2:
            shapes = []  # A second review example: empty annotation.
        core.save_as_voc_xml(shapes, str(image_path), str(annotations))
        risk, rules = core.annotation_risk(shapes, str(image_path))
        project.upsert_quality(filename, risk, rules)
        status = STATUS_NEEDS_REVIEW if number <= 2 else STATUS_SKIPPED if number == 7 else STATUS_APPROVED
        project.set_status(filename, status)
    return project


def settle(app, seconds=0.6):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        time.sleep(0.02)
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def capture(window, app, filename):
    settle(app)
    target = OUTPUT / filename
    if not window.grab().save(str(target), "PNG"):
        raise RuntimeError(f"Could not capture {target}")
    print(f"saved {target.relative_to(ROOT)}")


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    QApplication.setAttribute(Qt.AA_DontUseNativeDialogs, True)
    app = QApplication.instance() or QApplication([])
    apply_app_style(app)
    with tempfile.TemporaryDirectory(prefix="_docs_demo_", dir=ROOT) as temporary:
        demo_root = Path(temporary).resolve()
        if not demo_root.is_relative_to(ROOT.resolve()):
            raise RuntimeError("Demo directory escaped the repository")
        make_demo_project(demo_root)
        with patch.object(workbench_module, "WorkspaceStore", DemoWorkspace), patch.object(
            workbench_module.Workbench, "restore_last_project", lambda self: None,
        ):
            window = workbench_module.Workbench()
            window.resize(1420, 900)
            window.show()
            capture(window, app, "01-project-hub.png")
            if not window.load_project(str(demo_root), open_review=False):
                raise RuntimeError("Could not open synthetic documentation project")
            capture(window, app, "02-auto-label.png")
            category_dialog = CategoryManagerDialog(window.project, window)
            category_dialog.show()
            settle(app, 0.2)
            category_target = OUTPUT / "02-category-manager.png"
            if not category_dialog.grab().save(str(category_target), "PNG"):
                raise RuntimeError(f"Could not capture {category_target}")
            print(f"saved {category_target.relative_to(ROOT)}")
            category_dialog.close()
            window.open_workflow()
            capture(window, app, "03-workflow.png")
            window.show_page("review")
            window.review.issue_filter.setCurrentIndex(window.review.issue_filter.findData("risk"))
            window.review.select(1)  # Show editable boxes, not the empty-annotation example.
            capture(window, app, "04-review.png")
            window.review.toggle_annotation_drawer(True)
            capture(window, app, "04-annotation-list.png")
            window.review.toggle_annotation_drawer(False)
            window.show_page("approved")
            capture(window, app, "05-approved.png")
            window.show_page("export")
            capture(window, app, "06-export.png")
            window.close()


if __name__ == "__main__":
    main()
