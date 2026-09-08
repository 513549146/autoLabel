"""Persistent, local project metadata for autoLabel."""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone


PROJECT_FILE = ".autolabel-project.json"
WORKBENCH_FILE = "autolabel.project.json"
DATABASE_FILE = "autolabel.db"
WORKSPACE_DATABASE_FILE = "workspace.db"
STATUS_UNREVIEWED = "unreviewed"
STATUS_NEEDS_REVIEW = "needs_review"
STATUS_APPROVED = "approved"
STATUS_SKIPPED = "skipped"
STATUSES = (STATUS_UNREVIEWED, STATUS_NEEDS_REVIEW, STATUS_APPROVED, STATUS_SKIPPED)

STATUS_LABELS = {
    STATUS_UNREVIEWED: "未标注",
    STATUS_NEEDS_REVIEW: "待审核",
    STATUS_APPROVED: "已通过",
    STATUS_SKIPPED: "已跳过",
}


def _now():
    # Recent-project switches can happen within a second.  Retain enough
    # precision for SQLite ordering to faithfully reflect the latest project.
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _clean_categories(categories):
    """Return ordered, unique category names suitable for all exporters."""
    result, seen = [], set()
    for category in categories:
        name = str(category).strip()
        if name and name not in seen:
            result.append(name)
            seen.add(name)
    return result


def project_manifest_path(project_dir):
    """Return the user-visible project descriptor stored at the project root."""
    return os.path.join(os.path.abspath(project_dir), WORKBENCH_FILE)


def create_project_manifest(project_dir, name=None):
    """Create or repair the portable descriptor for a workbench project."""
    root = os.path.abspath(project_dir)
    os.makedirs(root, exist_ok=True)
    data = {
        "format": "autolabel-workbench-project",
        "version": 1,
        "name": (name or os.path.basename(root)).strip() or os.path.basename(root),
        "directories": {
            "images": "images",
            "annotations": "annotations",
            "dataset": "yolo_dataset",
        },
        "created_at": _now(),
    }
    path = project_manifest_path(root)
    if os.path.isfile(path):
        loaded = load_project_manifest(root)
        if loaded:
            return loaded
    temp_path = path + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
    os.replace(temp_path, path)
    return data


def load_project_manifest(project_dir):
    """Read a project descriptor, returning None for a folder that is not one."""
    try:
        with open(project_manifest_path(project_dir), encoding="utf-8") as file:
            data = json.load(file)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict) or data.get("format") != "autolabel-workbench-project":
        return None
    directories = data.get("directories")
    if not isinstance(directories, dict):
        return None
    return data


def resolve_project_directory(project_dir, relative_path):
    """Resolve a manifest directory without allowing it to leave the project root."""
    root = os.path.abspath(project_dir)
    candidate = os.path.abspath(os.path.join(root, str(relative_path)))
    if os.path.commonpath((root, candidate)) != root:
        raise ValueError("项目目录配置不能指向项目文件夹之外")
    return candidate


class WorkspaceStore:
    """Small app-level SQLite store for the last and recently opened projects."""

    def __init__(self, database_path=None):
        local_root = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or os.path.expanduser("~")
        self.directory = os.path.join(local_root, "autoLabel")
        self.path = os.path.abspath(database_path or os.path.join(self.directory, WORKSPACE_DATABASE_FILE))
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
        except OSError:
            # Restricted/portable environments can disallow LocalAppData. Keep the
            # same SQLite behavior in the current writable application folder.
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

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def remember_project(self, project_dir, name):
        root = os.path.abspath(project_dir)
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO recent_projects(path, name, opened_at) VALUES (?, ?, ?) "
                "ON CONFLICT(path) DO UPDATE SET name = excluded.name, opened_at = excluded.opened_at",
                (root, str(name or os.path.basename(root)), _now()),
            )
            connection.execute("INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)", ("last_project", root))
            stale = connection.execute("SELECT path FROM recent_projects ORDER BY opened_at DESC, rowid DESC LIMIT -1 OFFSET 12").fetchall()
            connection.executemany("DELETE FROM recent_projects WHERE path = ?", [(row["path"],) for row in stale])

    def last_project(self):
        with self._connect() as connection:
            row = connection.execute("SELECT value FROM settings WHERE key = 'last_project'").fetchone()
        return row["value"] if row else ""

    def recent_projects(self):
        with self._connect() as connection:
            return [(row["path"], row["name"]) for row in connection.execute("SELECT path, name FROM recent_projects ORDER BY opened_at DESC, rowid DESC LIMIT 12")]


