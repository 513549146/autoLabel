"""Per-image human review timing, independent of Qt and model execution."""
from __future__ import annotations

import hashlib
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds")


class ReviewMetricsRecorder:
    IDLE_NS = 60 * 1_000_000_000
    SUSPEND_GAP_NS = 15 * 1_000_000_000

    def __init__(self, project, *, monotonic_ns=None, utc_now=None, instance_id=None,
                 app_version="development", code_revision=None):
        self.project = project
        self._clock = monotonic_ns or time.monotonic_ns
        self._wall = utc_now or _utc_now
        self.instance_id = instance_id or str(uuid.uuid4())
        self.app_version = app_version
        self.code_revision = code_revision if code_revision is not None else os.environ.get("AUTOLABEL_BUILD_REVISION", "")
        self.session_id: str | None = None
        self.filename: str | None = None
        self.active_ns = 0
        self._last_tick_ns = 0
        self._last_input_ns = 0
        self._last_input_utc = ""
        self._active = False

    def recover_interrupted(self):
        cutoff = self._wall() - timedelta(seconds=30)
        return self.project.recover_interrupted_review_sessions(_stamp(cutoff), self.instance_id)

    def start(self, filename: str, xml_path: str | Path | None = None):
        if self.session_id and self.filename == filename:
            return self.session_id
        if self.session_id:
            self.finish("navigate")
        self.recover_interrupted()
        opened_digest = None
        if xml_path and Path(xml_path).is_file():
            opened_digest = hashlib.sha256(Path(xml_path).read_bytes()).hexdigest()
        now = self._wall()
        stamp = _stamp(now)
        self.session_id = self.project.start_review_session(
            filename, stamp, opened_digest,
            self.project.latest_preannotation_version_id(filename),
            self.app_version, self.code_revision, self.instance_id,
        )
        self.filename = filename
        self.active_ns = 0
        self._last_tick_ns = self._last_input_ns = self._clock()
        self._last_input_utc = stamp
        self._active = True
        return self.session_id

    def _advance(self, now_ns: int):
        if not self.session_id or not self._active:
            return
        elapsed = max(0, now_ns - self._last_tick_ns)
        if elapsed > self.SUSPEND_GAP_NS:
            self._active = False
            self._last_tick_ns = now_ns
            return
        counted_until = min(now_ns, self._last_input_ns + self.IDLE_NS)
        self.active_ns += max(0, counted_until - self._last_tick_ns)
        self._last_tick_ns = now_ns
        if now_ns >= self._last_input_ns + self.IDLE_NS:
            self._active = False

    @property
    def active_ms(self) -> int:
        return self.active_ns // 1_000_000

    def heartbeat(self):
        if not self.session_id:
            return
        self._advance(self._clock())
        self.project.checkpoint_review_session(
            self.session_id, self.active_ms, _stamp(self._wall()), self._last_input_utc,
        )
        self.recover_interrupted()

    def note_input(self):
        if not self.session_id:
            return
        now_ns = self._clock()
        self._advance(now_ns)
        self._last_tick_ns = now_ns
        self._last_input_ns = now_ns
        self._last_input_utc = _stamp(self._wall())
        self._active = True

    def pause(self):
        if not self.session_id:
            return
        self._advance(self._clock())
        self._active = False
        self.heartbeat()

    def record(self, event_type: str, **fields):
        if not self.session_id:
            return None
        return self.project.record_review_event(
            self.session_id, self.filename, _stamp(self._wall()), event_type, **fields,
        )

    def finish(self, reason: str, outcome: str = "none"):
        if not self.session_id:
            return
        self._advance(self._clock())
        self.project.finish_review_session(
            self.session_id, self.active_ms, _stamp(self._wall()), reason, outcome,
        )
        self.session_id = self.filename = None
        self._active = False
