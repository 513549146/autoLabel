import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

import config
import core


class MultiSelectCombo(ttk.Frame):
    """带复选框的下拉多选：默认全选，可取消勾选来过滤展示的框"""

    def __init__(self, master, command=None, width=16):
        super().__init__(master)
        self.command = command
        self._items = []
        self._vars = {}
        self._popup = None
        self._all_var = tk.BooleanVar(value=True)
        self._text = tk.StringVar(value="全选")

        # 用只读 Combobox 作为触发框，下拉按钮样式与普通下拉框一致
        self._combo = ttk.Combobox(self, textvariable=self._text, state="readonly", width=width)
        self._combo.pack(fill=tk.X, expand=True)
        self._combo.bind("<ButtonPress-1>", self._on_press)

        self._refresh_display()

    def _on_press(self, event):
        self._toggle_popup()
        return "break"

    # ---- 对外接口 ----
    def set_items(self, names):
        """设置类别列表：新增类别默认选中，已有类别保留原勾选状态"""
        self._close_popup()
        for n in names:
            if n not in self._vars:
                self._vars[n] = tk.BooleanVar(value=True)
        self._items = [n for n in names]
        for n in list(self._vars.keys()):
            if n not in self._items:
                del self._vars[n]
        self._sync_all_var()
        self._refresh_display()

    def get_selected(self):
        return {n for n in self._items if self._vars[n].get()}

    def select_all(self):
        for n in self._items:
            self._vars[n].set(True)
        self._sync_all_var()
        self._refresh_display()
        self._notify()

    def is_all_selected(self):
        return bool(self._items) and all(self._vars[n].get() for n in self._items)

    # ---- 显示 ----
    def _refresh_display(self):
        sel = self.get_selected()
        if not self._items:
            text = "无类别"
        elif self.is_all_selected():
            text = "全选"
        elif not sel:
            text = "无"
        else:
            text = ", ".join(sel)
            if len(text) > 24:
                text = text[:22] + "…"
        self._text.set(text)

    # ---- 弹层 ----
    def _toggle_popup(self):
        if self._popup is not None:
            self._close_popup()
        else:
            self._open_popup()

    def _open_popup(self):
        self._close_popup()
        popup = tk.Toplevel(self)
        popup.wm_overrideredirect(True)
        popup.configure(bg="#ffffff", highlightthickness=1, highlightbackground="#888888")

        tk.Checkbutton(
            popup, text="全选", variable=self._all_var, command=self._on_all_toggle,
            anchor="w", bg="#ffffff", activebackground="#e8e8e8", padx=6, pady=2,
        ).pack(fill=tk.X)
        tk.Frame(popup, height=1, bg="#cccccc").pack(fill=tk.X)
        for n in self._items:
            tk.Checkbutton(
                popup, text=n, variable=self._vars[n], command=self._on_item_toggle,
                anchor="w", bg="#ffffff", activebackground="#e8e8e8", padx=6, pady=2,
            ).pack(fill=tk.X)

        self._sync_all_var()

        x = self._combo.winfo_rootx()
        y = self._combo.winfo_rooty() + self._combo.winfo_height()
        popup.geometry(f"+{x}+{y}")

        popup.bind("<FocusOut>", lambda e: self._close_popup())
        popup.focus_set()
        self._popup = popup

    def _close_popup(self):
        if self._popup is not None:
            try:
                self._popup.destroy()
            except tk.TclError:
                pass
            self._popup = None

    def _sync_all_var(self):
        self._all_var.set(self.is_all_selected())

    def _on_all_toggle(self):
        val = self._all_var.get()
        for n in self._items:
            self._vars[n].set(val)
        self._refresh_display()
        self._notify()

    def _on_item_toggle(self):
        self._sync_all_var()
        self._refresh_display()
        self._notify()

    def _notify(self):
        if self.command:
            self.command()


