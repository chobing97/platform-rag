"""핵심 에이전트 루프 — 도구 호출 ↔ LLM 반복."""

from __future__ import annotations

import json
import logging
from collections.abc import Generator
from typing import TYPE_CHECKING

from . import config
from .llm import LLMResponse, create_provider
from .prompts import get_system_prompt
from .tools import execute_tool

if TYPE_CHECKING:
    from .attachments import ContentBlock

logger = logging.getLogger(__name__)

# ─── 사용자 친화적 메시지 ────────────────────────────

_TOOL_CALL_MESSAGES = {
    "search_knowledge": lambda args: f"지식베이스에서 '{args.get('query', '')}' 검색 중...",
    "get_document": lambda args: "문서 상세 내용을 가져오는 중...",
    "list_sources": lambda args: "수집된 문서 목록을 조회하는 중...",
    "get_related": lambda args: "관련 문서를 탐색하는 중...",
}


def _tool_call_message(name: str, args: dict) -> str:
    return _TOOL_CALL_MESSAGES.get(name, lambda a: f"{name} 실행 중...")(args)


def _tool_result_message(name: str, result: dict) -> str:
    if "error" in result:
        return f"오류 발생: {result['error']}"
    if name == "search_knowledge":
        count = result.get("count", 0)
        return f"{count}건의 관련 문서를 찾았습니다" if count else "검색 결과가 없습니다"
    if name == "list_sources":
        count = len(result.get("sources", []))
        return f"{count}개의 문서가 등록되어 있습니다"
    return "완료"


# ─── 이벤트 타입 ──────────────────────────────────

def _status(message: str, **extra) -> dict:
    return {"type": "status", "message": message, **extra}


def _tool_event(event_type: str, tool: str, message: str, **extra) -> dict:
    return {"type": event_type, "tool": tool, "message": message, **extra}


def _result(text: str) -> dict:
    return {"type": "result", "text": text}


def _error(message: str) -> dict:
    return {"type": "error", "message": message}


# ─── Agent ────────────────────────────────────────

class Agent:
    def __init__(self, provider: str | None = None, model: str | None = None, api_key: str | None = None):
        self.llm = create_provider(provider, model=model, api_key=api_key)
        self.system_prompt = get_system_prompt()
        self.conversation: list[dict] = []

    def ask(self, user_input: str, *, attachments: list[ContentBlock] | None = None) -> str:
        """사용자 질문에 대해 도구를 활용하여 답변한다 (CLI용)."""
        final_text = "(응답 없음)"
        for event in self.ask_stream(user_input, attachments=attachments):
            if event["type"] == "result":
                final_text = event["text"]
            elif event["type"] == "error":
                final_text = f"오류: {event['message']}"
        return final_text

    def ask_stream(self, user_input: str, *, attachments: list[ContentBlock] | None = None) -> Generator[dict, None, None]:
        """각 단계를 이벤트로 yield하는 스트리밍 버전."""
        logger.info("프롬프트 수신: %s (첨부 %d건)", user_input[:80] + ("..." if len(user_input) > 80 else ""), len(attachments or []))

        user_msg: dict = {"role": "user", "content": user_input}
        if attachments:
            user_msg["attachments"] = [
                {"type": a.type, "media_type": a.media_type, "data": a.data, "text": a.text, "file_name": a.file_name}
                for a in attachments
            ]
        self.conversation.append(user_msg)

        yield _status("질문을 분석하고 있습니다...")

        for round_num in range(config.MAX_TOOL_ROUNDS):
            logger.info("LLM 호출 (round %d/%d)...", round_num + 1, config.MAX_TOOL_ROUNDS)

            if round_num > 0:
                yield _status("추가 정보를 바탕으로 답변을 보완하고 있습니다...")

            try:
                response = self.llm.chat(self.system_prompt, self.conversation)
            except Exception as e:
                logger.error("LLM 호출 실패: %s", e)
                yield _error(f"LLM 호출 실패: {e}")
                return

            logger.info("LLM 응답 수신 — stop_reason=%s, tool_calls=%d", response.stop_reason, len(response.tool_calls))

            # assistant 메시지 기록
            assistant_msg: dict = {"role": "assistant", "text": response.text, "tool_calls": []}
            for tc in response.tool_calls:
                assistant_msg["tool_calls"].append({
                    "id": tc.id, "name": tc.name, "arguments": tc.arguments,
                })
            self.conversation.append(assistant_msg)

            # 도구 호출이 없으면 최종 답변
            if not response.tool_calls:
                logger.info("최종 답변 생성 완료 (%d자)", len(response.text or ""))
                yield _result(response.text or "(응답 없음)")
                return

            # 도구 실행
            results = []
            for tc in response.tool_calls:
                yield _tool_event("tool_call", tc.name, _tool_call_message(tc.name, tc.arguments), args=tc.arguments)

                logger.info("도구 실행: %s(%s)", tc.name, json.dumps(tc.arguments, ensure_ascii=False))
                result = execute_tool(tc.name, tc.arguments)
                result_size = len(json.dumps(result, ensure_ascii=False))
                has_error = "error" in result
                logger.info("도구 완료: %s → %d bytes%s", tc.name, result_size, " [ERROR]" if has_error else "")

                yield _tool_event("tool_result", tc.name, _tool_result_message(tc.name, result))

                results.append({"tool_call_id": tc.id, "name": tc.name, "content": result})

            self.conversation.append({"role": "tool", "results": results})
            yield _status("검색 결과를 바탕으로 답변을 생성하고 있습니다...")

        logger.warning("도구 호출 최대 횟수(%d) 도달", config.MAX_TOOL_ROUNDS)
        yield _error("도구 호출 횟수가 최대치에 도달했습니다. 질문을 좁혀서 다시 시도해 주세요.")

    def reset(self):
        """대화 기록을 초기화한다."""
        self.conversation = []
