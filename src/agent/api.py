"""Agent Streaming API — SSE 기반 실시간 상태 전달."""

import json
import logging
import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from . import config
from .agent import Agent

logger = logging.getLogger(__name__)

app = FastAPI(title="Platform RAG Agent API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 세션별 Agent 관리
_sessions: dict[str, dict] = {}  # session_id -> {"agent": Agent, "last_access": float}
_SESSION_TTL = 3600  # 1시간 미사용 시 자동 정리


def _cleanup_sessions():
    """만료된 세션을 정리한다."""
    now = time.time()
    expired = [sid for sid, s in _sessions.items() if now - s["last_access"] > _SESSION_TTL]
    for sid in expired:
        logger.info("세션 만료 정리: %s", sid)
        del _sessions[sid]


def _get_agent(session_id: str, provider: str | None = None, model: str | None = None) -> Agent:
    _cleanup_sessions()
    if session_id not in _sessions:
        _sessions[session_id] = {
            "agent": Agent(provider=provider, model=model),
            "last_access": time.time(),
        }
    _sessions[session_id]["last_access"] = time.time()
    return _sessions[session_id]["agent"]


class AskRequest(BaseModel):
    query: str
    session_id: str
    provider: str | None = None
    model: str | None = None


@app.post("/agent/ask")
async def ask_stream(req: AskRequest):
    """SSE 스트림으로 에이전트 상태와 최종 답변을 전달한다."""
    agent = _get_agent(req.session_id, req.provider, req.model)

    def event_generator():
        for event in agent.ask_stream(req.query):
            event_type = "result" if event["type"] == "result" else "status"
            data = json.dumps(event, ensure_ascii=False)
            yield f"event: {event_type}\ndata: {data}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


class ResetRequest(BaseModel):
    session_id: str
    provider: str | None = None
    model: str | None = None


@app.post("/agent/reset")
async def reset(req: ResetRequest | None = None):
    """대화 기록을 초기화한다."""
    if req and req.session_id in _sessions:
        del _sessions[req.session_id]
    return {"status": "ok"}


@app.get("/agent/models")
async def list_models():
    """사용 가능한 프로바이더별 모델 목록을 반환한다."""
    return config.get_available_models()


@app.get("/agent/health")
async def health():
    return {"status": "ok"}
