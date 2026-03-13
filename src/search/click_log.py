"""이용 로그 수집 (클릭, 검색, 채팅) 및 통계 조회."""

import os
import sqlite3
from collections import defaultdict

from config import WEB_DIR

DB_PATH = os.path.join(WEB_DIR, "click_log.db")


def _get_conn() -> sqlite3.Connection:
    os.makedirs(WEB_DIR, exist_ok=True)
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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS search_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query TEXT NOT NULL,
            result_count INTEGER NOT NULL,
            used_rerank INTEGER NOT NULL DEFAULT 1,
            time_embedding REAL NOT NULL DEFAULT 0,
            time_vector REAL NOT NULL DEFAULT 0,
            time_bm25 REAL NOT NULL DEFAULT 0,
            time_reranker REAL NOT NULL DEFAULT 0,
            time_total REAL NOT NULL DEFAULT 0,
            searched_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chat_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            model TEXT NOT NULL DEFAULT '',
            chatted_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            thinking TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    # 기존 DB에 thinking 컬럼이 없으면 추가
    try:
        conn.execute("SELECT thinking FROM chat_messages LIMIT 0")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE chat_messages ADD COLUMN thinking TEXT")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_chat_messages_session "
        "ON chat_messages(session_id, id DESC)"
    )
    conn.commit()
    return conn


# ─── 로깅 ─────────────────────────────────────────

def log_click(query: str, doc_id: str, rank: int):
    conn = _get_conn()
    conn.execute(
        "INSERT INTO clicks (query, doc_id, rank) VALUES (?, ?, ?)",
        (query, doc_id, rank),
    )
    conn.commit()
    conn.close()


def log_search(
    query: str,
    result_count: int,
    used_rerank: bool,
    timings: dict,
):
    conn = _get_conn()
    conn.execute(
        """INSERT INTO search_log
           (query, result_count, used_rerank,
            time_embedding, time_vector, time_bm25, time_reranker, time_total)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            query,
            result_count,
            int(used_rerank),
            timings.get("embedding", 0),
            timings.get("vector_search", 0),
            timings.get("bm25_search", 0),
            timings.get("reranker", 0),
            timings.get("total", 0),
        ),
    )
    conn.commit()
    conn.close()


def log_chat(session_id: str, provider: str, model: str = ""):
    conn = _get_conn()
    conn.execute(
        "INSERT INTO chat_log (session_id, provider, model) VALUES (?, ?, ?)",
        (session_id, provider, model),
    )
    conn.commit()
    conn.close()


# ─── 대화 저장/조회 ────────────────────────────────

def save_chat_message(
    session_id: str, role: str, content: str, thinking: str | None = None
) -> int:
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO chat_messages (session_id, role, content, thinking) VALUES (?, ?, ?, ?)",
        (session_id, role, content, thinking),
    )
    msg_id = cur.lastrowid
    conn.commit()
    conn.close()
    return msg_id  # type: ignore[return-value]


def get_chat_messages(
    session_id: str, limit: int = 5, before_id: int | None = None
) -> dict:
    conn = _get_conn()
    if before_id is not None:
        rows = conn.execute(
            """SELECT id, session_id, role, content, thinking, created_at
               FROM chat_messages
               WHERE session_id = ? AND id < ?
               ORDER BY id DESC LIMIT ?""",
            (session_id, before_id, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT id, session_id, role, content, thinking, created_at
               FROM chat_messages
               WHERE session_id = ?
               ORDER BY id DESC LIMIT ?""",
            (session_id, limit),
        ).fetchall()

    messages = [dict(r) for r in reversed(rows)]

    has_more = False
    if messages:
        oldest_id = messages[0]["id"]
        has_more = conn.execute(
            "SELECT EXISTS(SELECT 1 FROM chat_messages WHERE session_id = ? AND id < ?)",
            (session_id, oldest_id),
        ).fetchone()[0] == 1

    conn.close()
    return {"messages": messages, "has_more": has_more}


# ─── 통계 조회 ─────────────────────────────────────

def get_stats_summary() -> dict:
    conn = _get_conn()
    today_searches = conn.execute(
        "SELECT COUNT(*) c FROM search_log WHERE date(searched_at)=date('now')"
    ).fetchone()["c"]
    week_searches = conn.execute(
        "SELECT COUNT(*) c FROM search_log WHERE searched_at >= datetime('now', '-7 days')"
    ).fetchone()["c"]
    today_clicks = conn.execute(
        "SELECT COUNT(*) c FROM clicks WHERE date(clicked_at)=date('now')"
    ).fetchone()["c"]
    today_chats = conn.execute(
        "SELECT COUNT(*) c FROM chat_log WHERE date(chatted_at)=date('now')"
    ).fetchone()["c"]
    active_sessions = conn.execute(
        "SELECT COUNT(DISTINCT session_id) c FROM chat_messages"
    ).fetchone()["c"]
    conn.close()
    ctr = round(today_clicks / today_searches, 2) if today_searches > 0 else 0
    return {
        "today_searches": today_searches,
        "week_searches": week_searches,
        "today_clicks": today_clicks,
        "today_chats": today_chats,
        "ctr": ctr,
        "active_sessions": active_sessions,
    }


def get_stats_daily(days: int = 30) -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        """SELECT date(searched_at) as date, COUNT(*) as count
           FROM search_log
           WHERE searched_at >= datetime('now', ?)
           GROUP BY date(searched_at)
           ORDER BY date""",
        (f"-{days} days",),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_stats_top_queries(limit: int = 10) -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        """SELECT query, COUNT(*) as count
           FROM search_log
           GROUP BY query ORDER BY count DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_stats_top_docs(limit: int = 10) -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        """SELECT doc_id, COUNT(*) as clicks,
                  MAX(clicked_at) as last_clicked
           FROM clicks
           GROUP BY doc_id ORDER BY clicks DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_stats_timings(days: int = 7) -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        """SELECT date(searched_at) as date,
                  ROUND(AVG(time_total), 2) as avg_total,
                  ROUND(AVG(time_embedding), 2) as avg_embedding,
                  ROUND(AVG(time_vector), 2) as avg_vector,
                  ROUND(AVG(time_bm25), 2) as avg_bm25,
                  ROUND(AVG(time_reranker), 2) as avg_reranker
           FROM search_log
           WHERE searched_at >= datetime('now', ?)
           GROUP BY date(searched_at)
           ORDER BY date""",
        (f"-{days} days",),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_stats_providers() -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        """SELECT provider, COUNT(*) as count
           FROM chat_log GROUP BY provider ORDER BY count DESC"""
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─── 기존: 클릭 부스팅 ─────────────────────────────

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
