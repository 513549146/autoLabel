"""审核图片的上一张、下一张及长按连续翻页。"""

from __future__ import annotations


class ReviewNavigationMixin:
    """由 ReviewPage 提供当前队列、页码和翻页计时器。"""

    def _connect_navigation_button(self, control, direction):
        control.pressed.connect(lambda value=direction: self._begin_navigation_hold(value))
        control.released.connect(self._end_navigation_hold)

    def _begin_navigation_hold(self, direction):
        if not self.files:
            return
        self._nav_hold_direction = direction
        self._nav_hold_timer.start(380)

    def _end_navigation_hold(self):
        direction = self._nav_hold_direction
        if not direction:
            return
        if self._nav_hold_timer.isActive():
            self._nav_hold_timer.stop()
            self.move(direction)
        self._nav_repeat_timer.stop()
        self._nav_hold_direction = 0

    def _start_hold_navigation(self):
        if not self._nav_hold_direction:
            return
        self.move(self._nav_hold_direction)
        self._nav_repeat_timer.start(150)

    def _repeat_navigation(self):
        direction = self._nav_hold_direction
        at_end = direction > 0 and self.index >= len(self.files) - 1
        at_start = direction < 0 and self.index <= 0
        if not direction or not self.files or at_end or at_start:
            self._nav_repeat_timer.stop()
            return
        self.move(direction)
