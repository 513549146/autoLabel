"""Approved-library rendering stays bounded when a project has many images."""
from __future__ import annotations

import os
import unittest
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from gui.pages.library_page import LibraryPage  # noqa: E402


class _Project:
    categories = []

    def spot_checks_for(self, filenames):
        return {}

    def metadata_value(self, key, default=""):
        return default


class LibraryPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_large_library_builds_only_one_page_and_reuses_it(self):
        files = [f"photo_{index:04}.jpg" for index in range(1000)]
        window = SimpleNamespace(project=_Project(), images_dir="missing-images",
                                 annotations_dir="missing-annotations",
                                 project_images=lambda status: files)
        page = LibraryPage(window)
        page._queue_readiness = lambda: None
        page._load_thumbnails = lambda token, pending: None
        page.refresh()
        self.assertEqual(page._shown, page.PAGE_SIZE)
        self.assertEqual(page.grid.count(), page.PAGE_SIZE + 1)
        first_card = page.grid.itemAt(0).widget()
        page.refresh()
        self.assertIs(page.grid.itemAt(0).widget(), first_card)
        page.toggle_selected(files[0], True)
        self.assertIs(page.grid.itemAt(0).widget(), first_card)
        page._append_page()
        self.assertEqual(page._shown, page.PAGE_SIZE * 2)
        page.search.setText("photo_0099")
        self.assertEqual(page._shown, 1)
        page.close()

    def test_stale_thumbnail_result_is_ignored(self):
        files = ["sample.jpg"]
        window = SimpleNamespace(project=_Project(), images_dir="missing-images",
                                 annotations_dir="missing-annotations",
                                 project_images=lambda status: files)
        page = LibraryPage(window)
        page._queue_readiness = lambda: None
        page._load_thumbnails = lambda token, pending: None
        page.refresh()
        old_token = page._thumbnail_token
        entry = page._thumbnail_labels[files[0]]
        image = QImage(20, 20, QImage.Format_RGB32)
        page.search.setText("absent")
        page._apply_thumbnail(old_token, files[0], entry[1], image)
        self.assertFalse(page._thumbnail_cache)
        page.close()


if __name__ == "__main__":
    unittest.main()
