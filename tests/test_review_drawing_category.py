"""New manual shapes reuse the chosen project category without repeat dialogs."""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QPixmap  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
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
        self.page.canvas.shapes.append(
            {"name": self.page.canvas.default_label, "origin": "manual", "bbox": [1, 2, 30, 40]}
        )
        self.page.canvas.selected_index = index
        self.page.canvas._commit(before)
        return index

    def test_category_dialog_returns_to_drawing_and_selected_box_can_move(self):
        self.page.show()
        canvas = self.page.canvas
        canvas.resize(500, 400)
        canvas._pixmap = QPixmap(100, 100)
        canvas._pixmap.fill(Qt.white)
        canvas.show()
        self.app.processEvents()
        self.page.box_tool.setChecked(True)

        def point(x, y):
            mapped = canvas._to_canvas(QPointF(x, y))
            return QPoint(round(mapped.x()), round(mapped.y()))

        def drag(start, end):
            QTest.mousePress(canvas, Qt.LeftButton, pos=start)
            QTest.mouseMove(canvas, end)
            QTest.mouseRelease(canvas, Qt.LeftButton, pos=end)

        with patch("gui.pages.review_page.ios_item_input", return_value=("行人", True)) as choose:
            drag(point(20, 20), point(60, 60))
            self.assertEqual(canvas.selected_index, -1)
            drag(point(25, 25), point(40, 40))
        choose.assert_called_once()
        self.assertEqual(canvas.tool, "box")
        self.assertEqual([shape["name"] for shape in canvas.shapes], ["person", "person"])
        for actual, expected in zip(canvas.shapes[0]["bbox"], [20, 20, 60, 60]):
            self.assertAlmostEqual(actual, expected, delta=1)
        for actual, expected in zip(canvas.shapes[1]["bbox"], [25, 25, 40, 40]):
            self.assertAlmostEqual(actual, expected, delta=1)

        QTest.mouseClick(canvas, Qt.LeftButton, pos=point(50, 50))
        self.assertEqual(canvas.selected_index, 0)
        original_outer = list(canvas.shapes[0]["bbox"])
        original_inner = list(canvas.shapes[1]["bbox"])
        drag(point(50, 50), point(55, 55))
        moved_outer = canvas.shapes[0]["bbox"]
        self.assertGreater(moved_outer[0] - original_outer[0], 3)
        self.assertAlmostEqual(moved_outer[2] - moved_outer[0], original_outer[2] - original_outer[0], delta=1)
        self.assertEqual(canvas.shapes[1]["bbox"], original_inner)
        QTest.keyClick(canvas, Qt.Key_Escape)
        self.assertEqual(canvas.selected_index, -1)

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

    def test_hidden_category_is_not_offered_or_reused_for_new_boxes(self):
        with patch("gui.pages.review_page.ios_item_input", return_value=("行人", True)):
            self.page._new_box_created(self._add_provisional_box())

        self.page._filter_checks["person"].setChecked(False)
        self.assertIsNone(self.page._drawing_category)
        self.assertEqual(self.page.canvas.default_label, "object")
        self.assertFalse(self.page.canvas._is_visible(0))

        with patch("gui.pages.review_page.ios_item_input", return_value=("汽车", True)) as choose:
            self.page._new_box_created(self._add_provisional_box())
        self.assertEqual(choose.call_args.args[3], ["汽车"])
        self.assertEqual([shape["name"] for shape in self.page.canvas.shapes], ["person", "car"])
        self.assertFalse(self.page._filter_checks["person"].isChecked())
        self.assertTrue(self.page.canvas._is_visible(1))
        self.assertFalse(self.page.canvas._is_visible(0))

        with patch("gui.pages.review_page.ios_item_input") as choose_again:
            self.page._new_box_created(self._add_provisional_box())
        choose_again.assert_not_called()
        self.assertEqual(self.page.canvas.shapes[2]["name"], "car")

    def test_no_visible_category_cancels_new_box(self):
        self.page._filter_checks["person"].setChecked(False)
        self.page._filter_checks["car"].setChecked(False)
        with patch("gui.pages.review_page.ios_item_input") as choose:
            self.page._new_box_created(self._add_provisional_box())
        choose.assert_not_called()
        self.assertEqual(self.page.canvas.shapes, [])
        self.assertFalse(self.page._filter_checks["person"].isChecked())
        self.assertFalse(self.page._filter_checks["car"].isChecked())

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
