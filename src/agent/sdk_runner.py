"""Agent SDK 기반 러너 — Claude Max OAuth 토큰으로 MCP 서버를 통해 도구 실행."""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

from . import config
from .prompts import get_system_prompt

if TYPE_CHECKING:
    from .attachments import ContentBlock

logger = logging.getLogger(__name__)


class AgentSDKRunner:
    """Agent SDK를 통해 Claude를 호출하는 러너.

    Claude Max OAuth 토큰 사용 시 기존 MCP 서버(HTTP)에 연결하여
    search_knowledge, get_document 등 도구를 실행한다.
    """

    def __init__(self, model: str | None = None):
        self.system_prompt = get_system_prompt()
        self.model = model or config.CLAUDE_MODEL
        self.session_id: str | None = None

    async def ask_stream(
        self,
        user_input: str,
        *,
        attachments: list[ContentBlock] | None = None,
    ) -> AsyncGenerator[dict, None]:
        """Agent SDK를 통해 질문에 답변한다.

        Agent.ask_stream()과 동일한 이벤트 dict를 yield한다.
        """
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            ResultMessage,
            SystemMessage,
            TextBlock,
            query,
        )

        logger.info(
            "Agent SDK 프롬프트 수신: %s (첨부 %d건)",
            user_input[:80] + ("..." if len(user_input) > 80 else ""),
            len(attachments or []),
        )

        # 텍스트 첨부파일을 프롬프트에 포함 (Agent SDK는 멀티모달 프롬프트 미지원)
        prompt = _build_prompt(user_input, attachments)

        yield {"type": "status", "message": "질문을 분석하고 있습니다..."}

        # Claude Code 내부 터미널에서 실행 시 중첩 세션 방지 해제
        os.environ.pop("CLAUDECODE", None)
        # OAuth 토큰이 ANTHROPIC_API_KEY에 있으면 CLI가 잘못된 API 키로 인식 — 제거
        # (CLI는 자체 OAuth 인증 사용)
        os.environ.pop("ANTHROPIC_API_KEY", None)

        # 세션 유지 시 resume, 아니면 새 세션
        # resume 시에도 mcp_servers를 전달하여 MCP 세션이 만료되었더라도 재연결 가능
        mcp_cfg = {"knowledge": {"type": "http", "url": config.MCP_SERVER_URL}}
        if self.session_id:
            options = ClaudeAgentOptions(
                resume=self.session_id,
                mcp_servers=mcp_cfg,
                permission_mode="bypassPermissions",
            )
        else:
            options = ClaudeAgentOptions(
                system_prompt=self.system_prompt,
                model=self.model,
                thinking={"type": "adaptive"},
                mcp_servers=mcp_cfg,
                max_turns=config.MAX_TOOL_ROUNDS,
                permission_mode="bypassPermissions",
            )

        try:
            got_result = False
            async for message in query(prompt=prompt, options=options):
                # 수신된 모든 메시지 타입/내용 로깅
                msg_type = type(message).__name__
                logger.info(
                    "Agent SDK 메시지 수신: type=%s, attrs=%s",
                    msg_type,
                    {k: repr(v)[:200] for k, v in vars(message).items()} if hasattr(message, "__dict__") else repr(message)[:300],
                )

                if isinstance(message, SystemMessage):
                    if message.subtype == "init":
                        self.session_id = message.data.get("session_id")
                        logger.info("Agent SDK 세션: %s", self.session_id)

                elif isinstance(message, AssistantMessage):
                    # 중간 AssistantMessage에서 thinking/tool_use 블록 추출
                    for block in message.content:
                        block_cls = type(block).__name__
                        logger.info("  블록: %s, attrs=%s", block_cls, repr(block)[:200])

                        if block_cls == "ThinkingBlock":
                            thinking_text = getattr(block, "thinking", "")
                            if thinking_text:
                                logger.info("Agent SDK thinking (%d자)", len(thinking_text))
                                yield {"type": "status", "message": "생각하는 중..."}
                        elif block_cls == "ToolUseBlock":
                            tool_name = getattr(block, "name", "unknown")
                            tool_input = getattr(block, "input", {})
                            logger.info("Agent SDK 도구 호출: %s(%s)", tool_name, tool_input)
                            display = _humanize_tool_call(tool_name, tool_input)
                            if display:
                                yield {
                                    "type": "tool_call",
                                    "message": display,
                                    "tool": tool_name,
                                }
                        elif block_cls == "TextBlock":
                            text = getattr(block, "text", "")
                            if text:
                                logger.info("Agent SDK 중간 텍스트 (%d자)", len(text))
                        else:
                            logger.info("Agent SDK 알 수 없는 블록: %s", block_cls)

                elif isinstance(message, ResultMessage):
                    text = message.result or "(응답 없음)"
                    is_error = getattr(message, "is_error", False)
                    logger.info(
                        "Agent SDK 답변 수신 (%d자, is_error=%s)",
                        len(text), is_error,
                    )
                    yield {"type": "result", "text": text}
                    got_result = True

                    # 에러 결과(rate limit 등)인 경우 세션 리셋 —
                    # 손상된 세션을 재사용하면 권한/MCP 연결이 깨짐
                    if is_error:
                        logger.warning(
                            "에러 응답 수신 — 세션 리셋 (error=%s)",
                            getattr(message, "error", "unknown"),
                        )
                        self.session_id = None

            if not got_result:
                yield {"type": "result", "text": "(응답 없음)"}

        except Exception as e:
            if got_result:
                # 답변은 이미 전달됨 — CLI 종료 시 에러는 경고로만 기록
                logger.warning("Agent SDK CLI 종료 에러 (답변은 정상 수신): %s", e)
                # CLI 비정상 종료 시 세션 상태 불안정 — 리셋
                self.session_id = None
            else:
                logger.error("Agent SDK 호출 실패: %s", e)
                # MCP 연결 실패 가능성 — 세션 리셋하여 다음 요청에서 새로 시작
                self.session_id = None
                logger.info("세션 리셋 — 다음 요청 시 새 세션으로 시작합니다.")
                stderr = getattr(e, "stderr", None) or ""
                detail = f"{e}\nstderr: {stderr}" if stderr else str(e)
                yield {"type": "error", "message": f"Agent SDK 호출 실패: {detail}"}

    def reset(self):
        """세션을 초기화한다."""
        self.session_id = None


