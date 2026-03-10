"""FastAPI 검색 API 서버."""

import logging

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from click_log import log_click
from searcher import search, get_document, list_sources, get_related

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

app = FastAPI(title="Platform RAG Search API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class SearchResult(BaseModel):
    id: str
    text: str
    metadata: dict
    rrf_score: float | None = None
    rerank_score: float | None = None


class Timings(BaseModel):
    embedding: float = 0
    vector_search: float = 0
    bm25_search: float = 0
    rrf_fusion: float = 0
    reranker: float = 0
    total: float = 0


class SearchResponse(BaseModel):
    query: str
    count: int
    results: list[SearchResult]
    timings: Timings


@app.get("/search", response_model=SearchResponse)
def search_endpoint(
    q: str = Query(..., description="검색 쿼리"),
    top_k: int = Query(20, ge=1, le=50, description="결과 수"),
    rerank: bool = Query(True, description="Reranker 사용 여부"),
):
    data = search(q, top_k=top_k, use_reranker=rerank)
    return SearchResponse(
        query=q,
        count=len(data["results"]),
        results=data["results"],
        timings=data["timings"],
    )


class ClickEvent(BaseModel):
    query: str
    doc_id: str
    rank: int


@app.post("/click")
def click_endpoint(event: ClickEvent):
    log_click(event.query, event.doc_id, event.rank)
    return {"status": "logged"}


@app.get("/document/{doc_id}")
def document_endpoint(doc_id: str):
    doc = get_document(doc_id)
    if doc is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다")
    return doc


@app.get("/sources")
def sources_endpoint(
    source_type: str | None = Query(None, description="소스 유형 필터"),
    keyword: str | None = Query(None, description="제목 키워드 검색"),
):
    return {"sources": list_sources(source_type=source_type, keyword=keyword)}


@app.get("/related/{doc_id}")
def related_endpoint(
    doc_id: str,
    top_k: int = Query(5, ge=1, le=20, description="관련 문서 수"),
):
    return {"results": get_related(doc_id, top_k=top_k)}


@app.get("/health")
def health():
    return {"status": "ok"}
