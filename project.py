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
ANNOTATION_VERSION_DIR = "annotation_versions"
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
            "models": "models",
            "annotation_versions": ANNOTATION_VERSION_DIR,
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

    def forget_project(self, project_dir):
        """Remove a project from the launcher history without touching its files."""
        root = os.path.abspath(project_dir)
        try:
            with self._connect() as connection:
                connection.execute("DELETE FROM recent_projects WHERE path = ?", (root,))
                connection.execute("DELETE FROM settings WHERE key = ? AND value = ?", ("last_project", root))
        except sqlite3.Error:
            # History is convenience data. A read-only portable installation must
            # not block closing or deleting a valid on-disk project.
            return

    def clear_last_project(self, project_dir):
        """Stop auto-restoring one project while retaining its recent-project entry."""
        root = os.path.abspath(project_dir)
        try:
            with self._connect() as connection:
                connection.execute("DELETE FROM settings WHERE key = ? AND value = ?", ("last_project", root))
        except sqlite3.Error:
            return


class ProjectStore:
    """SQLite-backed project state, stored as ``autolabel.db`` at the project root.

    VOC XML remains in ``annotations`` for portability; the database only owns
    workflow data such as statuses, categories, reviewer notes, and project paths.
    """

    def __init__(self, annotations_dir, images_dir=None, project_dir=None):
        self.annotations_dir = os.path.abspath(annotations_dir)
        self.images_dir = os.path.abspath(images_dir) if images_dir else ""
        self.project_dir = os.path.abspath(project_dir or self._infer_project_dir())
        for label, directory in (("标注目录", self.annotations_dir), ("图片目录", self.images_dir)):
            if directory:
                try:
                    inside_project = os.path.commonpath((self.project_dir, directory)) == self.project_dir
                except ValueError:
                    inside_project = False
                if not inside_project:
                    raise ValueError(f"{label}必须位于项目文件夹内")
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
                CREATE TABLE IF NOT EXISTS image_notes (
                    filename TEXT PRIMARY KEY,
                    note TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS annotation_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    filename TEXT NOT NULL,
                    snapshot_path TEXT NOT NULL,
                    source TEXT NOT NULL,
                    model TEXT NOT NULL DEFAULT '',
                    prompt TEXT NOT NULL DEFAULT '',
                    annotation_count INTEGER NOT NULL DEFAULT 0,
                    risk_score INTEGER NOT NULL DEFAULT 0,
                    rules_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_annotation_versions_filename_created
                    ON annotation_versions(filename, created_at DESC);
                CREATE TABLE IF NOT EXISTS dataset_exports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    output_path TEXT NOT NULL,
                    data_yaml_path TEXT NOT NULL,
                    train_images INTEGER NOT NULL,
                    val_images INTEGER NOT NULL,
                    object_count INTEGER NOT NULL,
                    classes_json TEXT NOT NULL,
                    validation_ratio REAL NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS training_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    export_id INTEGER,
                    run_path TEXT NOT NULL,
                    weights_path TEXT NOT NULL,
                    model TEXT NOT NULL,
                    epochs INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(export_id) REFERENCES dataset_exports(id)
                );
                CREATE TABLE IF NOT EXISTS spot_checks (
                    filename TEXT PRIMARY KEY,
                    state TEXT NOT NULL CHECK (state IN ('sampled', 'passed')),
                    sampled_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL DEFAULT ''
                );
            """)
            connection.execute("INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)", ("schema_version", "4"))
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
            if status != STATUS_APPROVED:
                connection.execute("DELETE FROM spot_checks WHERE filename = ?", (filename,))

    def set_statuses(self, filenames, status):
        if status not in STATUSES:
            raise ValueError(f"Unsupported review status: {status}")
        names = list(dict.fromkeys(str(filename) for filename in filenames))
        if not names:
            return 0
        with self._connect() as connection:
            connection.executemany(
                "INSERT INTO image_statuses(filename, status, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(filename) DO UPDATE SET status = excluded.status, updated_at = excluded.updated_at",
                [(filename, status, _now()) for filename in names],
            )
            if status != STATUS_APPROVED:
                connection.executemany("DELETE FROM spot_checks WHERE filename = ?", [(filename,) for filename in names])
        return len(names)

    def spot_checks_for(self, filenames):
        names = list(dict.fromkeys(str(filename) for filename in filenames))
        result = {}
        if not names:
            return result
        with self._connect() as connection:
            for start in range(0, len(names), 900):
                batch = names[start:start + 900]
                rows = connection.execute(f"SELECT filename, state, sampled_at, completed_at FROM spot_checks WHERE filename IN ({', '.join('?' for _ in batch)} )", batch).fetchall()
                result.update({row["filename"]: dict(row) for row in rows})
        return result

    def mark_spot_checks(self, filenames, state="sampled"):
        if state not in {"sampled", "passed"}:
            raise ValueError("Unsupported spot-check state")
        names = list(dict.fromkeys(str(filename) for filename in filenames))
        now = _now()
        with self._connect() as connection:
            connection.executemany(
                "INSERT INTO spot_checks(filename, state, sampled_at, completed_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(filename) DO UPDATE SET state=excluded.state, sampled_at=excluded.sampled_at, completed_at=excluded.completed_at",
                [(filename, state, now, now if state == "passed" else "") for filename in names],
            )

    def record_dataset_export(self, output_dir, data_yaml, train_images, val_images, object_count, classes, validation_ratio):
        for path in (output_dir, data_yaml):
            if os.path.commonpath((self.project_dir, os.path.abspath(path))) != self.project_dir:
                raise ValueError("导出产物必须保存在项目目录内")
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO dataset_exports(output_path, data_yaml_path, train_images, val_images, object_count, classes_json, validation_ratio, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (os.path.relpath(output_dir, self.project_dir), os.path.relpath(data_yaml, self.project_dir), int(train_images), int(val_images), int(object_count), json.dumps(list(classes), ensure_ascii=False), float(validation_ratio), _now()),
            )
        return int(cursor.lastrowid)

    def recent_dataset_exports(self, limit=5):
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM dataset_exports ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
        result = []
        for row in rows:
            item = dict(row); item["output_path"] = resolve_project_directory(self.project_dir, item["output_path"]); item["data_yaml_path"] = resolve_project_directory(self.project_dir, item["data_yaml_path"]); item["classes"] = json.loads(item.pop("classes_json")); result.append(item)
        return result

    def record_training_run(self, export_id, run_dir, weights_path, model, epochs):
        for path in (run_dir, weights_path):
            if os.path.commonpath((self.project_dir, os.path.abspath(path))) != self.project_dir:
                raise ValueError("训练产物必须保存在项目目录内")
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO training_runs(export_id, run_path, weights_path, model, epochs, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (export_id, os.path.relpath(run_dir, self.project_dir), os.path.relpath(weights_path, self.project_dir), str(model), int(epochs), _now()),
            )
        return int(cursor.lastrowid)

    def recent_training_runs(self, limit=5):
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM training_runs ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
        result = []
        for row in rows:
            item = dict(row); item["run_path"] = resolve_project_directory(self.project_dir, item["run_path"]); item["weights_path"] = resolve_project_directory(self.project_dir, item["weights_path"]); result.append(item)
        return result

    def note_for(self, filename):
        """Return the reviewer note for an image, or an empty string."""
        with self._connect() as connection:
            row = connection.execute("SELECT note FROM image_notes WHERE filename = ?", (filename,)).fetchone()
        return row["note"] if row else ""

    def set_note(self, filename, note):
        """Persist the note independently from the portable annotation XML."""
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO image_notes(filename, note, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(filename) DO UPDATE SET note = excluded.note, updated_at = excluded.updated_at",
                (filename, str(note or ""), _now()),
            )

    def metadata_value(self, key, default=""):
        """Read a project-scoped preference without adding a separate config file."""
        with self._connect() as connection:
            row = connection.execute("SELECT value FROM metadata WHERE key = ?", (str(key),)).fetchone()
        return row["value"] if row else default

    def set_metadata_value(self, key, value):
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
                (str(key), str(value)),
            )

    def record_annotation_version(self, filename, snapshot_path, source, model="", prompt="", annotation_count=0, risk_score=0, rules=None):
        """Register an immutable XML snapshot created before/after pre-labeling."""
        absolute_path = os.path.abspath(snapshot_path)
        if os.path.commonpath((self.project_dir, absolute_path)) != self.project_dir:
            raise ValueError("标注版本必须保存在项目目录内")
        relative_path = os.path.relpath(absolute_path, self.project_dir)
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO annotation_versions(filename, snapshot_path, source, model, prompt, annotation_count, risk_score, rules_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (filename, relative_path, str(source), str(model), str(prompt), int(annotation_count), int(risk_score), json.dumps(sorted(rules or []), ensure_ascii=False), _now()),
            )

    def annotation_versions_for(self, filename):
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, snapshot_path, source, model, prompt, annotation_count, risk_score, rules_json, created_at "
                "FROM annotation_versions WHERE filename = ? ORDER BY created_at DESC, id DESC",
                (filename,),
            ).fetchall()
        versions = []
        for row in rows:
            item = dict(row)
            item["path"] = resolve_project_directory(self.project_dir, item.pop("snapshot_path"))
            try:
                item["rules"] = json.loads(item.pop("rules_json"))
            except (TypeError, json.JSONDecodeError):
                item["rules"] = []
            versions.append(item)
        return versions

    def mark_batch_for_review(self, filenames):
        ordered = list(dict.fromkeys(str(filename) for filename in filenames))
        statuses = self.statuses_for(ordered)
        pending = [filename for filename in ordered if statuses.get(filename) in (STATUS_UNREVIEWED, STATUS_SKIPPED)]
        if not pending:
            return False
        with self._connect() as connection:
            connection.executemany(
                "INSERT INTO image_statuses(filename, status, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(filename) DO UPDATE SET status = excluded.status, updated_at = excluded.updated_at",
                [(filename, STATUS_NEEDS_REVIEW, _now()) for filename in pending],
            )
        return True

    def statuses_for(self, filenames):
        """Return statuses for a file list with batched SQLite reads."""
        ordered = list(dict.fromkeys(str(filename) for filename in filenames))
        result = {filename: STATUS_UNREVIEWED for filename in ordered}
        if not ordered:
            return result
        with self._connect() as connection:
            for start in range(0, len(ordered), 900):
                batch = ordered[start:start + 900]
                placeholders = ", ".join("?" for _ in batch)
                rows = connection.execute(
                    f"SELECT filename, status FROM image_statuses WHERE filename IN ({placeholders})", batch
                ).fetchall()
                for row in rows:
                    if row["status"] in STATUSES:
                        result[row["filename"]] = row["status"]
        return result

    def summary(self, filenames):
        result = {status: 0 for status in STATUSES}
        for status in self.statuses_for(filenames).values():
            result[status] += 1
        return result
