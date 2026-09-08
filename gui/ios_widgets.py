"""Small native Tk controls with an iOS-like, borderless visual language."""
from __future__ import annotations

import tkinter as tk


class IOSButton(tk.Canvas):
    """A rounded, stateful button without the platform theme's square border."""

    PALETTES = {
        "primary": ("#007AFF", "#147FE8", "#0066D6", "#FFFFFF"),
        "secondary": ("#F2F3F5", "#E9EDF2", "#DEE4EB", "#2C2C2E"),
        "quiet": ("#FFFFFF", "#F2F7FF", "#E8F2FF", "#007AFF"),
        "danger": ("#FF3B30", "#F04A40", "#D92E25", "#FFFFFF"),
    }

    def __init__(self, master, text, command=None, kind="secondary", height=38, width=None, **kwargs):
        self.text = text
        self.command = command
        self.kind = kind
        self._height = height
        self._state = kwargs.pop("state", tk.NORMAL)
        self._hover = False
        self._pressed = False
        kwargs.setdefault("highlightthickness", 0)
        kwargs.setdefault("bd", 0)
        kwargs.setdefault("cursor", "hand2")
        kwargs.setdefault("height", height)
        if width is not None:
            kwargs.setdefault("width", width)
        super().__init__(master, **kwargs)
        self.bind("<Configure>", lambda _event: self._draw())
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<ButtonRelease-1>", self._release)
        self.after_idle(self._draw)

    def _draw(self):
        self.delete("all")
        width, height = max(self.winfo_width(), 2), max(self.winfo_height(), 2)
        normal, hover, pressed, foreground = self.PALETTES[self.kind]
        fill = "#E5E5EA" if self._state == tk.DISABLED else (pressed if self._pressed else hover if self._hover else normal)
        foreground = "#AEAEB2" if self._state == tk.DISABLED else foreground
        radius = min(height // 2, 12)
        self.create_round_rect(0, 0, width, height, radius, fill=fill)
        self.create_text(width // 2, height // 2, text=self.text, fill=foreground,
                         font=("Microsoft YaHei UI", 10, "bold"))

    def create_round_rect(self, x1, y1, x2, y2, radius, **kwargs):
        points = (x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
                  x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
                  x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1)
        return self.create_polygon(points, smooth=True, **kwargs)

    def _enter(self, _event):
        if self._state != tk.DISABLED:
            self._hover = True
            self._draw()

    def _leave(self, _event):
        self._hover = self._pressed = False
        self._draw()

    def _press(self, _event):
        if self._state != tk.DISABLED:
            self._pressed = True
            self._draw()

    def _release(self, event):
        invoke = self._state != tk.DISABLED and self._pressed and 0 <= event.x <= self.winfo_width() and 0 <= event.y <= self.winfo_height()
        self._pressed = False
        self._draw()
        if invoke and self.command:
            self.command()

    def config(self, cnf=None, **kwargs):
        if "state" in kwargs:
            self._state = kwargs.pop("state")
            super().configure(cursor="arrow" if self._state == tk.DISABLED else "hand2")
            self._draw()
        if "command" in kwargs:
            self.command = kwargs.pop("command")
        if "text" in kwargs:
            self.text = kwargs.pop("text")
            self._draw()
        return super().config(cnf, **kwargs) if cnf or kwargs else None

    configure = config


class IOSPanel(tk.Canvas):
    """A real rounded surface for page regions; children live in ``content``."""

    def __init__(self, master, *, height=None, radius=16, fill="#ffffff", canvas_bg="#f5f6f8",
                 border="#e2e4e9", **kwargs):
        self.radius = radius
        self.fill = fill
        self.border = border
        kwargs.setdefault("highlightthickness", 0)
        kwargs.setdefault("bd", 0)
        kwargs.setdefault("bg", canvas_bg)
        if height is not None:
            kwargs.setdefault("height", height)
        super().__init__(master, **kwargs)
        self.content = tk.Frame(self, bg=fill, highlightthickness=0, bd=0)
        self._window = self.create_window(radius, radius, anchor=tk.NW, window=self.content)
        self.bind("<Configure>", self._layout)
        self.after_idle(self._layout)

    def create_round_rect(self, x1, y1, x2, y2, radius, **kwargs):
        points = (x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
                  x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
                  x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1)
        return self.create_polygon(points, smooth=True, **kwargs)

    def _layout(self, _event=None):
        width, height = max(self.winfo_width(), 2), max(self.winfo_height(), 2)
        self.delete("surface")
        inset = 0.5
        self.create_round_rect(inset, inset, width - inset, height - inset, min(self.radius, height // 2),
                               fill=self.fill, outline=self.border, width=1, tags="surface")
        self.tag_lower("surface")
        content_x = max(1, min(self.radius, width // 3))
        content_y = max(1, min(self.radius, height // 3))
        self.coords(self._window, content_x, content_y)
        self.itemconfigure(self._window, width=max(1, width - content_x * 2), height=max(1, height - content_y * 2))


class IOSNavigationItem(tk.Canvas):
    """A single self-contained workflow row with a soft selected capsule."""

    def __init__(self, master, *, text, image, command, canvas_bg="#ffffff"):
        self.text = text
        self.image = image
        self.command = command
        self.canvas_bg = canvas_bg
        self.count = ""
        self.active = False
        super().__init__(master, height=52, bg=canvas_bg, highlightthickness=0, bd=0, cursor="hand2")
        self.bind("<Configure>", self._draw)
        self.bind("<Button-1>", lambda _event: self.command())
        self.bind("<Enter>", lambda _event: self._draw(hover=True))
        self.bind("<Leave>", lambda _event: self._draw())
        self.after_idle(self._draw)

    def set_active(self, active):
        self.active = bool(active)
        self._draw()

    def set_count(self, count):
        self.count = str(count or "")
        self._draw()

    def _draw(self, _event=None, hover=False):
        self.delete("all")
        width, height = max(2, self.winfo_width()), max(2, self.winfo_height())
        selected = self.active
        fill = "#eaf3ff" if selected else ("#f6f7f9" if hover else self.canvas_bg)
        if selected or hover:
            self.create_round_rect(2, 3, width - 2, height - 3, 12, fill=fill)
        self.create_image(22, height // 2, image=self.image, anchor=tk.CENTER)
        text_color = "#007aff" if selected else "#3a3d45"
        self.create_text(42, height // 2, text=self.text, anchor=tk.W, fill=text_color,
                         font=("Microsoft YaHei UI", 11, "bold" if selected else "normal"))
        if self.count:
            badge_fill = "#147fe8" if selected else "#f1f2f5"
            badge_text = "#ffffff" if selected else "#6e7280"
            badge_w = max(28, 14 + len(self.count) * 8)
            x2 = width - 12
            self.create_round_rect(x2 - badge_w, height // 2 - 12, x2, height // 2 + 12, 12, fill=badge_fill)
            self.create_text(x2 - badge_w / 2, height // 2, text=self.count, fill=badge_text,
                             font=("Segoe UI", 9, "bold"))

    def create_round_rect(self, x1, y1, x2, y2, radius, **kwargs):
        points = (x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
                  x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
                  x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1)
        return self.create_polygon(points, smooth=True, outline="", **kwargs)
