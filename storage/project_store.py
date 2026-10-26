"""项目级 SQLite：审核状态、类别、备注、版本和导出记录。"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager

from .constants import (
    DATABASE_FILE,
    PROJECT_FILE,
    QUALITY_RULES_VERSION,
    STATUSES,
    STATUS_APPROVED,
    STATUS_NEEDS_REVIEW,
    STATUS_SKIPPED,
    STATUS_UNREVIEWED,
)
from .manifest import _clean_categories, _now, resolve_project_directory


class ProjectStore:
    """项目状态保存在根目录的 ``autolabel.db``。

    标注几何仍保存在 ``annotations/`` 的 VOC XML 中；数据库只管理流程状态、
    类别、备注以及导出等记录，方便单独复制项目目录。
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

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

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
                    name TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL DEFAULT ''
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
                CREATE TABLE IF NOT EXISTS current_annotation_quality (
                    filename TEXT PRIMARY KEY,
                    risk_score INTEGER NOT NULL,
                    rules_json TEXT NOT NULL,
                    xml_mtime_ns INTEGER NOT NULL,
                    xml_size INTEGER NOT NULL,
                    image_mtime_ns INTEGER NOT NULL,
                    image_size INTEGER NOT NULL,
                    rules_version INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_current_annotation_quality_risk
                    ON current_annotation_quality(risk_score DESC, filename);
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
            category_columns = {row["name"] for row in connection.execute("PRAGMA table_info(categories)")}
            if "display_name" not in category_columns:
                connection.execute("ALTER TABLE categories ADD COLUMN display_name TEXT NOT NULL DEFAULT ''")
            connection.execute("UPDATE categories SET display_name = name WHERE display_name = ''")
            connection.execute("INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)", ("schema_version", "6"))
            if self.images_dir:
                connection.execute(
                    "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)", ("images_dir", self.images_dir)
                )
            connection.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)", ("annotations_dir", self.annotations_dir)
            )
        if is_new:
            self._migrate_legacy_json()

    def _migrate_legacy_json(self):
        """只迁移一次旧 JSON 状态，并保留原文件作为回退备份。"""
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
            connection.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)", ("migrated_from", PROJECT_FILE)
            )

    @property
    def categories(self):
        with self._connect() as connection:
            return [row["name"] for row in connection.execute("SELECT name FROM categories ORDER BY position")]

    def category_records(self):
        """按保存顺序返回英文机器标签与审核显示名。"""
        with self._connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT position, name, COALESCE(NULLIF(display_name, ''), name) AS display_name "
                    "FROM categories ORDER BY position"
                )
            ]

    def category_display_names(self):
        return {item["name"]: item["display_name"] for item in self.category_records()}

    def set_categories(self, categories):
        cleaned = _clean_categories(categories)
        self.set_category_records([{"name": name, "display_name": name} for name in cleaned])

    def set_category_records(self, records):
        """替换类别表，保证英文机器标签唯一且稳定。"""
        cleaned = []
        seen = set()
        for record in records or []:
            name = str(record.get("name", "") if isinstance(record, dict) else "").strip()
            display_name = str(record.get("display_name", "") if isinstance(record, dict) else "").strip()
            key = name.casefold()
            if not name or key in seen:
                continue
            seen.add(key)
            cleaned.append((name, display_name or name))
        with self._connect() as connection:
            connection.execute("DELETE FROM categories")
            connection.executemany(
                "INSERT INTO categories(position, name, display_name) VALUES (?, ?, ?)",
                [(position, name, display_name) for position, (name, display_name) in enumerate(cleaned)],
            )

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
                connection.executemany(
                    "DELETE FROM spot_checks WHERE filename = ?", [(filename,) for filename in names]
                )
        return len(names)

    def spot_checks_for(self, filenames):
        names = list(dict.fromkeys(str(filename) for filename in filenames))
        result = {}
        if not names:
            return result
        with self._connect() as connection:
            for start in range(0, len(names), 900):
                batch = names[start : start + 900]
                rows = connection.execute(
                    f"SELECT filename, state, sampled_at, completed_at FROM spot_checks WHERE filename IN ({', '.join('?' for _ in batch)} )",
                    batch,
                ).fetchall()
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

    def record_dataset_export(
        self, output_dir, data_yaml, train_images, val_images, object_count, classes, validation_ratio
    ):
        for path in (output_dir, data_yaml):
            if os.path.commonpath((self.project_dir, os.path.abspath(path))) != self.project_dir:
                raise ValueError("导出产物必须保存在项目目录内")
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO dataset_exports(output_path, data_yaml_path, train_images, val_images, object_count, classes_json, validation_ratio, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    os.path.relpath(output_dir, self.project_dir),
                    os.path.relpath(data_yaml, self.project_dir),
                    int(train_images),
                    int(val_images),
                    int(object_count),
                    json.dumps(list(classes), ensure_ascii=False),
                    float(validation_ratio),
                    _now(),
                ),
            )
        return int(cursor.lastrowid)

    def recent_dataset_exports(self, limit=5):
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM dataset_exports ORDER BY id DESC LIMIT ?", (int(limit),)
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["output_path"] = resolve_project_directory(self.project_dir, item["output_path"])
            item["data_yaml_path"] = resolve_project_directory(self.project_dir, item["data_yaml_path"])
            item["classes"] = json.loads(item.pop("classes_json"))
            result.append(item)
        return result

    def record_training_run(self, export_id, run_dir, weights_path, model, epochs):
        for path in (run_dir, weights_path):
            if os.path.commonpath((self.project_dir, os.path.abspath(path))) != self.project_dir:
                raise ValueError("训练产物必须保存在项目目录内")
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO training_runs(export_id, run_path, weights_path, model, epochs, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    export_id,
                    os.path.relpath(run_dir, self.project_dir),
                    os.path.relpath(weights_path, self.project_dir),
                    str(model),
                    int(epochs),
                    _now(),
                ),
            )
        return int(cursor.lastrowid)

    def recent_training_runs(self, limit=5):
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM training_runs ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["run_path"] = resolve_project_directory(self.project_dir, item["run_path"])
            item["weights_path"] = resolve_project_directory(self.project_dir, item["weights_path"])
            result.append(item)
        return result

    def note_for(self, filename):
        """读取图片备注；没有记录时返回空字符串。"""
        with self._connect() as connection:
            row = connection.execute("SELECT note FROM image_notes WHERE filename = ?", (filename,)).fetchone()
        return row["note"] if row else ""

    def set_note(self, filename, note):
        """备注独立保存于数据库，不写入可携带的标注 XML。"""
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO image_notes(filename, note, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(filename) DO UPDATE SET note = excluded.note, updated_at = excluded.updated_at",
                (filename, str(note or ""), _now()),
            )

    def metadata_value(self, key, default=""):
        """从项目数据库读取偏好，避免另建配置文件。"""
        with self._connect() as connection:
            row = connection.execute("SELECT value FROM metadata WHERE key = ?", (str(key),)).fetchone()
        return row["value"] if row else default

    def set_metadata_value(self, key, value):
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
                (str(key), str(value)),
            )

    def record_annotation_version(
        self, filename, snapshot_path, source, model="", prompt="", annotation_count=0, risk_score=0, rules=None
    ):
        """记录预标注前后的不可变 XML 快照。"""
        absolute_path = os.path.abspath(snapshot_path)
        if os.path.commonpath((self.project_dir, absolute_path)) != self.project_dir:
            raise ValueError("标注版本必须保存在项目目录内")
        relative_path = os.path.relpath(absolute_path, self.project_dir)
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO annotation_versions(filename, snapshot_path, source, model, prompt, annotation_count, risk_score, rules_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    filename,
                    relative_path,
                    str(source),
                    str(model),
                    str(prompt),
                    int(annotation_count),
                    int(risk_score),
                    json.dumps(sorted(rules or []), ensure_ascii=False),
                    _now(),
                ),
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

    @staticmethod
    def _file_stamp(path):
        try:
            stat = os.stat(path)
            return stat.st_mtime_ns, stat.st_size
        except OSError:
            return -1, -1

    def quality_stamp(self, filename):
        """当前图片和 XML 的廉价失效键；它不是历史版本快照。"""
        stem = os.path.splitext(filename)[0]
        xml = os.path.join(self.annotations_dir, stem + ".xml")
        image = os.path.join(self.images_dir, filename)
        return (*self._file_stamp(xml), *self._file_stamp(image))

    def quality_records_for(self, filenames):
        """直接查询风险索引，不重新打开图片或 XML。"""
        names = list(dict.fromkeys(str(filename) for filename in filenames))
        result = {}
        if not names:
            return result
        with self._connect() as connection:
            for start in range(0, len(names), 900):
                batch = names[start : start + 900]
                placeholders = ", ".join("?" for _ in batch)
                rows = connection.execute(
                    f"SELECT filename, risk_score, rules_json, xml_mtime_ns, xml_size, image_mtime_ns, image_size, rules_version "
                    f"FROM current_annotation_quality WHERE filename IN ({placeholders})",
                    batch,
                ).fetchall()
                for row in rows:
                    try:
                        rules = frozenset(json.loads(row["rules_json"]))
                    except (TypeError, ValueError):
                        rules = frozenset({"invalid"})
                    result[row["filename"]] = (
                        row["risk_score"],
                        rules,
                        (row["xml_mtime_ns"], row["xml_size"], row["image_mtime_ns"], row["image_size"]),
                        row["rules_version"],
                    )
        return result

    def upsert_quality_records(self, records):
        """只保存与当前源文件时间戳仍匹配的质量结果。"""
        prepared = []
        for filename, risk, rules, stamp in records:
            if self.quality_stamp(filename) != stamp:
                continue
            prepared.append(
                (
                    str(filename),
                    int(risk),
                    json.dumps(sorted(rules), ensure_ascii=False),
                    *stamp,
                    QUALITY_RULES_VERSION,
                    _now(),
                )
            )
        if not prepared:
            return set()
        with self._connect() as connection:
            connection.executemany(
                "INSERT INTO current_annotation_quality "
                "(filename, risk_score, rules_json, xml_mtime_ns, xml_size, image_mtime_ns, image_size, rules_version, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(filename) DO UPDATE SET risk_score=excluded.risk_score, rules_json=excluded.rules_json, "
                "xml_mtime_ns=excluded.xml_mtime_ns, xml_size=excluded.xml_size, "
                "image_mtime_ns=excluded.image_mtime_ns, image_size=excluded.image_size, "
                "rules_version=excluded.rules_version, updated_at=excluded.updated_at",
                prepared,
            )
        return {row[0] for row in prepared}

    def upsert_quality(self, filename, risk, rules):
        self.upsert_quality_records([(filename, risk, rules, self.quality_stamp(filename))])

    def mark_batch_for_review(self, filenames, include_approved=False, include_skipped=False):
        """把预标注结果送入待审核，默认不撤销已通过记录。"""
        ordered = list(dict.fromkeys(str(filename) for filename in filenames))
        statuses = self.statuses_for(ordered)
        pending = [
            filename
            for filename in ordered
            if include_approved
            or statuses.get(filename) == STATUS_UNREVIEWED
            or (include_skipped and statuses.get(filename) == STATUS_SKIPPED)
        ]
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
        """批量读取文件状态，避免逐张图片查询 SQLite。"""
        ordered = list(dict.fromkeys(str(filename) for filename in filenames))
        result = {filename: STATUS_UNREVIEWED for filename in ordered}
        if not ordered:
            return result
        with self._connect() as connection:
            for start in range(0, len(ordered), 900):
                batch = ordered[start : start + 900]
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
