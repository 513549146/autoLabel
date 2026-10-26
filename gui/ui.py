"""A clean-room Qt workbench.  It intentionally does not instantiate the legacy Tk pages."""
from __future__ import annotations

import os
import random
import shutil
import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, QTimer, Qt, QSize, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QImage, QKeySequence, QPainter, QPainterPath, QPen, QPixmap, QPolygonF, QShortcut
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QMainWindow, QMenu, QPlainTextEdit, QProgressBar,
    QPushButton, QScrollArea, QSizePolicy, QSlider, QStackedWidget, QStyle, QToolButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget, QLayout,
)

import config
import core
from project import (
    ANNOTATION_VERSION_DIR, STATUS_APPROVED, STATUS_NEEDS_REVIEW, STATUS_SKIPPED, STATUS_UNREVIEWED,
    ProjectStore, WorkspaceStore, create_project_manifest, load_project_manifest, resolve_project_directory,
)


COLORS = {
    "canvas": "#F6F7F9", "surface": "#FFFFFF", "line": "#E3E5E9", "ink": "#1D1D1F",
    "muted": "#737780", "blue": "#0A74FF", "blue_soft": "#EAF3FF", "track": "#ECEEF2",
}
ROOT_DIR = Path(__file__).resolve().parent.parent
_ICON_CACHE: dict[tuple[str, bool, int], QIcon] = {}
_ASSET_ICON_CACHE: dict[tuple[str, int, float], QIcon] = {}


