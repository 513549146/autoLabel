"""Smoke checks for visible workflow cards and canvas navigation."""
from __future__ import annotations

import os
import unittest
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from gui.workflow_composer import WorkflowComposer  # noqa: E402
from gui.pages.workflow_page import WorkflowPage  # noqa: E402
from gui.pages.auto_page import AutoPage  # noqa: E402
from application.workflow_templates import WorkflowComposerSettings  # noqa: E402


class _MetadataProject:
    def __init__(self):
        self.values = {}

    def metadata_value(self, key, default=""):
        return self.values.get(key, default)

    def set_metadata_value(self, key, value):
        self.values[key] = value


class WorkflowCanvasTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_cards_survive_preset_switch_and_optional_toggle(self):
        composer = WorkflowComposer()
        composer.resize(1000, 700)
        composer.show()
        self.app.processEvents()
        self.assertEqual(len(composer.board.nodes), 8)
        self.assertTrue(all(node.isVisible() for node in composer.board.nodes.values()))
        composer.select_template("own_model")
        self.app.processEvents()
        self.assertEqual(len(composer.board.nodes), 8)
        self.assertTrue(all(node.isVisible() for node in composer.board.nodes.values()))
        composer.select_template("quick")
        composer.set_optional_enabled("recovery_model", False)
        self.app.processEvents()
        self.assertEqual(len(composer.board.nodes), 7)
        self.assertTrue(all(node.isVisible() for node in composer.board.nodes.values()))
        composer.select_template("custom")
        self.app.processEvents()
        self.assertEqual(len(composer.board.nodes), 9)
        composer.set_optional_enabled("bounds_check", False)
        self.app.processEvents()
        self.assertEqual(len(composer.board.nodes), 8)
        self.assertTrue(all(node.isVisible() for node in composer.board.nodes.values()))
        composer.close()

    def test_zoom_and_fit(self):
        composer = WorkflowComposer()
        composer.resize(1000, 700)
        composer.show()
        self.app.processEvents()
        board = composer.board
        board.fit_graph()
        initial = board.transform().m11()
        board.zoom_in()
        self.assertGreater(board.transform().m11(), initial)
        board.zoom_out()
        self.assertAlmostEqual(board.transform().m11(), initial, places=4)
        board.fit_graph()
        self.assertEqual(len(board.nodes), 8)
        composer.close()

    def test_custom_apply_is_reflected_on_main_page_and_preset_optional_can_hide(self):
        project = _MetadataProject()
        window = SimpleNamespace(project=project, project_root="project",
                                 auto=SimpleNamespace(_worker_thread=None),
                                 status_message=lambda message: None)
        page = WorkflowPage(window)
        page.show()
        page.sync_project()
        page.composer.select_template("custom")
        page.optional_buttons["recovery_model"].setChecked(False)
        page.apply.click()
        state = WorkflowComposerSettings.load(project)
        self.assertEqual(state.applied_template_id, "custom")
        self.assertNotIn("recovery_model", state.applied_graph.nodes)
        self.assertIn("bounds_check", state.applied_graph.nodes)
        main = AutoPage(window)
        main.set_active_workflow("custom")
        self.assertIn("自定义流程", main.project_summary.text())
        self.assertIn("边界整理", main.workflow_status.text())
        self.assertNotIn("召回补扫", main.workflow_status.text())
        page.composer.select_template("own_model")
        self.app.processEvents()
        self.assertTrue(page.optional_buttons["supplement_model"].isVisible())
        self.assertFalse(page.optional_buttons["recovery_model"].isVisible())
        page.optional_buttons["supplement_model"].setChecked(False)
        self.assertNotIn("supplement_model", page.composer.board.graph.nodes)
        main.close()
        page.close()


if __name__ == "__main__":
    unittest.main()