class ProjectStore:
    """SQLite-backed project state, stored as ``autolabel.db`` at the project root.

    VOC XML remains in ``annotations`` for portability; the database only owns
    workflow data such as statuses, categories, and project paths.
    """

    def __init__(self, annotations_dir, images_dir=None, project_dir=None):
        self.annotations_dir = os.path.abspath(annotations_dir)
        self.images_dir = os.path.abspath(images_dir) if images_dir else ""
        self.project_dir = os.path.abspath(project_dir or self._infer_project_dir())
        self.path = os.path.join(self.project_dir, DATABASE_FILE)
        self.legacy_path = os.path.join(self.annotations_dir, PROJECT_FILE)
        os.makedirs(self.project_dir, exist_ok=True)
        self._initialize()

    def _infer_project_dir(self):
        if self.images_dir:
            try:
                return os.path.commonpath((self.annotations_dir, self.images_dir))
            except ValueError:
                pass
        return self.annotations_dir

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self):
        is_new = not os.path.exists(self.path)
        with self._connect() as connection:
            connection.executescript("""
                PRAGMA foreign_keys = ON;
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS categories (
                    position INTEGER PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE
                );
                CREATE TABLE IF NOT EXISTS image_statuses (
                    filename TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    CHECK (status IN ('unreviewed', 'needs_review', 'approved', 'skipped'))
                );
                CREATE INDEX IF NOT EXISTS idx_image_statuses_status ON image_statuses(status);
            """)
            connection.execute("INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)", ("schema_version", "1"))
            if self.images_dir:
                connection.execute("INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)", ("images_dir", self.images_dir))
            connection.execute("INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)", ("annotations_dir", self.annotations_dir))
        if is_new:
            self._migrate_legacy_json()

    def _migrate_legacy_json(self):
        """Copy legacy JSON state once; keep the original file as a safe backup."""
        try:
            with open(self.legacy_path, encoding="utf-8") as file:
                legacy = json.load(file)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return
        if not isinstance(legacy, dict):
            return
        categories = _clean_categories(legacy.get("categories", []))
        statuses = legacy.get("image_statuses", {})
        with self._connect() as connection:
            for position, name in enumerate(categories):
                connection.execute("INSERT OR IGNORE INTO categories(position, name) VALUES (?, ?)", (position, name))
            if isinstance(statuses, dict):
                for filename, record in statuses.items():
                    if not isinstance(record, dict):
                        continue
                    status = record.get("status")
                    if status not in STATUSES:
                        continue
                    connection.execute(
                        "INSERT OR REPLACE INTO image_statuses(filename, status, updated_at) VALUES (?, ?, ?)",
                        (str(filename), status, str(record.get("updated_at") or _now())),
                    )
            connection.execute("INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)", ("migrated_from", PROJECT_FILE))

    @property
    def categories(self):
        with self._connect() as connection:
            return [row["name"] for row in connection.execute("SELECT name FROM categories ORDER BY position")]

    def set_categories(self, categories):
        cleaned = _clean_categories(categories)
        with self._connect() as connection:
            connection.execute("DELETE FROM categories")
            connection.executemany("INSERT INTO categories(position, name) VALUES (?, ?)", enumerate(cleaned))

    def status_for(self, filename):
        with self._connect() as connection:
            row = connection.execute("SELECT status FROM image_statuses WHERE filename = ?", (filename,)).fetchone()
        return row["status"] if row and row["status"] in STATUSES else STATUS_UNREVIEWED

    def set_status(self, filename, status):
        if status not in STATUSES:
            raise ValueError(f"Unsupported review status: {status}")
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO image_statuses(filename, status, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(filename) DO UPDATE SET status = excluded.status, updated_at = excluded.updated_at",
                (filename, status, _now()),
            )

    def mark_batch_for_review(self, filenames):
        changed = False
        with self._connect() as connection:
            for filename in filenames:
                row = connection.execute("SELECT status FROM image_statuses WHERE filename = ?", (filename,)).fetchone()
                current = row["status"] if row else STATUS_UNREVIEWED
                # Never downgrade a completed review when a batch is re-run.
                if current in (STATUS_UNREVIEWED, STATUS_SKIPPED):
                    connection.execute(
                        "INSERT INTO image_statuses(filename, status, updated_at) VALUES (?, ?, ?) "
                        "ON CONFLICT(filename) DO UPDATE SET status = excluded.status, updated_at = excluded.updated_at",
                        (filename, STATUS_NEEDS_REVIEW, _now()),
                    )
                    changed = True
        return changed

    def summary(self, filenames):
        result = {status: 0 for status in STATUSES}
        if not filenames:
            return result
        with self._connect() as connection:
            for filename in filenames:
                row = connection.execute("SELECT status FROM image_statuses WHERE filename = ?", (filename,)).fetchone()
                status = row["status"] if row and row["status"] in STATUSES else STATUS_UNREVIEWED
                result[status] += 1
        return result
