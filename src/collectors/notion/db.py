"""동기화 상태를 SQLite로 관리한다."""

import os
import sqlite3

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
DB_PATH = os.path.join(DATA_DIR, "sync_state.db")


def _get_conn() -> sqlite3.Connection:
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sync_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT NOT NULL DEFAULT 'running',
            total_pages INTEGER DEFAULT 0,
            synced_pages INTEGER DEFAULT 0,
            error_message TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS page_state (
            notion_id TEXT PRIMARY KEY,
            title TEXT,
            last_edited TEXT NOT NULL,
            file_path TEXT NOT NULL,
            synced_at TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn


def start_sync_run(source: str, started_at: str) -> int:
    """동기화 실행 기록을 생성하고 run_id를 반환한다."""
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO sync_log (source, started_at, status) VALUES (?, ?, 'running')",
        (source, started_at),
    )
    conn.commit()
    run_id = cur.lastrowid
    conn.close()
    return run_id


def finish_sync_run(run_id: int, finished_at: str, total: int, synced: int, error: str | None = None):
    """동기화 실행 기록을 완료로 갱신한다."""
    status = "error" if error else "completed"
    conn = _get_conn()
    conn.execute(
        "UPDATE sync_log SET finished_at=?, status=?, total_pages=?, synced_pages=?, error_message=? WHERE id=?",
        (finished_at, status, total, synced, error, run_id),
    )
    conn.commit()
    conn.close()


def upsert_page_state(notion_id: str, title: str, last_edited: str, file_path: str, synced_at: str):
    """페이지별 동기화 상태를 갱신한다."""
    conn = _get_conn()
    conn.execute(
        """INSERT INTO page_state (notion_id, title, last_edited, file_path, synced_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(notion_id) DO UPDATE SET
             title=excluded.title,
             last_edited=excluded.last_edited,
             file_path=excluded.file_path,
             synced_at=excluded.synced_at""",
        (notion_id, title, last_edited, file_path, synced_at),
    )
    conn.commit()
    conn.close()


def get_last_sync_time(source: str) -> str | None:
    """마지막으로 성공한 동기화의 시작 시각을 반환한다."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT started_at FROM sync_log WHERE source=? AND status='completed' ORDER BY id DESC LIMIT 1",
        (source,),
    ).fetchone()
    conn.close()
    return row["started_at"] if row else None


def clear_page_states():
    """모든 페이지 상태를 초기화한다 (full sync 용)."""
    conn = _get_conn()
    conn.execute("DELETE FROM page_state")
    conn.commit()
    conn.close()


def get_page_last_edited(notion_id: str) -> str | None:
    """특정 페이지의 마지막 수정 시각을 반환한다."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT last_edited FROM page_state WHERE notion_id=?",
        (notion_id,),
    ).fetchone()
    conn.close()
    return row["last_edited"] if row else None
