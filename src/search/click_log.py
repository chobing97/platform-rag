"""클릭 로그 수집 및 부스팅 점수 계산."""

import os
import sqlite3
from collections import defaultdict

from config import DATA_DIR

DB_PATH = os.path.join(DATA_DIR, "click_log.db")


def _get_conn() -> sqlite3.Connection:
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS clicks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query TEXT NOT NULL,
            doc_id TEXT NOT NULL,
            rank INTEGER NOT NULL,
            clicked_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    return conn


def log_click(query: str, doc_id: str, rank: int):
    """클릭 이벤트를 기록한다."""
    conn = _get_conn()
    conn.execute(
        "INSERT INTO clicks (query, doc_id, rank) VALUES (?, ?, ?)",
        (query, doc_id, rank),
    )
    conn.commit()
    conn.close()


def get_boost_scores() -> dict[str, float]:
    """문서별 클릭 부스팅 점수를 반환한다.

    점수 = sum(1 / rank) — 상위에서 클릭될수록 높은 가중치.
    """
    conn = _get_conn()
    rows = conn.execute("SELECT doc_id, rank FROM clicks").fetchall()
    conn.close()

    scores: dict[str, float] = defaultdict(float)
    for row in rows:
        scores[row["doc_id"]] += 1.0 / row["rank"]

    # 최대값으로 정규화 (0~1)
    if scores:
        max_score = max(scores.values())
        if max_score > 0:
            scores = {k: v / max_score for k, v in scores.items()}

    return dict(scores)
