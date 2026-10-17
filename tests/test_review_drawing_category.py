"""New manual shapes reuse the chosen project category without repeat dialogs."""
from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from gui.pages.review_page import ReviewPage  # noqa: E402


class _Project:
    categories = ["person", "car"]

    def category_records(self):
        return [
            {"name": "person", "display_name": "行人"},
            {"name": "car", "display_name": "汽车"},
        ]

    def category_display_names(self):
        return {"person": "行人", "car": "汽车"}


class ReviewDrawingCategoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = SimpleNamespace(project=_Project(), status_message=lambda _message: None)
        self.page = ReviewPage(self.window)
        self.page._rebuild_category_filters()

    def tearDown(self):
        self.page.clear_project_context()
        self.page.close()

    def _add_provisional_box(self):
        index = len(self.page.canvas.shapes)
        before = self.page.canvas._copy_shapes(self.page.canvas.shapes)
        self.page.canvas.shapes.append({"name": self.page.canvas.default_label, "origin": "manual", "bbox": [1, 2, 30, 40]})
        self.page.canvas.selected_index = index
        self.page.canvas._commit(before)
        return index

    def test_first_choice_is_reused_and_inspector_change_switches_future_boxes(self):
        with patch("gui.pages.review_page.ios_item_input", return_value=("行人", True)) as choose:
            self.page._new_box_created(self._add_provisional_box())
            self.page._new_box_created(self._add_provisional_box())

        self.assertEqual(choose.call_count, 1)
        self.assertEqual([shape["name"] for shape in self.page.canvas.shapes], ["person", "person"])
        self.assertIn("(2)", self.page._filter_checks["person"].text())
        self.assertIn("(0)", self.page._filter_checks["car"].text())
        self.assertIn("通过会加入训练候选", self.page.review_status_hint.text())
        self.assertEqual(len(self.page.canvas._history), 2)
        self.assertIn("行人", self.page.box_tool.toolTip())

        self.assertTrue(self.page.canvas.undo())
        self.assertEqual(len(self.page.canvas.shapes), 1)
        self.assertTrue(self.page.canvas.redo())
        self.assertEqual([shape["name"] for shape in self.page.canvas.shapes], ["person", "person"])
        self.page.canvas.selected_index = 1
        self.page._sync_selected_category_editor("person")

        self.page.category.setCurrentIndex(self.page.category.findData("car"))
        self.assertEqual(self.page._drawing_category, "car")
        self.assertEqual(self.page.canvas.shapes[1]["name"], "car")
        self.assertIn("(1)", self.page._filter_checks["person"].text())
        self.assertIn("(1)", self.page._filter_checks["car"].text())
        with patch("gui.pages.review_page.ios_item_input") as choose_again:
            self.page._new_box_created(self._add_provisional_box())
        choose_again.assert_not_called()
        self.assertEqual(self.page.canvas.shapes[2]["name"], "car")
        self.assertIn("(2)", self.page._filter_checks["car"].text())

    def test_project_switch_forgets_category_and_cancel_removes_shape(self):
        with patch("gui.pages.review_page.ios_item_input", return_value=("行人", True)):
            self.page._new_box_created(self._add_provisional_box())
        self.page.clear_project_context()
        self.assertIsNone(self.page._drawing_category)
        self.assertEqual(self.page.canvas.default_label, "object")

        with patch("gui.pages.review_page.ios_item_input", return_value=("", False)) as choose:
            self.page._new_box_created(self._add_provisional_box())
        choose.assert_called_once()
        self.assertEqual(self.page.canvas.shapes, [])
        self.assertIsNone(self.page._drawing_category)


if __name__ == "__main__":
    unittest.main()
