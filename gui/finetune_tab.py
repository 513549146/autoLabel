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


MODEL_CHOICES = [
    "yolov8n.pt", "yolov8s.pt", "yolov8m.pt", "yolov8l.pt", "yolov8x.pt",
    "yolov11n.pt", "yolov11s.pt", "yolov11m.pt", "yolov11l.pt", "yolov11x.pt",
]


class FineTuneTab(ttk.Frame):
    """微调页：可视化导出 VOC -> YOLO 训练集，并微调训练"""

    def __init__(self, parent, app):
        super().__init__(parent, padding=12)
        self.app = app
        self.worker_thread = None
        self.stop_flag = threading.Event()
        self.msg_queue = queue.Queue()
        self._build_ui()
        self.after(100, self._poll_queue)

    def _build_ui(self):
        # ---- 导出 ----
        exp = ttk.LabelFrame(self, text="1. 导出数据集（VOC → YOLO）", padding=8)
        exp.pack(fill=tk.X, pady=(0, 8))
        exp.columnconfigure(1, weight=1)

        ttk.Label(exp, text="图片目录:").grid(row=0, column=0, sticky=tk.W, pady=3)
        self.exp_images_var = tk.StringVar(value=config.INPUT_DIR)
        ttk.Entry(exp, textvariable=self.exp_images_var).grid(row=0, column=1, sticky=tk.EW, padx=4)
        ttk.Button(exp, text="浏览", command=lambda: self._browse_dir(self.exp_images_var)).grid(row=0, column=2)

        ttk.Label(exp, text="标注目录:").grid(row=1, column=0, sticky=tk.W, pady=3)
        self.exp_ann_var = tk.StringVar(value=config.OUTPUT_DIR)
        ttk.Entry(exp, textvariable=self.exp_ann_var).grid(row=1, column=1, sticky=tk.EW, padx=4)
        ttk.Button(exp, text="浏览", command=lambda: self._browse_dir(self.exp_ann_var)).grid(row=1, column=2)

        ttk.Label(exp, text="输出目录:").grid(row=2, column=0, sticky=tk.W, pady=3)
        self.exp_out_var = tk.StringVar(value=os.path.join(config.BASE_DIR, "yolo_dataset"))
        ttk.Entry(exp, textvariable=self.exp_out_var).grid(row=2, column=1, sticky=tk.EW, padx=4)
        ttk.Button(exp, text="浏览", command=lambda: self._browse_dir(self.exp_out_var)).grid(row=2, column=2)

        ttk.Label(exp, text="验证集比例:").grid(row=3, column=0, sticky=tk.W, pady=3)
        self.exp_val_var = tk.StringVar(value="0.2")
        ttk.Entry(exp, textvariable=self.exp_val_var, width=8).grid(row=3, column=1, sticky=tk.W, padx=4)
        ttk.Button(exp, text="导出数据集", command=self._start_export).grid(row=3, column=2)

        # ---- 训练 ----
        tr = ttk.LabelFrame(self, text="2. 微调训练（YOLO）", padding=8)
        tr.pack(fill=tk.X, pady=(0, 8))
        tr.columnconfigure(1, weight=1)

        ttk.Label(tr, text="数据配置:").grid(row=0, column=0, sticky=tk.W, pady=3)
        self.train_data_var = tk.StringVar(value=os.path.join(config.BASE_DIR, "yolo_dataset", "dataset.yaml"))
        ttk.Entry(tr, textvariable=self.train_data_var).grid(row=0, column=1, sticky=tk.EW, padx=4)
        ttk.Button(tr, text="浏览", command=self._browse_data).grid(row=0, column=2)

        ttk.Label(tr, text="预训练模型:").grid(row=1, column=0, sticky=tk.W, pady=3)
        self.train_model_var = tk.StringVar(value="yolov8s.pt")
        ttk.Combobox(tr, textvariable=self.train_model_var, values=MODEL_CHOICES, state="readonly", width=12).grid(
            row=1, column=1, sticky=tk.W, padx=4)

        ttk.Label(tr, text="轮数:").grid(row=2, column=0, sticky=tk.W, pady=3)
        self.train_epochs_var = tk.StringVar(value="100")
        ttk.Entry(tr, textvariable=self.train_epochs_var, width=8).grid(row=2, column=1, sticky=tk.W, padx=4)

        ttk.Label(tr, text="图像尺寸:").grid(row=3, column=0, sticky=tk.W, pady=3)
        self.train_imgsz_var = tk.StringVar(value="640")
        ttk.Entry(tr, textvariable=self.train_imgsz_var, width=8).grid(row=3, column=1, sticky=tk.W, padx=4)

        ttk.Label(tr, text="批量:").grid(row=4, column=0, sticky=tk.W, pady=3)
        self.train_batch_var = tk.StringVar(value="16")
        ttk.Entry(tr, textvariable=self.train_batch_var, width=8).grid(row=4, column=1, sticky=tk.W, padx=4)

        self.auto_copy_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(tr, text="训练完成后自动复制 best.pt 到 weights/yolo_best.pt",
                        variable=self.auto_copy_var).grid(row=5, column=0, columnspan=3, sticky=tk.W, pady=4)

        btn = ttk.Frame(tr)
        btn.grid(row=6, column=0, columnspan=3, sticky=tk.W, pady=2)
        self.train_btn = ttk.Button(btn, text="开始训练", command=self._start_train)
        self.train_btn.pack(side=tk.LEFT, padx=(0, 8))
        self.stop_btn = ttk.Button(btn, text="停止", command=self._stop, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT)

        # ---- 日志 ----
        logf = ttk.LabelFrame(self, text="日志", padding=8)
        logf.pack(fill=tk.BOTH, expand=True)
        self.log_text = ScrolledText(logf, state=tk.DISABLED)
        self.log_text.pack(fill=tk.BOTH, expand=True)

        hint = ("流程：自动标注+人工审核修正 VOC → 上一步「导出数据集」→ 「开始训练」→ 训练后自动设为 YOLO 模型\n"
                "训练会自动下载预训练权重（首次联网）；要求已安装 ultralytics（pip install ultralytics）")
        ttk.Label(self, text=hint, foreground="gray").pack(anchor=tk.W, pady=(4, 0))

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
