"""Agent Streaming API — SSE 기반 실시간 상태 전달."""

import asyncio
import json
import logging
import time

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from . import config
from .agent import Agent
from .attachments import SUPPORTED_MIMES, process_attachment

logger = logging.getLogger(__name__)

# Agent SDK (Claude CLI) 동시 실행 방지 — OAuth 토큰 동시 세션 제한 대응
_sdk_lock = asyncio.Lock()

app = FastAPI(title="Platform RAG Agent API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 세션별 Agent / SDKRunner 관리
_sessions: dict[str, dict] = {}  # session_id -> {"agent"|"runner": ..., "last_access": float}
_SESSION_TTL = 3600  # 1시간 미사용 시 자동 정리


def _cleanup_sessions():
    """만료된 세션을 정리한다."""
    now = time.time()
    expired = [sid for sid, s in _sessions.items() if now - s["last_access"] > _SESSION_TTL]
    for sid in expired:
        logger.info("세션 만료 정리: %s", sid)
        del _sessions[sid]


def _get_agent(session_id: str, provider: str | None = None, model: str | None = None, api_key: str | None = None) -> Agent:
    _cleanup_sessions()
    if session_id not in _sessions:
        _sessions[session_id] = {
            "agent": Agent(provider=provider, model=model, api_key=api_key),
            "last_access": time.time(),
        }
    _sessions[session_id]["last_access"] = time.time()
    return _sessions[session_id]["agent"]


def _get_sdk_runner(session_id: str, model: str | None = None):
    """Agent SDK 러너를 가져오거나 생성한다."""
    from .sdk_runner import AgentSDKRunner

    _cleanup_sessions()
    if session_id not in _sessions:
        _sessions[session_id] = {
            "runner": AgentSDKRunner(model=model),
            "last_access": time.time(),
        }
    _sessions[session_id]["last_access"] = time.time()
    return _sessions[session_id]["runner"]


@app.post("/agent/ask")
async def ask_stream(
    query: str = Form(...),
    session_id: str = Form(...),
    provider: str | None = Form(None),
    model: str | None = Form(None),
    api_key: str | None = Form(None),
    files: list[UploadFile] = File(default=[]),
):
    """SSE 스트림으로 에이전트 상태와 최종 답변을 전달한다.

    multipart/form-data로 텍스트 질의 + 첨부파일을 함께 수신한다.
    """
    # 첨부파일 전처리
    attachments = []
    for f in files:
        content_type = f.content_type or "application/octet-stream"
        if content_type not in SUPPORTED_MIMES:
            logger.warning("지원하지 않는 파일 건너뜀: %s (%s)", f.filename, content_type)
            continue
        data = await f.read()
        blocks = process_attachment(f.filename or "unknown", content_type, data)
        attachments.extend(blocks)
        logger.info("첨부파일 처리: %s → %d블록", f.filename, len(blocks))

    # Agent SDK (OAuth 토큰) vs 기존 경로 분기
    # 사용자가 개인 API 키를 제공한 경우 Agent SDK 대신 직접 API 경로 사용
    logger.info("요청 수신 — provider=%s, model=%s, session=%s, user_key=%s", provider, model, session_id, "yes" if api_key else "no")
    if not api_key and config.use_agent_sdk(provider):
        runner = _get_sdk_runner(session_id, model)

        async def sdk_event_generator():
            # Agent SDK (Claude CLI)는 동시 실행 시 OAuth 세션 충돌 발생
            # Lock으로 직렬화하되, 대기 중인 사용자에게 상태 알림
            if _sdk_lock.locked():
                wait_msg = json.dumps(
                    {"type": "status", "message": "다른 사용자의 질문을 처리 중입니다. 잠시 대기 중..."},
                    ensure_ascii=False,
                )
                yield f"event: status\ndata: {wait_msg}\n\n"

            async with _sdk_lock:
                async for event in runner.ask_stream(query, attachments=attachments):
                    event_type = "result" if event["type"] == "result" else "status"
                    data = json.dumps(event, ensure_ascii=False)
                    sse = f"event: {event_type}\ndata: {data}\n\n"
                    logger.info("SSE 전송: event=%s, data=%s", event_type, data[:200])
                    yield sse

        return StreamingResponse(sdk_event_generator(), media_type="text/event-stream")

    # 기존 경로 (API 키 Claude / Gemini)
    agent = _get_agent(session_id, provider, model, api_key=api_key)

    def event_generator():
        for event in agent.ask_stream(query, attachments=attachments):
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
        session = _sessions[req.session_id]
        # SDK 러너의 Agent SDK 세션도 정리
        if "runner" in session:
            session["runner"].reset()
        del _sessions[req.session_id]
    return {"status": "ok"}


@app.get("/agent/models")
async def list_models():
    """사용 가능한 프로바이더별 모델 목록을 반환한다."""
    return config.get_available_models()


@app.get("/agent/health")
async def health():
    return {"status": "ok"}


@app.on_event("startup")
async def _log_mode():
    if config.use_agent_sdk():
        print(f"Agent 모드: SDK (model={config.CLAUDE_MODEL}, MCP 서버: {config.MCP_SERVER_URL})")
    else:
        model = config.CLAUDE_MODEL if config.LLM_PROVIDER == "claude" else config.GEMINI_MODEL
        print(f"Agent 모드: API (provider={config.LLM_PROVIDER}, model={model})")