def _humanize_tool_call(tool_name: str, tool_input: dict) -> str | None:
    """기술적 도구 이름을 사용자 친화적 한국어 메시지로 변환한다.

    None을 반환하면 UI에 표시하지 않는다 (내부 도구).
    """
    # MCP 도구: mcp__knowledge__<tool> 형식
    if tool_name == "mcp__knowledge__search_knowledge":
        query = tool_input.get("query", "")
        return f'지식베이스 검색 중: "{query}"' if query else "지식베이스 검색 중..."
    if tool_name == "mcp__knowledge__get_document":
        doc_id = tool_input.get("doc_id", "")
        return f"문서 조회 중: {doc_id}" if doc_id else "문서 조회 중..."
    if tool_name == "mcp__knowledge__list_sources":
        return "문서 목록 조회 중..."
    if tool_name == "mcp__knowledge__get_related":
        return "관련 문서 검색 중..."
    if tool_name == "mcp__knowledge__list_email_contacts":
        keyword = tool_input.get("keyword", "")
        return f'이메일 인물 검색 중: "{keyword}"' if keyword else "이메일 인물 검색 중..."
    if tool_name == "mcp__knowledge__get_search_filters":
        return "검색 필터 조회 중..."

    # Agent SDK 내부 도구 — UI에 표시하지 않음
    if tool_name in ("ToolSearch", "ToolSearch_bm25", "ToolSearch_regex"):
        return None

    # 알 수 없는 도구 — 이름 그대로 표시
    return f"도구 실행 중: {tool_name}"


def _build_prompt(user_input: str, attachments: list[ContentBlock] | None) -> str:
    """첨부파일이 있으면 텍스트로 변환하여 프롬프트에 포함한다."""
    if not attachments:
        return user_input

    parts: list[str] = []
    for att in attachments:
        if att.type == "text" and att.text:
            parts.append(f"[첨부: {att.file_name}]\n{att.text}")
        elif att.type in ("image", "document"):
            # Agent SDK는 바이너리 첨부 미지원 — 파일명만 알림
            parts.append(f"[첨부: {att.file_name} — 바이너리 파일은 Agent SDK에서 직접 처리할 수 없습니다]")

    if parts:
        return "\n\n".join(parts) + "\n\n" + user_input
    return user_input
