"""Reruns keep distinct pre-label results without duplicating unchanged XML."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PySide6.QtGui import QImage

import core
from application.preannotation import ImagePreannotationService
from project import ProjectStore


class AnnotationVersionDedupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.root = Path(self.temp.name)
        self.images = self.root / "images"
        self.annotations = self.root / "annotations"
        self.images.mkdir()
        self.annotations.mkdir()
        self.filename = "sample.jpg"
        self.image_path = self.images / self.filename
        image = QImage(40, 30, QImage.Format_RGB32)
        image.fill(0xFFCCDDEE)
        self.assertTrue(image.save(str(self.image_path), "JPEG"))
        self.xml_path = self.annotations / "sample.xml"
        self.project = ProjectStore(str(self.annotations), str(self.images), str(self.root))

    def tearDown(self):
        self.temp.cleanup()

    def _write_box(self, left):
        core.save_as_voc_xml(
            [{"name": "person", "bbox": [left, 2, left + 10, 12]}],
            str(self.image_path), str(self.annotations),
        )

    def _snapshot(self, source, deduplicate=False):
        ImagePreannotationService.snapshot_annotation(
            self.project, self.filename, str(self.image_path), str(self.xml_path),
            source, "model", "person", deduplicate=deduplicate,
        )

    def test_unchanged_pre_rerun_copy_is_skipped_but_manual_change_is_preserved(self):
        self._write_box(1)
        self._snapshot("自动预标注")
        self._snapshot("重跑前结果", deduplicate=True)
        self.assertEqual(len(self.project.annotation_versions_for(self.filename)), 1)

        self._write_box(2)
        self._snapshot("自动预标注")
        self.assertEqual(len(self.project.annotation_versions_for(self.filename)), 2)

        self._write_box(3)
        self._snapshot("重跑前结果", deduplicate=True)
        self._snapshot("重跑前结果", deduplicate=True)
        versions = self.project.annotation_versions_for(self.filename)
        self.assertEqual(len(versions), 3)
        self.assertEqual(versions[0]["source"], "重跑前结果")
        self.assertEqual(core.load_voc_xml(versions[0]["path"])[0]["bbox"][0], 3)


if __name__ == "__main__":
    unittest.main()
