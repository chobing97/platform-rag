"""DAOL 이메일 수집 상태 관리 (SQLite)."""

import json
import os
import sqlite3
from datetime import datetime, timezone

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
DB_PATH = os.path.join(PROJECT_ROOT, "data", "raw", "sync_state.db")


def _get_conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS mail_state (
            mail_idx    INTEGER PRIMARY KEY,
            mbox_idx    INTEGER NOT NULL,
            subject     TEXT,
            sender      TEXT,
            date        TEXT,
            file_path   TEXT,
            synced_at   TEXT
        );

        CREATE TABLE IF NOT EXISTS email_contacts (
            email       TEXT PRIMARY KEY,
            names       TEXT NOT NULL DEFAULT '[]',
            mail_count  INTEGER DEFAULT 1,
            first_seen  TEXT,
            last_seen   TEXT
        );

        CREATE TABLE IF NOT EXISTS mail_sync_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            source      TEXT DEFAULT 'daolemail',
            started_at  TEXT,
            finished_at TEXT,
            status      TEXT DEFAULT 'running',
            total_mails INTEGER DEFAULT 0,
            synced_mails INTEGER DEFAULT 0,
            error_message TEXT
        );
    """)
    conn.commit()
    return conn


def get_synced_mail_idxs() -> set[int]:
    """이미 수집된 mailIdx 목록."""
    conn = _get_conn()
    rows = conn.execute("SELECT mail_idx FROM mail_state").fetchall()
    conn.close()
    return {row["mail_idx"] for row in rows}


def upsert_mail_state(
    mail_idx: int,
    mbox_idx: int,
    subject: str,
    sender: str,
    date: str,
    file_path: str,
) -> None:
    """메일 수집 상태 저장/갱신."""
    conn = _get_conn()
    conn.execute(
        """INSERT INTO mail_state (mail_idx, mbox_idx, subject, sender, date, file_path, synced_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(mail_idx) DO UPDATE SET
               subject=excluded.subject,
               sender=excluded.sender,
               date=excluded.date,
               file_path=excluded.file_path,
               synced_at=excluded.synced_at
        """,
        (mail_idx, mbox_idx, subject, sender, date, file_path,
         datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()


def upsert_contact(email: str, name: str, date: str) -> None:
    """이메일 인물 정보 저장/갱신. 이름이 새로우면 names 배열에 추가."""
    email = email.lower().strip()
    name = name.strip()
    if not email:
        return

    conn = _get_conn()
    row = conn.execute("SELECT names, mail_count FROM email_contacts WHERE email=?", (email,)).fetchone()

    now = datetime.now(timezone.utc).isoformat()
    if row is None:
        names = [name] if name else []
        conn.execute(
            "INSERT INTO email_contacts (email, names, mail_count, first_seen, last_seen) VALUES (?, ?, 1, ?, ?)",
            (email, json.dumps(names, ensure_ascii=False), date or now, date or now),
        )
    else:
        existing_names = json.loads(row["names"])
        if name and name not in existing_names:
            existing_names.append(name)
        conn.execute(
            "UPDATE email_contacts SET names=?, mail_count=mail_count+1, last_seen=? WHERE email=?",
            (json.dumps(existing_names, ensure_ascii=False), date or now, email),
        )

    conn.commit()
    conn.close()


def get_contacts(keyword: str | None = None, limit: int = 100) -> list[dict]:
    """이메일 인물 목록 조회. keyword로 이름 또는 이메일 부분 매칭."""
    conn = _get_conn()
    if keyword:
        rows = conn.execute(
            "SELECT email, names, mail_count FROM email_contacts WHERE email LIKE ? OR names LIKE ? ORDER BY mail_count DESC LIMIT ?",
            (f"%{keyword}%", f"%{keyword}%", limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT email, names, mail_count FROM email_contacts ORDER BY mail_count DESC LIMIT ?",
            (limit,),
        ).fetchall()
    conn.close()
    return [
        {"email": r["email"], "names": json.loads(r["names"]), "mail_count": r["mail_count"]}
        for r in rows
    ]


def start_sync_run() -> int:
    """동기화 실행 기록 시작. run_id 반환."""
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO mail_sync_log (started_at, status) VALUES (?, 'running')",
        (datetime.now(timezone.utc).isoformat(),),
    )
    run_id = cur.lastrowid
    conn.commit()
    conn.close()
    return run_id


def finish_sync_run(
    run_id: int,
    total: int,
    synced: int,
    error: str | None = None,
) -> None:
    """동기화 실행 기록 완료."""
    status = "error" if error else "completed"
    conn = _get_conn()
    conn.execute(
        """UPDATE mail_sync_log
           SET finished_at=?, status=?, total_mails=?, synced_mails=?, error_message=?
           WHERE id=?""",
        (datetime.now(timezone.utc).isoformat(), status, total, synced, error, run_id),
    )
    conn.commit()
    conn.close()
