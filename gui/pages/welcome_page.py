"""A project hub shown before a project is opened."""
from __future__ import annotations

from ..ui import *


class WelcomePage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        root = QVBoxLayout(self); root.setContentsMargins(56, 42, 56, 46); root.setSpacing(0)
        root.addStretch(1)
        panel = QWidget(); panel.setObjectName("welcomePanel"); panel.setMaximumWidth(980)
        panel_layout = QVBoxLayout(panel); panel_layout.setContentsMargins(0, 0, 0, 0); panel_layout.setSpacing(0)

        eyebrow = label("AUTOLABEL  /  PROJECT HUB", "welcomeEyebrow"); panel_layout.addWidget(eyebrow)
        title = label("开始一个标注项目", "welcomeTitle"); panel_layout.addWidget(title)
        subtitle = label("创建本地工作区，导入已有项目，或从最近工作继续。", "welcomeSubtitle"); panel_layout.addWidget(subtitle)
        panel_layout.addSpacing(28)

        actions = QHBoxLayout(); actions.setSpacing(16)
        actions.addWidget(self._action_card(
            "welcome-new-project-v1.png", "新建项目", "创建独立的图片、标注和模型工作区", self.window.new_project,
        ))
        actions.addWidget(self._action_card(
            "welcome-open-project-v1.png", "导入 / 打开项目", "继续已有的 autoLabel 项目或迁移旧目录", self.window.open_project,
        ))
        panel_layout.addLayout(actions)
        panel_layout.addSpacing(24)

        recent_card = card(); recent_card.setObjectName("welcomeRecentCard")
        recent_layout = QVBoxLayout(recent_card); recent_layout.setContentsMargins(20, 18, 20, 18); recent_layout.setSpacing(8)
        header = QHBoxLayout(); header.addWidget(label("最近项目", "section")); header.addStretch(); self.recent_count = label("", "welcomeRecentCount"); header.addWidget(self.recent_count); recent_layout.addLayout(header)
        self.recent_host = QWidget(); self.recent_host.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum); self.recent_layout = FlowLayout(self.recent_host); recent_layout.addWidget(self.recent_host)
        panel_layout.addWidget(recent_card)
        root.addWidget(panel, 0, Qt.AlignHCenter)
        root.addStretch(2)

    def _action_card(self, icon_name, title, detail, callback, primary=False):
        action = QToolButton(self)
        action.setObjectName("welcomeActionPrimary" if primary else "welcomeAction")
        action.setIcon(QIcon(str(ROOT_DIR / "assets" / icon_name)))
        action.setIconSize(QSize(64, 64))
        action.setText(f"{title}\n{detail}")
        action.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        action.setMinimumHeight(112)
        action.setCursor(Qt.PointingHandCursor)
        action.clicked.connect(callback)
        return action

    def refresh(self):
        while self.recent_layout.count():
            item = self.recent_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        recents = self.window.workspace.recent_projects()
        if not recents:
            self.recent_count.setText("尚无项目")
            self.recent_layout.addWidget(label("还没有最近项目。使用上方入口创建或导入一个项目。", "muted"))
            return
        visible_recents = recents[:5]
        self.recent_count.setText(f"{len(visible_recents)} 个项目")
        for path, name in visible_recents:
            entry = QToolButton(self.recent_host); entry.setObjectName("welcomeRecentProject"); entry.setText(name); entry.setToolTip(path); entry.setToolButtonStyle(Qt.ToolButtonTextOnly); entry.setFixedHeight(34); entry.setCursor(Qt.PointingHandCursor); entry.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed); entry.setFixedWidth(min(260, max(88, entry.sizeHint().width()))); entry.clicked.connect(lambda checked=False, target=path: self.window.load_project(target)); self.recent_layout.addWidget(entry)
