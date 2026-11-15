"""SQLite persistence: analyses, uploads, frames, and quota accounting.

One database file per user under the cache directory. Connections are short-lived
and opened per operation so MCP tool calls running in worker threads do not share
a connection. The directory is 0700 and the database 0600.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import Quota
from .errors import QuotaExhausted
from .models import MediaInfo, SourceRef, Usage

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    canonical_id TEXT NOT NULL,
    path_or_url TEXT NOT NULL,
    duration_s REAL,
    size_bytes INTEGER,
    mime TEXT,
    first_seen_at INTEGER NOT NULL,
    last_seen_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS uploads (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    uri TEXT NOT NULL,
    mime TEXT NOT NULL,
    uploaded_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS analyses (
    cache_key TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    tool TEXT NOT NULL,
    request_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    model TEXT NOT NULL,
    detail TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    hit_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS analyses_source ON analyses(source_id);
CREATE INDEX IF NOT EXISTS analyses_expiry ON analyses(expires_at);

CREATE TABLE IF NOT EXISTS frames (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    timestamp_ms INTEGER NOT NULL,
    path TEXT NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    size_bytes INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    last_used_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS frames_source ON frames(source_id);
CREATE INDEX IF NOT EXISTS frames_lru ON frames(last_used_at);

CREATE TABLE IF NOT EXISTS usage_counters (
    pacific_date TEXT NOT NULL,
    model TEXT NOT NULL,
    requests INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (pacific_date, model)
);

CREATE TABLE IF NOT EXISTS usage_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    model TEXT NOT NULL,
    tokens INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS usage_events_window ON usage_events(model, ts);
"""

_tz_warning_emitted = False


def pacific_date(now: datetime | None = None) -> str:
    """Return the quota day in America/Los_Angeles, falling back to UTC."""

    global _tz_warning_emitted
    moment = now or datetime.now(UTC)
    try:
        zone = ZoneInfo("America/Los_Angeles")
    except ZoneInfoNotFoundError:
        if not _tz_warning_emitted:
            log.warning("tz database unavailable; quota days fall back to UTC")
            _tz_warning_emitted = True
        return moment.astimezone(UTC).date().isoformat()
    return moment.astimezone(zone).date().isoformat()


