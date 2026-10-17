"""Regression checks for opening an unavailable project."""
from __future__ import annotations

import os
import gc
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QDialog

import core
from gui.canvas import ImageCanvas
from gui.workbench import Workbench
from project import ProjectStore, create_project_manifest


class ProjectOpenRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(self.temp.cleanup)
        self.environment = patch.dict(os.environ, {"LOCALAPPDATA": self.temp.name})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.window = Workbench()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.window = None
        self.app.processEvents()
        gc.collect()

    def project_dir(self, name):
        root = Path(self.temp.name) / name
        (root / "images").mkdir(parents=True)
        (root / "annotations").mkdir()
        create_project_manifest(str(root), name)
        return str(root)

    def test_manual_switch_failure_keeps_current_project(self):
        current = self.project_dir("current")
        blocked = self.project_dir("blocked")
        self.assertTrue(self.window.load_project(current, open_review=False))
        old_store = self.window.project
        with patch.object(self.window, "_project_recovery_dialog", return_value="home") as dialog:
            with patch("gui.workbench.ProjectStore", side_effect=sqlite3.OperationalError("attempt to write a readonly database")):
                self.assertFalse(self.window.load_project(blocked))
        self.assertIs(self.window.project, old_store)
        self.assertEqual(self.window.project_root, current)
        self.assertIn("写入权限", dialog.call_args.args[1])

    def test_failed_startup_restore_does_not_repeat_next_launch(self):
        blocked = self.project_dir("blocked")
        self.window.workspace.remember_project(blocked, "blocked")
        with patch.object(self.window, "_project_recovery_dialog", return_value="home"):
            with patch("gui.workbench.ProjectStore", side_effect=sqlite3.OperationalError("attempt to write a readonly database")):
                self.window.restore_last_project()
        self.assertEqual(self.window.workspace.last_project(), "")
        self.assertIn(blocked, [path for path, _ in self.window.workspace.recent_projects()])
        self.assertIsNone(self.window.project)

    def test_actual_sqlite_read_only_connection_shows_recovery(self):
        blocked = self.project_dir("blocked")
        ProjectStore(str(Path(blocked) / "annotations"), str(Path(blocked) / "images"), blocked)

        @contextmanager
        def read_only_connection(store):
            connection = sqlite3.connect(f"file:{Path(store.path).as_posix()}?mode=ro", uri=True)
            connection.row_factory = sqlite3.Row
            try:
                with connection:
                    yield connection
            finally:
                connection.close()

        with patch.object(self.window, "_project_recovery_dialog", return_value="home") as dialog:
            with patch.object(ProjectStore, "_connect", read_only_connection):
                self.assertFalse(self.window.load_project(blocked))
        self.assertIn("写入权限", dialog.call_args.args[1])
        self.assertIsNone(self.window.project)

    def test_read_only_view_uses_images_and_xml_without_database(self):
        blocked = self.project_dir("blocked")
        image_path = Path(blocked) / "images" / "example.png"
        image = QImage(32, 24, QImage.Format_RGB32)
        image.fill(0xFFFFFF)
        self.assertTrue(image.save(str(image_path)))
        core.save_as_voc_xml([{"name": "person", "bbox": [1, 2, 20, 18]}], str(image_path), str(Path(blocked) / "annotations"))

        def inspect_view(dialog):
            canvas = dialog.findChild(ImageCanvas)
            self.assertIsNotNone(canvas)
            self.assertEqual(canvas.tool, "pan")
            self.assertEqual(len(canvas.shapes), 1)
            self.assertEqual(canvas.shapes[0]["name"], "person")
            return QDialog.DialogCode.Accepted

        with patch("gui.workbench.ProjectStore", side_effect=AssertionError("read-only view opened the database")):
            with patch.object(QDialog, "exec", inspect_view):
                self.window._show_read_only_project(blocked)


if __name__ == "__main__":
    unittest.main()
