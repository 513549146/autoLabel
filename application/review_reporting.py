"""Deterministic local summaries of human review time and edit actions."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path


ACTION_FIELDS = ("add_count", "delete_count", "class_change_count", "geometry_change_count")


def build_review_activity_report(project_dir: str | Path, image_names: list[str]) -> dict:
    names = [str(name).strip() for name in image_names]
    if not names or any(not name or Path(name).name != name for name in names):
        raise ValueError("评测清单必须包含项目图片文件名，不能包含路径或空行")
    if len(names) != len(set(names)):
        raise ValueError("评测清单包含重复图片")
    database = Path(project_dir) / "autolabel.db"
    if not database.is_file():
        raise FileNotFoundError(f"项目数据库不存在：{database}")

    per_image = {name: {"current_status": "unreviewed", "active_ms": 0, "sessions": 0, "revisits": 0,
                        "approved": 0, "skipped": 0, "spot_check_pass": 0,
                        "undo": 0, "redo": 0, **{field: 0 for field in ACTION_FIELDS}}
                 for name in names}
    incomplete = 0
    uri = database.resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=10)) as connection:
        connection.row_factory = sqlite3.Row
        tables = {row["name"] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"review_sessions", "review_events"} <= tables:
            raise ValueError("项目尚无审核统计表；请先用新版应用打开此项目")
        for start in range(0, len(names), 500):
            batch = names[start:start + 500]
            placeholders = ",".join("?" for _ in batch)
            for row in connection.execute(
                f"SELECT filename, status FROM image_statuses WHERE filename IN ({placeholders})", batch
            ):
                per_image[row["filename"]]["current_status"] = row["status"]
            for row in connection.execute(
                f"SELECT filename, active_ms, ended_at_utc, outcome FROM review_sessions "
                f"WHERE filename IN ({placeholders}) ORDER BY started_at_utc, session_id", batch
            ):
                item = per_image[row["filename"]]
                item["sessions"] += 1
                item["active_ms"] += int(row["active_ms"])
                if row["outcome"] in {"approved", "skipped", "spot_check_pass"}:
                    item[row["outcome"]] += 1
                if row["ended_at_utc"] is None:
                    incomplete += 1
            for row in connection.execute(
                f"SELECT filename, event_type, {', '.join(ACTION_FIELDS)} FROM review_events "
                f"WHERE filename IN ({placeholders}) ORDER BY id", batch
            ):
                item = per_image[row["filename"]]
                if row["event_type"] == "edit_commit":
                    for field in ACTION_FIELDS:
                        item[field] += int(row[field])
                elif row["event_type"] in {"undo", "redo"}:
                    item[row["event_type"]] += 1

    for item in per_image.values():
        item["revisits"] = max(0, item["sessions"] - 1)
    total_ms = sum(item["active_ms"] for item in per_image.values())
    totals = {field: sum(item[field] for item in per_image.values())
              for field in ("sessions", "revisits", "approved", "skipped", "spot_check_pass",
                            "undo", "redo", *ACTION_FIELDS)}
    unopened = sum(item["sessions"] == 0 for item in per_image.values())
    unresolved = sum(item["current_status"] != "approved" for item in per_image.values())
    observed_minutes = round(1000 * total_ms / 60000 / len(names), 3)
    totals.update({"images": len(names), "opened_images": len(names) - unopened,
                   "unopened_images": unopened, "unresolved_images": unresolved,
                   "incomplete_sessions": incomplete,
                   "active_ms": total_ms,
                   "observed_minutes_per_1000_images": observed_minutes,
                   "minutes_per_1000_images": observed_minutes if not unopened and not unresolved and not incomplete else None})
    return {"contract_version": 1, "metric": "human_review_activity",
            "quality_recall": None, "quality_note": "召回率需用独立真值集和封存的模型输出另行评估",
            "totals": totals, "images": {name: per_image[name] for name in sorted(per_image)}}
