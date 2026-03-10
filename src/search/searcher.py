"""Hybrid Search: Vector + BM25 → RRF 합산 → Reranker."""

import json
import logging
import os
import sqlite3

import httpx
import MeCab
from qdrant_client import QdrantClient
from rank_bm25 import BM25Okapi

from config import (
    DATA_DIR,
    EMBED_MODEL,
    OLLAMA_URL,
    QDRANT_COLLECTION,
    QDRANT_URL,
    RERANKER_ENABLED,
    RERANKER_MODEL,
    RRF_K,
    TOP_K_RERANK,
    TOP_K_RETRIEVAL,
)

logger = logging.getLogger(__name__)

BM25_DB = os.path.join(DATA_DIR, "bm25_corpus.db")

# 모듈 수준 캐시 — API 서버 기동 시 한 번만 로드
_bm25_index: BM25Okapi | None = None
_bm25_corpus: list[dict] | None = None  # [{id, text, metadata}, ...]
_reranker = None


_mecab = MeCab.Tagger()


def _tokenize(text: str) -> list[str]:
    """MeCab 한국어 형태소 분석기로 토크나이징한다."""
    parsed = _mecab.parse(text)
    return [line.split("\t")[0] for line in parsed.splitlines() if "\t" in line]


def _load_bm25():
    """SQLite에서 BM25 코퍼스를 로드하여 인덱스를 구축한다."""
    global _bm25_index, _bm25_corpus

    if _bm25_index is not None:
        return

    conn = sqlite3.connect(BM25_DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT id, text, metadata FROM chunks").fetchall()
    conn.close()

    _bm25_corpus = [
        {"id": r["id"], "text": r["text"], "metadata": json.loads(r["metadata"])}
        for r in rows
    ]
    tokenized = [_tokenize(doc["text"]) for doc in _bm25_corpus]
    _bm25_index = BM25Okapi(tokenized)

    logger.info("BM25 인덱스 로드 완료: %d개 문서", len(_bm25_corpus))


def _get_device() -> str:
    """사용 가능한 최적 디바이스를 반환한다."""
    import torch
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def _load_reranker():
    """Cross-encoder reranker를 로드한다."""
    global _reranker

    if _reranker is not None or not RERANKER_ENABLED:
        return

    from sentence_transformers import CrossEncoder
    device = _get_device()
    logger.info("Reranker 로딩 중: %s (device=%s)", RERANKER_MODEL, device)
    _reranker = CrossEncoder(RERANKER_MODEL, device=device)
    logger.info("Reranker 로딩 완료")


def _embed_query(query: str) -> list[float]:
    response = httpx.post(
        f"{OLLAMA_URL}/api/embed",
        json={"model": EMBED_MODEL, "input": [query]},
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()["embeddings"][0]


def _vector_search(query_vec: list[float], top_k: int) -> list[dict]:
    """Qdrant 벡터 검색."""
    client = QdrantClient(url=QDRANT_URL)
    results = client.query_points(
        collection_name=QDRANT_COLLECTION,
        query=query_vec,
        limit=top_k,
        with_payload=True,
    )
    return [
        {
            "id": str(hit.id),
            "text": hit.payload.get("text", ""),
            "metadata": {k: v for k, v in hit.payload.items() if k != "text"},
            "score": hit.score,
        }
        for hit in results.points
    ]


def _bm25_search(query: str, top_k: int) -> list[dict]:
    """BM25 키워드 검색."""
    _load_bm25()
    tokens = _tokenize(query)
    scores = _bm25_index.get_scores(tokens)

    top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]
    return [
        {
            "id": _bm25_corpus[i]["id"],
            "text": _bm25_corpus[i]["text"],
            "metadata": _bm25_corpus[i]["metadata"],
            "score": float(scores[i]),
        }
        for i in top_indices
        if scores[i] > 0
    ]


def _rrf_fusion(vector_results: list[dict], bm25_results: list[dict], k: int = RRF_K) -> list[dict]:
    """Reciprocal Rank Fusion + 클릭 부스팅으로 두 결과를 합산한다."""
    from click_log import get_boost_scores

    scores: dict[str, float] = {}
    doc_map: dict[str, dict] = {}

    for rank, doc in enumerate(vector_results):
        doc_id = doc["id"]
        scores[doc_id] = scores.get(doc_id, 0) + 1.0 / (k + rank + 1)
        doc_map[doc_id] = doc

    for rank, doc in enumerate(bm25_results):
        doc_id = doc["id"]
        scores[doc_id] = scores.get(doc_id, 0) + 1.0 / (k + rank + 1)
        doc_map[doc_id] = doc

    # 클릭 부스팅 (최대 RRF 점수의 10% 가중치)
    boost = get_boost_scores()
    if boost:
        max_rrf = max(scores.values()) if scores else 1.0
        boost_weight = max_rrf * 0.1
        for doc_id in scores:
            if doc_id in boost:
                scores[doc_id] += boost[doc_id] * boost_weight

    sorted_ids = sorted(scores, key=lambda x: scores[x], reverse=True)
    return [
        {**doc_map[doc_id], "rrf_score": scores[doc_id]}
        for doc_id in sorted_ids
    ]


def _rerank(query: str, candidates: list[dict], top_k: int) -> list[dict]:
    """Cross-encoder로 후보를 재정렬한다."""
    if not RERANKER_ENABLED:
        return candidates[:top_k]

    _load_reranker()

    pairs = [(query, c["text"]) for c in candidates]
    rerank_scores = _reranker.predict(pairs)

    for i, score in enumerate(rerank_scores):
        candidates[i]["rerank_score"] = float(score)

    candidates.sort(key=lambda x: x["rerank_score"], reverse=True)
    return candidates[:top_k]


def get_document(doc_id: str) -> dict | None:
    """chunk ID로 문서 전체 내용을 반환한다."""
    client = QdrantClient(url=QDRANT_URL)
    try:
        points = client.retrieve(
            collection_name=QDRANT_COLLECTION,
            ids=[doc_id],
            with_payload=True,
        )
    except Exception:
        return None

    if not points:
        return None

    point = points[0]
    metadata = {k: v for k, v in point.payload.items() if k != "text"}
    file_name = metadata.get("file_name", "")

    # 원본 마크다운 파일에서 전체 텍스트 읽기
    full_text = point.payload.get("text", "")
    if file_name:
        file_path = os.path.join(DATA_DIR, "notion", file_name)
        if os.path.exists(file_path):
            with open(file_path, "r", encoding="utf-8") as f:
                full_text = f.read()

    return {"id": doc_id, "text": full_text, "metadata": metadata}


def list_sources(source_type: str | None = None, keyword: str | None = None) -> list[dict]:
    """문서 목록을 조회한다. BM25 코퍼스에서 고유 파일 기준으로 집계."""
    _load_bm25()

    seen: dict[str, dict] = {}
    for doc in _bm25_corpus:
        file_name = doc["metadata"].get("file_name", doc["id"])

        if source_type and doc["metadata"].get("source") != source_type:
            continue
        if keyword and keyword.lower() not in doc["metadata"].get("title", "").lower():
            continue

        if file_name in seen:
            seen[file_name]["chunk_count"] += 1
            continue

        seen[file_name] = {
            "file_name": file_name,
            "title": doc["metadata"].get("title", ""),
            "source": doc["metadata"].get("source", ""),
            "url": doc["metadata"].get("url", ""),
            "chunk_count": 1,
        }

    return sorted(seen.values(), key=lambda x: x["title"])


def get_related(doc_id: str, top_k: int = 5) -> list[dict]:
    """특정 문서와 벡터 유사도가 높은 관련 문서를 반환한다."""
    client = QdrantClient(url=QDRANT_URL)
    try:
        points = client.retrieve(
            collection_name=QDRANT_COLLECTION,
            ids=[doc_id],
            with_vectors=True,
            with_payload=True,
        )
    except Exception:
        return []

    if not points:
        return []

    query_vec = points[0].vector
    results = client.query_points(
        collection_name=QDRANT_COLLECTION,
        query=query_vec,
        limit=top_k + 1,  # 자기 자신 제외용
        with_payload=True,
    )

    return [
        {
            "id": str(hit.id),
            "text": hit.payload.get("text", "")[:300],
            "metadata": {k: v for k, v in hit.payload.items() if k != "text"},
            "score": hit.score,
        }
        for hit in results.points
        if str(hit.id) != doc_id
    ][:top_k]


def search(query: str, top_k: int = TOP_K_RERANK, use_reranker: bool = True) -> dict:
    """Hybrid Search 전체 파이프라인. 결과와 성능 정보를 반환한다."""
    import time
    logger.info("검색 쿼리: %s", query)
    timings: dict[str, float] = {}

    # 1. 임베딩
    t = time.time()
    query_vec = _embed_query(query)
    timings["embedding"] = time.time() - t

    # 2. 벡터 검색
    t = time.time()
    vector_results = _vector_search(query_vec, TOP_K_RETRIEVAL)
    timings["vector_search"] = time.time() - t

    # 3. BM25 검색
    t = time.time()
    bm25_results = _bm25_search(query, TOP_K_RETRIEVAL)
    timings["bm25_search"] = time.time() - t

    logger.info("Vector: %d건, BM25: %d건", len(vector_results), len(bm25_results))

    # 4. RRF 합산
    t = time.time()
    fused = _rrf_fusion(vector_results, bm25_results)
    timings["rrf_fusion"] = time.time() - t

    # 5. Reranker
    if use_reranker and RERANKER_ENABLED:
        t = time.time()
        results = _rerank(query, fused[:TOP_K_RETRIEVAL], top_k)
        timings["reranker"] = time.time() - t
    else:
        results = fused[:top_k]

    timings["total"] = sum(timings.values())
    logger.info("검색 완료: %.1f초 (rerank: %.1fs)", timings["total"], timings.get("reranker", 0))

    return {"results": results, "timings": timings}