class Cache:
    def __init__(
        self, data_dir: Path, *, ttl_seconds: int = 7 * 24 * 3600, max_mb: int = 512
    ) -> None:
        self.data_dir = data_dir
        self.ttl_seconds = ttl_seconds
        self.max_mb = max_mb
        self.frames_dir = data_dir / "frames"
        self.db_path = data_dir / "cache.db"
        self._quota_lock = threading.Lock()
        self._initialise()

    def _initialise(self) -> None:
        self.frames_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            self.data_dir.chmod(0o700)
            self.frames_dir.chmod(0o700)
        except OSError:
            pass
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(_SCHEMA)
        with suppress(OSError):
            self.db_path.chmod(0o600)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    # -- sources ---------------------------------------------------------

    @staticmethod
    def source_id(source: SourceRef) -> str:
        return hashlib.sha256(source.canonical_id.encode()).hexdigest()[:32]

    def record_source(self, source: SourceRef, media: MediaInfo | None = None) -> str:
        source_id = self.source_id(source)
        now = int(time.time())
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO sources (id, kind, canonical_id, path_or_url, duration_s, size_bytes,
                                     mime, first_seen_at, last_seen_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    duration_s=excluded.duration_s,
                    size_bytes=excluded.size_bytes,
                    mime=excluded.mime,
                    last_seen_at=excluded.last_seen_at
                """,
                (
                    source_id,
                    source.kind,
                    source.canonical_id,
                    source.display,
                    media.duration_s if media else None,
                    media.size_bytes if media else None,
                    media.mime_type if media else None,
                    now,
                    now,
                ),
            )
        return source_id

    def cached_analyses(self, source_id: str) -> int:
        now = int(time.time())
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS n FROM analyses WHERE source_id=? AND expires_at>?",
                (source_id, now),
            ).fetchone()
        return int(row["n"]) if row else 0

    # -- analyses --------------------------------------------------------

    def get_analysis(self, cache_key: str) -> dict[str, Any] | None:
        now = int(time.time())
        with self._connect() as connection:
            row = connection.execute(
                "SELECT result_json, expires_at FROM analyses WHERE cache_key=?",
                (cache_key,),
            ).fetchone()
            if row is None:
                return None
            if row["expires_at"] <= now:
                connection.execute("DELETE FROM analyses WHERE cache_key=?", (cache_key,))
                return None
            connection.execute(
                "UPDATE analyses SET hit_count=hit_count+1 WHERE cache_key=?", (cache_key,)
            )
        try:
            result = json.loads(row["result_json"])
        except json.JSONDecodeError:
            return None
        return result if isinstance(result, dict) else None

    def put_analysis(
        self,
        *,
        cache_key: str,
        source_id: str,
        tool: str,
        request: dict[str, Any],
        result: dict[str, Any],
        model: str,
        detail: str,
        usage: Usage,
    ) -> None:
        now = int(time.time())
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO analyses
                    (cache_key, source_id, tool, request_json, result_json, model, detail,
                     input_tokens, output_tokens, created_at, expires_at, hit_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    cache_key,
                    source_id,
                    tool,
                    json.dumps(request, sort_keys=True),
                    json.dumps(result),
                    model,
                    detail,
                    usage.input_tokens or 0,
                    usage.output_tokens or 0,
                    now,
                    now + self.ttl_seconds,
                ),
            )

    # -- uploads ---------------------------------------------------------

    def get_upload(self, source_id: str, fingerprint: str) -> tuple[str, str] | None:
        now = int(time.time())
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT uri, mime FROM uploads
                WHERE source_id=? AND fingerprint=? AND expires_at>?
                ORDER BY uploaded_at DESC LIMIT 1
                """,
                (source_id, fingerprint, now),
            ).fetchone()
        if row is None:
            return None
        return str(row["uri"]), str(row["mime"])

    def put_upload(self, source_id: str, fingerprint: str, uri: str, mime: str) -> None:
        now = int(time.time())
        upload_id = hashlib.sha256(f"{source_id}:{fingerprint}".encode()).hexdigest()[:32]
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO uploads
                    (id, source_id, fingerprint, uri, mime, uploaded_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (upload_id, source_id, fingerprint, uri, mime, now, now + 48 * 3600),
            )

    # -- frames ----------------------------------------------------------

    def get_frame(
        self, source_id: str, timestamp_ms: int, width: int
    ) -> tuple[Path, int, int] | None:
        frame_id = _frame_id(source_id, timestamp_ms, width)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT path, width, height FROM frames WHERE id=?", (frame_id,)
            ).fetchone()
            if row is None:
                return None
            path = Path(row["path"])
            if not path.exists():
                connection.execute("DELETE FROM frames WHERE id=?", (frame_id,))
                return None
            connection.execute(
                "UPDATE frames SET last_used_at=? WHERE id=?", (int(time.time()), frame_id)
            )
            return path, int(row["width"]), int(row["height"])

    def store_frame(
        self,
        *,
        source_id: str,
        timestamp: float,
        width: int,
        jpeg_path: Path,
        frame_width: int,
        frame_height: int,
    ) -> tuple[Path, bool]:
        """Move an extracted JPEG into the cache; returns (path, cache_hit)."""

        frame_id = _frame_id(source_id, int(round(timestamp * 1000)), width)
        data_hash = _file_digest(jpeg_path)
        destination = self.frames_dir / f"{data_hash}.jpg"
        if destination.exists():
            jpeg_path.unlink(missing_ok=True)
            hit = True
        else:
            os.replace(jpeg_path, destination)
            with suppress(OSError):
                destination.chmod(0o600)
            hit = False

        now = int(time.time())
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO frames
                    (id, source_id, timestamp_ms, path, width, height, size_bytes,
                     created_at, last_used_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    frame_id,
                    source_id,
                    int(round(timestamp * 1000)),
                    str(destination),
                    frame_width,
                    frame_height,
                    destination.stat().st_size,
                    now,
                    now,
                ),
            )
        self.prune_frames()
        return destination, hit

    def prune_frames(self) -> int:
        limit = self.max_mb * 1024 * 1024
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, path, size_bytes FROM frames ORDER BY last_used_at ASC"
            ).fetchall()
            total = sum(int(row["size_bytes"]) for row in rows)
            removed = 0
            for row in rows:
                if total <= limit:
                    break
                path = Path(row["path"])
                try:
                    size = path.stat().st_size
                except OSError:
                    size = int(row["size_bytes"])
                path.unlink(missing_ok=True)
                connection.execute("DELETE FROM frames WHERE id=?", (row["id"],))
                total -= size
                removed += 1
        return removed

    # -- quota -----------------------------------------------------------

    def reserve_quota(
        self,
        model: str,
        quota: Quota | None,
        estimated_tokens: int,
        *,
        now: float | None = None,
    ) -> int | None:
        """Check RPD/RPM/TPM and reserve a call. Raises QuotaExhausted."""

        moment = time.time() if now is None else now
        day = pacific_date(datetime.fromtimestamp(moment, UTC))
        with self._quota_lock, self._connect() as connection:
            row = connection.execute(
                "SELECT requests FROM usage_counters WHERE pacific_date=? AND model=?",
                (day, model),
            ).fetchone()
            used_requests = int(row["requests"]) if row else 0

            if quota and quota.rpd is not None and used_requests >= quota.rpd:
                raise QuotaExhausted(
                    f"daily request limit reached for {model} ({quota.rpd} RPD)",
                    hint=f"quota resets after midnight Pacific ({day}); cached answers still work",
                )

            window_start = moment - 60.0
            window = connection.execute(
                "SELECT COUNT(*) AS n, COALESCE(SUM(tokens), 0) AS tokens FROM usage_events "
                "WHERE model=? AND ts>=?",
                (model, window_start),
            ).fetchone()
            window_count = int(window["n"]) if window else 0
            window_tokens = int(window["tokens"]) if window else 0

            if quota and quota.rpm is not None and window_count >= quota.rpm:
                raise QuotaExhausted(
                    f"per-minute request limit reached for {model} ({quota.rpm} RPM)",
                    hint="wait about a minute, or use fewer requests",
                )
            if quota and quota.tpm is not None and window_tokens + estimated_tokens > quota.tpm:
                raise QuotaExhausted(
                    f"per-minute token limit reached for {model} ({quota.tpm} TPM)",
                    hint="wait about a minute, narrow the range, or raise the configured limit",
                )

            connection.execute(
                "INSERT INTO usage_counters (pacific_date, model, requests) VALUES (?, ?, 1) "
                "ON CONFLICT(pacific_date, model) DO UPDATE SET requests=requests+1",
                (day, model),
            )
            cursor = connection.execute(
                "INSERT INTO usage_events (ts, model, tokens) VALUES (?, ?, ?)",
                (moment, model, max(0, int(estimated_tokens))),
            )
            return int(cursor.lastrowid) if cursor.lastrowid is not None else None

    def settle_quota(
        self,
        reservation_id: int | None,
        model: str,
        usage: Usage,
        *,
        now: float | None = None,
    ) -> None:
        if reservation_id is None:
            return
        moment = time.time() if now is None else now
        day = pacific_date(datetime.fromtimestamp(moment, UTC))
        tokens = (
            (usage.input_tokens or 0)
            + (usage.output_tokens or 0)
            + (usage.thought_tokens or 0)
        )
        with self._quota_lock, self._connect() as connection:
            connection.execute(
                "UPDATE usage_events SET tokens=? WHERE id=?", (max(0, tokens), reservation_id)
            )
            connection.execute(
                "UPDATE usage_counters SET input_tokens=input_tokens+?, "
                "output_tokens=output_tokens+? WHERE pacific_date=? AND model=?",
                (usage.input_tokens or 0, usage.output_tokens or 0, day, model),
            )

    def release_quota(self, reservation_id: int | None, *, now: float | None = None) -> None:
        if reservation_id is None:
            return
        moment = time.time() if now is None else now
        day = pacific_date(datetime.fromtimestamp(moment, UTC))
        with self._quota_lock, self._connect() as connection:
            row = connection.execute(
                "SELECT model FROM usage_events WHERE id=?", (reservation_id,)
            ).fetchone()
            if row is None:
                return
            model = str(row["model"])
            connection.execute("DELETE FROM usage_events WHERE id=?", (reservation_id,))
            connection.execute(
                "UPDATE usage_counters SET requests=MAX(0, requests-1) "
                "WHERE pacific_date=? AND model=?",
                (day, model),
            )

    def usage_today(self, model: str, *, now: datetime | None = None) -> dict[str, int]:
        day = pacific_date(now)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT requests, input_tokens, output_tokens FROM usage_counters "
                "WHERE pacific_date=? AND model=?",
                (day, model),
            ).fetchone()
        if row is None:
            return {"requests": 0, "input_tokens": 0, "output_tokens": 0}
        return {
            "requests": int(row["requests"]),
            "input_tokens": int(row["input_tokens"]),
            "output_tokens": int(row["output_tokens"]),
        }

    def window_usage(self, model: str, *, now: float | None = None) -> tuple[int, int]:
        moment = time.time() if now is None else now
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS n, COALESCE(SUM(tokens), 0) AS tokens FROM usage_events "
                "WHERE model=? AND ts>=?",
                (model, moment - 60.0),
            ).fetchone()
        return (int(row["n"]), int(row["tokens"])) if row else (0, 0)


def _frame_id(source_id: str, timestamp_ms: int, width: int) -> str:
    return hashlib.sha256(f"{source_id}:{timestamp_ms}:{width}".encode()).hexdigest()[:32]


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()[:40]
