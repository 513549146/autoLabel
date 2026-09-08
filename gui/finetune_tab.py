# -*- coding: utf-8 -*-
import os
import queue
import shutil
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

import config
import finetune
from gui.ios_widgets import IOSButton, IOSPanel


MODEL_CHOICES = [
    "yolov8n.pt", "yolov8s.pt", "yolov8m.pt", "yolov8l.pt", "yolov8x.pt",
    "yolov11n.pt", "yolov11s.pt", "yolov11m.pt", "yolov11l.pt", "yolov11x.pt",
]


class FineTuneTab(ttk.Frame):
    """微调页：可视化导出 VOC -> YOLO 训练集，并微调训练"""

    def __init__(self, parent, app):
        super().__init__(parent, padding=22)
        self.app = app
        self.worker_thread = None
        self.stop_flag = threading.Event()
        self.msg_queue = queue.Queue()
        self._build_ui()
        self.after(100, self._poll_queue)

    def _build_ui(self):
        header = tk.Frame(self, bg="#f5f6f8")
        header.pack(fill=tk.X, pady=(0, 18))
        tk.Label(header, text="训练集导出", bg="#f5f6f8", fg="#1c1c1e", font=("Microsoft YaHei UI", 20, "bold")).pack(anchor=tk.W)
        tk.Label(header, text="仅导出已通过审核的结果，然后可直接启动 YOLO 微调训练。", bg="#f5f6f8", fg="#6e7280", font=("Microsoft YaHei UI", 10)).pack(anchor=tk.W, pady=(4, 0))
        tk.Label(header, text="流程 4 / 4", bg="#eaf3ff", fg="#007aff", padx=10, pady=5, font=("Microsoft YaHei UI", 9, "bold")).pack(side=tk.RIGHT, anchor=tk.N, pady=(4, 0))

        stages = tk.Frame(self, bg="#f5f6f8", height=386)
        stages.pack(fill=tk.X, pady=(0, 12))
        stages.pack_propagate(False)

        # ---- 导出 ----
        exp_surface = IOSPanel(stages, width=520, height=386, radius=16, canvas_bg="#f5f6f8")
        exp_surface.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 6))
        exp = exp_surface.content
        exp.configure(padx=8, pady=8)
        exp.columnconfigure(1, weight=1)
        tk.Label(exp, text="1 · 导出数据集", bg="#ffffff", fg="#1c1c1e", font=("Microsoft YaHei UI", 13, "bold")).grid(row=0, column=0, columnspan=3, sticky=tk.W)
        tk.Label(exp, text="将审核通过的 VOC 标注整理为 YOLO 训练集。", bg="#ffffff", fg="#8e8e93", font=("Microsoft YaHei UI", 9)).grid(row=1, column=0, columnspan=3, sticky=tk.W, pady=(2, 12))

        tk.Label(exp, text="图片目录", bg="#ffffff", fg="#535763").grid(row=2, column=0, sticky=tk.W, pady=7)
        self.exp_images_var = tk.StringVar(value=config.INPUT_DIR)
        ttk.Entry(exp, textvariable=self.exp_images_var).grid(row=2, column=1, sticky=tk.NSEW, padx=(16, 8))
        IOSButton(exp, text="浏览", command=lambda: self._browse_dir(self.exp_images_var), width=76, height=38, bg="#ffffff").grid(row=2, column=2, sticky=tk.NS)

        tk.Label(exp, text="标注目录", bg="#ffffff", fg="#535763").grid(row=3, column=0, sticky=tk.W, pady=7)
        self.exp_ann_var = tk.StringVar(value=config.OUTPUT_DIR)
        ttk.Entry(exp, textvariable=self.exp_ann_var).grid(row=3, column=1, sticky=tk.NSEW, padx=(16, 8))
        IOSButton(exp, text="浏览", command=lambda: self._browse_dir(self.exp_ann_var), width=76, height=38, bg="#ffffff").grid(row=3, column=2, sticky=tk.NS)

        tk.Label(exp, text="输出目录", bg="#ffffff", fg="#535763").grid(row=4, column=0, sticky=tk.W, pady=7)
        self.exp_out_var = tk.StringVar(value=os.path.join(config.BASE_DIR, "yolo_dataset"))
        ttk.Entry(exp, textvariable=self.exp_out_var).grid(row=4, column=1, sticky=tk.NSEW, padx=(16, 8))
        IOSButton(exp, text="浏览", command=lambda: self._browse_dir(self.exp_out_var), width=76, height=38, bg="#ffffff").grid(row=4, column=2, sticky=tk.NS)

        tk.Label(exp, text="验证集比例", bg="#ffffff", fg="#535763").grid(row=5, column=0, sticky=tk.W, pady=(10, 0))
        self.exp_val_var = tk.StringVar(value="0.2")
        ttk.Entry(exp, textvariable=self.exp_val_var, width=8).grid(row=5, column=1, sticky=tk.W, padx=(16, 0), pady=(10, 0))
        IOSButton(exp, text="导出数据集", command=self._start_export, kind="primary", width=108, height=40, bg="#ffffff").grid(row=5, column=2, sticky=tk.NS, pady=(8, 0))

        # ---- 训练 ----
        tr_surface = IOSPanel(stages, width=520, height=386, radius=16, canvas_bg="#f5f6f8")
        tr_surface.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(6, 0))
        tr = tr_surface.content
        tr.configure(padx=8, pady=8)
        tr.columnconfigure(1, weight=1)
        tk.Label(tr, text="2 · 微调训练", bg="#ffffff", fg="#1c1c1e", font=("Microsoft YaHei UI", 13, "bold")).grid(row=0, column=0, columnspan=3, sticky=tk.W)
        tk.Label(tr, text="选择模型与训练参数；训练完成后可回到自动标注页切换 YOLO 模型。", bg="#ffffff", fg="#8e8e93", font=("Microsoft YaHei UI", 9)).grid(row=1, column=0, columnspan=3, sticky=tk.W, pady=(2, 12))

        tk.Label(tr, text="数据配置", bg="#ffffff", fg="#535763").grid(row=2, column=0, sticky=tk.W, pady=7)
        self.train_data_var = tk.StringVar(value=os.path.join(config.BASE_DIR, "yolo_dataset", "dataset.yaml"))
        ttk.Entry(tr, textvariable=self.train_data_var).grid(row=2, column=1, sticky=tk.NSEW, padx=(16, 8))
        IOSButton(tr, text="浏览", command=self._browse_data, width=76, height=38, bg="#ffffff").grid(row=2, column=2, sticky=tk.NS)

        tk.Label(tr, text="预训练模型", bg="#ffffff", fg="#535763").grid(row=3, column=0, sticky=tk.W, pady=7)
        self.train_model_var = tk.StringVar(value="yolov8s.pt")
        ttk.Combobox(tr, textvariable=self.train_model_var, values=MODEL_CHOICES, state="readonly", width=12).grid(
            row=3, column=1, sticky=tk.W, padx=(16, 0))

        tk.Label(tr, text="轮数", bg="#ffffff", fg="#535763").grid(row=4, column=0, sticky=tk.W, pady=7)
        self.train_epochs_var = tk.StringVar(value="100")
        ttk.Entry(tr, textvariable=self.train_epochs_var, width=8).grid(row=4, column=1, sticky=tk.W, padx=(16, 0))

        tk.Label(tr, text="图像尺寸", bg="#ffffff", fg="#535763").grid(row=5, column=0, sticky=tk.W, pady=7)
        self.train_imgsz_var = tk.StringVar(value="640")
        ttk.Entry(tr, textvariable=self.train_imgsz_var, width=8).grid(row=5, column=1, sticky=tk.W, padx=(16, 0))

        tk.Label(tr, text="批量", bg="#ffffff", fg="#535763").grid(row=6, column=0, sticky=tk.W, pady=7)
        self.train_batch_var = tk.StringVar(value="16")
        ttk.Entry(tr, textvariable=self.train_batch_var, width=8).grid(row=6, column=1, sticky=tk.W, padx=(16, 0))

        self.auto_copy_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(tr, text="训练完成后自动复制 best.pt 到 weights/yolo_best.pt",
                        variable=self.auto_copy_var).grid(row=7, column=0, columnspan=3, sticky=tk.W, pady=(10, 4))

        btn = tk.Frame(tr, bg="#ffffff")
        btn.grid(row=8, column=0, columnspan=3, sticky=tk.W, pady=(6, 0))
        self.train_btn = IOSButton(btn, text="开始训练", command=self._start_train, kind="primary", width=110, height=42, bg="#ffffff")
        self.train_btn.pack(side=tk.LEFT, padx=(0, 8))
        self.stop_btn = IOSButton(btn, text="停止", command=self._stop, width=76, height=42, bg="#ffffff", state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT)

        # ---- 日志 ----
        log_surface = IOSPanel(self, radius=16, canvas_bg="#f5f6f8")
        log_surface.pack(fill=tk.BOTH, expand=True)
        logf = log_surface.content
        logf.configure(padx=4, pady=4)
        tk.Label(logf, text="运行记录", bg="#ffffff", fg="#1c1c1e", font=("Microsoft YaHei UI", 12, "bold")).pack(anchor=tk.W, pady=(0, 8))
        self.log_text = ScrolledText(
            logf, state=tk.DISABLED, bg="#ffffff", fg="#3d424d", insertbackground="#1c1c1e",
            relief=tk.FLAT, highlightthickness=1, highlightbackground="#e1e3e8", font=("Consolas", 10),
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)

        hint = "首次训练会下载预训练权重；请确保已安装 ultralytics。"
        tk.Label(self, text=hint, bg="#f5f6f8", fg="#8e8e93", font=("Microsoft YaHei UI", 9)).pack(anchor=tk.W, pady=(8, 0))

    def _browse_dir(self, var):
        path = filedialog.askdirectory()
        if path:
            var.set(path)

    def _browse_data(self):
        path = filedialog.askopenfilename(filetypes=[("YAML", "*.yaml *.yml"), ("All", "*.*")])
        if path:
            self.train_data_var.set(path)

    def _log(self, text):
        self.msg_queue.put(("log", text))

    def _append_log(self, text):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, text + "\n")
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)

    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.msg_queue.get_nowait()
                if kind == "log":
                    self._append_log(payload)
                elif kind == "done":
                    self.train_btn.config(state=tk.NORMAL)
                    self.stop_btn.config(state=tk.DISABLED)
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    # ---- 导出 ----
    def _start_export(self):
        images = self.exp_images_var.get().strip()
        ann = self.exp_ann_var.get().strip()
        out = self.exp_out_var.get().strip()
        try:
            val_ratio = float(self.exp_val_var.get())
        except ValueError:
            messagebox.showerror("参数错误", "验证集比例必须是数字")
            return
        if not os.path.isdir(images) or not os.path.isdir(ann):
            messagebox.showerror("路径错误", "图片目录或标注目录不存在")
            return

        self._log("开始导出...")

        def worker():
            try:
                finetune.export_voc_to_yolo(images, ann, out, val_ratio=val_ratio, log=self._log)
                self._log("导出完成，请在上方「开始训练」中确认数据配置路径后训练。")
            except Exception as exc:
                self._log(f"导出失败: {exc}")

        threading.Thread(target=worker, daemon=True).start()

    # ---- 训练 ----
    def _start_train(self):
        if self.worker_thread and self.worker_thread.is_alive():
            return
        data = self.train_data_var.get().strip()
        model_name = self.train_model_var.get().strip()
        try:
            epochs = int(self.train_epochs_var.get())
            imgsz = int(self.train_imgsz_var.get())
            batch = int(self.train_batch_var.get())
        except ValueError:
            messagebox.showerror("参数错误", "轮数/图像尺寸/批量必须是整数")
            return
        if not os.path.isfile(data):
            messagebox.showerror("路径错误", f"数据配置不存在: {data}")
            return

        # 数据量检查：过少时提醒用户可能过拟合/不准确
        n_train = self._count_train_images(data)
        if n_train < config.MIN_TRAIN_IMAGES:
            proceed = messagebox.askyesno(
                "数据量较少",
                f"训练集仅 {n_train} 张图片。\n\n"
                "数据量过少容易导致模型过拟合、标注结果不准确。\n"
                f"建议至少 {config.MIN_TRAIN_IMAGES} 张（推荐 200 张以上）。\n\n"
                "是否仍然继续训练？",
            )
            if not proceed:
                return

        self.stop_flag.clear()
        self.train_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.worker_thread = threading.Thread(
            target=self._train_worker, args=(data, model_name, epochs, imgsz, batch), daemon=True)
        self.worker_thread.start()

    def _count_train_images(self, data_yaml):
        """从 dataset.yaml 统计训练集图片数"""
        try:
            import yaml
            with open(data_yaml, encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
            base = cfg.get("path", "") or ""
            train = cfg.get("train", "images/train")
            train_dir = os.path.join(base, train) if not os.path.isabs(train) else train
            if not os.path.isdir(train_dir):
                return 0
            return len([f for f in os.listdir(train_dir)
                        if f.lower().endswith((".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"))])
        except Exception:
            return 0

    def _stop(self):
        self.stop_flag.set()
        self._log("正在停止（当前 epoch 结束后生效）...")

    def _train_worker(self, data, model_name, epochs, imgsz, batch):
        try:
            self._log(f"开始训练: {model_name}, epochs={epochs}, imgsz={imgsz}, batch={batch}")
            best = finetune.train_yolo(
                data, model_name, epochs=epochs, imgsz=imgsz, batch=batch,
                log=self._log, stop_event=self.stop_flag)
            if self.auto_copy_var.get() and best and os.path.isfile(best):
                os.makedirs(os.path.dirname(config.YOLO_WEIGHTS), exist_ok=True)
                shutil.copy2(best, config.YOLO_WEIGHTS)
                self._log(f"已复制 best.pt 到 {config.YOLO_WEIGHTS}")
                self._log("现在可在「自动标注」页把检测模型切换为「YOLO（微调）」")
        except ModuleNotFoundError as exc:
            self._log(f"缺少依赖: {exc.name}，请先执行 pip install ultralytics")
        except Exception as exc:
            self._log(f"训练失败: {exc}")
        finally:
            self.msg_queue.put(("done", None))
