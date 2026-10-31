"""Enabled model versions remain in sync with the auto-label selector."""
from __future__ import annotations

import os
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QAbstractButton, QLabel  # noqa: E402

import config  # noqa: E402
from application.model_library import DOWNLOADABLE_MODELS, InstalledModel, ModelLibrary  # noqa: E402
from gui.pages.auto_page import AutoPage  # noqa: E402
from gui.pages.model_manager_page import ModelManagerPage, ModelSwitch  # noqa: E402


class _MetadataProject:
    def __init__(self):
        self.values = {}

    def metadata_value(self, key, default=""):
        return self.values.get(key, default)

    def set_metadata_value(self, key, value):
        self.values[key] = value


class ModelManagerUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_switch_filters_the_same_model_choice_used_by_the_auto_page(self):
        project = _MetadataProject()
        window = SimpleNamespace(project=project, project_root="project")
        page = AutoPage(window)
        model = next(item for item in ModelLibrary().installed(project) if item.title == "yolo11n")
        choice = ("custom_yolo", model.path)
        self.assertGreaterEqual(page._model_index(choice), 0)
        library = ModelLibrary()
        library.set_enabled(project, model, False)
        page._refresh_model_choices()
        self.assertEqual(page._model_index(choice), -1)
        library.set_enabled(project, model, True)
        page._refresh_model_choices()
        self.assertGreaterEqual(page._model_index(choice), 0)
        page.close()

    def test_switch_uses_blue_left_for_enabled_and_gray_right_for_disabled(self):
        switch = ModelSwitch()
        switch.setChecked(True)
        enabled = switch.grab().toImage()
        self.assertEqual(enabled.pixelColor(13, 13).name(), "#0a74ff")
        switch.setChecked(False)
        disabled = switch.grab().toImage()
        self.assertEqual(disabled.pixelColor(34, 13).name(), "#aab6c6")
        self.assertNotEqual(enabled.pixelColor(34, 13).name(), "#0a74ff")
        switch.close()

    def test_official_bundled_model_has_an_enabled_delete_button(self):
        window = SimpleNamespace(project=_MetadataProject(), auto=SimpleNamespace(_worker_thread=None))
        page = ModelManagerPage(window)
        model = InstalledModel("YOLO11n", "custom_yolo", str(Path(config.WEIGHTS_DIR) / "yolo11n.pt"), "应用自带", "")
        row = page._installed_row(model, True)
        delete = next(item for item in row.findChildren(QAbstractButton) if item.text() == "删除")
        self.assertTrue(delete.isEnabled())
        row.close()
        page.close()

    def test_installed_offer_only_appears_for_download_after_deletion(self):
        window = SimpleNamespace(project=_MetadataProject(), auto=SimpleNamespace(_worker_thread=None))
        page = ModelManagerPage(window)
        offer = next(item for item in DOWNLOADABLE_MODELS if item.filename == "yolo11n.pt")

        def listed():
            return any(offer.title == child.text()
                       for index in range(page.download_rows.count())
                       for child in page.download_rows.itemAt(index).widget().findChildren(QLabel))

        with tempfile.TemporaryDirectory() as directory:
            weights = Path(directory) / "weights"
            weights.mkdir()
            (weights / offer.filename).write_bytes(b"checkpoint")
            page.library = ModelLibrary(Path(directory) / "library")
            with patch("application.model_library.config.WEIGHTS_DIR", str(weights)):
                page.refresh()
                self.assertFalse(listed())
                model = next(item for item in page.library.installed(window.project)
                             if item.path == str(weights / offer.filename))
                page.library.remove(model)
                page.refresh()
                self.assertTrue(listed())
        page.close()


if __name__ == "__main__":
    unittest.main()
