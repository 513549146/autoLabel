"""桌面工作台的统一色板与 Qt 样式表。"""

from __future__ import annotations

import os

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

COLORS = {
    "canvas": "#F6F7F9",
    "surface": "#FFFFFF",
    "line": "#E3E5E9",
    "ink": "#1D1D1F",
    "muted": "#737780",
    "blue": "#0A74FF",
    "blue_soft": "#EAF3FF",
    "track": "#ECEEF2",
}


def apply_app_style(app: QApplication):
    # 离屏环境和打包程序可能找不到系统中文字体，先注册可用的中文后备字体。
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