class ReviewTab(ttk.Frame):
    """人工审核页：查看 / 修正预标注结果，支持缩放与平移"""

    def __init__(self, parent, app):
        super().__init__(parent, padding=8)
        self.app = app
        self.images_dir = None
        self.img_files = []
        self.cur_idx = -1
        self.pil_img = None
        self.photo = None
        self.zoom = 1.0
        self.pan_x = 0
        self.pan_y = 0
        self.shapes = []
        self._vis_idx = []
        self.selected = None
        self.drag_start = None
        self.rubber_id = None
        self.pan_start = None
        self.pan_origin = (0, 0)
        self._manual_view = False
        self._build_ui()

    def _build_ui(self):
        bar = ttk.Frame(self)
        bar.pack(fill=tk.X)
        ttk.Button(bar, text="打开图片目录", command=self._open_dir).pack(side=tk.LEFT)
        ttk.Label(bar, text="输出:").pack(side=tk.LEFT, padx=(8, 0))
        self.output_var = tk.StringVar(value=config.OUTPUT_DIR)
        ttk.Entry(bar, textvariable=self.output_var, width=30).pack(side=tk.LEFT, padx=4)
        ttk.Button(bar, text="浏览", command=self._browse_output).pack(side=tk.LEFT)

        bar2 = ttk.Frame(self)
        bar2.pack(fill=tk.X, pady=(4, 0))
        ttk.Label(bar2, text="类别:").pack(side=tk.LEFT)
        self.filter_combo = MultiSelectCombo(bar2, command=self._on_cat_filter_change, width=16)
        self.filter_combo.pack(side=tk.LEFT, padx=(4, 8))
        ttk.Label(bar2, text="标注:").pack(side=tk.LEFT)
        self.label_var = tk.StringVar()
        self.label_combo = ttk.Combobox(bar2, textvariable=self.label_var, width=14)
        self.label_combo.pack(side=tk.LEFT, padx=4)
        self.label_combo.bind("<<ComboboxSelected>>", lambda e: self._apply_label())
        self.label_combo.bind("<Return>", lambda e: self._apply_label())
        ttk.Button(bar2, text="保存", command=self._save).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(bar2, text="删除选中", command=self._delete_selected).pack(side=tk.LEFT, padx=4)
        ttk.Button(bar2, text="重新标注此图", command=self._reannotate).pack(side=tk.LEFT, padx=4)

        bar3 = ttk.Frame(self)
        bar3.pack(fill=tk.X, pady=(4, 0))
        ttk.Button(bar3, text="-", width=3, command=self._zoom_out).pack(side=tk.LEFT)
        self.zoom_var = tk.StringVar(value="100%")
        ttk.Label(bar3, textvariable=self.zoom_var, width=8).pack(side=tk.LEFT, padx=4)
        ttk.Button(bar3, text="+", width=3, command=self._zoom_in).pack(side=tk.LEFT)
        ttk.Button(bar3, text="适应窗口", command=self._fit_window).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(bar3, text="实际大小", command=self._actual_size).pack(side=tk.LEFT, padx=4)
        ttk.Label(
            bar3,
            text="滚轮缩放 | 中键拖动平移 | 左键拖拽画框 | 点击选中 | Delete 删除 | Ctrl+S 保存 | ↑/↓ 切换",
            foreground="gray",
        ).pack(side=tk.LEFT, padx=10)

        # 三栏布局：图片列表 | 画布 | 框列表（分隔条可拖动）
        main = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        main.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(main)
        ttk.Label(left, text="图片").pack(anchor=tk.W)
        self.listbox = tk.Listbox(left, width=22)
        self.listbox.pack(fill=tk.BOTH, expand=True)
        self.listbox.bind("<<ListboxSelect>>", self._on_list_select)

        mid = ttk.Frame(main)
        self.canvas = tk.Canvas(mid, bg="#2b2b2b", cursor="crosshair")
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<ButtonPress-2>", self._on_pan_press)
        self.canvas.bind("<B2-Motion>", self._on_pan_drag)
        self.canvas.bind("<ButtonRelease-2>", self._on_pan_release)
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<Configure>", self._on_canvas_resize)
        self.canvas.bind("<Delete>", lambda e: self._delete_selected())

        right = ttk.Frame(main)
        ttk.Label(right, text="当前框").pack(anchor=tk.W)
        self.box_list = tk.Listbox(right, width=22)
        self.box_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.box_list.bind("<<ListboxSelect>>", self._on_box_list_select)
        self.box_list.bind("<Delete>", lambda e: self._delete_selected())

        main.add(left, weight=0)
        main.add(mid, weight=1)
        main.add(right, weight=0)

        nav = ttk.Frame(self)
        nav.pack(fill=tk.X, pady=(6, 0))
        ttk.Button(nav, text="上一张 (↑)", command=self._prev).pack(side=tk.LEFT)
        ttk.Button(nav, text="下一张 (↓)", command=self._next).pack(side=tk.LEFT, padx=4)
        self.counter_var = tk.StringVar(value="0 / 0")
        ttk.Label(nav, textvariable=self.counter_var).pack(side=tk.LEFT, padx=8)
        self.status_var = tk.StringVar(value="")
        ttk.Label(nav, textvariable=self.status_var).pack(side=tk.RIGHT)

        self.bind("<Up>", lambda e: self._prev())
        self.bind("<Down>", lambda e: self._next())
        self.bind("<Control-s>", lambda e: self._save())

    # ---------- 目录 / 图片加载 ----------
    def _open_dir(self):
        path = filedialog.askdirectory(title="选择图片目录")
        if not path:
            return
        self.images_dir = path
        self.img_files = core.list_images(path)
        self.listbox.delete(0, tk.END)
        for f in self.img_files:
            self.listbox.insert(tk.END, f)
        if self.img_files:
            self.listbox.selection_set(0)
            self._load_image(0)
        else:
            self.status_var.set("目录中没有支持的图片")

    def _browse_output(self):
        path = filedialog.askdirectory(title="选择输出标注目录")
        if path:
            self.output_var.set(path)

    def _load_image(self, idx):
        if idx < 0 or idx >= len(self.img_files):
            return
        self._autosave_current()
        self.cur_idx = idx
        fname = self.img_files[idx]
        image_path = os.path.join(self.images_dir, fname)
        self.pil_img = Image.open(image_path).convert("RGB")

        output_dir = self.output_var.get().strip()
        xml_path = os.path.join(output_dir, os.path.splitext(fname)[0] + ".xml")
        self.shapes = []
        if output_dir and os.path.isfile(xml_path):
            try:
                for ann in core.load_voc_xml(xml_path):
                    self.shapes.append({"name": ann["name"], "bbox": ann["bbox"]})
            except Exception as exc:
                self.status_var.set(f"读取标注失败: {exc}")

        self.selected = None
        self.listbox.selection_clear(0, tk.END)
        self.listbox.selection_set(idx)
        self.listbox.see(idx)
        self.counter_var.set(f"{idx + 1} / {len(self.img_files)}")
        self._refresh_categories()
        self._collect_cats()
        self._rebuild_vis_idx()
        self._refresh_box_list()
        self._fit_window()
        self.after(150, self._fit_window)

    def _refresh_categories(self):
        cats = core.parse_categories(self.app.auto_tab.prompt_var.get())
        self.label_combo["values"] = cats
        if not self.label_var.get() and cats:
            self.label_var.set(cats[0])

    # ---------- 类别筛选 ----------
    def _visible_cats(self):
        return self.filter_combo.get_selected()

    def _rebuild_vis_idx(self):
        vis = self._visible_cats()
        if vis:
            self._vis_idx = [i for i, s in enumerate(self.shapes) if s["name"] in vis]
        else:
            self._vis_idx = []
        if self.selected is not None and self.selected >= len(self._vis_idx):
            self.selected = None

    def _collect_cats(self):
        prompt_cats = core.parse_categories(self.app.auto_tab.prompt_var.get())
        shape_cats = []
        for s in self.shapes:
            if s["name"] and s["name"] not in shape_cats:
                shape_cats.append(s["name"])
        desired = prompt_cats + [c for c in shape_cats if c not in prompt_cats]
        self.filter_combo.set_items(desired)

    def _on_cat_filter_change(self, event=None):
        self._rebuild_vis_idx()
        self._render()
        self._refresh_box_list()

    def _refresh_box_list(self):
        self.box_list.delete(0, tk.END)
        for idx in self._vis_idx:
            s = self.shapes[idx]
            x1, y1, x2, y2 = s["bbox"]
            self.box_list.insert(tk.END, f"{s['name']}  [{x1},{y1},{x2},{y2}]")
        if self.selected is not None and 0 <= self.selected < len(self._vis_idx):
            self.box_list.selection_set(self.selected)
            self.box_list.see(self.selected)

    # ---------- 渲染 / 缩放 / 平移 ----------
    def _render(self):
        self.canvas.delete("all")
        if self.pil_img is None:
            return
        ow, oh = self.pil_img.size
        dw, dh = max(1, int(ow * self.zoom)), max(1, int(oh * self.zoom))
        img = self.pil_img.resize((dw, dh), Image.Resampling.LANCZOS)
        self.photo = ImageTk.PhotoImage(img)
        self.canvas.create_image(self.pan_x, self.pan_y, anchor=tk.NW, image=self.photo)
        for vi, idx in enumerate(self._vis_idx):
            s = self.shapes[idx]
            x1, y1, x2, y2 = s["bbox"]
            cx1 = x1 * self.zoom + self.pan_x
            cy1 = y1 * self.zoom + self.pan_y
            cx2 = x2 * self.zoom + self.pan_x
            cy2 = y2 * self.zoom + self.pan_y
            color = "#ff5252" if vi == self.selected else "#00e676"
            self.canvas.create_rectangle(cx1, cy1, cx2, cy2, outline=color, width=2)
            self.canvas.create_text(cx1, max(cy1 - 8, 2), anchor=tk.SW, text=s["name"],
                                    fill=color, font=("", 10, "bold"))

    def _fit_window(self):
        if self.pil_img is None:
            return
        self._manual_view = False
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        if cw < 50 or ch < 50:
            cw, ch = 900, 650
        ow, oh = self.pil_img.size
        self.zoom = min(cw / ow, ch / oh)
        self.pan_x = int((cw - ow * self.zoom) / 2)
        self.pan_y = int((ch - oh * self.zoom) / 2)
        self._update_zoom_label()
        self._render()

    def _actual_size(self):
        if self.pil_img is None:
            return
        self._manual_view = True
        self.zoom = 1.0
        self.pan_x = 0
        self.pan_y = 0
        self._update_zoom_label()
        self._render()

    def _zoom_at(self, cx, cy, factor):
        if self.pil_img is None:
            return
        self._manual_view = True
        ix = (cx - self.pan_x) / self.zoom
        iy = (cy - self.pan_y) / self.zoom
        new_zoom = max(0.02, min(self.zoom * factor, 20.0))
        self.pan_x = cx - ix * new_zoom
        self.pan_y = cy - iy * new_zoom
        self.zoom = new_zoom
        self._update_zoom_label()
        self._render()

    def _zoom_in(self):
        if self.pil_img is None:
            return
        self._zoom_at(self.canvas.winfo_width() / 2, self.canvas.winfo_height() / 2, 1.2)

    def _zoom_out(self):
        if self.pil_img is None:
            return
        self._zoom_at(self.canvas.winfo_width() / 2, self.canvas.winfo_height() / 2, 1 / 1.2)

    def _on_mousewheel(self, event):
        if self.pil_img is None:
            return
        factor = 1.1 if event.delta > 0 else 1 / 1.1
        self._zoom_at(event.x, event.y, factor)

    def _on_pan_press(self, event):
        self.pan_start = (event.x, event.y)
        self.pan_origin = (self.pan_x, self.pan_y)

    def _on_pan_drag(self, event):
        if self.pan_start is None:
            return
        self._manual_view = True
        self.pan_x = self.pan_origin[0] + (event.x - self.pan_start[0])
        self.pan_y = self.pan_origin[1] + (event.y - self.pan_start[1])
        self._render()

    def _on_pan_release(self, event):
        self.pan_start = None

    def _on_canvas_resize(self, event):
        if getattr(self, "_resize_job", None):
            self.after_cancel(self._resize_job)
        if self.pil_img is None:
            return
        # 未手动缩放/平移时，窗口变化自动重新适应；否则保持当前视角
        if not self._manual_view:
            self._resize_job = self.after(120, self._fit_window)
        else:
            self._resize_job = self.after(120, self._render)

    def _update_zoom_label(self):
        self.zoom_var.set(f"{int(self.zoom * 100)}%")

    def _canvas_to_img(self, cx, cy):
        ix = (cx - self.pan_x) / self.zoom
        iy = (cy - self.pan_y) / self.zoom
        return int(ix), int(iy)

    def _clamp_box(self, x1, y1, x2, y2):
        w, h = self.pil_img.size
        x1 = max(0, min(x1, w - 1))
        y1 = max(0, min(y1, h - 1))
        x2 = max(0, min(x2, w - 1))
        y2 = max(0, min(y2, h - 1))
        return x1, y1, x2, y2

    # ---------- 画框 / 选中 / 编辑 ----------
    def _on_press(self, event):
        if self.pil_img is None:
            return
        self.drag_start = (event.x, event.y)
        self.rubber_id = self.canvas.create_rectangle(
            event.x, event.y, event.x, event.y, outline="yellow", width=2, dash=(4, 2))

    def _on_drag(self, event):
        if self.rubber_id is not None and self.drag_start is not None:
            x0, y0 = self.drag_start
            self.canvas.coords(self.rubber_id, x0, y0, event.x, event.y)

    def _on_release(self, event):
        if self.rubber_id is None:
            return
        self.canvas.delete(self.rubber_id)
        self.rubber_id = None
        if self.drag_start is None:
            return
        x0, y0 = self.drag_start
        self.drag_start = None
        if abs(event.x - x0) < 4 and abs(event.y - y0) < 4:
            self._select_at(event.x, event.y)
        else:
            ix1, iy1 = self._canvas_to_img(x0, y0)
            ix2, iy2 = self._canvas_to_img(event.x, event.y)
            x1, x2 = sorted([ix1, ix2])
            y1, y2 = sorted([iy1, iy2])
            x1, y1, x2, y2 = self._clamp_box(x1, y1, x2, y2)
            if x2 > x1 and y2 > y1:
                label = self.label_var.get().strip() or "object"
                self.shapes.append({"name": label, "bbox": [x1, y1, x2, y2]})
                self._collect_cats()
                self._rebuild_vis_idx()
                try:
                    self.selected = self._vis_idx.index(len(self.shapes) - 1)
                except ValueError:
                    self.selected = None
                self._render()
                self._refresh_box_list()

    def _select_at(self, cx, cy):
        ix, iy = self._canvas_to_img(cx, cy)
        for vi in range(len(self._vis_idx) - 1, -1, -1):
            idx = self._vis_idx[vi]
            x1, y1, x2, y2 = self.shapes[idx]["bbox"]
            if x1 <= ix <= x2 and y1 <= iy <= y2:
                self.selected = vi
                self.label_var.set(self.shapes[idx]["name"])
                self._render()
                self._refresh_box_list()
                return
        self.selected = None
        self._render()
        self._refresh_box_list()

    def _on_box_list_select(self, event):
        sel = self.box_list.curselection()
        if not sel:
            return
        self.selected = sel[0]
        idx = self._vis_idx[self.selected]
        self.label_var.set(self.shapes[idx]["name"])
        self._render()

    def _apply_label(self):
        label = self.label_var.get().strip()
        if label and self.selected is not None and 0 <= self.selected < len(self._vis_idx):
            idx = self._vis_idx[self.selected]
            self.shapes[idx]["name"] = label
            self._collect_cats()
            self._rebuild_vis_idx()
            try:
                self.selected = self._vis_idx.index(idx)
            except ValueError:
                self.selected = None
            self._render()
            self._refresh_box_list()

    def _delete_selected(self):
        if self.selected is not None and 0 <= self.selected < len(self._vis_idx):
            idx = self._vis_idx[self.selected]
            del self.shapes[idx]
            self.selected = None
            self._rebuild_vis_idx()
            self._render()
            self._refresh_box_list()

    # ---------- 保存 / 重新标注 ----------
    def _autosave_current(self):
        if self.cur_idx >= 0 and self.pil_img is not None and self.images_dir:
            self._save(silent=True)

    def _save(self, silent=False):
        if self.cur_idx < 0 or self.pil_img is None:
            return
        output_dir = self.output_var.get().strip()
        if not output_dir:
            if not silent:
                messagebox.showerror("错误", "请先设置输出目录")
            return
        os.makedirs(output_dir, exist_ok=True)
        image_path = os.path.join(self.images_dir, self.img_files[self.cur_idx])
        annotations = [{"name": s["name"], "bbox": s["bbox"]} for s in self.shapes]
        core.save_as_voc_xml(annotations, image_path, output_dir)
        self.status_var.set(f"已保存 {len(annotations)} 个目标")

    def _reannotate(self):
        if self.cur_idx < 0 or self.pil_img is None:
            return
        image_path = os.path.join(self.images_dir, self.img_files[self.cur_idx])
        prompt = self.app.auto_tab.prompt_var.get().strip()
        try:
            box_thr = float(self.app.auto_tab.box_thr_var.get())
            text_thr = float(self.app.auto_tab.text_thr_var.get())
        except ValueError:
            messagebox.showerror("参数错误", "请在「自动标注」页填写正确的阈值")
            return
        if not prompt:
            messagebox.showerror("参数错误", "提示词不能为空")
            return

        self.status_var.set("正在重新标注...")

        def worker():
            try:
                self.app.ensure_model()
                anns = core.auto_label(image_path, prompt, self.app.detector, box_thr, text_thr)
            except Exception as exc:
                self.after(0, lambda: self.status_var.set(f"标注失败: {exc}"))
                return
            self.after(0, lambda: self._apply_reannotate(anns))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_reannotate(self, anns):
        self.shapes = [{"name": a["name"], "bbox": a["bbox"]} for a in anns]
        self.selected = None
        self._collect_cats()
        self._rebuild_vis_idx()
        self._render()
        self._refresh_box_list()
        self.status_var.set(f"重新标注完成，共 {len(self.shapes)} 个目标")

    # ---------- 导航 ----------
    def _prev(self):
        if self.img_files:
            self._load_image(max(0, self.cur_idx - 1))

    def _next(self):
        if self.img_files:
            self._load_image(min(len(self.img_files) - 1, self.cur_idx + 1))

    def _on_list_select(self, event):
        sel = self.listbox.curselection()
        if sel and sel[0] != self.cur_idx:
            self._load_image(sel[0])
