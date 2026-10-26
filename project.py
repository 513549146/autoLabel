"""项目存储的兼容入口。

新代码按职责放在 storage/；现有 ``from project import ...`` 无需修改。
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from storage.constants import (
    ANNOTATION_VERSION_DIR,
    DATABASE_FILE,
    PROJECT_FILE,
    QUALITY_RULES_VERSION,
    STATUSES,
    STATUS_APPROVED,
    STATUS_LABELS,
    STATUS_NEEDS_REVIEW,
    STATUS_SKIPPED,
    STATUS_UNREVIEWED,
    WORKBENCH_FILE,
    WORKSPACE_DATABASE_FILE,
)
from storage.manifest import (
    _clean_categories,
    _now,
    create_project_manifest,
    load_project_manifest,
    project_manifest_path,
    resolve_project_directory,
)
from storage.project_store import ProjectStore
from storage.workspace import WorkspaceStore
