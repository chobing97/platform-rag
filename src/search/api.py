"""FastAPI 검색 API 서버."""

import json
import logging
import os
import sqlite3

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from click_log import (
    log_click,
    log_search,
    log_chat,
    get_stats_summary,
    get_stats_daily,
    get_stats_top_queries,
    get_stats_top_docs,
    get_stats_timings,
    get_stats_providers,
    save_chat_message,
    get_chat_messages,
)
from config import RAW_DIR
from searcher import SearchFilters, search, get_document, list_sources, get_related, get_filters, reload_bm25

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

app = FastAPI(title="Platform RAG Search API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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
    source: str | None = Query(None, description="데이터 소스 필터 (notion, daolemail)"),
    source_type: str | None = Query(None, description="콘텐츠 유형 필터 (document, email_body, email_attachment)"),
    sender: str | None = Query(None, description="발신자 이메일 주소"),
    recipient: str | None = Query(None, description="수신자 이메일 (To+CC)"),
    participant: str | None = Query(None, description="참여자 이메일 (발신+수신+참조 모두)"),
    direction: str | None = Query(None, description="메일 방향 필터 (sent, received)"),
):
    filters = SearchFilters(
        source=source,
        source_type=source_type,
        sender=sender,
        recipient=recipient,
        participant=participant,
        direction=direction,
    )
    data = search(q, top_k=top_k, use_reranker=rerank, filters=filters)
    log_search(
        query=q,
        result_count=len(data["results"]),
        used_rerank=rerank,
        timings=data["timings"],
    )
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


class ChatEvent(BaseModel):
    session_id: str
    provider: str
    model: str = ""


@app.post("/log/chat")
def chat_log_endpoint(event: ChatEvent):
    log_chat(event.session_id, event.provider, event.model)
    return {"status": "logged"}


@app.get("/stats/summary")
def stats_summary_endpoint():
    return get_stats_summary()


@app.get("/stats/daily")
def stats_daily_endpoint(days: int = Query(30, ge=1, le=90)):
    return {"data": get_stats_daily(days)}


@app.get("/stats/top-queries")
def stats_top_queries_endpoint(limit: int = Query(10, ge=1, le=50)):
    return {"data": get_stats_top_queries(limit)}


@app.get("/stats/top-docs")
def stats_top_docs_endpoint(limit: int = Query(10, ge=1, le=50)):
    return {"data": get_stats_top_docs(limit)}


@app.get("/stats/timings")
def stats_timings_endpoint(days: int = Query(7, ge=1, le=30)):
    return {"data": get_stats_timings(days)}


@app.get("/stats/providers")
def stats_providers_endpoint():
    return {"data": get_stats_providers()}


class ChatMessageEvent(BaseModel):
    session_id: str
    role: str
    content: str
    thinking: str | None = None


@app.post("/chat/messages")
def save_chat_message_endpoint(event: ChatMessageEvent):
    msg_id = save_chat_message(
        event.session_id, event.role, event.content, thinking=event.thinking
    )
    return {"id": msg_id}


@app.get("/chat/messages/{session_id}")
def get_chat_messages_endpoint(
    session_id: str,
    limit: int = Query(5, ge=1, le=50),
    before_id: int | None = Query(None),
):
    return get_chat_messages(session_id, limit=limit, before_id=before_id)


DAOLEMAIL_DB = os.path.join(RAW_DIR, "daolemail", "sync_state.db")


@app.get("/contacts")
def contacts_endpoint(
    keyword: str | None = Query(None, description="이름 또는 이메일 검색"),
    limit: int = Query(100, ge=1, le=500, description="결과 수"),
):
    """이메일 인물 목록 조회."""
    if not os.path.exists(DAOLEMAIL_DB):
        return {"contacts": []}

    conn = sqlite3.connect(DAOLEMAIL_DB)
    conn.row_factory = sqlite3.Row
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

    return {
        "contacts": [
            {"email": r["email"], "names": json.loads(r["names"]), "mail_count": r["mail_count"]}
            for r in rows
        ]
    }


@app.get("/filters")
def filters_endpoint():
    """사용 가능한 검색 필터 옵션 (source, source_type) 조회."""
    return get_filters()


@app.post("/admin/reload-bm25")
def reload_bm25_endpoint():
    """BM25 인덱스를 핫 리로드한다. 검색 중단 없이 atomic swap."""
    reload_bm25()
    return {"status": "ok"}


@app.get("/health")
def health():
    return {"status": "ok"}
