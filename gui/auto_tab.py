import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

import config
import core


DETECTOR_OPTIONS = {
    "gd15": "Grounding DINO 1.5（推荐）",
    "gd_ogc": "Grounding DINO（旧版）",
    "yolo": "YOLO（微调）",
}


class AutoTab(ttk.Frame):
    """自动标注页：批量预标注"""

    def __init__(self, parent, app):
        super().__init__(parent, padding=12)
        self.app = app
        self.stop_flag = threading.Event()
        self.worker_thread = None
        self.msg_queue = queue.Queue()
        self._build_ui()

    def _build_ui(self):
        self.columnconfigure(1, weight=1)

        ttk.Label(self, text="输入目录:").grid(row=0, column=0, sticky=tk.W, pady=4)
        self.input_var = tk.StringVar(value=config.INPUT_DIR)
        ttk.Entry(self, textvariable=self.input_var).grid(row=0, column=1, sticky=tk.EW, padx=4)
        ttk.Button(self, text="浏览...", command=self._browse_input).grid(row=0, column=2)

        ttk.Label(self, text="输出目录:").grid(row=1, column=0, sticky=tk.W, pady=4)
        self.output_var = tk.StringVar(value=config.OUTPUT_DIR)
        ttk.Entry(self, textvariable=self.output_var).grid(row=1, column=1, sticky=tk.EW, padx=4)
        ttk.Button(self, text="浏览...", command=self._browse_output).grid(row=1, column=2)

        ttk.Label(self, text="检测模型:").grid(row=2, column=0, sticky=tk.W, pady=4)
        self.detector_var = tk.StringVar(value=DETECTOR_OPTIONS.get(config.DETECTOR, DETECTOR_OPTIONS["gd15"]))
        self.detector_combo = ttk.Combobox(self, textvariable=self.detector_var,
                                           values=list(DETECTOR_OPTIONS.values()), state="readonly")
        self.detector_combo.grid(row=2, column=1, sticky=tk.EW, padx=4, columnspan=2)
        self.detector_combo.bind("<<ComboboxSelected>>", self._on_detector_change)

        ttk.Label(self, text="提示词:").grid(row=3, column=0, sticky=tk.W, pady=4)
        self.prompt_var = tk.StringVar(value=config.PROMPT)
        ttk.Entry(self, textvariable=self.prompt_var).grid(row=3, column=1, sticky=tk.EW, padx=4, columnspan=2)

        thr_frame = ttk.Frame(self)
        thr_frame.grid(row=4, column=0, columnspan=3, sticky=tk.W, pady=4)
        ttk.Label(thr_frame, text="框阈值:").pack(side=tk.LEFT)
        self.box_thr_var = tk.StringVar(value=str(config.BOX_THRESHOLD))
        ttk.Entry(thr_frame, textvariable=self.box_thr_var, width=8).pack(side=tk.LEFT, padx=(0, 16))
        ttk.Label(thr_frame, text="文本阈值:").pack(side=tk.LEFT)
        self.text_thr_var = tk.StringVar(value=str(config.TEXT_THRESHOLD))
        ttk.Entry(thr_frame, textvariable=self.text_thr_var, width=8).pack(side=tk.LEFT)

        btn_frame = ttk.Frame(self)
        btn_frame.grid(row=5, column=0, columnspan=3, sticky=tk.W, pady=6)
        self.start_btn = ttk.Button(btn_frame, text="开始标注", command=self._start)
        self.start_btn.pack(side=tk.LEFT, padx=(0, 8))
        self.stop_btn = ttk.Button(btn_frame, text="停止", command=self._stop, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT)

        self.progress = ttk.Progressbar(self, mode="determinate", maximum=100)
        self.progress.grid(row=6, column=0, columnspan=3, sticky=tk.EW, pady=6)

        self.log_text = ScrolledText(self, height=18, state=tk.DISABLED)
        self.log_text.grid(row=7, column=0, columnspan=3, sticky=tk.NSEW)
        self.rowconfigure(7, weight=1)

    def _on_detector_change(self, event=None):
        display = self.detector_var.get()
        backend = {v: k for k, v in DETECTOR_OPTIONS.items()}.get(display, "gd15")
        self.app.set_detector_backend(backend)

    def _browse_input(self):
        path = filedialog.askdirectory(title="选择输入图片目录")
        if path:
            self.input_var.set(path)

    def _browse_output(self):
        path = filedialog.askdirectory(title="选择输出标注目录")
        if path:
            self.output_var.set(path)

    def _log(self, text):
        self.msg_queue.put(("log", text))

    def _start(self):
        if self.worker_thread and self.worker_thread.is_alive():
            return

        input_dir = self.input_var.get().strip()
        output_dir = self.output_var.get().strip()
        prompt = self.prompt_var.get().strip()
        try:
            box_thr = float(self.box_thr_var.get())
            text_thr = float(self.text_thr_var.get())
        except ValueError:
            messagebox.showerror("参数错误", "阈值必须是数字")
            return

        if not os.path.isdir(input_dir):
            messagebox.showerror("路径错误", f"输入目录不存在: {input_dir}")
            return
        if not prompt:
            messagebox.showerror("参数错误", "提示词不能为空")
            return

        self.stop_flag.clear()
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.progress["value"] = 0
        self.worker_thread = threading.Thread(
            target=self._worker,
            args=(input_dir, output_dir, prompt, box_thr, text_thr),
            daemon=True,
        )
        self.worker_thread.start()
        self.after(100, self._poll_queue)

    def _stop(self):
        self.stop_flag.set()
        self._log("正在停止...")

    def _worker(self, input_dir, output_dir, prompt, box_thr, text_thr):
        try:
            self._log("加载模型，请稍候...")
            self.app.ensure_model()
            self._log("模型加载完成")

            img_files = core.list_images(input_dir)
            total = len(img_files)
            self._log(f"共发现 {total} 张图片")
            if total == 0:
                self._log("输入目录中没有支持的图片")

            os.makedirs(output_dir, exist_ok=True)
            for idx, img_file in enumerate(img_files):
                if self.stop_flag.is_set():
                    self._log("已停止")
                    break
                image_path = os.path.join(input_dir, img_file)
                try:
                    annotations = core.auto_label(image_path, prompt, self.app.detector, box_thr, text_thr)
                    core.save_as_voc_xml(annotations, image_path, output_dir)
                    self._log(f"[完成] {img_file}: {len(annotations)} 个目标")
                except Exception as exc:
                    self._log(f"[失败] {img_file}: {exc}")
                self.msg_queue.put(("progress", (idx + 1) / total * 100))
        except ModuleNotFoundError as exc:
            self.msg_queue.put(("error", f"缺少依赖: {exc.name}\n请先执行 pip install -r requirements.txt"))
        except Exception as exc:
            self.msg_queue.put(("error", f"处理出错: {exc}"))
        finally:
            self._log("处理结束")
            self.msg_queue.put(("done", None))

    def _poll_queue(self):
        try:
            while True:
                msg = self.msg_queue.get_nowait()
                kind = msg[0]
                if kind == "log":
                    self._append_log(msg[1])
                elif kind == "error":
                    self._append_log(msg[1])
                    messagebox.showerror("错误", msg[1])
                elif kind == "progress":
                    self.progress["value"] = msg[1]
                elif kind == "done":
                    self.start_btn.config(state=tk.NORMAL)
                    self.stop_btn.config(state=tk.DISABLED)
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    def _append_log(self, text):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, text + "\n")
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)
