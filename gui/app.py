import threading
import tkinter as tk
from tkinter import messagebox, ttk

import config
import core

from gui.auto_tab import AutoTab
from gui.review_tab import ReviewTab
from gui.finetune_tab import FineTuneTab


DETECTOR_OPTIONS = [
    ("gd15", "Grounding DINO 1.5（推荐）"),
    ("gd_ogc", "Grounding DINO（旧版）"),
    ("yolo", "YOLO（微调）"),
]


class AutoLabelApp:
    def __init__(self, root):
        self.root = root
        self.root.title("自动化标注工具")
        self.root.geometry("1150x720")
        self.detector = None
        self.detector_backend = config.DETECTOR
        self.model_lock = threading.Lock()

        self._setup_style()
        self.notebook = ttk.Notebook(root, style="App.TNotebook")
        self.notebook.pack(fill=tk.BOTH, expand=True)
        self.auto_tab = AutoTab(self.notebook, self)
        self.review_tab = ReviewTab(self.notebook, self)
        self.finetune_tab = FineTuneTab(self.notebook, self)
        self.notebook.add(self.auto_tab, text="自动标注")
        self.notebook.add(self.review_tab, text="人工审核")
        self.notebook.add(self.finetune_tab, text="微调")

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _setup_style(self):
        style = ttk.Style(self.root)
        style.configure(
            "App.TNotebook.Tab",
            font=("Microsoft YaHei UI", 13, "bold"),
            padding=(40, 14),
        )
        style.map(
            "App.TNotebook.Tab",
            foreground=[("selected", "#1a73e8"), ("!selected", "#555555")],
        )

    def set_detector_backend(self, backend):
        if backend != self.detector_backend:
            self.detector_backend = backend
            self.detector = None

    def ensure_model(self):
        with self.model_lock:
            if self.detector is None:
                self.detector = core.load_models(self.detector_backend)
            return self.detector

    def _on_close(self):
        if self.auto_tab.worker_thread and self.auto_tab.worker_thread.is_alive():
            if messagebox.askyesno("退出", "自动标注仍在运行，确定退出？"):
                self.auto_tab.stop_flag.set()
                self.root.destroy()
        else:
            self.root.destroy()


def main():
    root = tk.Tk()
    AutoLabelApp(root)
    root.mainloop()
