"""应用级数据库：记录最近项目和上次打开的项目。"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager

from .constants import WORKSPACE_DATABASE_FILE
from .manifest import _now


class WorkspaceStore:
    """工作区记录与具体项目分离，便于在多个项目间切换。"""

    def __init__(self, database_path=None):
        local_root = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or os.path.expanduser("~")
        self.directory = os.path.join(local_root, "autoLabel")
        self.path = os.path.abspath(database_path or os.path.join(self.directory, WORKSPACE_DATABASE_FILE))
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
        except OSError:
            # 便携或受限环境可能无法写入 LocalAppData，此时回退到当前可写目录。
            self.directory = os.getcwd()
            self.path = os.path.abspath(os.path.join(self.directory, WORKSPACE_DATABASE_FILE))
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with self._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS recent_projects (
                    path TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    opened_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_recent_projects_opened_at ON recent_projects(opened_at DESC);
            """)

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def remember_project(self, project_dir, name):
        root = os.path.abspath(project_dir)
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO recent_projects(path, name, opened_at) VALUES (?, ?, ?) "
                "ON CONFLICT(path) DO UPDATE SET name = excluded.name, opened_at = excluded.opened_at",
                (root, str(name or os.path.basename(root)), _now()),
            )
            connection.execute("INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)", ("last_project", root))
            stale = connection.execute(
                "SELECT path FROM recent_projects ORDER BY opened_at DESC, rowid DESC LIMIT -1 OFFSET 12"
            ).fetchall()
            connection.executemany("DELETE FROM recent_projects WHERE path = ?", [(row["path"],) for row in stale])

    def last_project(self):
        with self._connect() as connection:
            row = connection.execute("SELECT value FROM settings WHERE key = 'last_project'").fetchone()
        return row["value"] if row else ""

    def recent_projects(self):
        with self._connect() as connection:
            return [
                (row["path"], row["name"])
                for row in connection.execute(
                    "SELECT path, name FROM recent_projects ORDER BY opened_at DESC, rowid DESC LIMIT 12"
                )
            ]

    def forget_project(self, project_dir):
        """仅从最近项目中移除记录，不删除项目文件。"""
        root = os.path.abspath(project_dir)
        try:
            with self._connect() as connection:
                connection.execute("DELETE FROM recent_projects WHERE path = ?", (root,))
                connection.execute("DELETE FROM settings WHERE key = ? AND value = ?", ("last_project", root))
        except sqlite3.Error:
            # 最近项目只是便利信息；其数据库只读时不能阻碍关闭项目。
            return

    def clear_last_project(self, project_dir):
        """停止自动恢复指定项目，但仍保留最近项目记录。"""
        root = os.path.abspath(project_dir)
        try:
            with self._connect() as connection:
                connection.execute("DELETE FROM settings WHERE key = ? AND value = ?", ("last_project", root))
        except sqlite3.Error:
            return
