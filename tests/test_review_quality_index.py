"""Review filters use persisted current risk metadata and global progress."""
from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from gui.pages.review_page import ReviewPage  # noqa: E402
from project import (  # noqa: E402
    ProjectStore, STATUS_APPROVED, STATUS_NEEDS_REVIEW, STATUS_SKIPPED,
)


class ReviewQualityIndexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.root = Path(self.temp.name)
        self.images = self.root / "images"
        self.annotations = self.root / "annotations"
        self.images.mkdir()
        self.annotations.mkdir()
        self.project = ProjectStore(str(self.annotations), str(self.images), str(self.root))
        self.page = None

    def tearDown(self):
        if self.page is not None:
            worker = self.page._quality_scan_thread
            self.page.clear_project_context()
            if worker is not None:
                worker.join(timeout=5)
            self.page.close()
        self.temp.cleanup()

    def _images(self, count):
        image = QImage(40, 30, QImage.Format_RGB32)
        image.fill(0xFFCCDDEE)
        names = []
        for index in range(count):
            filename = f"sample-{index:03d}.jpg"
            self.assertTrue(image.save(str(self.images / filename), "JPEG"))
            names.append(filename)
        self.project.set_statuses(names, STATUS_NEEDS_REVIEW)
        return names

    def _page(self, names):
        def project_images(status=None):
            if status is None:
                return list(names)
            statuses = self.project.statuses_for(names)
            return [filename for filename in names if statuses[filename] == status]

        window = SimpleNamespace(
            project=self.project, project_root=str(self.root), images_dir=str(self.images),
            annotations_dir=str(self.annotations), project_images=project_images,
            status_message=lambda _message: None,
        )
        self.page = ReviewPage(window)
        self.page.resize(1420, 900)
        self.page.show()
        self.app.processEvents()
        return self.page

    def test_cached_filters_do_not_recalculate_and_reset_to_first_result(self):
        names = self._images(4)
        for filename, score, rules in zip(
            names, (0, 65, 30, 90),
            (set(), {"out_of_bounds"}, {"low_confidence"}, {"empty"}),
        ):
            self.project.upsert_quality(filename, score, rules)
        page = self._page(names)
        page.refresh()
        page.select(2)

        with patch("core.annotation_risk", side_effect=AssertionError("risk was recalculated")):
            page.issue_filter.setCurrentIndex(page.issue_filter.findData("risk"))
            self.assertEqual(page.files, [names[3], names[1], names[2]])
            self.assertEqual(page.index, 0)
            self.assertEqual(page.issue_filter.itemText(page.issue_filter.findData("risk")), "风险队列")
            page.issue_filter.setCurrentIndex(page.issue_filter.findData("low_confidence"))
            self.assertEqual(page.files, [names[2]])
        self.assertEqual(page.index, 0)

        reopened = ProjectStore(str(self.annotations), str(self.images), str(self.root))
        self.assertEqual(reopened.quality_records_for([names[3]])[names[3]][0], 90)

    def test_missing_index_is_built_in_worker_and_filter_remains_responsive(self):
        names = self._images(12)
        page = self._page(names)
        main_thread = threading.current_thread()
        calls = []

        def slow_risk(_shapes, _image_path):
            calls.append(threading.current_thread())
            time.sleep(0.02)
            return 30, {"low_confidence"}

        with patch("core.annotation_risk", side_effect=slow_risk):
            page.refresh()
            started = time.perf_counter()
            page.issue_filter.setCurrentIndex(page.issue_filter.findData("risk"))
            elapsed = time.perf_counter() - started
            self.assertLess(elapsed, 0.4)
            self.assertIn("正在建立质量索引", page.queue_summary.text())
            deadline = time.perf_counter() + 5
            while len(page.files) != len(names) and time.perf_counter() < deadline:
                self.app.processEvents()
                time.sleep(0.01)
            self.assertEqual(len(page.files), len(names))
            self.assertTrue(calls)
            self.assertTrue(all(thread is not main_thread for thread in calls))

    def test_filter_switch_revalidates_external_file_change(self):
        names = self._images(1)
        self.project.upsert_quality(names[0], 0, set())
        page = self._page(names)
        page.refresh()
        deadline = time.perf_counter() + 5
        while names[0] not in page._quality_known_files and time.perf_counter() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertIn(names[0], page._quality_known_files)

        image_path = self.images / names[0]
        stamp = image_path.stat()
        os.utime(image_path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 1_000_000_000))
        with patch("core.annotation_risk", return_value=(55, {"out_of_bounds"})) as recalculate:
            page.issue_filter.setCurrentIndex(page.issue_filter.findData("risk"))
            deadline = time.perf_counter() + 5
            while page.files != names and time.perf_counter() < deadline:
                self.app.processEvents()
                time.sleep(0.01)
        self.assertEqual(page.files, names)
        recalculate.assert_called()
        self.assertEqual(self.project.quality_records_for(names)[names[0]][0], 55)

    def test_progress_is_global_and_skip_is_not_completion(self):
        names = self._images(3)
        for filename in names:
            self.project.upsert_quality(filename, 30, {"low_confidence"})
        self.project.set_status(names[0], STATUS_APPROVED)
        self.project.set_status(names[1], STATUS_SKIPPED)
        page = self._page(names)
        page.refresh()
        self.assertEqual(page.progress.value(), 33)
        page.issue_filter.setCurrentIndex(page.issue_filter.findData("risk"))
        self.assertEqual(page.progress.value(), 33)
        page.issue_filter.setCurrentIndex(page.issue_filter.findData("skipped"))
        self.assertEqual(page.progress.value(), 33)

        # Project rescans preserve a temporary skip; a new model result reopens it.
        self.project.mark_batch_for_review(names)
        self.assertEqual(self.project.status_for(names[1]), STATUS_SKIPPED)
        self.project.mark_batch_for_review([names[1]], include_skipped=True)
        self.assertEqual(self.project.status_for(names[1]), STATUS_NEEDS_REVIEW)
        self.project.set_status(names[1], STATUS_APPROVED)
        page.refresh()
        self.assertEqual(page.progress.value(), 67)


if __name__ == "__main__":
    unittest.main()