class FlowLayout(QLayout):
    """A compact wrapping layout for text-sized project chips."""

    def __init__(self, parent=None, spacing=5):
        """初始化流动布局的条目列表与默认间距。"""
        super().__init__(parent)
        self._items = []
        self.setContentsMargins(0, 2, 0, 2)
        self.setSpacing(spacing)

    def addItem(self, item):
        """追加一个布局条目，供换行排布使用。"""
        self._items.append(item)

    def count(self):
        """返回当前条目数量。"""
        return len(self._items)

    def itemAt(self, index):
        """按下标取条目，越界返回 None。"""
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        """取出并移除指定条目，让调用方可安全重建内容。"""
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def hasHeightForWidth(self):
        """声明高度依赖宽度，Qt 才会按换行结果分配空间。"""
        return True

    def heightForWidth(self, width):
        """试排一遍算出给定宽度下所需高度。"""
        return self._layout_items(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        """按实际矩形重新排布全部条目。"""
        super().setGeometry(rect)
        self._layout_items(rect, False)

    def sizeHint(self):
        """以侧栏宽度估算首选尺寸。"""
        return QSize(0, self.heightForWidth(232))

    def minimumSize(self):
        """以最窄可用宽度估算最小尺寸。"""
        return QSize(0, self.heightForWidth(120))

    def _layout_items(self, rect, test_only):
        """逐条换行排布；test_only 为真时只计算高度而不移动控件。"""
        left, top, right, bottom = self.getContentsMargins()
        area = rect.adjusted(left, top, -right, -bottom)
        x, y, line_height = area.x(), area.y(), 0
        spacing = self.spacing()
        for item in self._items:
            hint = item.sizeHint()
            if x > area.x() and x + hint.width() > area.right() + 1:
                x, y, line_height = area.x(), y + line_height + spacing, 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + spacing
            line_height = max(line_height, hint.height())
        return max(0, y + line_height - rect.y() + bottom)


def sprite_icon(name: str, nav=False, size=24) -> QIcon:
    """Use the existing generated line-art as transparent Qt icons, never themed 3D glyphs."""
    key = (name, nav, size)
    if key in _ICON_CACHE:
        return _ICON_CACHE[key]
    path = ROOT_DIR / "assets" / ("nav-icons-v1.png" if nav else "ui-icons-v3.png")
    positions = ({"auto": (0, 0), "review": (1, 0), "approved": (0, 1), "export": (1, 1)} if nav else
                 {"back": (0, 0), "forward": (1, 0), "pan": (2, 0), "select": (3, 0), "box": (0, 1), "undo": (1, 1), "redo": (2, 1), "delete": (3, 1)})
    image = QImage(str(path))
    if image.isNull() or name not in positions:
        return QIcon()
    columns, rows = (2, 2) if nav else (4, 2)
    cell_w, cell_h = image.width() // columns, image.height() // rows
    col, row = positions[name]
    cell = image.copy(col * cell_w, row * cell_h, cell_w, cell_h).convertToFormat(QImage.Format_ARGB32)
    left, top, right, bottom = cell.width(), cell.height(), -1, -1
    for y in range(cell.height()):
        for x in range(cell.width()):
            color = cell.pixelColor(x, y)
            # Generated sheets have a white matte. Remove it and retain only line art.
            if min(color.red(), color.green(), color.blue()) > 238:
                color.setAlpha(0)
                cell.setPixelColor(x, y, color)
            else:
                left, top, right, bottom = min(left, x), min(top, y), max(right, x), max(bottom, y)
    if right >= left:
        margin = 22
        left, top = max(0, left - margin), max(0, top - margin)
        right, bottom = min(cell.width(), right + margin + 1), min(cell.height(), bottom + margin + 1)
        cell = cell.copy(left, top, right - left, bottom - top)
    # Render at a higher device-pixel ratio.  This keeps icons from the sprite
    # sheet as crisp as the ImageGen assets on scaled Windows displays.
    scale = 3
    pixmap = QPixmap.fromImage(cell).scaled(size * scale, size * scale, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    pixmap.setDevicePixelRatio(scale)
    result = QIcon(pixmap)
    _ICON_CACHE[key] = result
    return result


def workflow_nav_icon(size=22) -> QIcon:
    """A small neutral node-link icon for the dedicated workflow workbench."""
    scale = 3
    pixmap = QPixmap(size * scale, size * scale); pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap); painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(QColor("#596270"), 2.0 * scale, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    painter.drawLine(6 * scale, 7 * scale, 11 * scale, 11 * scale); painter.drawLine(11 * scale, 11 * scale, 17 * scale, 6 * scale); painter.drawLine(11 * scale, 11 * scale, 17 * scale, 17 * scale)
    painter.setBrush(QColor("#FFFFFF"))
    for x, y in ((5, 6), (11, 11), (17, 6), (17, 17)):
        painter.drawEllipse(QPointF(x * scale, y * scale), 2.3 * scale, 2.3 * scale)
    painter.end(); pixmap.setDevicePixelRatio(scale)
    return QIcon(pixmap)


def asset_icon(filename: str, size=24, glyph_scale=1.0) -> QIcon:
    """Crop transparent ImageGen padding and normalize the icon to toolbar ink."""
    key = (filename, size, glyph_scale)
    if key in _ASSET_ICON_CACHE:
        return _ASSET_ICON_CACHE[key]
    image = QImage(str(ROOT_DIR / "assets" / filename)).convertToFormat(QImage.Format_ARGB32)
    if image.isNull():
        return QIcon()
    left, top, right, bottom = image.width(), image.height(), -1, -1
    for y in range(image.height()):
        for x in range(image.width()):
            color = image.pixelColor(x, y)
            if color.alpha() > 20:
                left, top, right, bottom = min(left, x), min(top, y), max(right, x), max(bottom, y)
                color.setRgb(35, 39, 47, min(255, max(210, color.alpha())))
                image.setPixelColor(x, y, color)
    if right < left:
        return QIcon()
    # Asset-specific transparent gutters are deliberately removed here.  A
    # small, fixed visual inset keeps the two custom tools aligned with the
    # rest of the 24 px toolbar glyphs.
    padding = max(8, min(image.width(), image.height()) // 42)
    left, top = max(0, left - padding), max(0, top - padding)
    right, bottom = min(image.width(), right + padding + 1), min(image.height(), bottom + padding + 1)
    scale = 3
    glyph_size = max(1, round(size * scale * max(0.1, min(1.0, glyph_scale))))
    glyph = QPixmap.fromImage(image.copy(left, top, right - left, bottom - top)).scaled(glyph_size, glyph_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    pixmap = QPixmap(size * scale, size * scale); pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap); painter.drawPixmap((pixmap.width() - glyph.width()) // 2, (pixmap.height() - glyph.height()) // 2, glyph); painter.end()
    pixmap.setDevicePixelRatio(scale)
    icon = QIcon(pixmap); _ASSET_ICON_CACHE[key] = icon
    return icon


def apply_app_style(app: QApplication):
    # Qt's off-screen and packaged environments do not always discover Windows'
    # CJK fonts automatically. Register a deterministic Chinese UI fallback.
    """注册中文字体并应用全局样式表；各页面靠 objectName 命中样式。"""
    for font_path in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\NotoSansSC-VF.ttf"):
        if os.path.isfile(font_path):
            QFontDatabase.addApplicationFont(font_path)
    app.setFont(QFont("Microsoft YaHei UI", 10))
    app.setStyleSheet(f"""
        QWidget {{ font-family: 'Microsoft YaHei UI', 'Segoe UI'; color: {COLORS['ink']}; }}
        QMainWindow {{ background: {COLORS['canvas']}; }}
        QFrame#windowbar {{ background: #FFFFFF; border: 0; }}
        QFrame#card {{ background: {COLORS['surface']}; border: 1px solid {COLORS['line']}; border-radius: 16px; }}
        QFrame#sidebar {{ background: {COLORS['surface']}; border-right: 1px solid {COLORS['line']}; }}
        QLabel#title {{ font-size: 21px; font-weight: 700; }}
        QLabel#section {{ font-size: 13px; font-weight: 700; }}
        QLabel#muted {{ color: {COLORS['muted']}; font-size: 12px; }}
        QLineEdit, QComboBox, QPlainTextEdit {{ background: white; border: 1px solid #DCE0E6; border-radius: 10px; padding: 9px 11px; }}
        QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus {{ border: 1px solid {COLORS['blue']}; }}
        QComboBox::drop-down {{ border: 0; width: 26px; subcontrol-origin: padding; subcontrol-position: top right; }}
        QPushButton#primary {{ background: {COLORS['blue']}; color: white; border: 0; border-radius: 10px; padding: 10px 18px; font-weight: 700; }}
        QPushButton#primary:hover {{ background: #2A8AFF; }}
        QPushButton#secondary {{ background: #F1F3F6; color: #2E3138; border: 0; border-radius: 10px; padding: 10px 16px; font-weight: 600; }}
        QPushButton#secondary:hover {{ background: #E7EBF0; }}
        QPushButton#destructive {{ background: #FFF1F0; color: #D92D20; border: 0; border-radius: 9px; padding: 7px 10px; font-weight: 700; }}
        QPushButton#destructive:hover {{ background: #FFE1DE; }}
        QToolButton#tool {{ background: transparent; border: 0; border-radius: 10px; padding: 8px; }}
        QToolButton#tool:hover, QToolButton#tool:checked {{ background: {COLORS['blue_soft']}; }}
        QToolButton#shortcutGuide {{ background:#F1F3F6; border:0; border-radius:10px; color:#34373E; font-family:'Segoe UI Symbol', 'Microsoft YaHei UI'; font-size:19px; padding:0; }}
        QToolButton#shortcutGuide:hover, QToolButton#shortcutGuide:pressed {{ background:#E7EBF0; }}
        QToolButton#visibilityCheck {{ background:#FFFFFF; border:1px solid #C9CED6; border-radius:4px; color:#0A74FF; font-size:14px; font-weight:700; padding:0; }}
        QToolButton#visibilityCheck:hover {{ border-color:#0A74FF; background:#F6F9FE; }}
        QToolButton#visibilityCheck:checked {{ background:#FFFFFF; border-color:#0A74FF; color:#0A74FF; }}
        QToolButton#nav {{ border: 0; border-radius: 12px; text-align: left; padding: 10px 12px; font-size: 15px; color: #34373E; }}
        QToolButton#nav:checked {{ background: {COLORS['blue_soft']}; color: {COLORS['blue']}; font-weight: 700; }}
        QToolButton#nav:hover {{ background: #F3F5F8; }}
        QFrame#projectCard {{ background: #FFFFFF; border: 1px solid #E0E6EE; border-left: 3px solid #0A74FF; border-radius: 12px; }}
        QLabel#projectEyebrow {{ color: #6E7785; font-size: 10px; font-weight: 700; letter-spacing: .6px; }}
        QLabel#projectName {{ color: #20242B; font-size: 15px; font-weight: 750; }}
        QLabel#projectPath {{ color: #78818D; font-size: 10px; }}
        QLabel#projectStatus {{ color: #16794C; background: #EEF9F3; border-radius: 7px; font-size: 10px; font-weight: 650; padding: 3px 7px; }}
        QToolButton#projectWorkflow {{ background: #F5F8FC; border: 1px solid #E4ECF5; border-radius: 8px; color: #36618F; text-align: left; font-size: 11px; font-weight: 650; padding: 7px 9px; }}
        QToolButton#projectWorkflow:hover {{ background: #EAF3FF; color: #0A74FF; }}
        QToolButton#projectWorkflow:disabled {{ color: #9AA3AE; background: #F7F8FA; }}
        QToolButton#recentProject {{ background: #F6F7F9; border: 0; border-radius: 9px; padding: 7px 10px; text-align: left; color: #454953; font-size: 12px; }}
        QToolButton#recentProject:hover {{ background: #EAF3FF; color: {COLORS['blue']}; }}
        QToolButton#recentProject:checked {{ background: #EAF3FF; color: {COLORS['blue']}; font-weight: 700; }}
        QLabel#welcomeEyebrow {{ color:#6C7685; font-size:11px; font-weight:750; letter-spacing:1.2px; }}
        QLabel#welcomeTitle {{ color:#1D1D1F; font-size:34px; font-weight:750; margin-top:8px; }}
        QLabel#welcomeSubtitle {{ color:#737780; font-size:14px; margin-top:5px; }}
        QToolButton#welcomeAction, QToolButton#welcomeActionPrimary {{ min-width:0; border-radius:16px; padding:18px 22px; text-align:left; font-size:15px; font-weight:750; }}
        QToolButton#welcomeAction {{ background:#FFFFFF; border:1px solid #E1E5EB; color:#252933; }}
        QToolButton#welcomeAction:hover {{ background:#F8FBFF; border-color:#BFD8FF; color:#0A74FF; }}
        QToolButton#welcomeActionPrimary {{ background:#F4F8FF; border:1px solid #C9DEFF; color:#135EA8; }}
        QToolButton#welcomeActionPrimary:hover {{ background:#EAF3FF; border-color:#8EBEFF; }}
        QFrame#welcomeRecentCard {{ background:#FFFFFF; border:1px solid #E1E5EB; border-radius:16px; }}
        QLabel#welcomeRecentCount {{ color:#7A838F; background:#F3F5F7; border-radius:8px; padding:3px 8px; font-size:11px; }}
        QToolButton#welcomeRecentProject {{ background:#F6F7F9; border:0; border-radius:10px; padding:7px 10px; text-align:left; color:#333842; font-size:13px; font-weight:650; }}
        QToolButton#welcomeRecentProject:hover {{ background:#F1F6FE; color:#0A74FF; }}
        QProgressBar {{ height: 5px; border: 0; background: {COLORS['track']}; border-radius: 3px; }}
        QProgressBar::chunk {{ background: {COLORS['blue']}; border-radius: 3px; }}
        QProgressBar#reviewProgress {{ height: 4px; min-height: 4px; max-height: 4px; border-radius: 2px; }}
        QProgressBar#reviewProgress::chunk {{ border-radius: 2px; }}
        QFrame#paging {{ background: #FFFFFF; border: 1px solid {COLORS['line']}; border-radius: 11px; }}
        QToolButton#pagingChevron {{ background: transparent; border: 0; border-radius: 10px; font-family: 'Segoe UI Symbol', 'Microsoft YaHei UI'; font-size: 22px; font-weight: 600; color: #23272F; padding: 0; margin: 0; }}
        QToolButton#pagingChevron:hover {{ background: #F3F5F8; }}
        QLabel#pagingPosition {{ border-left: 1px solid {COLORS['line']}; border-right: 1px solid {COLORS['line']}; font-size: 16px; padding: 0; }}
        QFrame#annotationDrawer {{ background: rgba(255,255,255,248); border: 1px solid #DCE0E6; border-radius: 14px; }}
        QToolButton#annotationRow {{ background: #FFFFFF; border: 1px solid #E7E9ED; border-radius: 10px; text-align: left; padding: 7px 10px; color: #2E3138; }}
        QToolButton#annotationRow:hover {{ background: #F6F9FE; border-color: #CFE1FF; }}
        QToolButton#annotationRow:checked {{ background: #EAF3FF; border-color: #0A74FF; font-weight: 700; }}
        QLabel#saveToast {{ background:#FFFFFF; color:#0A74FF; border:1px solid #C9DEFF; border-radius:12px; padding:10px 18px; font-size:13px; font-weight:700; }}
        QFileDialog {{ background: #F7F8FA; }}
        QFileDialog QPushButton {{ min-height: 34px; background: #FFFFFF; border: 1px solid #D9DEE6; border-radius: 9px; padding: 0 14px; font-weight: 650; }}
        QFileDialog QPushButton:hover {{ background: #F1F6FE; border-color: #BFD8FF; color: #0A74FF; }}
        QFileDialog QLineEdit, QFileDialog QComboBox {{ background: #FFFFFF; border: 1px solid #DCE1E8; border-radius: 9px; padding: 7px 10px; }}
        QFileDialog QTreeView, QFileDialog QListView {{ background: #FFFFFF; border: 1px solid #E5E8ED; border-radius: 10px; selection-background-color: #EAF3FF; selection-color: #0A74FF; }}
        QMenu {{ background: #FFFFFF; border: 1px solid #E2E6EB; border-radius: 11px; padding: 6px; }}
        QMenu::item {{ padding: 8px 22px; border-radius: 7px; color: #2E3138; }}
        QMenu::item:selected {{ background: #EAF3FF; color: #0A74FF; }}
        QMenu::separator {{ height: 1px; background: #ECEEF2; margin: 5px 8px; }}
        QDialog#iosDialog {{ background: transparent; }}
        QFrame#iosDialogPanel {{ background: #FFFFFF; border: 1px solid #E5E8ED; border-radius: 18px; }}
        QLabel#iosDialogTitle {{ color: #1D1D1F; font-size: 17px; font-weight: 750; }}
        QLabel#iosDialogMessage {{ color: #68707C; font-size: 13px; line-height: 1.45; }}
        QPushButton#iosDialogPrimary, QPushButton#iosDialogSecondary, QPushButton#iosDialogDestructive {{ min-height: 38px; border-radius: 10px; font-size: 13px; font-weight: 700; }}
        QPushButton#iosDialogPrimary {{ background: #0A74FF; color: #FFFFFF; border: 0; }}
        QPushButton#iosDialogPrimary:hover {{ background: #2A8AFF; }}
        QPushButton#iosDialogSecondary {{ background: #F2F4F7; color: #3D4652; border: 0; }}
        QPushButton#iosDialogSecondary:hover {{ background: #E8ECF1; }}
        QPushButton#iosDialogDestructive {{ background: #FFF0EF; color: #D92D20; border: 0; }}
        QPushButton#iosDialogDestructive:hover {{ background: #FFE1DE; }}
        QToolTip {{ background:#2E3138; color:#FFFFFF; border:0; border-radius:7px; padding:5px 8px; font-size:12px; }}
    """)


def card() -> QFrame:
    """创建标准白色圆角卡片容器。"""
    frame = QFrame()
    frame.setObjectName("card")
    return frame


def label(text: str, object_name: str | None = None) -> QLabel:
    """创建文本标签，可按 objectName 套用预定义样式。"""
    item = QLabel(text)
    if object_name:
        item.setObjectName(object_name)
    return item


def button(text: str, primary=False) -> QPushButton:
    """创建普通按钮，primary 为真时使用主色样式。"""
    item = QPushButton(text)
    item.setObjectName("primary" if primary else "secondary")
    item.setCursor(Qt.PointingHandCursor)
    return item


def destructive_button(text: str) -> QPushButton:
    """创建危险操作按钮（红色弱底样式）。"""
    item = QPushButton(text)
    item.setObjectName("destructive")
    item.setCursor(Qt.PointingHandCursor)
    return item


def _ios_dialog(parent, title: str, message: str):
    """Create a compact, titlebar-free alert surface inspired by iOS dialogs."""
    dialog = QDialog(parent)
    dialog.setObjectName("iosDialog")
    dialog.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
    dialog.setModal(True)
    dialog.setAttribute(Qt.WA_TranslucentBackground)
    dialog.setMinimumWidth(390)
    outer = QVBoxLayout(dialog); outer.setContentsMargins(14, 14, 14, 14)
    panel = QFrame(); panel.setObjectName("iosDialogPanel"); outer.addWidget(panel)
    layout = QVBoxLayout(panel); layout.setContentsMargins(28, 25, 28, 20); layout.setSpacing(10)
    heading = label(title, "iosDialogTitle"); heading.setAlignment(Qt.AlignHCenter); heading.setWordWrap(True); layout.addWidget(heading)
    copy = label(message, "iosDialogMessage"); copy.setAlignment(Qt.AlignHCenter); copy.setWordWrap(True); layout.addWidget(copy)
    return dialog, layout


def ios_alert(parent, title: str, message: str, confirm_text="好", cancel_text=None, destructive=False):
    """弹出无边框确认对话框，返回用户是否确认。"""
    dialog, layout = _ios_dialog(parent, title, message)
    layout.addSpacing(8)
    actions = QVBoxLayout(); actions.setSpacing(7)
    if cancel_text:
        cancel = QPushButton(cancel_text); cancel.setObjectName("iosDialogSecondary"); cancel.clicked.connect(dialog.reject); actions.addWidget(cancel)
    confirm = QPushButton(confirm_text); confirm.setObjectName("iosDialogDestructive" if destructive else "iosDialogPrimary"); confirm.clicked.connect(dialog.accept); actions.addWidget(confirm)
    layout.addLayout(actions)
    return dialog.exec() == QDialog.DialogCode.Accepted


def ios_text_input(parent, title: str, prompt: str, text="", placeholder="", confirm_text="完成"):
    """弹出单行文本输入对话框，返回 (文本, 是否确认)。"""
    dialog, layout = _ios_dialog(parent, title, prompt)
    field = QLineEdit(text); field.setPlaceholderText(placeholder); layout.addWidget(field)
    layout.addSpacing(6)
    actions = QHBoxLayout(); actions.setSpacing(8); cancel = QPushButton("取消"); cancel.setObjectName("iosDialogSecondary"); cancel.clicked.connect(dialog.reject); confirm = QPushButton(confirm_text); confirm.setObjectName("iosDialogPrimary"); confirm.clicked.connect(dialog.accept); actions.addWidget(cancel); actions.addWidget(confirm); layout.addLayout(actions)
    QTimer.singleShot(0, field.setFocus)
    accepted = dialog.exec() == QDialog.DialogCode.Accepted
    return field.text(), accepted


def ios_item_input(parent, title: str, prompt: str, items, current=0, editable=False, confirm_text="完成"):
    """弹出下拉选择对话框，返回 (选中项, 是否确认)。"""
    dialog, layout = _ios_dialog(parent, title, prompt)
    field = FlatComboBox(); field.addItems([str(item) for item in items]); field.setEditable(editable)
    if field.count(): field.setCurrentIndex(max(0, min(field.count() - 1, current)))
    layout.addWidget(field)
    layout.addSpacing(6)
    actions = QHBoxLayout(); actions.setSpacing(8); cancel = QPushButton("取消"); cancel.setObjectName("iosDialogSecondary"); cancel.clicked.connect(dialog.reject); confirm = QPushButton(confirm_text); confirm.setObjectName("iosDialogPrimary"); confirm.clicked.connect(dialog.accept); actions.addWidget(cancel); actions.addWidget(confirm); layout.addLayout(actions)
    QTimer.singleShot(0, field.setFocus)
    accepted = dialog.exec() == QDialog.DialogCode.Accepted
    return field.currentText(), accepted


def tool(parent: QWidget, icon: QIcon, tip: str, checkable=False, icon_size=24) -> QToolButton:
    """创建统一 40px 命中区的工具栏图标按钮。"""
    item = QToolButton(parent)
    item.setObjectName("tool")
    item.setIcon(icon)
    item.setIconSize(QSize(icon_size, icon_size))
    # Every toolbar target uses the same 40 px hit area, so an icon's source
    # aspect ratio can never shift its visual position or the group spacing.
    item.setFixedSize(40, 40)
    item.setToolTip(tip)
    item.setCheckable(checkable)
    item.setCursor(Qt.PointingHandCursor)
    return item


class DoubleChevronButton(QToolButton):
    """A restrained, iOS-like double chevron for collapsible inspector sections."""

    def __init__(self, parent=None):
        """初始化折叠箭头按钮的尺寸与提示文案。"""
        super().__init__(parent)
        self._collapsed = False
        self.setFixedSize(34, 18)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("收起类别筛选")

    def set_collapsed(self, collapsed):
        """记录折叠状态并同步提示文案与重绘。"""
        self._collapsed = bool(collapsed)
        self.setToolTip("展开类别筛选" if self._collapsed else "收起类别筛选")
        self.update()

    def paintEvent(self, _event):
        """按折叠状态绘制上下指向的双箭头，悬停时高亮。"""
        painter = QPainter(self); painter.setRenderHint(QPainter.Antialiasing)
        if self.underMouse():
            painter.fillRect(self.rect(), QColor("#F0F6FF"))
        pen = QPen(QColor("#0A74FF" if self.underMouse() else "#7B8491"), 1.6)
        pen.setCapStyle(Qt.RoundCap); pen.setJoinStyle(Qt.RoundJoin); painter.setPen(pen)
        for center_y in (6, 12):
            tip_y = center_y + 2.5 if self._collapsed else center_y - 2.5
            base_y = center_y - 1.5 if self._collapsed else center_y + 1.5
            painter.drawLine(QPointF(11, base_y), QPointF(17, tip_y))
            painter.drawLine(QPointF(17, tip_y), QPointF(23, base_y))
        painter.end()


class FlatComboBox(QComboBox):
    """Flat selector chrome with an intentionally visible, quiet chevron."""

    def paintEvent(self, event):
        """在原生绘制之上叠加自绘的下拉箭头。"""
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor("#737780"), 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        x, y = self.width() - 18, self.height() // 2 - 2
        painter.drawLine(x, y, x + 4, y + 4)
        painter.drawLine(x + 4, y + 4, x + 8, y)


class WindowChrome(QFrame):
    """A compact desktop title bar: colored workspace dots on the left, controls on the right."""

    def __init__(self, window):
        """构建无边框标题栏：左侧装饰圆点，右侧最小化/最大化/关闭。"""
        super().__init__(window)
        self.window = window
        self._drag_origin = None
        self.setFixedHeight(38)
        self.setObjectName("windowbar")
        layout = QHBoxLayout(self); layout.setContentsMargins(20, 0, 10, 0); layout.setSpacing(8)
        for color in ("#FF5F57", "#FEBC2E", "#28C840"):
            dot = QFrame(); dot.setFixedSize(11, 11); dot.setStyleSheet(f"background:{color}; border-radius:5px;")
            layout.addWidget(dot)
        layout.addStretch()
        for caption, action, tip in (("−", window.showMinimized, "最小化"), ("□", window.toggle_maximized, "最大化"), ("×", window.close, "关闭")):
            control = QToolButton(); control.setText(caption); control.setToolTip(tip); control.setFixedSize(34, 30); control.setCursor(Qt.PointingHandCursor)
            control.setStyleSheet("QToolButton {border:0;border-radius:8px;color:#3E424A;font-size:18px;} QToolButton:hover {background:#EEF1F5;} QToolButton:pressed {background:#E2E7ED;}")
            control.clicked.connect(action)
            layout.addWidget(control)

    def mousePressEvent(self, event):
        """记录按下时指针相对窗口的偏移，供拖动移动窗口。"""
        if event.button() == Qt.LeftButton and not self.window.isMaximized():
            self._drag_origin = event.globalPosition().toPoint() - self.window.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        """按住左键拖动标题栏时移动整个窗口。"""
        if self._drag_origin is not None and event.buttons() & Qt.LeftButton and not self.window.isMaximized():
            self.window.move(event.globalPosition().toPoint() - self._drag_origin)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        """结束标题栏拖动并清除偏移记录。"""
        self._drag_origin = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        """双击标题栏在最大化与还原之间切换。"""
        if event.button() == Qt.LeftButton:
            self.window.toggle_maximized()
        super().mouseDoubleClickEvent(event)


class ResizableRoot(QWidget):
    """Six-pixel resize gutter for the frameless workbench window."""

    EDGE = 7

    def __init__(self, window):
        """初始化根容器的边缘缩放状态与光标跟踪。"""
        super().__init__(window)
        self.window = window
        self._edges = (False, False, False, False)
        self._start_global = None
        self._start_geometry = None
        self.setMouseTracking(True)

    def track_descendants(self):
        """Keep the resize cursor in sync while the pointer crosses child widgets."""
        self.installEventFilter(self)
        for child in self.findChildren(QWidget):
            child.setMouseTracking(True)
            child.installEventFilter(self)

    def _edges_at(self, position):
        """判断指针靠近哪些窗口边缘，用于决定缩放方向。"""
        return (position.x() <= self.EDGE, position.x() >= self.width() - self.EDGE,
                position.y() <= self.EDGE, position.y() >= self.height() - self.EDGE)

    def _set_resize_cursor(self, edges):
        """按可缩放边缘设置对应的缩放光标。"""
        left, right, top, bottom = edges
        if (left and top) or (right and bottom): self.setCursor(Qt.SizeFDiagCursor)
        elif (right and top) or (left and bottom): self.setCursor(Qt.SizeBDiagCursor)
        elif left or right: self.setCursor(Qt.SizeHorCursor)
        elif top or bottom: self.setCursor(Qt.SizeVerCursor)
        else: self.unsetCursor()

    def mousePressEvent(self, event):
        """在边缘按下时进入缩放状态。"""
        edges = self._edges_at(event.position().toPoint())
        if event.button() == Qt.LeftButton and any(edges) and not self.window.isMaximized():
            self._edges = edges
            self._start_global = event.globalPosition().toPoint()
            self._start_geometry = self.window.geometry()
            event.accept(); return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        """在边缘拖动时调整窗口几何，其余位置只更新光标。"""
        if self._start_global is None:
            self._set_resize_cursor(self._edges_at(event.position().toPoint()))
            super().mouseMoveEvent(event); return
        delta = event.globalPosition().toPoint() - self._start_global
        left, right, top, bottom = self._edges
        rect = self._start_geometry
        min_width, min_height = self.window.minimumWidth(), self.window.minimumHeight()
        x, y, width, height = rect.x(), rect.y(), rect.width(), rect.height()
        if left: x, width = x + delta.x(), width - delta.x()
        if right: width += delta.x()
        if top: y, height = y + delta.y(), height - delta.y()
        if bottom: height += delta.y()
        if width < min_width:
            if left: x -= min_width - width
            width = min_width
        if height < min_height:
            if top: y -= min_height - height
            height = min_height
        self.window.setGeometry(x, y, width, height)
        event.accept()

    def eventFilter(self, watched, event):
        """跟踪子控件内的指针位置，避免边缘热点被子控件遮挡失效。"""
        if self._start_global is None and event.type() == QEvent.MouseMove:
            # Mouse moves over the sidebar/page child widgets do not reach the root.
            # Resolve the edge from the global position so a stale resize cursor is
            # always reset immediately after leaving the seven-pixel gutter.
            point = self.mapFromGlobal(event.globalPosition().toPoint())
            self._set_resize_cursor(self._edges_at(point))
        elif self._start_global is None and event.type() == QEvent.Leave:
            point = self.mapFromGlobal(self.cursor().pos())
            if not self.rect().contains(point):
                self.unsetCursor()
        return super().eventFilter(watched, event)

    def mouseReleaseEvent(self, event):
        """结束窗口缩放并恢复普通光标。"""
        self._edges = (False, False, False, False)
        self._start_global = self._start_geometry = None
        self.unsetCursor()
        super().mouseReleaseEvent(event)


class SidebarItem(QToolButton):
    """侧栏导航项，可在标题右侧追加计数徽标。"""
    def __init__(self, title: str, icon: QIcon, parent):
        """初始化导航项的图标、可选中状态与最小高度。"""
        super().__init__(parent)
        self.title, self.count = title, ""
        self.setObjectName("nav")
        self.setCheckable(True)
        self.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.setIcon(icon)
        self.setIconSize(QSize(22, 22))
        self.setMinimumHeight(52)
        self.setCursor(Qt.PointingHandCursor)
        self._render()

    def set_count(self, count: str):
        """更新计数徽标并立即重绘标签。"""
        self.count = count
        self._render()

    def _render(self):
        """按有无计数拼装按钮文本。"""
        self.setText(f"{self.title}        {self.count}" if self.count else self.title)
