"""Agent 설정 — API 키, 모델 설정, 검색 API 주소."""

import os

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"), override=True)

# LLM Provider: "claude" or "gemini"
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "claude")

# Claude (Anthropic)
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-4-6")
CLAUDE_MODELS = [
    "claude-opus-4-6",
    "claude-sonnet-4-20250514",
    "claude-haiku-4-5-20251001",
]

# Gemini (Google)
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-2.0-flash",
]

# Search API (FastAPI)
SEARCH_API_URL = os.environ.get("SEARCH_API_URL", "http://localhost:8000")

# MCP Server (HTTP)
MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL", "http://localhost:3001/mcp")

# Agent behavior
MAX_TOOL_ROUNDS = int(os.environ.get("MAX_TOOL_ROUNDS", "10"))
RERANK_SCORE_THRESHOLD = float(os.environ.get("RERANK_SCORE_THRESHOLD", "0.3"))
MAX_TOKENS = int(os.environ.get("MAX_TOKENS", "4096"))


def use_agent_sdk(provider: str | None = None) -> bool:
    """OAuth 토큰 사용 시 Agent SDK 활용 여부를 판단한다."""
    provider = provider or LLM_PROVIDER
    return provider == "claude" and ANTHROPIC_API_KEY.startswith("sk-ant-oat")


def get_available_models() -> dict:
    """사용 가능한 모델 목록을 반환한다."""
    models = {}
    if ANTHROPIC_API_KEY:
        models["claude"] = CLAUDE_MODELS
    if GOOGLE_API_KEY:
        models["gemini"] = GEMINI_MODELS
    return models
