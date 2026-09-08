import copy
import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

import config
import core
from gui.icon_assets import icon as generated_icon
from gui.ios_widgets import IOSButton, IOSPanel
from project import STATUS_APPROVED, STATUS_LABELS, STATUS_NEEDS_REVIEW, STATUS_SKIPPED, STATUS_UNREVIEWED


class MultiSelectCombo(ttk.Frame):
    """Category filter with persistent checkbox selections."""

    def __init__(self, master, command=None, width=16):
        super().__init__(master)
        self.command, self._items, self._vars, self._popup = command, [], {}, None
        self._all_var, self._text = tk.BooleanVar(value=True), tk.StringVar(value="全选")
        self._combo = ttk.Combobox(self, textvariable=self._text, state="readonly", width=width)
        self._combo.pack(fill=tk.X, expand=True)
        self._combo.bind("<ButtonPress-1>", self._toggle)

    def set_items(self, names):
        self._close()
        for name in names:
            self._vars.setdefault(name, tk.BooleanVar(value=True))
        self._items = list(names)
        for name in list(self._vars):
            if name not in self._items:
                del self._vars[name]
        self._refresh()

    def get_selected(self):
        return {name for name in self._items if self._vars[name].get()}

    def _toggle(self, event):
        if self._popup:
            self._close()
        else:
            popup = tk.Toplevel(self)
            popup.wm_overrideredirect(True)
            popup.configure(bg="#ffffff", highlightthickness=1, highlightbackground="#d9dce3")
            opts = {"anchor": "w", "bg": "#ffffff", "fg": "#1c1c1e", "activebackground": "#eaf3ff",
                    "activeforeground": "#007aff", "selectcolor": "#ffffff", "padx": 8, "pady": 3}
            all_selected = bool(self._items) and all(self._vars[name].get() for name in self._items)
            self._all_var.set(all_selected)
            tk.Checkbutton(popup, text="全选", variable=self._all_var, command=self._set_all, **opts).pack(fill=tk.X)
            for name in self._items:
                tk.Checkbutton(popup, text=name, variable=self._vars[name], command=self._changed, **opts).pack(fill=tk.X)
            popup.geometry(f"+{self._combo.winfo_rootx()}+{self._combo.winfo_rooty() + self._combo.winfo_height()}")
            popup.bind("<FocusOut>", lambda _event: self._close())
            popup.focus_set()
            self._popup = popup
        return "break"

    def _close(self):
        if self._popup:
            try:
                self._popup.destroy()
            except tk.TclError:
                pass
            self._popup = None

    def _set_all(self):
        for name in self._items:
            self._vars[name].set(self._all_var.get())
        self._changed()

    def _changed(self):
        self._refresh()
        if self.command:
            self.command()

    def _refresh(self):
        selected = self.get_selected()
        if not self._items:
            text = "无类别"
        elif len(selected) == len(self._items):
            text = "全选"
        elif not selected:
            text = "无"
        else:
            text = ", ".join(selected)
            text = text[:22] + "…" if len(text) > 24 else text
        self._text.set(text)


