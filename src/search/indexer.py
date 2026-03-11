"""청크를 임베딩하여 Qdrant에 저장하고, BM25 코퍼스를 SQLite에 기록한다."""

import json
import logging
import os
import sqlite3
import uuid

import httpx
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from chunker import chunk_all
from config import (
    INDEX_DIR,
    EMBED_DIM,
    EMBED_MODEL,
    OLLAMA_URL,
    QDRANT_COLLECTION,
    QDRANT_URL,
)

logger = logging.getLogger(__name__)

BM25_DB = os.path.join(INDEX_DIR, "bm25_corpus.db")
BATCH_SIZE = 64


def _init_bm25_db():
    os.makedirs(INDEX_DIR, exist_ok=True)
    conn = sqlite3.connect(BM25_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chunks (
            id TEXT PRIMARY KEY,
            text TEXT NOT NULL,
            metadata TEXT NOT NULL
        )
    """)
    conn.execute("DELETE FROM chunks")  # 전체 재인덱싱
    conn.commit()
    return conn


def _embed_batch(texts: list[str]) -> list[list[float]]:
    """Ollama API로 텍스트 배치를 임베딩한다."""
    response = httpx.post(
        f"{OLLAMA_URL}/api/embed",
        json={"model": EMBED_MODEL, "input": texts},
        timeout=120.0,
    )
    response.raise_for_status()
    return response.json()["embeddings"]


def _init_qdrant() -> QdrantClient:
    client = QdrantClient(url=QDRANT_URL)

    collections = [c.name for c in client.get_collections().collections]
    if QDRANT_COLLECTION in collections:
        logger.info("기존 컬렉션 삭제: %s", QDRANT_COLLECTION)
        client.delete_collection(QDRANT_COLLECTION)

    client.create_collection(
        collection_name=QDRANT_COLLECTION,
        vectors_config=VectorParams(size=EMBED_DIM, distance=Distance.COSINE),
    )
    logger.info("Qdrant 컬렉션 생성: %s (dim=%d)", QDRANT_COLLECTION, EMBED_DIM)
    return client


def index():
    """전체 인덱싱 파이프라인: 청크 → 임베딩 → Qdrant + BM25 DB."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 1. 청크 분할
    chunks = chunk_all()
    if not chunks:
        logger.warning("인덱싱할 청크가 없습니다.")
        return

    # 2. 인프라 초기화
    qdrant = _init_qdrant()
    bm25_conn = _init_bm25_db()

    # 3. 배치 임베딩 + 저장
    total = len(chunks)
    logger.info("임베딩 시작: %d개 청크 (배치 크기 %d)", total, BATCH_SIZE)

    for batch_start in range(0, total, BATCH_SIZE):
        batch_end = min(batch_start + BATCH_SIZE, total)
        batch = chunks[batch_start:batch_end]

        texts = [c["text"] for c in batch]
        embeddings = _embed_batch(texts)

        # Qdrant에 저장
        points = []
        bm25_rows = []
        for chunk, embedding in zip(batch, embeddings):
            point_id = str(uuid.uuid4())
            points.append(PointStruct(
                id=point_id,
                vector=embedding,
                payload={
                    "text": chunk["text"],
                    **chunk["metadata"],
                },
            ))
            bm25_rows.append((point_id, chunk["text"], json.dumps(chunk["metadata"], ensure_ascii=False)))

        qdrant.upsert(collection_name=QDRANT_COLLECTION, points=points)

        # BM25 DB에 저장
        bm25_conn.executemany("INSERT INTO chunks (id, text, metadata) VALUES (?, ?, ?)", bm25_rows)
        bm25_conn.commit()

        logger.info("  인덱싱: %d/%d 청크 완료", batch_end, total)

    logger.info("인덱싱 완료: %d개 청크 → Qdrant + BM25", total)
    bm25_conn.close()


if __name__ == "__main__":
    index()
