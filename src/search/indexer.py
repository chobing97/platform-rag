"""청크를 임베딩하여 Qdrant에 저장하고, BM25 코퍼스를 SQLite에 기록한다.

증분 인덱싱: 파일 mtime 기반으로 변경분만 처리. --full로 전체 재구축.
"""

import argparse
import json
import logging
import os
import sqlite3
import uuid
from datetime import datetime, timezone

import httpx
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PayloadSchemaType, PointStruct, PointIdsList, VectorParams

from chunker import chunk_all, chunk_file
from config import (
    DAOLEMAIL_DIR,
    INDEX_DIR,
    EMBED_DIM,
    EMBED_MODEL,
    NOTION_DIR,
    OLLAMA_URL,
    QDRANT_COLLECTION,
    QDRANT_URL,
)

logger = logging.getLogger(__name__)

BM25_DB = os.path.join(INDEX_DIR, "bm25_corpus.db")
INDEX_STATE_DB = os.path.join(INDEX_DIR, "index_state.db")
BATCH_SIZE = 64


# ─── Index State DB ──────────────────────────────

def _init_state_db() -> sqlite3.Connection:
    os.makedirs(INDEX_DIR, exist_ok=True)
    conn = sqlite3.connect(INDEX_STATE_DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS indexed_files (
            file_path  TEXT PRIMARY KEY,
            mtime      REAL NOT NULL,
            chunk_ids  TEXT NOT NULL,
            indexed_at TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn


def _get_indexed_files(conn: sqlite3.Connection) -> dict[str, dict]:
    """인덱싱된 파일 목록. {file_path: {mtime, chunk_ids}}"""
    rows = conn.execute("SELECT file_path, mtime, chunk_ids FROM indexed_files").fetchall()
    return {
        r["file_path"]: {"mtime": r["mtime"], "chunk_ids": json.loads(r["chunk_ids"])}
        for r in rows
    }


# ─── BM25 DB ─────────────────────────────────────

def _init_bm25_db(full: bool = False) -> sqlite3.Connection:
    os.makedirs(INDEX_DIR, exist_ok=True)
    conn = sqlite3.connect(BM25_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chunks (
            id TEXT PRIMARY KEY,
            text TEXT NOT NULL,
            metadata TEXT NOT NULL
        )
    """)
    if full:
        conn.execute("DELETE FROM chunks")
    conn.commit()
    return conn


# ─── Embedding ───────────────────────────────────

def _embed_batch(texts: list[str]) -> list[list[float]]:
    """Ollama API로 텍스트 배치를 임베딩한다."""
    response = httpx.post(
        f"{OLLAMA_URL}/api/embed",
        json={"model": EMBED_MODEL, "input": texts},
        timeout=120.0,
    )
    response.raise_for_status()
    return response.json()["embeddings"]


# ─── Qdrant ──────────────────────────────────────

_PAYLOAD_INDEXES = {
    "source": PayloadSchemaType.KEYWORD,
    "source_type": PayloadSchemaType.KEYWORD,
    "sender_email": PayloadSchemaType.KEYWORD,
    "recipient_emails": PayloadSchemaType.KEYWORD,
    "cc_emails": PayloadSchemaType.KEYWORD,
    "direction": PayloadSchemaType.KEYWORD,
}


def _create_payload_indexes(client: QdrantClient):
    """검색 필터용 payload 인덱스를 생성한다."""
    for field, schema in _PAYLOAD_INDEXES.items():
        try:
            client.create_payload_index(
                collection_name=QDRANT_COLLECTION,
                field_name=field,
                field_schema=schema,
            )
        except Exception:
            pass  # 이미 존재하면 무시


def _ensure_qdrant_collection(client: QdrantClient):
    """컬렉션이 없으면 생성."""
    collections = [c.name for c in client.get_collections().collections]
    if QDRANT_COLLECTION not in collections:
        client.create_collection(
            collection_name=QDRANT_COLLECTION,
            vectors_config=VectorParams(size=EMBED_DIM, distance=Distance.COSINE),
        )
        logger.info("Qdrant 컬렉션 생성: %s (dim=%d)", QDRANT_COLLECTION, EMBED_DIM)
    _create_payload_indexes(client)


def _reset_qdrant_collection(client: QdrantClient):
    """컬렉션 삭제 후 재생성."""
    collections = [c.name for c in client.get_collections().collections]
    if QDRANT_COLLECTION in collections:
        client.delete_collection(QDRANT_COLLECTION)
        logger.info("기존 컬렉션 삭제: %s", QDRANT_COLLECTION)
    client.create_collection(
        collection_name=QDRANT_COLLECTION,
        vectors_config=VectorParams(size=EMBED_DIM, distance=Distance.COSINE),
    )
    logger.info("Qdrant 컬렉션 생성: %s (dim=%d)", QDRANT_COLLECTION, EMBED_DIM)
    _create_payload_indexes(client)


# ─── 변경 감지 ───────────────────────────────────

def _collect_md_files() -> dict[str, float]:
    """현재 .md 파일 목록과 mtime. {file_path: mtime}"""
    files = {}
    for base_dir in (NOTION_DIR, DAOLEMAIL_DIR):
        if not os.path.isdir(base_dir):
            continue
        for root, _dirs, fnames in os.walk(base_dir):
            for fname in fnames:
                if fname.endswith(".md"):
                    fpath = os.path.join(root, fname)
                    files[fpath] = os.path.getmtime(fpath)
    return files


def _detect_changes(current: dict[str, float], indexed: dict[str, dict]) -> tuple[list[str], list[str], list[str]]:
    """변경 감지. (신규, 변경, 삭제) 파일 경로 리스트 반환."""
    added = []
    modified = []
    deleted = []

    for fpath, mtime in current.items():
        if fpath not in indexed:
            added.append(fpath)
        elif mtime != indexed[fpath]["mtime"]:
            modified.append(fpath)

    for fpath in indexed:
        if fpath not in current:
            deleted.append(fpath)

    return added, modified, deleted


# ─── 인덱싱 파이프라인 ──────────────────────────

def _index_chunks(
    chunks: list[dict],
    qdrant: QdrantClient,
    bm25_conn: sqlite3.Connection,
) -> list[str]:
    """청크를 임베딩하여 Qdrant + BM25 DB에 저장. 생성된 chunk_id 리스트 반환."""
    all_ids = []
    total = len(chunks)

    for batch_start in range(0, total, BATCH_SIZE):
        batch_end = min(batch_start + BATCH_SIZE, total)
        batch = chunks[batch_start:batch_end]

        texts = [c["text"] for c in batch]
        embeddings = _embed_batch(texts)

        points = []
        bm25_rows = []
        batch_ids = []
        for chunk, embedding in zip(batch, embeddings):
            point_id = str(uuid.uuid4())
            batch_ids.append(point_id)
            points.append(PointStruct(
                id=point_id,
                vector=embedding,
                payload={"text": chunk["text"], **chunk["metadata"]},
            ))
            bm25_rows.append((point_id, chunk["text"], json.dumps(chunk["metadata"], ensure_ascii=False)))

        qdrant.upsert(collection_name=QDRANT_COLLECTION, points=points)
        bm25_conn.executemany("INSERT OR REPLACE INTO chunks (id, text, metadata) VALUES (?, ?, ?)", bm25_rows)
        bm25_conn.commit()
        all_ids.extend(batch_ids)

    return all_ids


def _delete_chunks(
    chunk_ids: list[str],
    qdrant: QdrantClient,
    bm25_conn: sqlite3.Connection,
):
    """Qdrant + BM25 DB에서 청크 삭제."""
    if not chunk_ids:
        return

    qdrant.delete(
        collection_name=QDRANT_COLLECTION,
        points_selector=PointIdsList(points=chunk_ids),
    )

    # SQLite placeholders
    placeholders = ",".join("?" for _ in chunk_ids)
    bm25_conn.execute(f"DELETE FROM chunks WHERE id IN ({placeholders})", chunk_ids)
    bm25_conn.commit()


# ─── 메인 ────────────────────────────────────────

def index(full: bool = False):
    """인덱싱 파이프라인. full=False이면 증분, True이면 전체 재구축."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    qdrant = QdrantClient(url=QDRANT_URL)
    state_conn = _init_state_db()

    if full:
        logger.info("=== 전체 재인덱싱 ===")
        _reset_qdrant_collection(qdrant)
        bm25_conn = _init_bm25_db(full=True)
        state_conn.execute("DELETE FROM indexed_files")
        state_conn.commit()

        chunks = chunk_all()
        if not chunks:
            logger.warning("인덱싱할 청크가 없습니다.")
            return

        total = len(chunks)
        logger.info("임베딩 시작: %d개 청크 (배치 크기 %d)", total, BATCH_SIZE)

        # 파일별로 chunk_ids 추적
        file_chunks: dict[str, list[dict]] = {}
        for chunk in chunks:
            fp = chunk["metadata"]["file_path"]
            file_chunks.setdefault(fp, []).append(chunk)

        for fpath, fchunks in file_chunks.items():
            chunk_ids = _index_chunks(fchunks, qdrant, bm25_conn)
            mtime = os.path.getmtime(fpath) if os.path.exists(fpath) else 0
            state_conn.execute(
                "INSERT OR REPLACE INTO indexed_files (file_path, mtime, chunk_ids, indexed_at) VALUES (?, ?, ?, ?)",
                (fpath, mtime, json.dumps(chunk_ids), datetime.now(timezone.utc).isoformat()),
            )

        state_conn.commit()
        bm25_conn.close()

        indexed_count = sum(len(ids) for ids in file_chunks.values())
        logger.info("전체 인덱싱 완료: %d개 파일 → %d개 청크", len(file_chunks), indexed_count)

    else:
        logger.info("=== 증분 인덱싱 ===")
        _ensure_qdrant_collection(qdrant)
        bm25_conn = _init_bm25_db(full=False)

        current_files = _collect_md_files()
        indexed_files = _get_indexed_files(state_conn)

        added, modified, deleted = _detect_changes(current_files, indexed_files)

        if not added and not modified and not deleted:
            logger.info("변경 없음 — 인덱싱 스킵")
            bm25_conn.close()
            state_conn.close()
            return

        logger.info("변경 감지: 신규 %d, 변경 %d, 삭제 %d", len(added), len(modified), len(deleted))

        # 삭제된 파일 처리
        for fpath in deleted:
            old_ids = indexed_files[fpath]["chunk_ids"]
            _delete_chunks(old_ids, qdrant, bm25_conn)
            state_conn.execute("DELETE FROM indexed_files WHERE file_path=?", (fpath,))
            logger.debug("삭제: %s (%d 청크)", os.path.basename(fpath), len(old_ids))

        # 변경된 파일 처리 (기존 삭제 → 재인덱싱)
        for fpath in modified:
            old_ids = indexed_files[fpath]["chunk_ids"]
            _delete_chunks(old_ids, qdrant, bm25_conn)

        # 신규 + 변경 파일 인덱싱
        to_index = added + modified
        total_chunks = 0
        for fpath in to_index:
            chunks = chunk_file(fpath)
            if not chunks:
                state_conn.execute(
                    "INSERT OR REPLACE INTO indexed_files (file_path, mtime, chunk_ids, indexed_at) VALUES (?, ?, ?, ?)",
                    (fpath, current_files[fpath], "[]", datetime.now(timezone.utc).isoformat()),
                )
                continue

            chunk_ids = _index_chunks(chunks, qdrant, bm25_conn)
            total_chunks += len(chunk_ids)
            state_conn.execute(
                "INSERT OR REPLACE INTO indexed_files (file_path, mtime, chunk_ids, indexed_at) VALUES (?, ?, ?, ?)",
                (fpath, current_files[fpath], json.dumps(chunk_ids), datetime.now(timezone.utc).isoformat()),
            )

        state_conn.commit()
        bm25_conn.close()

        logger.info(
            "증분 인덱싱 완료: +%d /%d -%d 파일, %d 청크 갱신",
            len(added), len(modified), len(deleted), total_chunks,
        )

    state_conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="검색 인덱스 구축")
    parser.add_argument("--full", action="store_true", help="전체 재인덱싱 (기본: 증분)")
    args = parser.parse_args()
    index(full=args.full)
