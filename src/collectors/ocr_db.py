"""OCR 처리 상태 관리 (SQLite) — 스킵/실패 파일 추적."""

import os
import sqlite3
from datetime import datetime, timezone

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DB_PATH = os.path.join(PROJECT_ROOT, "data", "raw", "ocr_state.db")


def _get_conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS ocr_skip_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path   TEXT NOT NULL,
            file_size   INTEGER NOT NULL,
            reason      TEXT NOT NULL,
            error_message TEXT,
            created_at  TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_skip_reason ON ocr_skip_log (reason);
        CREATE INDEX IF NOT EXISTS idx_skip_path ON ocr_skip_log (file_path);

        CREATE TABLE IF NOT EXISTS ocr_benchmark_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            workers     INTEGER NOT NULL,
            file_count  INTEGER NOT NULL,
            size_bucket TEXT NOT NULL,
            peak_memory_mb REAL NOT NULL,
            baseline_memory_mb REAL NOT NULL,
            delta_memory_mb REAL NOT NULL,
            avg_time_sec REAL NOT NULL,
            total_time_sec REAL NOT NULL,
            system_memory_pct REAL NOT NULL,
            created_at  TEXT NOT NULL
        );
    """)
    conn.commit()
    return conn


def log_skip(file_path: str, file_size: int, reason: str, error_message: str | None = None) -> None:
    """스킵/실패 파일 기록.

    reason: 'size_exceeded', 'memory_limit', 'error', 'empty', 'timeout'
    """
    conn = _get_conn()
    conn.execute(
        "INSERT INTO ocr_skip_log (file_path, file_size, reason, error_message, created_at) VALUES (?, ?, ?, ?, ?)",
        (file_path, file_size, reason, error_message, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()


def log_benchmark(
    workers: int,
    file_count: int,
    size_bucket: str,
    peak_memory_mb: float,
    baseline_memory_mb: float,
    avg_time_sec: float,
    total_time_sec: float,
    system_memory_pct: float,
) -> None:
    """벤치마크 결과 기록."""
    conn = _get_conn()
    conn.execute(
        """INSERT INTO ocr_benchmark_log
           (workers, file_count, size_bucket, peak_memory_mb, baseline_memory_mb,
            delta_memory_mb, avg_time_sec, total_time_sec, system_memory_pct, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (workers, file_count, size_bucket, peak_memory_mb, baseline_memory_mb,
         peak_memory_mb - baseline_memory_mb, avg_time_sec, total_time_sec,
         system_memory_pct, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()


def get_skip_summary() -> list[dict]:
    """reason별 스킵 건수 요약."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT reason, COUNT(*) as count, SUM(file_size) as total_size FROM ocr_skip_log GROUP BY reason ORDER BY count DESC"
    ).fetchall()
    conn.close()
    return [{"reason": r["reason"], "count": r["count"], "total_size_mb": r["total_size"] / 1024 / 1024} for r in rows]


def get_skipped_files(reason: str | None = None, limit: int = 100) -> list[dict]:
    """스킵된 파일 목록 조회."""
    conn = _get_conn()
    if reason:
        rows = conn.execute(
            "SELECT file_path, file_size, reason, error_message, created_at FROM ocr_skip_log WHERE reason=? ORDER BY created_at DESC LIMIT ?",
            (reason, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT file_path, file_size, reason, error_message, created_at FROM ocr_skip_log ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def clear_skip_log() -> int:
    """스킵 로그 전체 삭제. 삭제 건수 반환."""
    conn = _get_conn()
    cur = conn.execute("DELETE FROM ocr_skip_log")
    count = cur.rowcount
    conn.commit()
    conn.close()
    return count
