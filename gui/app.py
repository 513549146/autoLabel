import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import config
import core
from project import ProjectStore, STATUS_APPROVED, STATUS_NEEDS_REVIEW

from gui.auto_tab import AutoTab
from gui.review_tab import ReviewTab
from gui.finetune_tab import FineTuneTab
from gui.approved_tab import ApprovedTab
from gui.icon_assets import nav_icon
from gui.ios_widgets import IOSButton, IOSNavigationItem


DETECTOR_OPTIONS = [
    ("gd15", "Grounding DINO 1.5（推荐）"),
    ("gd_ogc", "Grounding DINO（旧版）"),
    ("yolo", "YOLO（微调）"),
]


class AutoLabelApp:
    def __init__(self, root):
        self.root = root
        self.root.title(" ")
        self.root.geometry("1150x720")
        # Keep the native caption controls (minimize / maximize / close).  The
        # default Tk feather is removed after the first native frame is drawn.
        self.root.overrideredirect(False)
        self._nav_icon_refs = []
        self.project_title_var = tk.StringVar(value="未创建项目")
        self.project_meta_var = tk.StringVar(value="新建项目后开始工作流")
        self.detector = None
        self.detector_backend = config.DETECTOR
        self.model_lock = threading.Lock()
        self.project = ProjectStore(config.OUTPUT_DIR, config.INPUT_DIR)

        self._setup_style()
        self._build_shell()
        # Tk completes native-window initialization asynchronously.  Clear the
        # fallback class icon once immediately after mapping and once after its
        # final theme pass, while preserving the normal Windows caption buttons.
        self.root.after(120, self._hide_caption_icon)
        self.root.after(700, self._hide_caption_icon)

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _hide_caption_icon(self):
        """Hide Tk's caption icon on Windows without removing system controls."""
        if os.name != "nt":
            return
        try:
            import ctypes

            user32 = ctypes.windll.user32
            hwnd = self.root.winfo_id()
            # Tk can fall back to the icon registered on its Windows *class*,
            # even when the window-level icon was cleared.  Remove both levels;
            # this keeps the native minimize, maximize and close controls.
            WM_SETICON = 0x0080
            ICON_SMALL = 0
            ICON_BIG = 1
            GCLP_HICON = -14
            GCLP_HICONSM = -34
            set_class_long_ptr = user32.SetClassLongPtrW
            set_class_long_ptr.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p)
            set_class_long_ptr.restype = ctypes.c_void_p
            set_class_long_ptr(hwnd, GCLP_HICON, None)
            set_class_long_ptr(hwnd, GCLP_HICONSM, None)
            user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, 0)
            user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, 0)
            ex_style = user32.GetWindowLongW(hwnd, -20)  # GWL_EXSTYLE
            user32.SetWindowLongW(hwnd, -20, ex_style | 0x0001)  # WS_EX_DLGMODALFRAME
            user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0027)  # FRAMECHANGED | NOMOVE | NOSIZE | NOZORDER
        except (AttributeError, OSError, tk.TclError):
            # Native controls remain available if a platform refuses this hint.
            pass

    def _setup_style(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        self.root.configure(bg="#f5f6f8")
        style.configure(".", background="#f5f6f8", foreground="#1c1c1e", font=("Microsoft YaHei UI", 10))
        style.configure("TFrame", background="#f5f6f8")
        style.configure("Card.TFrame", background="#ffffff")
        style.configure("TLabel", background="#f5f6f8", foreground="#1c1c1e")
        style.configure("Muted.TLabel", foreground="#6e7280")
        style.configure("TLabelframe", background="#ffffff", foreground="#6e7280", bordercolor="#e5e5ea", relief="solid")
        style.configure("TLabelframe.Label", background="#ffffff", foreground="#6e7280", font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("TButton", background="#ffffff", foreground="#1c1c1e", bordercolor="#e1e3e8", padding=(14, 9), font=("Microsoft YaHei UI", 10, "bold"))
        style.map("TButton", background=[("active", "#f0f6ff"), ("pressed", "#e4f1ff")])
        style.configure("Accent.TButton", background="#007aff", foreground="#ffffff", bordercolor="#007aff", padding=(18, 10), font=("Microsoft YaHei UI", 10, "bold"))
        style.map("Accent.TButton", background=[("active", "#268dff"), ("pressed", "#0068d9")])
        style.configure("Browse.TButton", padding=(16, 5), font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("TEntry", fieldbackground="#ffffff", foreground="#1c1c1e", bordercolor="#d9dce3", insertcolor="#1c1c1e", padding=(10, 8))
        style.configure("TCombobox", fieldbackground="#ffffff", background="#ffffff", foreground="#1c1c1e", arrowcolor="#4d5360", bordercolor="#d9dce3", padding=(10, 7))
        style.map("TCombobox", fieldbackground=[("readonly", "#ffffff")], foreground=[("readonly", "#1c1c1e")])
        style.configure("TProgressbar", background="#007aff", troughcolor="#e9edf2", bordercolor="#e9edf2")
        style.configure(
            "App.TNotebook.Tab",
            font=("Microsoft YaHei UI", 13, "bold"),
            padding=(34, 12),
            background="#ffffff",
            foreground="#6e7280",
        )
        style.map(
            "App.TNotebook.Tab",
            background=[("selected", "#f5f6f8")],
            foreground=[("selected", "#007aff"), ("!selected", "#6e7280")],
        )

    def _build_shell(self):
        shell = ttk.Frame(self.root)
        shell.pack(fill=tk.BOTH, expand=True)
        sidebar = tk.Frame(shell, bg="#ffffff", width=216, highlightthickness=0)
        sidebar.pack(side=tk.LEFT, fill=tk.Y)
        sidebar.pack_propagate(False)
        sidebar_header = tk.Frame(sidebar, bg="#ffffff")
        sidebar_header.pack(fill=tk.X, padx=25, pady=(24, 14))
        ttk.Label(sidebar_header, text="项目", background="#ffffff", foreground="#1c1c1e", font=("Microsoft YaHei UI", 15, "bold")).pack(side=tk.LEFT)
        project_menu = tk.Label(sidebar_header, text="•••", bg="#ffffff", fg="#63666f", cursor="hand2", font=("Segoe UI", 11, "bold"))
        project_menu.pack(side=tk.RIGHT, pady=(2, 0))
        project_menu.bind("<Button-1>", self._show_project_menu)
        # Project creation is intentionally one global action, not a button
        # repeated inside each stage of the workflow.
        self.content = ttk.Frame(shell)
        self.content.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.auto_tab = AutoTab(self.content, self)
        self.review_tab = ReviewTab(self.content, self)
        self.approved_tab = ApprovedTab(self.content, self)
        self.finetune_tab = FineTuneTab(self.content, self)
        self.pages = {"auto": self.auto_tab, "review": self.review_tab, "approved": self.approved_tab, "finetune": self.finetune_tab}
        self.nav_buttons = {}
        self.nav_label_widgets = {}
        for key, label, icon_name in (("auto", "自动标注", "auto"), ("review", "待审核", "review"),
                                      ("approved", "已通过", "approved")):
            self.nav_buttons[key] = self._make_nav_item(sidebar, key, label, icon_name)
        tk.Frame(sidebar, bg="#e5e5ea", height=1).pack(fill=tk.X, padx=24, pady=18)
        self.nav_buttons["finetune"] = self._make_nav_item(sidebar, "finetune", "训练集导出", "settings")
        tk.Label(sidebar, text="本地优先 · 数据可控", bg="#ffffff", fg="#a1a4ad", font=("Microsoft YaHei UI", 9)).pack(side=tk.BOTTOM, anchor=tk.W, padx=25, pady=24)
        self.refresh_workspace()
        self.show_page("auto")

    def _make_nav_item(self, parent, key, label, icon_name):
        image = nav_icon(icon_name, 19)
        self._nav_icon_refs.append(image)
        item = IOSNavigationItem(parent, text=label, image=image, command=lambda page=key: self.show_page(page))
        item.pack(fill=tk.X, padx=12, pady=2)
        self.nav_label_widgets[key] = item
        return item

    def _show_project_menu(self, event):
        menu = tk.Menu(self.root, tearoff=False, bg="#ffffff", fg="#1c1c1e", activebackground="#eaf3ff",
                       activeforeground="#007aff", relief=tk.FLAT, bd=0, font=("Microsoft YaHei UI", 10))
        menu.add_command(label="新建项目", command=self.new_project)
        menu.add_command(label="打开项目", command=self.open_project)
        menu.tk_popup(event.x_root, event.y_root + 4)

    def _toggle_maximize(self):
        current = self.root.state()
        self.root.state("normal" if current == "zoomed" else "zoomed")

    def new_project(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("新建项目")
        dialog.configure(bg="#ffffff")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)
        frame = ttk.Frame(dialog, padding=22, style="Card.TFrame")
        frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(frame, text="新建项目", font=("Microsoft YaHei UI", 16, "bold"), background="#ffffff").pack(anchor=tk.W)
        ttk.Label(frame, text="会创建 images、annotations 和 yolo_dataset 三个目录。", style="Muted.TLabel", background="#ffffff").pack(anchor=tk.W, pady=(4, 16))
        name_var = tk.StringVar()
        parent_var = tk.StringVar(value=config.BASE_DIR)
        ttk.Label(frame, text="项目名称", background="#ffffff").pack(anchor=tk.W)
        name_entry = ttk.Entry(frame, textvariable=name_var, width=38)
        name_entry.pack(fill=tk.X, pady=(4, 12))
        ttk.Label(frame, text="保存位置", background="#ffffff").pack(anchor=tk.W)
        location = ttk.Frame(frame, style="Card.TFrame")
        location.pack(fill=tk.X, pady=(4, 16))
        ttk.Entry(location, textvariable=parent_var).pack(side=tk.LEFT, fill=tk.X, expand=True)
        IOSButton(location, text="选择", command=lambda: self._choose_project_parent(parent_var), width=72, height=36, bg="#ffffff").pack(side=tk.LEFT, padx=(6, 0))

        def create():
            name = name_var.get().strip().strip(". ")
            parent = parent_var.get().strip()
            if not name or any(char in name for char in '<>:"/\\|?*'):
                messagebox.showerror("项目名称", "请输入有效的项目名称")
                return
            if not os.path.isdir(parent):
                messagebox.showerror("保存位置", "请选择已有的保存位置")
                return
            project_root = os.path.abspath(os.path.join(parent, name))
            if os.path.exists(project_root):
                messagebox.showerror("项目已存在", f"该目录已存在：{project_root}")
                return
            images_dir = os.path.join(project_root, "images")
            annotations_dir = os.path.join(project_root, "annotations")
            yolo_dir = os.path.join(project_root, "yolo_dataset")
            os.makedirs(images_dir)
            os.makedirs(annotations_dir)
            os.makedirs(yolo_dir)
            self.project = ProjectStore(annotations_dir, images_dir)
            self.project.data["name"] = name
            self.project.data["project_root"] = project_root
            self.project.save()
            self.auto_tab.input_var.set(images_dir)
            self.auto_tab.output_var.set(annotations_dir)
            self.review_tab.open_project(images_dir, annotations_dir)
            self.approved_tab.open_project(images_dir)
            self.finetune_tab.exp_images_var.set(images_dir)
            self.finetune_tab.exp_ann_var.set(annotations_dir)
            self.finetune_tab.exp_out_var.set(yolo_dir)
            self.finetune_tab.train_data_var.set(os.path.join(yolo_dir, "dataset.yaml"))
            self.project_title_var.set(name)
            self.project_meta_var.set("自动标注 → 审核 → 已通过")
            self.refresh_workspace()
            self.show_page("auto")
            dialog.destroy()

        IOSButton(frame, text="创建项目", command=create, kind="primary", width=108, height=40, bg="#ffffff").pack(side=tk.RIGHT)
        IOSButton(frame, text="取消", command=dialog.destroy, width=76, height=40, bg="#ffffff").pack(side=tk.RIGHT, padx=6)
        name_entry.focus_set()

    def open_project(self):
        """Activate an existing project root and restore its three-stage flow."""
        project_root = filedialog.askdirectory(title="选择项目文件夹")
        if not project_root:
            return
        images_dir = os.path.join(project_root, "images")
        annotations_dir = os.path.join(project_root, "annotations")
        if not os.path.isdir(images_dir):
            messagebox.showerror("无法打开项目", "请选择包含 images 文件夹的项目目录")
            return
        os.makedirs(annotations_dir, exist_ok=True)
        yolo_dir = os.path.join(project_root, "yolo_dataset")
        os.makedirs(yolo_dir, exist_ok=True)
        self.project = ProjectStore(annotations_dir, images_dir)
        self.project.data.setdefault("name", os.path.basename(project_root))
        self.project.data["project_root"] = project_root
        self.project.save()
        self.auto_tab.input_var.set(images_dir)
        self.auto_tab.output_var.set(annotations_dir)
        self.review_tab.open_project(images_dir, annotations_dir)
        self.approved_tab.open_project(images_dir)
        self.finetune_tab.exp_images_var.set(images_dir)
        self.finetune_tab.exp_ann_var.set(annotations_dir)
        self.finetune_tab.exp_out_var.set(yolo_dir)
        self.finetune_tab.train_data_var.set(os.path.join(yolo_dir, "dataset.yaml"))
        self.project_title_var.set(self.project.data.get("name") or os.path.basename(project_root))
        self.project_meta_var.set("自动标注 → 审核 → 已通过")
        self.refresh_workspace()
        self.show_page("auto")

    @staticmethod
    def _choose_project_parent(variable):
        path = filedialog.askdirectory(title="选择项目保存位置")
        if path:
            variable.set(path)

    def show_page(self, page):
        if page == "review" and self.review_tab.images_dir:
            desired_filter = "待审核"
            if self.review_tab.queue_filter_var.get() != desired_filter:
                self.review_tab.queue_filter_var.set(desired_filter)
                self.review_tab._apply_queue_filter(load_first=True)
        for key, frame in self.pages.items():
            frame.pack_forget()
        for key, item in self.nav_buttons.items():
            item.set_active(key == page)
        self.pages[page].pack(fill=tk.BOTH, expand=True)

    def refresh_workspace(self):
        """Keep workflow counts meaningful instead of decorative sidebar badges."""
        filenames = self.review_tab.img_files if hasattr(self, "review_tab") else []
        summary = self.project.summary(filenames) if filenames else {STATUS_NEEDS_REVIEW: 0, STATUS_APPROVED: 0}
        counts = {
            "auto": f"{len(filenames)} 张" if filenames else "",
            "review": str(summary.get(STATUS_NEEDS_REVIEW, 0)) if summary.get(STATUS_NEEDS_REVIEW, 0) else "",
            "approved": str(summary.get(STATUS_APPROVED, 0)) if summary.get(STATUS_APPROVED, 0) else "",
            "finetune": "",
        }
        for key, count in counts.items():
            if key in self.nav_label_widgets:
                self.nav_label_widgets[key].set_count(count)
        if hasattr(self, "approved_tab"):
            self.approved_tab.refresh()

    def configure_project(self, images_dir, annotations_dir):
        """Switch local project metadata when the working folders change."""
        images_dir = images_dir or config.INPUT_DIR
        annotations_dir = annotations_dir or config.OUTPUT_DIR
        if (os.path.abspath(annotations_dir) != self.project.annotations_dir or
                os.path.abspath(images_dir) != self.project.images_dir):
            self.project = ProjectStore(annotations_dir, images_dir)
        self.refresh_workspace()
        return self.project

    def open_category_manager(self, on_saved=None):
        dialog = tk.Toplevel(self.root)
        dialog.title("项目类别管理")
        dialog.configure(bg="#ffffff")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)

        frame = ttk.Frame(dialog, padding=16, style="Card.TFrame")
        frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(frame, text="项目类别", font=("Microsoft YaHei UI", 13, "bold")).pack(anchor=tk.W)
        ttk.Label(frame, text="类别会用于审核、导出和下一次预标注提示词。", style="Muted.TLabel").pack(anchor=tk.W, pady=(2, 10))
        listbox = tk.Listbox(frame, width=34, height=10, bg="#ffffff", fg="#1c1c1e", selectbackground="#007aff", selectforeground="#ffffff", relief=tk.FLAT, highlightthickness=1, highlightbackground="#d9dce3")
        listbox.pack(fill=tk.X)
        for category in self.project.categories:
            listbox.insert(tk.END, category)
        entry_var = tk.StringVar()
        entry = ttk.Entry(frame, textvariable=entry_var)
        entry.pack(fill=tk.X, pady=(10, 6))

        def add_category(event=None):
            name = entry_var.get().strip()
            existing = list(listbox.get(0, tk.END))
            if name and name not in existing:
                listbox.insert(tk.END, name)
            entry_var.set("")

        def remove_category():
            selected = listbox.curselection()
            for index in reversed(selected):
                listbox.delete(index)

        def save_categories():
            categories = list(listbox.get(0, tk.END))
            self.project.set_categories(categories)
            if on_saved:
                on_saved(self.project.categories)
            dialog.destroy()

        entry.bind("<Return>", add_category)
        buttons = ttk.Frame(frame, style="Card.TFrame")
        buttons.pack(fill=tk.X)
        IOSButton(buttons, text="添加", command=add_category, width=70, height=36, bg="#ffffff").pack(side=tk.LEFT)
        IOSButton(buttons, text="删除选中", command=remove_category, width=88, height=36, bg="#ffffff").pack(side=tk.LEFT, padx=6)
        IOSButton(buttons, text="保存类别", command=save_categories, kind="primary", width=92, height=36, bg="#ffffff").pack(side=tk.RIGHT)
        entry.focus_set()

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
