"""Notion 데이터 증분 동기화 — 진입점."""

import logging
import os
import re
from datetime import datetime, timezone

from .client import get_client, get_page_blocks, get_page_comments, list_all_pages
from .db import (
    clear_page_states,
    finish_sync_run,
    get_page_last_edited,
    start_sync_run,
    upsert_page_state,
)
from .exporter import blocks_to_markdown, comments_to_markdown, get_page_title
from .ocr import process_media_blocks

logger = logging.getLogger(__name__)

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
DATA_DIR = os.path.join(PROJECT_ROOT, "data", "notion")
MEDIA_DIR = os.path.join(DATA_DIR, "media")


def _sanitize_filename(name: str) -> str:
    """파일명에 사용할 수 없는 문자를 제거한다."""
    name = re.sub(r'[<>:"/\\|?*]', "", name)
    name = name.strip()
    return name[:100] if name else "Untitled"


def _setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def sync(full: bool = False):
    """Notion에서 페이지를 수집하여 data/notion/에 Markdown으로 저장한다.

    Args:
        full: True이면 모든 페이지를 강제로 재수집한다.
    """
    _setup_logging()

    if full:
        logger.info("전체 동기화 모드 — 페이지 상태 초기화")
        clear_page_states()

    now = datetime.now(timezone.utc).isoformat()
    run_id = start_sync_run("notion", now)
    logger.info("동기화 시작 (run_id=%d)", run_id)

    client = get_client()
    os.makedirs(DATA_DIR, exist_ok=True)

    total_pages = 0
    synced_count = 0
    error_msg = None

    try:
        logger.info("Notion 페이지 목록 조회 중...")
        pages = list_all_pages(client)
        page_list = [p for p in pages if p.get("object") == "page"]
        total_pages = len(page_list)
        logger.info("전체 %d개 페이지 발견", total_pages)

        for i, page in enumerate(page_list, 1):
            page_id = page["id"]
            last_edited = page.get("last_edited_time", "")
            title = get_page_title(page)

            # 증분 동기화: 페이지별 last_edited 비교
            prev_edited = get_page_last_edited(page_id)
            if prev_edited and last_edited <= prev_edited:
                logger.debug("[%d/%d] 변경 없음, 건너뜀: %s", i, total_pages, title)
                continue

            logger.info("[%d/%d] 수집 중: %s (id=%s)", i, total_pages, title, page_id[:8])

            blocks = get_page_blocks(client, page_id)
            comments = get_page_comments(client, page_id)

            # 미디어 다운로드 + OCR 텍스트 추출
            media_count = process_media_blocks(blocks, page_id, MEDIA_DIR)
            if media_count:
                logger.info("  미디어 처리: %d개 파일", media_count)

            markdown = blocks_to_markdown(blocks)
            comments_md = comments_to_markdown(comments)

            synced_at = datetime.now(timezone.utc).isoformat()

            frontmatter = [
                "---",
                f"title: \"{title}\"",
                f"notion_id: {page_id}",
                f"last_edited: {last_edited}",
                f"url: {page.get('url', '')}",
                f"synced_at: {synced_at}",
                "---",
            ]

            content = "\n".join(frontmatter) + "\n\n" + f"# {title}\n\n" + markdown
            if comments_md:
                content += "\n\n---\n\n" + comments_md

            filename = f"{_sanitize_filename(title)}_{page_id[:8]}.md"
            filepath = os.path.join(DATA_DIR, filename)

            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)

            upsert_page_state(page_id, title, last_edited, filepath, synced_at)
            synced_count += 1

            comment_count = len(comments)
            logger.info("  저장 완료: %s (블록 %d개, 댓글 %d개)", filename, len(blocks), comment_count)

    except Exception as e:
        error_msg = str(e)
        logger.error("동기화 실패: %s", error_msg, exc_info=True)
    finally:
        finished_at = datetime.now(timezone.utc).isoformat()
        finish_sync_run(run_id, finished_at, total_pages, synced_count, error_msg)

    if error_msg:
        logger.error("동기화 비정상 종료 (run_id=%d)", run_id)
    else:
        logger.info("동기화 완료: %d/%d개 페이지 수집 (run_id=%d)", synced_count, total_pages, run_id)


if __name__ == "__main__":
    import sys
    sync(full="--full" in sys.argv)
