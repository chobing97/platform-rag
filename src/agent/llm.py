"""LLM 프로바이더 추상화 — Claude / Gemini 지원."""

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from . import config
from .tools import to_claude_tools, to_gemini_declarations

logger = logging.getLogger(__name__)


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class LLMResponse:
    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = "end_turn"


class LLMProvider(ABC):
    """LLM 프로바이더 추상 인터페이스."""

    @abstractmethod
    def chat(self, system_prompt: str, messages: list[dict]) -> LLMResponse:
        ...


# ─── Claude (Anthropic) ──────────────────────────────

class ClaudeProvider(LLMProvider):
    def __init__(self, model: str | None = None, api_key: str | None = None):
        import anthropic
        key = api_key or config.ANTHROPIC_API_KEY
        if not key:
            raise ValueError("ANTHROPIC_API_KEY가 설정되지 않았습니다. agent/.env 파일을 확인하세요.")
        if key.startswith("sk-ant-oat"):
            # OAuth 토큰: Bearer 인증 사용. 환경변수 ANTHROPIC_API_KEY에서
            # api_key도 자동 로드되어 X-Api-Key 헤더가 함께 전송되는 것을 방지.
            self.client = anthropic.Anthropic(auth_token=key)
            self.client.api_key = None
        else:
            self.client = anthropic.Anthropic(api_key=key)
        self.model = model or config.CLAUDE_MODEL
        self.tools = to_claude_tools()

    def chat(self, system_prompt: str, messages: list[dict]) -> LLMResponse:
        api_messages = _to_claude_messages(messages)
        logger.info("Claude API 호출 — model=%s, messages=%d", self.model, len(api_messages))
        response = self.client.messages.create(
            model=self.model,
            max_tokens=config.MAX_TOKENS,
            extra_headers={
                "anthropic-beta": "oauth-2025-04-20,claude-code-20250219,interleaved-thinking-2025-05-14"
            },
            thinking={"type": "adaptive"},
            system=system_prompt,
            tools=self.tools,
            messages=api_messages,
        )
        logger.info("Claude API 응답 — usage: input=%d, output=%d tokens",
                     response.usage.input_tokens, response.usage.output_tokens)

        text_parts = []
        tool_calls = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(id=block.id, name=block.name, arguments=block.input))

        return LLMResponse(
            text="\n".join(text_parts) if text_parts else None,
            tool_calls=tool_calls,
            stop_reason="tool_use" if response.stop_reason == "tool_use" else "end_turn",
        )


def _to_claude_messages(messages: list[dict]) -> list[dict]:
    """내부 메시지 형식을 Anthropic API 형식으로 변환한다."""
    api_msgs = []
    for msg in messages:
        role = msg["role"]

        if role == "user":
            attachments = msg.get("attachments", [])
            if not attachments:
                api_msgs.append({"role": "user", "content": msg["content"]})
            else:
                # 멀티모달 메시지: 텍스트 + 첨부파일 블록
                content_blocks: list[dict] = []
                for att in attachments:
                    if att["type"] == "image":
                        content_blocks.append({
                            "type": "image",
                            "source": {"type": "base64", "media_type": att["media_type"], "data": att["data"]},
                        })
                    elif att["type"] == "document":
                        content_blocks.append({
                            "type": "document",
                            "source": {"type": "base64", "media_type": att["media_type"], "data": att["data"]},
                        })
                    elif att["type"] == "text":
                        content_blocks.append({"type": "text", "text": att["text"]})
                content_blocks.append({"type": "text", "text": msg["content"]})
                api_msgs.append({"role": "user", "content": content_blocks})

        elif role == "assistant":
            content_blocks = []
            if msg.get("text"):
                content_blocks.append({"type": "text", "text": msg["text"]})
            for tc in msg.get("tool_calls", []):
                content_blocks.append({
                    "type": "tool_use",
                    "id": tc["id"],
                    "name": tc["name"],
                    "input": tc["arguments"],
                })
            api_msgs.append({"role": "assistant", "content": content_blocks})

        elif role == "tool":
            result_blocks = []
            for r in msg["results"]:
                result_blocks.append({
                    "type": "tool_result",
                    "tool_use_id": r["tool_call_id"],
                    "content": json.dumps(r["content"], ensure_ascii=False),
                })
            api_msgs.append({"role": "user", "content": result_blocks})

    return api_msgs


