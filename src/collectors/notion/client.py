"""Notion API 클라이언트 래퍼."""

import logging
import os
import time

from dotenv import load_dotenv
from notion_client import Client

logger = logging.getLogger(__name__)

RETRYABLE_STATUS = {429, 500, 502, 503, 504}
MAX_RETRIES = 3
RETRY_BACKOFF = 2  # 초 (2, 4, 8)


def _retry_call(fn, *args, **kwargs):
    """Notion API 호출을 재시도 가능한 에러(429/5xx)에 대해 exponential backoff으로 재시도."""
    for attempt in range(MAX_RETRIES + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            status = getattr(getattr(e, "response", None), "status_code", None) or getattr(e, "status", None)
            if status in RETRYABLE_STATUS and attempt < MAX_RETRIES:
                wait = RETRY_BACKOFF * (2 ** attempt)
                logger.warning("Notion API %d 에러 — %d초 후 재시도 (%d/%d)", status, wait, attempt + 1, MAX_RETRIES)
                time.sleep(wait)
            else:
                raise

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))


def get_client() -> Client:
    token = os.environ.get("NOTION_API_TOKEN")
    if not token:
        raise RuntimeError("NOTION_API_TOKEN이 .env에 설정되지 않았습니다.")
    return Client(auth=token)


def list_all_pages(client: Client, query: str = "") -> list[dict]:
    """Integration에 연결된 페이지/DB를 검색한다."""
    results = []
    response = _retry_call(client.search, query=query, page_size=100)
    results.extend(response["results"])

    while response.get("has_more"):
        response = _retry_call(
            client.search,
            query=query,
            page_size=100,
            start_cursor=response["next_cursor"],
        )
        results.extend(response["results"])

    logger.debug("검색 완료: %d개 결과", len(results))
    return results


def get_page_blocks(client: Client, page_id: str) -> list[dict]:
    """페이지의 모든 블록(하위 블록 포함)을 재귀적으로 가져온다."""
    blocks = []
    response = _retry_call(client.blocks.children.list, block_id=page_id, page_size=100)
    blocks.extend(response["results"])

    while response.get("has_more"):
        response = _retry_call(
            client.blocks.children.list,
            block_id=page_id,
            page_size=100,
            start_cursor=response["next_cursor"],
        )
        blocks.extend(response["results"])

    # 하위 블록이 있는 경우 재귀 탐색
    for block in blocks:
        if block.get("has_children"):
            block["children"] = get_page_blocks(client, block["id"])

    logger.debug("블록 조회 완료: %s → %d개", page_id[:8], len(blocks))
    return blocks


def get_page_comments(client: Client, page_id: str) -> list[dict]:
    """페이지에 달린 모든 댓글을 가져온다."""
    comments = []
    response = _retry_call(client.comments.list, block_id=page_id, page_size=100)
    comments.extend(response["results"])

    while response.get("has_more"):
        response = _retry_call(
            client.comments.list,
            block_id=page_id,
            page_size=100,
            start_cursor=response["next_cursor"],
        )
        comments.extend(response["results"])

    logger.debug("댓글 조회 완료: %s → %d개", page_id[:8], len(comments))
    return comments
