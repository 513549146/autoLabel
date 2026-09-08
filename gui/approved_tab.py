"""Read-only library for images whose annotations have passed review."""
from __future__ import annotations

import os
import tkinter as tk

from PIL import Image, ImageTk

import core
from gui.ios_widgets import IOSButton, IOSPanel
from project import STATUS_APPROVED


class ApprovedTab(tk.Frame):
    """A visual result library, deliberately separate from the editable review canvas."""

    def __init__(self, parent, app):
        super().__init__(parent, bg="#f5f6f8", padx=18, pady=18)
        self.app = app
        self.images_dir = None
        self.thumbnail_refs = []
        self.count_var = tk.StringVar(value="0 张已通过图片")
        self._build_ui()

    def _build_ui(self):
        heading = tk.Frame(self, bg="#f5f6f8")
        heading.pack(fill=tk.X, pady=(0, 14))
        tk.Label(heading, text="已通过", bg="#f5f6f8", fg="#1c1c1e",
                 font=("Microsoft YaHei UI", 20, "bold")).pack(side=tk.LEFT)
        tk.Label(heading, textvariable=self.count_var, bg="#f5f6f8", fg="#8e8e93",
                 font=("Microsoft YaHei UI", 10)).pack(side=tk.LEFT, padx=12, pady=(7, 0))
        IOSButton(heading, text="进入训练集导出", command=lambda: self.app.show_page("finetune"),
                  kind="primary", width=132, height=40, bg="#f5f6f8").pack(side=tk.RIGHT)
        tk.Label(self, text="这里仅保留已确认的标注结果；需要修改时，请回到“待审核”队列。",
                 bg="#f5f6f8", fg="#6e7280", font=("Microsoft YaHei UI", 10)).pack(anchor=tk.W, pady=(0, 14))

        library_surface = IOSPanel(self, radius=16, canvas_bg="#f5f6f8")
        library_surface.pack(fill=tk.BOTH, expand=True)
        self.library = library_surface.content
        self.empty = tk.Label(self.library, text="暂无已通过图片\n审核完成后，结果会自动出现在这里。",
                              bg="#ffffff", fg="#8e8e93", justify=tk.CENTER,
                              font=("Microsoft YaHei UI", 11), pady=50)

    def open_project(self, images_dir):
        self.images_dir = images_dir
        self.refresh()

    def refresh(self):
        for child in self.library.winfo_children():
            child.destroy()
        self.thumbnail_refs = []
        if not self.images_dir:
            self.count_var.set("0 张已通过图片")
            self._empty()
            return
        filenames = [name for name in core.list_images(self.images_dir)
                     if self.app.project.status_for(name) == STATUS_APPROVED]
        self.count_var.set(f"{len(filenames)} 张已通过图片")
        if not filenames:
            self._empty()
            return
        grid = tk.Frame(self.library, bg="#ffffff", padx=18, pady=18)
        grid.pack(anchor=tk.NW, fill=tk.BOTH, expand=True)
        for index, filename in enumerate(filenames):
            card = tk.Frame(grid, bg="#ffffff", highlightthickness=1, highlightbackground="#e2e4e9", padx=8, pady=8)
            card.grid(row=index // 4, column=index % 4, padx=8, pady=8, sticky=tk.NW)
            try:
                image = Image.open(os.path.join(self.images_dir, filename)).convert("RGB")
                image.thumbnail((172, 112), Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(image)
                self.thumbnail_refs.append(photo)
                tk.Label(card, image=photo, bg="#ffffff").pack()
            except Exception:
                tk.Label(card, text="图片不可用", width=22, height=7, bg="#f2f3f5", fg="#8e8e93").pack()
            tk.Label(card, text=filename, bg="#ffffff", fg="#3a3a3c", anchor=tk.W,
                     font=("Microsoft YaHei UI", 9), width=22).pack(fill=tk.X, pady=(7, 2))
            tk.Label(card, text="已通过", bg="#EAF3FF", fg="#007AFF", anchor=tk.W,
                     font=("Microsoft YaHei UI", 9, "bold"), padx=8, pady=3).pack(anchor=tk.W)

    def _empty(self):
        self.empty = tk.Label(self.library, text="暂无已通过图片\n审核完成后，结果会自动出现在这里。",
                              bg="#ffffff", fg="#8e8e93", justify=tk.CENTER,
                              font=("Microsoft YaHei UI", 11), pady=50)
        self.empty.pack(expand=True)