# ─── Gemini (Google) ─────────────────────────────────

class GeminiProvider(LLMProvider):
    def __init__(self, model: str | None = None):
        from google import genai
        if not config.GOOGLE_API_KEY:
            raise ValueError("GOOGLE_API_KEY가 설정되지 않았습니다. agent/.env 파일을 확인하세요.")
        self.client = genai.Client(api_key=config.GOOGLE_API_KEY)
        self.model = model or config.GEMINI_MODEL
        self.declarations = to_gemini_declarations()

    def chat(self, system_prompt: str, messages: list[dict]) -> LLMResponse:
        from google.genai import types

        contents = _to_gemini_contents(messages)
        tools = [types.Tool(function_declarations=self.declarations)]

        logger.info("Gemini API 호출 — model=%s, contents=%d", self.model, len(contents))
        response = self.client.models.generate_content(
            model=self.model,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                tools=tools,
                max_output_tokens=config.MAX_TOKENS,
            ),
        )
        usage = response.usage_metadata
        logger.info("Gemini API 응답 — usage: input=%d, output=%d tokens",
                     usage.prompt_token_count or 0, usage.candidates_token_count or 0)

        text_parts = []
        tool_calls = []
        for i, part in enumerate(response.candidates[0].content.parts):
            if part.function_call:
                fc = part.function_call
                tool_calls.append(ToolCall(
                    id=f"gemini_{i}",
                    name=fc.name,
                    arguments=dict(fc.args) if fc.args else {},
                ))
            elif part.text:
                text_parts.append(part.text)

        return LLMResponse(
            text="\n".join(text_parts) if text_parts else None,
            tool_calls=tool_calls,
            stop_reason="tool_use" if tool_calls else "end_turn",
        )


def _to_gemini_contents(messages: list[dict]) -> list[dict]:
    """내부 메시지 형식을 Gemini API 형식으로 변환한다."""
    from google.genai import types

    contents = []
    for msg in messages:
        role = msg["role"]

        if role == "user":
            parts = []
            for att in msg.get("attachments", []):
                if att["type"] == "image":
                    import base64
                    parts.append(types.Part.from_bytes(data=base64.b64decode(att["data"]), mime_type=att["media_type"]))
                elif att["type"] == "document":
                    import base64
                    parts.append(types.Part.from_bytes(data=base64.b64decode(att["data"]), mime_type=att["media_type"]))
                elif att["type"] == "text":
                    parts.append(types.Part.from_text(text=att["text"]))
            parts.append(types.Part.from_text(text=msg["content"]))
            contents.append(types.Content(role="user", parts=parts))

        elif role == "assistant":
            parts = []
            if msg.get("text"):
                parts.append(types.Part.from_text(text=msg["text"]))
            for tc in msg.get("tool_calls", []):
                parts.append(types.Part.from_function_call(
                    name=tc["name"],
                    args=tc["arguments"],
                ))
            contents.append(types.Content(role="model", parts=parts))

        elif role == "tool":
            parts = []
            for r in msg["results"]:
                parts.append(types.Part.from_function_response(
                    name=r["name"],
                    response=r["content"],
                ))
            contents.append(types.Content(role="user", parts=parts))

    return contents


# ─── 팩토리 ─────────────────────────────────────────

def create_provider(provider: str | None = None, model: str | None = None, api_key: str | None = None) -> LLMProvider:
    """LLM 프로바이더 인스턴스를 생성한다."""
    provider = provider or config.LLM_PROVIDER
    if provider == "claude":
        return ClaudeProvider(model=model, api_key=api_key)
    elif provider == "gemini":
        return GeminiProvider(model=model)
    else:
        raise ValueError(f"지원하지 않는 LLM 프로바이더: {provider}. 'claude' 또는 'gemini'를 사용하세요.")
