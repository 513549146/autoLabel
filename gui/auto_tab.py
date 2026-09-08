import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

import config
import core
from gui.ios_widgets import IOSButton, IOSPanel


DETECTOR_OPTIONS = {
    "gd15": "Grounding DINO 1.5（推荐）",
    "gd_ogc": "Grounding DINO（旧版）",
    "yolo": "YOLO（微调）",
}


class AutoTab(tk.Frame):
    """自动标注页：批量预标注"""

    def __init__(self, parent, app):
        super().__init__(parent, bg="#f5f6f8", padx=22, pady=22)
        self.app = app
        self.stop_flag = threading.Event()
        self.worker_thread = None
        self.msg_queue = queue.Queue()
        self._build_ui()

    def _build_ui(self):
        header = tk.Frame(self, bg="#f5f6f8")
        header.pack(fill=tk.X, pady=(0, 18))
        tk.Label(header, text="自动标注", bg="#f5f6f8", fg="#1c1c1e", font=("Microsoft YaHei UI", 20, "bold")).pack(anchor=tk.W)
        tk.Label(header, text="选择项目图片，生成初始标注后自动进入待审核队列。", bg="#f5f6f8", fg="#6e7280",
                 font=("Microsoft YaHei UI", 10)).pack(anchor=tk.W, pady=(4, 0))
        tk.Label(header, text="流程 1 / 4", bg="#eaf3ff", fg="#007aff", padx=10, pady=5,
                 font=("Microsoft YaHei UI", 9, "bold")).pack(side=tk.RIGHT, anchor=tk.N, pady=(4, 0))

        form_surface = IOSPanel(self, height=206, radius=16, canvas_bg="#f5f6f8")
        form_surface.pack(fill=tk.X)
        form = form_surface.content
        form.configure(padx=8, pady=8)
        form.columnconfigure(1, weight=1)
        tk.Label(form, text="项目数据", bg="#ffffff", fg="#1c1c1e", font=("Microsoft YaHei UI", 13, "bold")).grid(
            row=0, column=0, columnspan=3, sticky=tk.W, pady=(0, 2))
        tk.Label(form, text="图片与标注文件始终保存在当前项目内。", bg="#ffffff", fg="#8e8e93", font=("Microsoft YaHei UI", 9)).grid(
            row=1, column=0, columnspan=3, sticky=tk.W, pady=(0, 12))

        tk.Label(form, text="图片目录", bg="#ffffff", fg="#535763", font=("Microsoft YaHei UI", 10)).grid(row=2, column=0, sticky=tk.W, pady=7)
        self.input_var = tk.StringVar(value=config.INPUT_DIR)
        ttk.Entry(form, textvariable=self.input_var).grid(row=2, column=1, sticky=tk.NSEW, padx=(16, 8))
        IOSButton(form, text="浏览", command=self._browse_input, kind="secondary", width=76, height=38, bg="#ffffff").grid(row=2, column=2, sticky=tk.NS)

        tk.Label(form, text="标注目录", bg="#ffffff", fg="#535763", font=("Microsoft YaHei UI", 10)).grid(row=3, column=0, sticky=tk.W, pady=7)
        self.output_var = tk.StringVar(value=config.OUTPUT_DIR)
        ttk.Entry(form, textvariable=self.output_var).grid(row=3, column=1, sticky=tk.NSEW, padx=(16, 8))
        IOSButton(form, text="浏览", command=self._browse_output, kind="secondary", width=76, height=38, bg="#ffffff").grid(row=3, column=2, sticky=tk.NS)

        config_surface = IOSPanel(self, height=244, radius=16, canvas_bg="#f5f6f8")
        config_surface.pack(fill=tk.X, pady=(12, 0))
        config_card = config_surface.content
        config_card.configure(padx=8, pady=8)
        config_card.columnconfigure(1, weight=1)
        tk.Label(config_card, text="标注配置", bg="#ffffff", fg="#1c1c1e", font=("Microsoft YaHei UI", 13, "bold")).grid(
            row=0, column=0, columnspan=3, sticky=tk.W, pady=(0, 2))
        tk.Label(config_card, text="先生成候选框，审核阶段可继续修正。", bg="#ffffff", fg="#8e8e93", font=("Microsoft YaHei UI", 9)).grid(
            row=1, column=0, columnspan=3, sticky=tk.W, pady=(0, 12))
        tk.Label(config_card, text="检测模型", bg="#ffffff", fg="#535763", font=("Microsoft YaHei UI", 10)).grid(row=2, column=0, sticky=tk.W, pady=7)
        self.detector_var = tk.StringVar(value=DETECTOR_OPTIONS.get(config.DETECTOR, DETECTOR_OPTIONS["gd15"]))
        self.detector_combo = ttk.Combobox(config_card, textvariable=self.detector_var,
                                           values=list(DETECTOR_OPTIONS.values()), state="readonly")
        self.detector_combo.grid(row=2, column=1, sticky=tk.EW, padx=(16, 0), columnspan=2)
        self.detector_combo.bind("<<ComboboxSelected>>", self._on_detector_change)

        tk.Label(config_card, text="提示词", bg="#ffffff", fg="#535763", font=("Microsoft YaHei UI", 10)).grid(row=3, column=0, sticky=tk.W, pady=7)
        self.prompt_var = tk.StringVar(value=config.PROMPT)
        ttk.Entry(config_card, textvariable=self.prompt_var).grid(row=3, column=1, sticky=tk.NSEW, padx=(16, 8))
        IOSButton(config_card, text="项目类别", command=self._manage_categories, kind="secondary", width=88, height=38, bg="#ffffff").grid(row=3, column=2, sticky=tk.NS)

        thr_frame = tk.Frame(config_card, bg="#ffffff")
        thr_frame.grid(row=4, column=1, columnspan=2, sticky=tk.W, padx=(16, 0), pady=(10, 0))
        tk.Label(thr_frame, text="框阈值", bg="#ffffff", fg="#535763").pack(side=tk.LEFT)
        self.box_thr_var = tk.StringVar(value=str(config.BOX_THRESHOLD))
        ttk.Entry(thr_frame, textvariable=self.box_thr_var, width=8).pack(side=tk.LEFT, padx=(0, 16))
        tk.Label(thr_frame, text="文本阈值", bg="#ffffff", fg="#535763").pack(side=tk.LEFT)
        self.text_thr_var = tk.StringVar(value=str(config.TEXT_THRESHOLD))
        ttk.Entry(thr_frame, textvariable=self.text_thr_var, width=8).pack(side=tk.LEFT)

        action_card = tk.Frame(self, bg="#f5f6f8")
        action_card.pack(fill=tk.X, pady=(16, 10))
        self.start_btn = IOSButton(action_card, text="开始自动标注", command=self._start, kind="primary", width=140, height=44, bg="#f5f6f8")
        self.start_btn.pack(side=tk.LEFT, padx=(0, 8))
        self.stop_btn = IOSButton(action_card, text="停止", command=self._stop, kind="secondary", width=76, height=44, bg="#f5f6f8", state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT)
        tk.Label(action_card, text="完成后将自动进入“待审核”队列", bg="#f5f6f8", fg="#8e8e93",
                 font=("Microsoft YaHei UI", 9)).pack(side=tk.LEFT, padx=12)

        self.progress = ttk.Progressbar(self, mode="determinate", maximum=100)
        self.progress.pack(fill=tk.X, pady=(0, 10))

        log_surface = IOSPanel(self, radius=16, canvas_bg="#f5f6f8")
        log_surface.pack(fill=tk.BOTH, expand=True)
        log_card = log_surface.content
        log_card.configure(padx=4, pady=4)
        tk.Label(log_card, text="运行记录", bg="#ffffff", fg="#1c1c1e", font=("Microsoft YaHei UI", 12, "bold")).pack(anchor=tk.W, pady=(0, 8))
        self.log_text = ScrolledText(
            log_card, height=12, state=tk.DISABLED, bg="#fbfbfc", fg="#3d424d",
            insertbackground="#1c1c1e", relief=tk.FLAT, highlightthickness=1,
            highlightbackground="#e1e3e8", font=("Consolas", 10),
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)

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

    def _manage_categories(self):
        self.app.configure_project(self.input_var.get().strip(), self.output_var.get().strip())

        def on_saved(categories):
            if categories:
                self.prompt_var.set(" . ".join(categories))
            self._log(f"项目类别已更新：{', '.join(categories) if categories else '无'}")

        self.app.open_category_manager(on_saved)

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

        project = self.app.configure_project(input_dir, output_dir)
        prompt_categories = core.parse_categories(prompt)
        if not project.categories and prompt_categories:
            project.set_categories(prompt_categories)

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
        processed_files = []
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
                    processed_files.append(img_file)
                    self._log(f"[完成] {img_file}: {len(annotations)} 个目标")
                except Exception as exc:
                    self._log(f"[失败] {img_file}: {exc}")
                self.msg_queue.put(("progress", (idx + 1) / total * 100))
        except ModuleNotFoundError as exc:
            self.msg_queue.put(("error", f"缺少依赖: {exc.name}\n请先执行 pip install -r requirements.txt"))
        except Exception as exc:
            self.msg_queue.put(("error", f"处理出错: {exc}"))
        finally:
            if processed_files:
                self.app.project.mark_batch_for_review(processed_files)
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
                    # The next stage is ready immediately after a batch: review reads
                    # the same project folders and starts in the pending-review queue.
                    self.app.review_tab.open_project(self.input_var.get().strip(), self.output_var.get().strip())
                    self.app.refresh_workspace()
                    self.app.show_page("review")
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    def _append_log(self, text):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, text + "\n")
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)