class ReviewTab(ttk.Frame):
    """Review workspace with direct box editing, history, and state queues."""

    HANDLE_RADIUS = 5
    HISTORY_LIMIT = 100
    STATUS_FILTERS = (("所有图片", None), ("待审核", STATUS_NEEDS_REVIEW), ("未标注", STATUS_UNREVIEWED),
                      ("已通过", STATUS_APPROVED), ("已跳过", STATUS_SKIPPED))

    def __init__(self, parent, app):
        super().__init__(parent, padding=10)
        self.app = app
        self.images_dir, self.img_files, self.queue_indices, self.cur_idx = None, [], [], -1
        self.pil_img = self.photo = None
        self.zoom, self.pan_x, self.pan_y, self._manual_view = 1.0, 0, 0, False
        self.shapes, self._vis_idx, self.selected = [], [], None
        self.undo_stack, self.redo_stack = [], []
        self.edit_mode = self.edit_handle = self.edit_origin = self.edit_start_img = None
        self.drag_start = self.rubber_id = self.pan_start = None
        self.pan_origin = (0, 0)
        self.icon_refs = []
        self._build_ui()

    def _build_ui(self):
        self.output_var = tk.StringVar(value=config.OUTPUT_DIR)
        self.queue_filter_var = tk.StringVar(value="所有图片")
        self.header_status_var = tk.StringVar(value="0 / 0")
        self.thumbnail_refs = []

        self.configure(padding=12, style="TFrame")
        # The toolbar is deliberately one aligned strip: page navigation,
        # selection tools, history, then a single primary save action.
        toolbar_surface = IOSPanel(self, height=84, radius=12, canvas_bg="#f5f6f8")
        toolbar_surface.pack(fill=tk.X, pady=(0, 10))
        toolbar = toolbar_surface.content
        navigation = tk.Frame(toolbar, bg="#ffffff")
        navigation.pack(side=tk.LEFT, padx=(16, 10), pady=4)
        self._toolbar_icon(navigation, "back", "上一张", self._prev).pack(side=tk.LEFT)
        self._toolbar_icon(navigation, "forward", "下一张", self._next).pack(side=tk.LEFT, padx=(2, 8))
        tk.Label(navigation, textvariable=self.header_status_var, bg="#ffffff", fg="#1d1d1f",
                 font=("Microsoft YaHei UI", 11, "bold"), padx=8).pack(side=tk.LEFT, pady=(3, 0))
        self._toolbar_divider(toolbar).pack(side=tk.LEFT, padx=8, pady=13, fill=tk.Y)
        commands = tk.Frame(toolbar, bg="#ffffff")
        commands.pack(side=tk.LEFT, pady=4)
        self._toolbar_icon(commands, "pan", "适应画面", self._fit_window, selected=True).pack(side=tk.LEFT)
        self._toolbar_icon(commands, "box", "框选", lambda: self.canvas.focus_set()).pack(side=tk.LEFT, padx=2)
        self._toolbar_divider(commands).pack(side=tk.LEFT, padx=8, pady=7, fill=tk.Y)
        self._toolbar_icon(commands, "undo", "撤销", self._undo).pack(side=tk.LEFT)
        self._toolbar_icon(commands, "redo", "重做", self._redo).pack(side=tk.LEFT, padx=2)
        self._toolbar_divider(commands).pack(side=tk.LEFT, padx=8, pady=7, fill=tk.Y)
        self._toolbar_icon(commands, "delete", "删除", self._delete_selected).pack(side=tk.LEFT)
        IOSButton(toolbar, text="保存", command=self._save, kind="primary", width=94, height=44, bg="#ffffff").pack(side=tk.RIGHT, padx=16, pady=16)

        main = tk.Frame(self, bg="#f5f6f8")
        main.pack(fill=tk.BOTH, expand=True)
        middle_surface = IOSPanel(main, radius=16, canvas_bg="#f5f6f8")
        middle_surface.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        middle = middle_surface.content
        self.canvas = tk.Canvas(middle, bg="#f1f2f4", cursor="crosshair", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<ButtonPress-2>", self._on_pan_press)
        self.canvas.bind("<B2-Motion>", self._on_pan_drag)
        self.canvas.bind("<ButtonRelease-2>", self._on_pan_release)
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<Configure>", self._on_canvas_resize)
        inspector_surface = IOSPanel(main, width=292, radius=16, canvas_bg="#f5f6f8")
        inspector_surface.pack(side=tk.RIGHT, fill=tk.Y, padx=(10, 0))
        inspector = inspector_surface.content
        inspector.pack_propagate(False)
        tk.Label(inspector, text="标签", bg="#ffffff", fg="#1d1d1f", font=("Microsoft YaHei UI", 14, "bold")).pack(anchor=tk.W, padx=16, pady=(18, 0))
        tk.Label(inspector, text="类别", bg="#ffffff", fg="#63666f", font=("Microsoft YaHei UI", 10)).pack(anchor=tk.W, padx=16, pady=(24, 7))
        self.label_var = tk.StringVar()
        self.label_combo = ttk.Combobox(inspector, textvariable=self.label_var, state="normal")
        self.label_combo.pack(fill=tk.X, padx=16)
        self.label_combo.bind("<<ComboboxSelected>>", lambda _event: self._apply_label())
        self.label_combo.bind("<Return>", lambda _event: self._apply_label())
        tk.Label(inspector, text="状态", bg="#ffffff", fg="#63666f", font=("Microsoft YaHei UI", 10)).pack(anchor=tk.W, padx=16, pady=(24, 7))
        status_row = tk.Frame(inspector, bg="#f2f3f5")
        status_row.pack(fill=tk.X, padx=16)
        IOSButton(status_row, text="审核", command=lambda: self._set_status(STATUS_NEEDS_REVIEW), kind="secondary", width=70, height=38, bg="#f2f3f5").pack(side=tk.LEFT, expand=True, fill=tk.X, padx=2, pady=2)
        IOSButton(status_row, text="通过", command=lambda: self._set_status(STATUS_APPROVED), kind="primary", width=70, height=38, bg="#f2f3f5").pack(side=tk.LEFT, expand=True, fill=tk.X, padx=2, pady=2)
        IOSButton(status_row, text="跳过", command=lambda: self._set_status(STATUS_SKIPPED), kind="secondary", width=70, height=38, bg="#f2f3f5").pack(side=tk.LEFT, expand=True, fill=tk.X, padx=2, pady=2)
        tk.Label(inspector, text="备注", bg="#ffffff", fg="#63666f", font=("Microsoft YaHei UI", 10)).pack(anchor=tk.W, padx=16, pady=(24, 7))
        self.note_text = tk.Text(inspector, height=6, bg="#f8f9fb", fg="#1c1c1e", relief=tk.FLAT, highlightthickness=1, highlightbackground="#e5e5ea", padx=8, pady=8)
        self.note_text.pack(fill=tk.X, padx=16)

        thumbs_surface = IOSPanel(self, height=116, radius=16, canvas_bg="#f5f6f8")
        thumbs_surface.pack(fill=tk.X, pady=(10, 0))
        thumbs = thumbs_surface.content
        thumbs.pack_propagate(False)
        IOSButton(thumbs, text="‹", command=self._prev, kind="quiet", width=42, height=42, bg="#ffffff").pack(side=tk.LEFT, padx=(12, 6), pady=31)
        self.thumbnail_frame = tk.Frame(thumbs, bg="#ffffff")
        self.thumbnail_frame.pack(side=tk.LEFT, fill=tk.X, expand=True, pady=12)
        IOSButton(thumbs, text="›", command=self._next, kind="quiet", width=42, height=42, bg="#ffffff").pack(side=tk.RIGHT, padx=(6, 12), pady=31)
        footer = tk.Frame(self, bg="#f5f6f8")
        footer.pack(fill=tk.X, pady=(8, 0))
        self.counter_var, self.status_var = tk.StringVar(value="0 / 0"), tk.StringVar(value="打开项目以开始审核")
        tk.Label(footer, text="审核进度", bg="#f5f6f8", fg="#63666f", font=("Microsoft YaHei UI", 10)).pack(side=tk.LEFT)
        self.review_progress = ttk.Progressbar(footer, mode="determinate", maximum=100, length=220)
        self.review_progress.pack(side=tk.LEFT, padx=8)
        tk.Label(footer, textvariable=self.counter_var, bg="#f5f6f8", fg="#63666f", font=("Microsoft YaHei UI", 10)).pack(side=tk.LEFT)
        tk.Label(footer, textvariable=self.status_var, bg="#f5f6f8", fg="#63666f", font=("Microsoft YaHei UI", 10)).pack(side=tk.RIGHT)

        # Hidden compatibility widgets: selection is done directly on the canvas.
        self.listbox = self._listbox(self, 1)
        self.listbox.bind("<<ListboxSelect>>", self._on_list_select)
        self.box_list = self._listbox(self, 1)
        self.box_list.bind("<<ListboxSelect>>", self._on_box_list_select)
        self.filter_combo = MultiSelectCombo(self, command=self._on_cat_filter_change, width=1)
        self.bind_all("<Control-z>", lambda _event: self._undo())
        self.bind_all("<Control-y>", lambda _event: self._redo())
        self.bind_all("<Control-d>", lambda _event: self._copy_selected())
        self.bind_all("<Control-s>", lambda _event: self._save())
        self.bind_all("<Delete>", lambda _event: self._delete_selected())

    @staticmethod
    def _listbox(parent, width):
        return tk.Listbox(parent, width=width, bg="#ffffff", fg="#1c1c1e", selectbackground="#007aff",
                          selectforeground="#ffffff", relief=tk.FLAT, highlightthickness=1, highlightbackground="#e1e3e8")

    @staticmethod
    def _toolbar_divider(parent):
        return tk.Frame(parent, bg="#e5e5ea", width=1)

    def _toolbar_icon(self, parent, name, label, command, selected=False):
        """Transparent icon-only command, matching the approved workbench target."""
        image = generated_icon(name, 36)
        self.icon_refs.append(image)
        background = "#eaf3ff" if selected else "#ffffff"
        button = tk.Frame(parent, bg=background, cursor="hand2", width=52, height=58)
        button.pack_propagate(False)
        icon_label = tk.Label(button, image=image, bg=background, cursor="hand2")
        icon_label.place(relx=0.5, rely=0.5, anchor=tk.CENTER)
        # Keep a native accessibility hint without reintroducing visible labels.
        button._command_label = label
        button.bind("<Button-1>", lambda _event: command())
        for child in button.winfo_children():
            child.bind("<Button-1>", lambda _event: command())
            child.bind("<Enter>", lambda _event: self._set_toolbar_hover(button, "#eaf3ff"))
            child.bind("<Leave>", lambda _event, base=background: self._set_toolbar_hover(button, base))
        button.bind("<Enter>", lambda _event: self._set_toolbar_hover(button, "#eaf3ff"))
        button.bind("<Leave>", lambda _event, base=background: self._set_toolbar_hover(button, base))
        return button

    @staticmethod
    def _set_toolbar_hover(button, color):
        button.configure(bg=color)
        for child in button.winfo_children():
            child.configure(bg=color)

    def _refresh_thumbnails(self):
        """Render a small, clickable filmstrip around the current review item."""
        for child in self.thumbnail_frame.winfo_children():
            child.destroy()
        self.thumbnail_refs = []
        if not self.images_dir or not self.queue_indices:
            return
        current_pos = self.queue_indices.index(self.cur_idx) if self.cur_idx in self.queue_indices else 0
        start = max(0, current_pos - 3)
        visible = self.queue_indices[start:start + 7]
        for index in visible:
            filename = self.img_files[index]
            selected = index == self.cur_idx
            holder = tk.Frame(self.thumbnail_frame, bg="#007aff" if selected else "#ffffff", padx=2, pady=2)
            holder.pack(side=tk.LEFT, padx=5)
            try:
                image = Image.open(os.path.join(self.images_dir, filename)).convert("RGB")
                image.thumbnail((112, 62), Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(image)
                self.thumbnail_refs.append(photo)
                label = tk.Label(holder, image=photo, bg="#ffffff", cursor="hand2")
            except Exception:
                label = tk.Label(holder, text=filename[:10], width=14, height=4, bg="#f2f3f5", fg="#6e7280", cursor="hand2")
            label.pack()
            label.bind("<Button-1>", lambda _event, target=index: self._load_image(target))

    # Project & queue
    def _sync_project(self):
        if self.images_dir:
            project = self.app.configure_project(self.images_dir, self.output_var.get().strip())
            if not project.categories:
                categories = core.parse_categories(self.app.auto_tab.prompt_var.get())
                if categories:
                    project.set_categories(categories)

    def _open_dir(self):
        path = filedialog.askdirectory(title="选择项目图片目录")
        if path:
            self.open_project(path, self.output_var.get().strip())

    def open_project(self, images_dir, annotations_dir):
        """Load a newly-created or existing project into the review workspace."""
        self.images_dir = images_dir
        self.output_var.set(annotations_dir)
        self.img_files = core.list_images(images_dir)
        self._sync_project()
        self._apply_queue_filter(load_first=True)

    def _browse_output(self):
        path = filedialog.askdirectory(title="选择标注目录")
        if path:
            self.output_var.set(path)
            self._sync_project()
            self._apply_queue_filter(load_first=True)

    def _manage_categories(self):
        if not self.images_dir:
            messagebox.showinfo("提示", "请先打开项目图片目录")
            return
        self._sync_project()
        def saved(categories):
            if categories:
                self.app.auto_tab.prompt_var.set(" . ".join(categories))
            self._refresh_categories()
            self._collect_cats()
            self._rebuild_vis_idx()
            self._render()
        self.app.open_category_manager(saved)

    def _apply_queue_filter(self, event=None, load_first=False):
        if not self.images_dir:
            return
        wanted = dict(self.STATUS_FILTERS).get(self.queue_filter_var.get())
        self.queue_indices = [index for index, filename in enumerate(self.img_files)
                              if wanted is None or self.app.project.status_for(filename) == wanted]
        self.listbox.delete(0, tk.END)
        for index in self.queue_indices:
            filename = self.img_files[index]
            self.listbox.insert(tk.END, f"{STATUS_LABELS[self.app.project.status_for(filename)]} · {filename}")
        if not self.queue_indices:
            self.cur_idx, self.pil_img = -1, None
            self.canvas.delete("all")
            self.box_list.delete(0, tk.END)
            self.counter_var.set("0 / 0")
            self.status_var.set("当前队列没有图片")
            self.review_progress["value"] = 0
            self._refresh_thumbnails()
            return
        target = self.queue_indices[0] if load_first or self.cur_idx not in self.queue_indices else self.cur_idx
        self._load_image(target)

    def _set_status(self, status):
        if self.cur_idx < 0:
            return
        self._save(silent=True)
        filename = self.img_files[self.cur_idx]
        self.app.project.set_status(filename, status)
        self.status_var.set(f"{filename}：{STATUS_LABELS[status]}")
        self.app.refresh_workspace()
        old = self.cur_idx
        self._apply_queue_filter()
        if self.queue_indices and old not in self.queue_indices:
            self._load_image(self.queue_indices[0])

    # Image data
    def _load_image(self, idx):
        if idx < 0 or idx >= len(self.img_files):
            return
        self._autosave_current()
        self.cur_idx = idx
        filename = self.img_files[idx]
        self.pil_img = Image.open(os.path.join(self.images_dir, filename)).convert("RGB")
        self.shapes = []
        xml_path = os.path.join(self.output_var.get().strip(), os.path.splitext(filename)[0] + ".xml")
        if os.path.isfile(xml_path):
            try:
                self.shapes = [{"name": item["name"], "bbox": item["bbox"]} for item in core.load_voc_xml(xml_path)]
            except Exception as exc:
                self.status_var.set(f"读取标注失败：{exc}")
        self.selected, self.undo_stack, self.redo_stack = None, [], []
        self._refresh_categories()
        self._collect_cats()
        self._rebuild_vis_idx()
        self._refresh_box_list()
        self._refresh_queue_selection()
        self._fit_window()
        self.after(120, self._fit_window)

    def _refresh_queue_selection(self):
        if self.cur_idx in self.queue_indices:
            pos = self.queue_indices.index(self.cur_idx)
            self.listbox.selection_clear(0, tk.END)
            self.listbox.selection_set(pos)
            self.listbox.see(pos)
            status = STATUS_LABELS[self.app.project.status_for(self.img_files[self.cur_idx])]
            self.counter_var.set(f"{pos + 1} / {len(self.queue_indices)} · {status}")
            self.header_status_var.set(f"{status} · {len(self.queue_indices)} 张队列")
            self.review_progress["value"] = (pos + 1) / len(self.queue_indices) * 100
            self._refresh_thumbnails()

    def _refresh_categories(self):
        categories = self.app.project.categories if self.images_dir else core.parse_categories(self.app.auto_tab.prompt_var.get())
        self.label_combo["values"] = categories
        if not self.label_var.get() and categories:
            self.label_var.set(categories[0])

    def _collect_cats(self):
        base = self.app.project.categories if self.images_dir else []
        self.filter_combo.set_items(base + [shape["name"] for shape in self.shapes if shape["name"] not in base])

    def _rebuild_vis_idx(self):
        visible = self.filter_combo.get_selected()
        self._vis_idx = [index for index, shape in enumerate(self.shapes) if shape["name"] in visible]
        if self.selected is not None and self.selected >= len(self._vis_idx):
            self.selected = None

    def _on_cat_filter_change(self):
        self._rebuild_vis_idx()
        self._render()
        self._refresh_box_list()

    def _refresh_box_list(self):
        self.box_list.delete(0, tk.END)
        for index in self._vis_idx:
            shape = self.shapes[index]
            self.box_list.insert(tk.END, f"{shape['name']}  [{', '.join(map(str, shape['bbox']))}]")
        if self.selected is not None and self.selected < len(self._vis_idx):
            self.box_list.selection_set(self.selected)

    # Canvas view
    def _render(self):
        self.canvas.delete("all")
        if not self.pil_img:
            return
        width, height = self.pil_img.size
        image = self.pil_img.resize((max(1, int(width * self.zoom)), max(1, int(height * self.zoom))), Image.Resampling.LANCZOS)
        self.photo = ImageTk.PhotoImage(image)
        self.canvas.create_image(self.pan_x, self.pan_y, anchor=tk.NW, image=self.photo)
        for visible_index, index in enumerate(self._vis_idx):
            shape = self.shapes[index]
            x1, y1, x2, y2 = shape["bbox"]
            x1, y1, x2, y2 = x1 * self.zoom + self.pan_x, y1 * self.zoom + self.pan_y, x2 * self.zoom + self.pan_x, y2 * self.zoom + self.pan_y
            color = "#ffb020" if visible_index == self.selected else "#32d583"
            self.canvas.create_rectangle(x1, y1, x2, y2, outline=color, width=2)
            self.canvas.create_text(x1 + 3, max(y1 - 5, 3), anchor=tk.SW, text=shape["name"], fill=color, font=("Microsoft YaHei UI", 9, "bold"))
            if visible_index == self.selected:
                for x, y in self._handles(x1, y1, x2, y2).values():
                    self.canvas.create_rectangle(x - self.HANDLE_RADIUS, y - self.HANDLE_RADIUS, x + self.HANDLE_RADIUS, y + self.HANDLE_RADIUS, fill="#ffffff", outline="#007aff")

    @staticmethod
    def _handles(x1, y1, x2, y2):
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        return {"nw": (x1, y1), "n": (mx, y1), "ne": (x2, y1), "e": (x2, my), "se": (x2, y2), "s": (mx, y2), "sw": (x1, y2), "w": (x1, my)}

    def _fit_window(self):
        if self.pil_img:
            self._manual_view = False
            cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
            cw, ch = (900, 650) if cw < 50 or ch < 50 else (cw, ch)
            width, height = self.pil_img.size
            self.zoom = min(cw / width, ch / height)
            self.pan_x, self.pan_y = int((cw - width * self.zoom) / 2), int((ch - height * self.zoom) / 2)
            self._render()

    def _actual_size(self):
        if self.pil_img:
            self._manual_view, self.zoom, self.pan_x, self.pan_y = True, 1.0, 0, 0
            self._render()

    def _canvas_to_img(self, x, y):
        return int((x - self.pan_x) / self.zoom), int((y - self.pan_y) / self.zoom)

    def _clamp_box(self, x1, y1, x2, y2):
        width, height = self.pil_img.size
        return tuple(max(0, min(int(v), limit - 1)) for v, limit in ((x1, width), (y1, height), (x2, width), (y2, height)))

    def _on_mousewheel(self, event):
        if not self.pil_img:
            return
        self._manual_view = True
        ix, iy = (event.x - self.pan_x) / self.zoom, (event.y - self.pan_y) / self.zoom
        new_zoom = max(0.02, min(self.zoom * (1.1 if event.delta > 0 else 1 / 1.1), 20.0))
        self.pan_x, self.pan_y, self.zoom = event.x - ix * new_zoom, event.y - iy * new_zoom, new_zoom
        self._render()

    def _on_pan_press(self, event):
        self.pan_start, self.pan_origin = (event.x, event.y), (self.pan_x, self.pan_y)

    def _on_pan_drag(self, event):
        if self.pan_start:
            self._manual_view = True
            self.pan_x, self.pan_y = self.pan_origin[0] + event.x - self.pan_start[0], self.pan_origin[1] + event.y - self.pan_start[1]
            self._render()

    def _on_pan_release(self, event):
        self.pan_start = None

    def _on_canvas_resize(self, event):
        if getattr(self, "_resize_job", None):
            self.after_cancel(self._resize_job)
        if self.pil_img:
            self._resize_job = self.after(120, self._render if self._manual_view else self._fit_window)

    # Box editing and history
    def _selected_actual(self):
        return self._vis_idx[self.selected] if self.selected is not None and self.selected < len(self._vis_idx) else None

    def _hit_handle(self, cx, cy):
        index = self._selected_actual()
        if index is None:
            return None
        x1, y1, x2, y2 = self.shapes[index]["bbox"]
        for name, (x, y) in self._handles(x1 * self.zoom + self.pan_x, y1 * self.zoom + self.pan_y, x2 * self.zoom + self.pan_x, y2 * self.zoom + self.pan_y).items():
            if abs(cx - x) <= self.HANDLE_RADIUS + 2 and abs(cy - y) <= self.HANDLE_RADIUS + 2:
                return name
        return None

    def _find_at(self, cx, cy):
        x, y = self._canvas_to_img(cx, cy)
        for visible_index in range(len(self._vis_idx) - 1, -1, -1):
            x1, y1, x2, y2 = self.shapes[self._vis_idx[visible_index]]["bbox"]
            if x1 <= x <= x2 and y1 <= y <= y2:
                return visible_index
        return None

    def _record(self):
        self.undo_stack.append(copy.deepcopy(self.shapes))
        if len(self.undo_stack) > self.HISTORY_LIMIT:
            self.undo_stack.pop(0)
        self.redo_stack.clear()

    def _restore(self, shapes):
        self.shapes, self.selected = copy.deepcopy(shapes), None
        self._collect_cats()
        self._rebuild_vis_idx()
        self._render()
        self._refresh_box_list()

    def _undo(self):
        if self.undo_stack:
            self.redo_stack.append(copy.deepcopy(self.shapes))
            self._restore(self.undo_stack.pop())
            self.status_var.set("已撤销上一步修改")

    def _redo(self):
        if self.redo_stack:
            self.undo_stack.append(copy.deepcopy(self.shapes))
            self._restore(self.redo_stack.pop())
            self.status_var.set("已重做上一步修改")

    def _on_press(self, event):
        if not self.pil_img:
            return
        self.canvas.focus_set()
        handle = self._hit_handle(event.x, event.y)
        hit = self._find_at(event.x, event.y)
        if handle:
            self._record()
            self.edit_mode, self.edit_handle, self.edit_origin, self.edit_start_img = "resize", handle, list(self.shapes[self._selected_actual()]["bbox"]), self._canvas_to_img(event.x, event.y)
        elif hit is not None:
            self.selected = hit
            self.label_var.set(self.shapes[self._vis_idx[hit]]["name"])
            self._record()
            self.edit_mode, self.edit_origin, self.edit_start_img = "move", list(self.shapes[self._selected_actual()]["bbox"]), self._canvas_to_img(event.x, event.y)
            self._render()
        else:
            self.selected, self.edit_mode, self.drag_start = None, "create", (event.x, event.y)
            self.rubber_id = self.canvas.create_rectangle(event.x, event.y, event.x, event.y, outline="#007aff", width=2, dash=(4, 2))
        self._refresh_box_list()

    def _on_drag(self, event):
        if self.edit_mode == "create" and self.rubber_id:
            self.canvas.coords(self.rubber_id, self.drag_start[0], self.drag_start[1], event.x, event.y)
        elif self.edit_mode in ("move", "resize"):
            self._edit_box(*self._canvas_to_img(event.x, event.y))

    def _edit_box(self, image_x, image_y):
        index = self._selected_actual()
        if index is None:
            return
        x1, y1, x2, y2 = self.edit_origin
        if self.edit_mode == "move":
            dx, dy = image_x - self.edit_start_img[0], image_y - self.edit_start_img[1]
            width, height = self.pil_img.size
            dx, dy = max(-x1, min(dx, width - 1 - x2)), max(-y1, min(dy, height - 1 - y2))
            x1, y1, x2, y2 = x1 + dx, y1 + dy, x2 + dx, y2 + dy
        else:
            image_x, image_y, _unused_x, _unused_y = self._clamp_box(image_x, image_y, image_x, image_y)
            if "w" in self.edit_handle: x1 = min(image_x, x2 - 1)
            if "e" in self.edit_handle: x2 = max(image_x, x1 + 1)
            if "n" in self.edit_handle: y1 = min(image_y, y2 - 1)
            if "s" in self.edit_handle: y2 = max(image_y, y1 + 1)
        self.shapes[index]["bbox"] = list(self._clamp_box(x1, y1, x2, y2))
        self._render()
        self._refresh_box_list()

    def _on_release(self, event):
        if self.edit_mode == "create" and self.rubber_id:
            self.canvas.delete(self.rubber_id)
            x0, y0 = self.drag_start
            if abs(event.x - x0) >= 4 or abs(event.y - y0) >= 4:
                x1, y1 = self._canvas_to_img(x0, y0)
                x2, y2 = self._canvas_to_img(event.x, event.y)
                x1, x2, y1, y2 = min(x1, x2), max(x1, x2), min(y1, y2), max(y1, y2)
                x1, y1, x2, y2 = self._clamp_box(x1, y1, x2, y2)
                if x2 > x1 and y2 > y1:
                    self._record()
                    self.shapes.append({"name": self.label_var.get().strip() or "object", "bbox": [x1, y1, x2, y2]})
                    self._collect_cats(); self._rebuild_vis_idx()
                    self.selected = self._vis_idx.index(len(self.shapes) - 1) if len(self.shapes) - 1 in self._vis_idx else None
                    self._render(); self._refresh_box_list()
        self.rubber_id = self.drag_start = self.edit_mode = self.edit_handle = self.edit_origin = self.edit_start_img = None

    def _on_box_list_select(self, event):
        selection = self.box_list.curselection()
        if selection:
            self.selected = selection[0]
            self.label_var.set(self.shapes[self._vis_idx[self.selected]]["name"])
            self._render()

    def _apply_label(self):
        index, label = self._selected_actual(), self.label_var.get().strip()
        if index is not None and label:
            self._record(); self.shapes[index]["name"] = label
            self._collect_cats(); self._rebuild_vis_idx(); self._render(); self._refresh_box_list()

    def _delete_selected(self):
        index = self._selected_actual()
        if index is not None:
            self._record(); del self.shapes[index]; self.selected = None
            self._collect_cats(); self._rebuild_vis_idx(); self._render(); self._refresh_box_list()

    def _copy_selected(self):
        index = self._selected_actual()
        if index is None:
            return
        self._record()
        source = self.shapes[index]
        x1, y1, x2, y2 = source["bbox"]
        width, height = self.pil_img.size
        dx, dy = min(12, width - 1 - x2), min(12, height - 1 - y2)
        self.shapes.append({"name": source["name"], "bbox": [x1 + dx, y1 + dy, x2 + dx, y2 + dy]})
        self._collect_cats(); self._rebuild_vis_idx()
        self.selected = self._vis_idx.index(len(self.shapes) - 1) if len(self.shapes) - 1 in self._vis_idx else None
        self._render(); self._refresh_box_list()

    # Save, reannotate, navigation
    def _autosave_current(self):
        if self.cur_idx >= 0 and self.pil_img and self.images_dir:
            self._save(silent=True)

    def _save(self, silent=False):
        if self.cur_idx < 0 or not self.pil_img:
            return
        output_dir = self.output_var.get().strip()
        if not output_dir:
            if not silent: messagebox.showerror("错误", "请先设置标注目录")
            return
        self._sync_project()
        self.app.project.set_categories(self.app.project.categories + [shape["name"] for shape in self.shapes])
        core.save_as_voc_xml(self.shapes, os.path.join(self.images_dir, self.img_files[self.cur_idx]), output_dir)
        if not silent: self.status_var.set(f"已保存 {len(self.shapes)} 个目标")

    def _reannotate(self):
        if self.cur_idx < 0 or not self.pil_img:
            return
        prompt = self.app.auto_tab.prompt_var.get().strip()
        try:
            box_threshold, text_threshold = float(self.app.auto_tab.box_thr_var.get()), float(self.app.auto_tab.text_thr_var.get())
        except ValueError:
            messagebox.showerror("参数错误", "请在自动标注页填写正确的阈值")
            return
        if not prompt:
            messagebox.showerror("参数错误", "提示词不能为空")
            return
        path = os.path.join(self.images_dir, self.img_files[self.cur_idx])
        self.status_var.set("正在重新标注…")
        def worker():
            try:
                annotations = core.auto_label(path, prompt, self.app.ensure_model(), box_threshold, text_threshold)
                self.after(0, lambda: self._apply_reannotate(annotations))
            except Exception as exc:
                self.after(0, lambda: self.status_var.set(f"标注失败：{exc}"))
        threading.Thread(target=worker, daemon=True).start()

    def _apply_reannotate(self, annotations):
        self._record()
        self.shapes, self.selected = [{"name": item["name"], "bbox": item["bbox"]} for item in annotations], None
        self.app.project.set_status(self.img_files[self.cur_idx], STATUS_NEEDS_REVIEW)
        self._collect_cats(); self._rebuild_vis_idx(); self._render(); self._refresh_box_list()
        self.status_var.set(f"重新标注完成，共 {len(self.shapes)} 个目标")

    def _prev(self):
        if self.cur_idx in self.queue_indices:
            self._load_image(self.queue_indices[max(0, self.queue_indices.index(self.cur_idx) - 1)])

    def _next(self):
        if self.cur_idx in self.queue_indices:
            self._load_image(self.queue_indices[min(len(self.queue_indices) - 1, self.queue_indices.index(self.cur_idx) + 1)])

    def _on_list_select(self, event):
        selection = self.listbox.curselection()
        if selection:
            target = self.queue_indices[selection[0]]
            if target != self.cur_idx:
                self._load_image(target)
