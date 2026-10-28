"""主窗口：组合各页面，并管理项目切换与跨页刷新。"""

from __future__ import annotations

import gc
import sqlite3
import stat
import time
from xml.etree.ElementTree import ParseError

from .ui import *
from .canvas import ImageCanvas
from .pages import AutoPage, ExportPage, LibraryPage, ModelManagerPage, ReviewPage, WelcomePage, WorkflowPage


class Workbench(QMainWindow):
    """桌面工作台入口；具体标注、审核和导出操作由各页面执行。"""

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setWindowTitle(" ")
        self.setWindowIcon(QIcon())
        self.resize(1420, 900)
        self.setMinimumSize(1100, 720)
        self.workspace = WorkspaceStore()
        self.project: ProjectStore | None = None
        self.project_root = ""
        self.project_name = ""
        self.images_dir = ""
        self.annotations_dir = ""
        self.active_workflow_id = "quick"
        # 先建立窗口外壳和导航，再创建页面；页面构造时会使用这里的项目状态。
        root = ResizableRoot(self)
        root.setObjectName("root")
        self.setCentralWidget(root)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(7, 7, 7, 7)
        root_layout.setSpacing(0)
        root_layout.addWidget(WindowChrome(self))
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        root_layout.addLayout(layout, 1)
        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(264)
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(16, 24, 16, 20)
        side_layout.setSpacing(6)
        title = QHBoxLayout()
        title.addWidget(label("项目", "section"))
        title.addStretch()
        more = QToolButton()
        more.setText("•••")
        more.setCursor(Qt.PointingHandCursor)
        more.setStyleSheet("border:0;font-weight:700;font-size:14px;color:#646873;")
        more.clicked.connect(self.project_menu)
        title.addWidget(more)
        side_layout.addLayout(title)
        side_layout.addSpacing(10)
        self.sidebar = side
        self.project_card = QFrame()
        self.project_card.setObjectName("projectCard")
        project_layout = QVBoxLayout(self.project_card)
        project_layout.setContentsMargins(12, 11, 12, 11)
        project_layout.setSpacing(5)
        project_layout.addWidget(label("当前工作区", "projectEyebrow"))
        self.current_project = label("还未打开项目", "projectName")
        self.current_project.setWordWrap(False)
        project_layout.addWidget(self.current_project)
        self.current_project_path = label("新建或打开一个项目开始", "projectPath")
        self.current_project_path.setWordWrap(False)
        project_layout.addWidget(self.current_project_path)
        project_footer = QHBoxLayout()
        project_footer.setContentsMargins(0, 2, 0, 0)
        self.project_status = label("等待选择项目", "projectStatus")
        project_footer.addWidget(self.project_status)
        project_footer.addStretch()
        project_layout.addLayout(project_footer)
        self.project_workflow = QToolButton(self.project_card)
        self.project_workflow.setObjectName("projectWorkflow")
        self.project_workflow.setText("当前工作流 · 快速标注  ›")
        self.project_workflow.setCursor(Qt.PointingHandCursor)
        self.project_workflow.setToolTip("打开当前项目的工作流配置")
        self.project_workflow.clicked.connect(self.open_workflow)
        self.project_workflow.setEnabled(False)
        project_layout.addWidget(self.project_workflow)
        side_layout.addWidget(self.project_card)
        side_layout.addSpacing(8)
        self.recent_title = label("最近项目", "muted")
        side_layout.addWidget(self.recent_title)
        self.recent_host = QWidget()
        self.recent_host.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self.recent_layout = FlowLayout(self.recent_host)
        side_layout.addWidget(self.recent_host)
        side_layout.addSpacing(12)
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self.nav = {}
        for key, title, icon in (
            ("auto", "自动标注", sprite_icon("auto", nav=True)),
            ("review", "待审核", sprite_icon("review", nav=True)),
            ("approved", "已通过", sprite_icon("approved", nav=True)),
            ("export", "模型微调", sprite_icon("export", nav=True)),
        ):
            item = SidebarItem(title, icon, self)
            item.clicked.connect(lambda checked=False, page=key: self.show_page(page))
            self.nav_group.addButton(item)
            side_layout.addWidget(item)
            self.nav[key] = item
        side_layout.addStretch()
        side_layout.addWidget(label("本地优先 · 数据可控", "muted"))
        layout.addWidget(side)
        # 页面实例只创建一次，切换时由 QStackedWidget 复用。
        self.pages = QStackedWidget()
        layout.addWidget(self.pages, 1)
        self.welcome = WelcomePage(self)
        self.auto = AutoPage(self)
        self.model_manager = ModelManagerPage(self)
        self.workflow = WorkflowPage(self)
        self.workflow.workflow_changed.connect(self._workflow_changed)
        self.review = ReviewPage(self)
        self.library = LibraryPage(self)
        self.export = ExportPage(self)
        self.keys = {
            "welcome": self.welcome,
            "auto": self.auto,
            "model_manager": self.model_manager,
            "workflow": self.workflow,
            "review": self.review,
            "approved": self.library,
            "export": self.export,
        }
        for page in self.keys.values():
            self.pages.addWidget(page)
        # 页面就绪后刷新项目入口，最后异步恢复上次项目。
        root.track_descendants()
        self.workflow.sync_project()
        self.auto.sync_project()
        self.refresh_recent_projects()
        self.show_page("welcome")
        QTimer.singleShot(0, self.restore_last_project)

    def project_menu(self):
        menu = QMenu(self)
        menu.addAction("新建项目", self.new_project)
        menu.addAction("导入 / 打开项目", self.open_project)
        if self.project_root:
            menu.addSeparator()
            menu.addAction("工作流配置", self.open_workflow)
            menu.addAction("关闭当前项目", self.close_project)
            menu.addAction("从最近项目中移除", lambda: self.remove_project_from_workspace(self.project_root))
            menu.addAction("删除当前项目及全部数据…", lambda: self.delete_project(self.project_root))
        recent = self.workspace.recent_projects()
        if recent:
            recent_menu = menu.addMenu("最近项目")
            for path, name in recent:
                action = recent_menu.addAction(name)
                action.setToolTip(path)
                action.triggered.connect(lambda checked=False, target=path: self.load_project(target))
        menu.addSeparator()
        menu.addAction("返回首页", self.return_home)
        menu.exec(self.cursor().pos())

    def return_home(self):
        """关闭当前项目在界面中的会话，返回项目首页。"""
        if self.project_root:
            self.close_project()
        else:
            self.show_page("welcome")

    def restore_last_project(self):
        path = self.workspace.last_project()
        if path:
            self.load_project(path, restoring=True)

    def refresh_recent_projects(self):
        """在侧边栏同步最近项目入口。"""
        while self.recent_layout.count():
            item = self.recent_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        recents = self.workspace.recent_projects()
        self.recent_title.setVisible(bool(recents))
        self.recent_host.setVisible(bool(recents))
        for path, name in recents[:5]:
            item = QToolButton(self.recent_host)
            item.setObjectName("recentProject")
            item.setText(name)
            item.setToolTip(path)
            item.setToolButtonStyle(Qt.ToolButtonTextOnly)
            item.setCheckable(True)
            item.setChecked(os.path.normcase(os.path.abspath(path)) == os.path.normcase(self.project_root))
            item.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
            item.setMinimumHeight(32)
            item.setFixedWidth(min(232, max(72, item.sizeHint().width())))
            item.setCursor(Qt.PointingHandCursor)
            item.clicked.connect(lambda checked=False, target=path: self.load_project(target))
            self.recent_layout.addWidget(item)
        if hasattr(self, "welcome"):
            self.welcome.refresh()

    def remove_project_from_workspace(self, project_root: str):
        """Forget a launcher entry but keep all project data intact."""
        root = os.path.abspath(project_root)
        self.workspace.forget_project(root)
        if os.path.normcase(root) == os.path.normcase(self.project_root):
            self.close_project(update_workspace=False)
        self.refresh_recent_projects()
        self.status_message("已从最近项目中移除；项目文件仍保留在原位置")

    def delete_project(self, project_root: str):
        """Permanently delete a valid project only after an explicit name check."""
        root = os.path.abspath(project_root)
        manifest = load_project_manifest(root)
        app_root = os.path.abspath(config.BASE_DIR)
        if not manifest or not os.path.isdir(root) or os.path.normcase(root) == os.path.normcase(app_root):
            ios_alert(
                self,
                "无法删除项目",
                "为保护应用和普通文件夹，只能删除带 autolabel.project.json 的独立项目目录。",
                confirm_text="知道了",
                destructive=True,
            )
            return
        name = str(manifest.get("name") or Path(root).name)
        if not ios_alert(
            self,
            "删除项目及全部数据",
            f"将永久删除项目“{name}”及其中所有图片、XML 标注、模型、版本和训练集。\n\n此操作无法撤销。",
            "删除项目",
            "取消",
            destructive=True,
        ):
            return
        typed, ok = ios_text_input(
            self, "确认删除项目", f"请输入项目名称“{name}”以确认删除：", placeholder=name, confirm_text="确认删除"
        )
        if not ok or typed.strip() != name:
            self.status_message("项目名称不匹配，已取消删除")
            return
        active_tasks = self._active_project_tasks()
        if active_tasks:
            ios_alert(
                self,
                "暂时无法删除项目",
                f"当前仍在进行：{'、'.join(active_tasks)}。\n\n请先停止或等待任务完成后再删除项目，避免后台任务继续写入已删除的目录。",
                confirm_text="知道了",
                destructive=True,
            )
            return
        is_current = os.path.normcase(root) == os.path.normcase(self.project_root)
        if is_current:
            self.close_project(update_workspace=False)
        # All ProjectStore calls are short-lived, but proactively drop references
        # and process deferred Qt clean-up before Windows releases files such as
        # autolabel.db.  This also makes a retry below effective after a just-ended
        # background operation.
        self.project = None
        gc.collect()
        QApplication.processEvents()
        try:
            self._remove_project_tree(root)
        except OSError as error:
            ios_alert(
                self,
                "删除失败",
                f"无法删除项目目录：\n{error}\n\n文件可能仍被其他程序占用。请关闭打开该项目文件夹、图片或数据库的程序后重试。",
                confirm_text="知道了",
                destructive=True,
            )
            return
        self.workspace.forget_project(root)
        self.refresh_recent_projects()
        self.status_message(f"已删除项目：{name}")

    def _active_project_tasks(self):
        """Return work that could still be reading or writing a project tree."""
        active = []
        if self.auto._worker_thread and self.auto._worker_thread.is_alive():
            active.append("自动标注")
        if self.export._worker_thread and self.export._worker_thread.is_alive():
            active.append("数据集导出或模型训练")
        return active

    @staticmethod
    def _remove_project_tree(root: str):
        """Remove a confirmed project despite transient Windows file locks.

        SQLite connections and Explorer/antivirus scans can retain a just-closed
        ``autolabel.db`` handle briefly.  Read-only files are made writable; a
        genuine persistent lock still surfaces to the user after bounded retries.
        """

        def retry_readonly(func, path, exc_info):
            error = exc_info[1]
            if isinstance(error, PermissionError):
                try:
                    os.chmod(path, stat.S_IWRITE)
                    func(path)
                    return
                except OSError:
                    pass
            raise error

        last_error = None
        for delay in (0, 0.15, 0.35, 0.75, 1.5):
            if delay:
                time.sleep(delay)
            try:
                shutil.rmtree(root, onerror=retry_readonly)
                return
            except OSError as error:
                last_error = error
                if not os.path.exists(root):
                    return
        raise last_error

    def close_project(self, update_workspace=True):
        """Detach the current workspace without deleting any on-disk project files."""
        closing_root = self.project_root
        if closing_root:
            self.review.flush_autosave()
        self.review.clear_project_context()
        self.project = None
        self.project_root = ""
        self.project_name = ""
        self.images_dir = ""
        self.annotations_dir = ""
        self.workflow.sync_project()
        self.auto.sync_project()
        self.export.sync_project("")
        self._render_current_project()
        self.show_page("welcome")
        self.refresh_all()
        self.refresh_recent_projects()
        if update_workspace:
            self.workspace.clear_last_project(closing_root)
            self.status_message("已关闭项目；文件仍保留在原位置")

    def toggle_maximized(self):
        self.showNormal() if self.isMaximized() else self.showMaximized()

    def new_project(self):
        name, ok = ios_text_input(
            self, "新建项目", "项目名称：", placeholder="例如：街景行人标注", confirm_text="下一步"
        )
        if not ok or not name.strip():
            return
        parent = QFileDialog.getExistingDirectory(self, "选择项目保存位置", config.BASE_DIR)
        if not parent:
            return
        root = os.path.join(parent, name.strip())
        if os.path.exists(root):
            self.status_message("项目目录已存在")
            return
        for folder in ("images", "annotations", "annotation_versions", "models", "yolo_dataset"):
            os.makedirs(os.path.join(root, folder), exist_ok=True)
        create_project_manifest(root, name.strip())
        self.load_project(root, open_review=False)

    def open_project(self):
        root = QFileDialog.getExistingDirectory(self, "导入 / 打开项目", config.BASE_DIR)
        if root:
            self.load_project(root)

    def load_project(self, root: str, open_review=True, restoring=False):
        root = os.path.abspath(root)
        try:
            manifest = load_project_manifest(root)
            if not manifest:
                # Adopt the original folder convention once, then retain an explicit
                # project file so all future opens restore the exact same workspace.
                if not os.path.isdir(os.path.join(root, "images")):
                    raise ValueError("项目目录不存在，或缺少 autolabel.project.json 和 images 文件夹")
                manifest = create_project_manifest(root, Path(root).name)
            directories = manifest["directories"]
            images = resolve_project_directory(root, directories.get("images", "images"))
            annotations = resolve_project_directory(root, directories.get("annotations", "annotations"))
            models = resolve_project_directory(root, directories.get("models", "models"))
            versions = resolve_project_directory(root, directories.get("annotation_versions", ANNOTATION_VERSION_DIR))
            dataset = resolve_project_directory(root, directories.get("dataset", "yolo_dataset"))
            if not os.path.isdir(images):
                raise ValueError("项目图片目录不存在")
            for directory in (annotations, models, versions, dataset):
                os.makedirs(directory, exist_ok=True)
            imported = self.use_folders(images, annotations, root)
        except (OSError, sqlite3.Error, KeyError, TypeError, ValueError) as error:
            self._project_open_failed(root, error, restoring)
            return False
        self.project_name = str(manifest.get("name") or Path(root).name)
        image_count = len(core.list_images(images))
        self._render_current_project()
        self.export.sync_project(root)
        self.workspace.remember_project(root, self.project_name)
        self.refresh_recent_projects()
        self.workflow.sync_project(root)
        self.auto.sync_project(root, self.project_name)
        self.status_message(f"已打开项目：{self.project_name} · 新导入 {imported} 张待审核")
        # Opening an existing project may lead directly to review, but an empty
        # or unsuccessfully imported image directory has nothing to review.
        # Keep the user on the import-friendly auto-label page in that case.
        self.show_page("review" if open_review and image_count else "auto")
        return True

    def _project_open_failed(self, root, error, restoring=False):
        if restoring:
            # Keep the recent entry, but do not retry the same broken project on
            # every launch unless the user explicitly opens it again.
            self.workspace.clear_last_project(root)
        message = str(error).strip() or type(error).__name__
        if isinstance(error, (PermissionError, sqlite3.OperationalError)) and any(
            word in message.lower() for word in ("readonly", "read-only", "permission", "unable to open", "access")
        ):
            reason = "项目数据库或目录没有写入权限。"
        else:
            reason = f"打开项目时遇到错误：{message}"
        choice = self._project_recovery_dialog(root, reason)
        self.status_message(f"未能打开项目：{Path(root).name}")
        if choice == "choose":
            QTimer.singleShot(0, self.open_project)
        elif choice == "view":
            self._show_read_only_project(root)
        elif choice == "forget":
            self.workspace.forget_project(root)
            self.refresh_recent_projects()

    def _project_recovery_dialog(self, root, reason):
        dialog = QDialog(self)
        dialog.setObjectName("iosDialog")
        dialog.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        dialog.setModal(True)
        dialog.setAttribute(Qt.WA_TranslucentBackground)
        dialog.setMinimumWidth(460)
        outer = QVBoxLayout(dialog)
        outer.setContentsMargins(14, 14, 14, 14)
        panel = QFrame()
        panel.setObjectName("iosDialogPanel")
        outer.addWidget(panel)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(28, 25, 28, 20)
        layout.setSpacing(10)
        title = label("无法打开项目", "iosDialogTitle")
        title.setAlignment(Qt.AlignHCenter)
        layout.addWidget(title)
        detail = label(f"{reason}\n\n{root}\n\n项目文件没有被删除。", "iosDialogMessage")
        detail.setAlignment(Qt.AlignHCenter)
        detail.setWordWrap(True)
        layout.addWidget(detail)
        layout.addSpacing(8)
        for caption, code, primary in (
            ("选择其他项目", 1, True),
            ("只读查看图片与标注", 2, False),
            ("从最近项目中移除", 3, False),
            ("继续当前项目" if self.project_root else "返回首页", 0, False),
        ):
            action = QPushButton(caption)
            action.setObjectName("iosDialogPrimary" if primary else "iosDialogSecondary")
            action.clicked.connect(lambda checked=False, value=code: dialog.done(value))
            layout.addWidget(action)
        return {1: "choose", 2: "view", 3: "forget"}.get(dialog.exec(), "home")

    def _show_read_only_project(self, root):
        manifest = load_project_manifest(root)
        try:
            relative_images = manifest["directories"].get("images", "images") if manifest else "images"
            relative_annotations = (
                manifest["directories"].get("annotations", "annotations") if manifest else "annotations"
            )
            images = resolve_project_directory(root, relative_images)
            annotations = resolve_project_directory(root, relative_annotations)
            filenames = core.list_images(images)
        except (OSError, KeyError, TypeError, ValueError) as error:
            ios_alert(self, "无法只读查看", f"图片目录不可访问：{error}", confirm_text="知道了")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(f"只读查看 · {Path(root).name}")
        dialog.resize(1040, 760)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)
        header = QHBoxLayout()
        header.addWidget(label("只读查看 · 图片与 XML 标注", "section"))
        header.addStretch()
        position = label("", "muted")
        header.addWidget(position)
        layout.addLayout(header)
        canvas = ImageCanvas()
        canvas.set_tool("pan")
        layout.addWidget(canvas, 1)
        note = label("只能查看和缩放，不能修改或保存。", "muted")
        layout.addWidget(note)
        navigation = QHBoxLayout()
        previous = button("‹ 上一张")
        following = button("下一张 ›")
        navigation.addWidget(previous)
        navigation.addWidget(following)
        navigation.addStretch()
        close = button("关闭")
        close.clicked.connect(dialog.accept)
        navigation.addWidget(close)
        layout.addLayout(navigation)
        index = 0

        def show_image(offset=0):
            nonlocal index
            if not filenames:
                position.setText("项目内没有图片")
                canvas.set_content(None, [])
                previous.setEnabled(False)
                following.setEnabled(False)
                return
            index = max(0, min(index + offset, len(filenames) - 1))
            filename = filenames[index]
            xml = os.path.join(annotations, Path(filename).stem + ".xml")
            try:
                shapes = core.load_voc_xml(xml) if os.path.isfile(xml) else []
                note.setText("只能查看和缩放，不能修改或保存。")
            except (OSError, ValueError, ParseError) as error:
                shapes = []
                note.setText(f"无法读取此图的 XML 标注：{error}")
            canvas.set_content(os.path.join(images, filename), shapes)
            position.setText(f"{index + 1} / {len(filenames)} · {filename} · {len(shapes)} 个标注")
            previous.setEnabled(index > 0)
            following.setEnabled(index < len(filenames) - 1)

        previous.clicked.connect(lambda: show_image(-1))
        following.clicked.connect(lambda: show_image(1))
        show_image()
        dialog.exec()

    def use_folders(self, images: str, annotations: str, project_root: str | None = None):
        root = os.path.abspath(project_root or self.project_root)
        try:
            paths_are_isolated = root and all(
                os.path.commonpath((root, os.path.abspath(path))) == root for path in (images, annotations)
            )
        except ValueError:
            paths_are_isolated = False
        if not paths_are_isolated:
            raise ValueError("图片和标注目录必须位于当前项目文件夹内")
        project = ProjectStore(annotations, images, root)
        existing = [
            filename
            for filename in core.list_images(images)
            if os.path.isfile(os.path.join(annotations, Path(filename).stem + ".xml"))
        ]
        statuses = project.statuses_for(existing)
        queued = [filename for filename in existing if statuses.get(filename) == STATUS_UNREVIEWED]
        project.mark_batch_for_review(existing)
        # Do not detach the current project until the replacement is usable.
        self.review.flush_autosave()
        self.review.clear_project_context()
        self.project_root = root
        self.images_dir, self.annotations_dir = images, annotations
        self.project = project
        self.refresh_all()
        return len(queued)

    def refresh_project_state(self):
        """Re-scan the project after a one-time image/XML import."""
        if not self.project_root or not self.images_dir or not self.annotations_dir:
            return 0
        return self.use_folders(self.images_dir, self.annotations_dir, self.project_root)

    def project_images(self, status=None):
        if not self.images_dir:
            return []
        files = core.list_images(self.images_dir)
        if status is None or not self.project:
            return files
        statuses = self.project.statuses_for(files)
        return [file for file in files if statuses.get(file) == status]

    def refresh_all(self):
        files = self.project_images()
        summary = self.project.summary(files) if self.project else {}
        self.nav["auto"].set_count(str(len(files)) if files else "")
        self.nav["review"].set_count(
            str(summary.get(STATUS_NEEDS_REVIEW, 0)) if summary.get(STATUS_NEEDS_REVIEW, 0) else ""
        )
        self.nav["approved"].set_count(str(summary.get(STATUS_APPROVED, 0)) if summary.get(STATUS_APPROVED, 0) else "")
        current_page = self.pages.currentWidget()
        if current_page is self.review:
            self.review.refresh()
        elif current_page is self.library:
            self.library.refresh()

    def _render_current_project(self):
        if not self.project_root:
            self.current_project.setText("还未打开项目")
            self.current_project_path.setText("新建或打开一个项目开始")
            self.project_status.setText("等待选择项目")
            self.project_card.setToolTip("当前项目工作区")
            self.project_workflow.setEnabled(False)
            self.setWindowTitle("autoLabel")
            return
        display_name = self.project_name or Path(self.project_root).name
        self.current_project.setText(display_name)
        self.current_project_path.setText(self.project_root)
        self.current_project_path.setToolTip(self.project_root)
        self.project_status.setText("项目已打开")
        self.project_workflow.setEnabled(True)
        self.project_card.setToolTip(f"当前项目工作区\n{self.project_root}")
        self.setWindowTitle(f"autoLabel · {self.project_name}")

    def show_page(self, key: str):
        if self.pages.currentWidget() is self.workflow and key != "workflow":
            self.workflow.flush_draft()
        self.pages.setCurrentWidget(self.keys[key])
        self.sidebar.setVisible(key not in {"workflow", "model_manager", "welcome"})
        if key in self.nav:
            self.nav[key].setChecked(True)
        if key == "review":
            self.review.clear_spot_check()
            self.review.refresh()
        if key == "approved":
            self.library.refresh()
        if key == "model_manager":
            self.model_manager.refresh()

    def open_workflow(self):
        if self.project:
            self.show_page("workflow")
            QTimer.singleShot(0, self.workflow.composer.board.fit_graph)

    def _workflow_changed(self, template_id: str):
        self.active_workflow_id = template_id
        self.auto.set_active_workflow(template_id)
        self.project_workflow.setText(f"当前工作流 · {self.workflow.registry.get(template_id).title}  ›")

    def open_spot_check_review(self, filenames):
        """Enter the regular canvas with only the sampled approved images queued."""
        self.review.open_spot_check(filenames)
        self.pages.setCurrentWidget(self.review)
        self.nav["review"].setChecked(True)

    def status_message(self, text: str):
        self.statusBar().showMessage(text, 4000)

    def closeEvent(self, event):
        # Persist the active project at the moment the user closes the app as
        # well as when it was opened, so startup can reliably restore it.
        if self.project_root:
            self.workflow.flush_draft()
            self.workspace.remember_project(self.project_root, self.project_name)
        super().closeEvent(event)


def main():
    QApplication.setAttribute(Qt.AA_DontUseNativeDialogs, True)
    app = QApplication.instance() or QApplication([])
    app.setWindowIcon(QIcon())
    apply_app_style(app)
    window = Workbench()
    window.show()
    return app.exec()
