"""Agent Streaming API — SSE 기반 실시간 상태 전달."""

import json
import logging

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

# provider:model 조합별 Agent 싱글턴
_agents: dict[str, Agent] = {}


def _get_agent(provider: str | None = None, model: str | None = None) -> Agent:
    key = f"{provider or 'default'}:{model or 'default'}"
    if key not in _agents:
        _agents[key] = Agent(provider=provider, model=model)
    return _agents[key]


class AskRequest(BaseModel):
    query: str
    provider: str | None = None
    model: str | None = None


@app.post("/agent/ask")
async def ask_stream(req: AskRequest):
    """SSE 스트림으로 에이전트 상태와 최종 답변을 전달한다."""
    agent = _get_agent(req.provider, req.model)

    def event_generator():
        for event in agent.ask_stream(req.query):
            event_type = "result" if event["type"] == "result" else "status"
            data = json.dumps(event, ensure_ascii=False)
            yield f"event: {event_type}\ndata: {data}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


class ResetRequest(BaseModel):
    provider: str | None = None
    model: str | None = None


@app.post("/agent/reset")
async def reset(req: ResetRequest | None = None):
    """대화 기록을 초기화한다."""
    provider = req.provider if req else None
    model = req.model if req else None
    agent = _get_agent(provider, model)
    agent.reset()
    return {"status": "ok"}


@app.get("/agent/models")
async def list_models():
    """사용 가능한 프로바이더별 모델 목록을 반환한다."""
    return config.get_available_models()


@app.get("/agent/health")
async def health():
    return {"status": "ok"}
