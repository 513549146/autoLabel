"""Off-image prediction edges remain editable from the review canvas."""
from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtGui import QPixmap  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from gui.canvas import ImageCanvas  # noqa: E402


class OutOfBoundsCanvasTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_off_image_corner_can_be_dragged_back_into_image(self):
        canvas = ImageCanvas()
        canvas.resize(500, 400)
        canvas._pixmap = QPixmap(100, 100)
        canvas._pixmap.fill(Qt.white)
        original = [-30, -20, 60, 80]
        canvas.shapes = [{"name": "fruit", "bbox": list(original), "score": 0.6}]
        canvas.set_tool("select")
        canvas._set_selected(0)
        canvas.show()
        self.app.processEvents()

        image_rect, scale = canvas._geometry()
        handles = canvas._visible_handle_positions(canvas._selected_canvas_rect())
        corner = handles["nw"]
        self.assertGreaterEqual(corner.x(), image_rect.left())
        self.assertGreaterEqual(corner.y(), image_rect.top())
        self.assertEqual(canvas._handle_at(corner), "nw")

        start = QPoint(round(corner.x()), round(corner.y()))
        destination = QPoint(round(image_rect.left() + 12 * scale),
                             round(image_rect.top() + 9 * scale))
        QTest.mousePress(canvas, Qt.LeftButton, pos=start)
        QTest.mouseMove(canvas, destination)
        QTest.mouseRelease(canvas, Qt.LeftButton, pos=destination)
        self.assertEqual(canvas.shapes[0]["bbox"], [12, 9, 60, 80])
        self.assertEqual(canvas.shapes[0].get("origin"), "revised")
        self.assertNotIn("score", canvas.shapes[0])
        self.assertTrue(canvas.undo())
        self.assertEqual(canvas.shapes[0]["bbox"], original)
        self.assertTrue(canvas.redo())
        self.assertEqual(canvas.shapes[0]["bbox"], [12, 9, 60, 80])
        canvas.close()

    def test_all_handles_remain_reachable_when_box_spans_beyond_image(self):
        canvas = ImageCanvas()
        canvas.resize(500, 400)
        canvas._pixmap = QPixmap(100, 100)
        canvas._pixmap.fill(Qt.white)
        canvas.shapes = [{"name": "fruit", "bbox": [-30, -20, 130, 140]}]
        canvas.set_tool("select")
        canvas._set_selected(0)
        canvas.show()
        self.app.processEvents()
        image_rect, _ = canvas._geometry()
        handles = canvas._visible_handle_positions(canvas._selected_canvas_rect())
        self.assertEqual(len(handles), 8)
        for name, point in handles.items():
            self.assertTrue(image_rect.contains(point), name)
            self.assertEqual(canvas._handle_at(point), name)
        canvas.close()

    def test_focusing_a_mostly_outside_box_keeps_image_visible(self):
        canvas = ImageCanvas()
        canvas.resize(500, 400)
        canvas._pixmap = QPixmap(100, 100)
        canvas._pixmap.fill(Qt.white)
        canvas.shapes = [{"name": "fruit", "bbox": [-300, 10, 30, 70]}]
        canvas.show()
        self.app.processEvents()
        self.assertTrue(canvas.focus_shape(0))
        image_rect, _ = canvas._geometry()
        self.assertTrue(image_rect.intersects(canvas.rect()))
        self.assertTrue(canvas._visible_handle_positions(canvas._selected_canvas_rect()))
        canvas.close()


if __name__ == "__main__":
    unittest.main()
