"""Existing boxes stay selectable while drawing or panning."""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QPixmap  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from gui.canvas import ImageCanvas  # noqa: E402


class CanvasToolSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.canvas = ImageCanvas()
        self.canvas.resize(500, 400)
        self.canvas._pixmap = QPixmap(100, 100)
        self.canvas._pixmap.fill(Qt.white)
        self.canvas.shapes = [{"name": "part", "bbox": [20, 20, 60, 60], "score": 0.8}]
        self.canvas.show()
        self.app.processEvents()

    def tearDown(self):
        self.canvas.close()

    def point(self, x, y):
        mapped = self.canvas._to_canvas(QPointF(x, y))
        return QPoint(round(mapped.x()), round(mapped.y()))

    def drag(self, start, end, modifier=Qt.NoModifier):
        QTest.mousePress(self.canvas, Qt.LeftButton, modifier, start)
        QTest.mouseMove(self.canvas, end)
        QTest.mouseRelease(self.canvas, Qt.LeftButton, modifier, end)

    def assert_bbox_close(self, actual, expected):
        for value, target in zip(actual, expected):
            self.assertAlmostEqual(value, target, delta=1)

    def test_pan_tool_requires_a_click_before_editing_a_box(self):
        self.canvas.set_tool("pan")
        QTest.mouseMove(self.canvas, self.point(35, 35))
        self.assertEqual(self.canvas.cursor().shape(), Qt.OpenHandCursor)

        self.drag(self.point(35, 35), self.point(45, 45))
        self.assertEqual(self.canvas.selected_index, -1)
        self.assertEqual(self.canvas.shapes[0]["bbox"], [20, 20, 60, 60])
        self.assertEqual(self.canvas.shapes[0]["score"], 0.8)
        self.assertNotEqual(self.canvas.pan, QPointF())
        self.canvas.pan = QPointF()

        QTest.mouseClick(self.canvas, Qt.LeftButton, pos=self.point(35, 35))
        self.assertEqual(self.canvas.selected_index, 0)
        self.assertEqual(self.canvas.shapes[0]["score"], 0.8)
        self.assertEqual(self.canvas.cursor().shape(), Qt.ArrowCursor)

        self.drag(self.point(35, 35), self.point(45, 45))
        self.assert_bbox_close(self.canvas.shapes[0]["bbox"], [30, 30, 70, 70])
        self.assertEqual(self.canvas.pan, QPointF())
        self.assertTrue(self.canvas.undo())

        QTest.mouseMove(self.canvas, self.point(80, 80))
        self.assertEqual(self.canvas.cursor().shape(), Qt.OpenHandCursor)
        self.drag(self.point(80, 80), self.point(85, 85))
        self.assertEqual(self.canvas.shapes[0]["bbox"], [20, 20, 60, 60])
        self.assertNotEqual(self.canvas.pan, QPointF())

        QTest.mouseClick(self.canvas, Qt.LeftButton, pos=self.point(80, 80))
        self.assertEqual(self.canvas.selected_index, -1)
        QTest.mouseMove(self.canvas, self.point(35, 35))
        self.assertEqual(self.canvas.cursor().shape(), Qt.OpenHandCursor)

    def test_box_tool_draws_inside_unselected_box_without_panning(self):
        self.canvas.set_tool("box")
        QTest.mouseMove(self.canvas, self.point(35, 35))
        self.assertEqual(self.canvas.cursor().shape(), Qt.CrossCursor)
        self.drag(self.point(35, 35), self.point(45, 45))
        self.assertEqual(self.canvas.selected_index, -1)
        self.assertEqual(len(self.canvas.shapes), 2)
        self.assertEqual(self.canvas.shapes[0]["bbox"], [20, 20, 60, 60])
        self.assert_bbox_close(self.canvas.shapes[1]["bbox"], [35, 35, 45, 45])
        self.assertEqual(self.canvas.pan, QPointF())

    def test_pan_tool_resizes_selected_box_at_handle(self):
        self.canvas.set_tool("pan")
        QTest.mouseClick(self.canvas, Qt.LeftButton, pos=self.point(35, 35))
        handle = self.canvas._visible_handle_positions(self.canvas._selected_canvas_rect())["se"]
        start = QPoint(round(handle.x()), round(handle.y()))
        QTest.mouseMove(self.canvas, start)
        self.assertEqual(self.canvas.cursor().shape(), Qt.SizeFDiagCursor)
        self.drag(start, self.point(70, 75))
        self.assert_bbox_close(self.canvas.shapes[0]["bbox"], [20, 20, 70, 75])
        self.assertEqual(self.canvas.pan, QPointF())

    def test_box_tool_clicks_and_moves_selected_box(self):
        self.canvas.set_tool("box")
        QTest.mouseClick(self.canvas, Qt.LeftButton, pos=self.point(35, 35))
        self.assertEqual(self.canvas.selected_index, 0)
        self.assertEqual(self.canvas.shapes[0]["score"], 0.8)
        self.assertEqual(len(self.canvas.shapes), 1)
        QTest.mouseMove(self.canvas, self.point(35, 35))
        self.assertEqual(self.canvas.cursor().shape(), Qt.ArrowCursor)

        self.drag(self.point(35, 35), self.point(40, 40))
        self.assertEqual(len(self.canvas.shapes), 1)
        self.assert_bbox_close(self.canvas.shapes[0]["bbox"], [25, 25, 65, 65])
        self.assertNotIn("score", self.canvas.shapes[0])
        self.assertEqual(self.canvas.pan, QPointF())
        self.assertTrue(self.canvas.undo())
        self.assertEqual(len(self.canvas.shapes), 1)
        self.assertEqual(self.canvas.shapes[0]["bbox"], [20, 20, 60, 60])

    def test_box_tool_draws_on_empty_space_and_over_existing_box_without_ctrl(self):
        self.canvas.set_tool("box")
        self.drag(self.point(70, 15), self.point(85, 30))
        self.assertEqual(len(self.canvas.shapes), 2)
        self.assert_bbox_close(self.canvas.shapes[1]["bbox"], [70, 15, 85, 30])

        self.drag(self.point(30, 30), self.point(50, 50))
        self.assertEqual(len(self.canvas.shapes), 3)
        self.assert_bbox_close(self.canvas.shapes[2]["bbox"], [30, 30, 50, 50])

    def test_new_box_can_immediately_contain_another_box_near_its_handle(self):
        self.canvas.shapes = []
        self.canvas.set_tool("box")
        self.drag(self.point(20, 20), self.point(40, 40))
        self.assertEqual(self.canvas.selected_index, -1)
        self.assertEqual(self.canvas.cursor().shape(), Qt.CrossCursor)
        self.drag(self.point(21, 21), self.point(30, 30))
        self.assertEqual(len(self.canvas.shapes), 2)
        self.assertEqual(self.canvas.selected_index, -1)
        self.assert_bbox_close(self.canvas.shapes[0]["bbox"], [20, 20, 40, 40])
        self.assert_bbox_close(self.canvas.shapes[1]["bbox"], [21, 21, 30, 30])
        self.assertEqual(self.canvas.pan, QPointF())

        overlap = self.point(25, 25)
        QTest.mouseClick(self.canvas, Qt.LeftButton, pos=overlap)
        self.assertEqual(self.canvas.selected_index, 1)
        QTest.mouseClick(self.canvas, Qt.LeftButton, pos=overlap)
        self.assertEqual(self.canvas.selected_index, 0)
        self.assertEqual(self.canvas.cursor().shape(), Qt.ArrowCursor)
        self.drag(self.point(35, 35), self.point(40, 40))
        self.assert_bbox_close(self.canvas.shapes[0]["bbox"], [25, 25, 45, 45])
        self.assert_bbox_close(self.canvas.shapes[1]["bbox"], [21, 21, 30, 30])
        handle = self.canvas._visible_handle_positions(self.canvas._selected_canvas_rect())["se"]
        start = QPoint(round(handle.x()), round(handle.y()))
        QTest.mouseMove(self.canvas, start)
        self.assertEqual(self.canvas.cursor().shape(), Qt.SizeFDiagCursor)
        self.drag(start, self.point(50, 55))
        self.assert_bbox_close(self.canvas.shapes[0]["bbox"], [25, 25, 50, 55])
        self.assert_bbox_close(self.canvas.shapes[1]["bbox"], [21, 21, 30, 30])
        self.canvas.clear_selection()
        self.assertEqual(self.canvas.cursor().shape(), Qt.CrossCursor)
        self.drag(self.point(30, 30), self.point(36, 36))
        self.assertEqual(len(self.canvas.shapes), 3)

    def test_polygon_tool_selects_existing_box_before_starting_draft(self):
        self.canvas.set_tool("polygon")
        QTest.mouseClick(self.canvas, Qt.LeftButton, pos=self.point(35, 35))
        self.assertEqual(self.canvas.selected_index, 0)
        self.assertFalse(self.canvas._polygon_draft)

        QTest.mouseClick(self.canvas, Qt.LeftButton, Qt.ControlModifier, self.point(35, 35))
        self.assertEqual(len(self.canvas._polygon_draft), 1)

    def test_double_click_on_box_preserves_zoom_and_blank_resets(self):
        self.canvas.set_tool("box")
        self.canvas.zoom = 2.0
        QTest.mouseDClick(self.canvas, Qt.LeftButton, pos=self.point(35, 35))
        self.assertEqual(self.canvas.selected_index, 0)
        self.assertEqual(self.canvas.zoom, 2.0)

        QTest.mouseDClick(self.canvas, Qt.LeftButton, pos=self.point(70, 60))
        self.assertEqual(self.canvas.zoom, 1.0)


if __name__ == "__main__":
    unittest.main()
