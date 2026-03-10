"""Markdown 파일을 청크로 분할한다."""

import logging
import os
import re

from config import CHUNK_OVERLAP, CHUNK_SIZE, NOTION_DIR

logger = logging.getLogger(__name__)

_MARKER_RE = re.compile(r"^<!-- @source_type:(\w+)(?::(.+?))? -->$")


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """YAML frontmatter를 파싱하여 메타데이터와 본문을 분리한다."""
    meta = {}
    body = text

    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            for line in parts[1].strip().splitlines():
                if ": " in line:
                    key, val = line.split(": ", 1)
                    meta[key.strip()] = val.strip().strip('"')
            body = parts[2].strip()

    return meta, body


def _split_by_headings(body: str) -> list[dict]:
    """Markdown 본문을 헤딩·소스 타입 마커 기준으로 섹션 분할한다."""
    sections = []
    current_heading = ""
    current_lines: list[str] = []
    current_source_type = "document"
    current_source_file = ""

    def _flush():
        if current_lines:
            sections.append({
                "heading": current_heading,
                "text": "\n".join(current_lines).strip(),
                "source_type": current_source_type,
                "source_file": current_source_file,
            })

    for line in body.splitlines():
        stripped = line.strip()
        marker = _MARKER_RE.match(stripped)

        if marker:
            _flush()
            current_lines = []
            current_source_type = marker.group(1)
            current_source_file = marker.group(2) or ""
        elif re.match(r"^#{1,3}\s+", line):
            _flush()
            current_heading = line.strip()
            current_lines = []
        else:
            current_lines.append(line)

    _flush()
    return sections


def _split_text(text: str, size: int, overlap: int) -> list[str]:
    """긴 텍스트를 고정 크기로 분할한다."""
    if len(text) <= size:
        return [text] if text.strip() else []

    chunks = []
    start = 0
    while start < len(text):
        end = start + size
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = end - overlap

    return chunks


def chunk_file(filepath: str) -> list[dict]:
    """Markdown 파일 하나를 청크 목록으로 변환한다."""
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    meta, body = _parse_frontmatter(content)
    if not body.strip():
        return []

    sections = _split_by_headings(body)
    chunks = []

    for section in sections:
        text = section["text"]
        if not text:
            continue

        sub_chunks = _split_text(text, CHUNK_SIZE, CHUNK_OVERLAP)
        for i, chunk_text in enumerate(sub_chunks):
            chunk_meta = {
                "source": "notion",
                "file_path": filepath,
                "file_name": os.path.basename(filepath),
                "title": meta.get("title", ""),
                "notion_id": meta.get("notion_id", ""),
                "url": meta.get("url", ""),
                "heading": section["heading"],
                "chunk_index": i,
                "source_type": section.get("source_type", "document"),
            }
            if section.get("source_file"):
                chunk_meta["source_file"] = section["source_file"]
            chunks.append({
                "text": chunk_text,
                "metadata": chunk_meta,
            })

    return chunks


def chunk_all() -> list[dict]:
    """data/notion/ 내 모든 Markdown 파일을 청크로 분할한다."""
    all_chunks = []

    if not os.path.isdir(NOTION_DIR):
        logger.warning("Notion 데이터 디렉토리 없음: %s", NOTION_DIR)
        return all_chunks

    files = [f for f in os.listdir(NOTION_DIR) if f.endswith(".md")]
    logger.info("청크 분할 대상: %d개 파일", len(files))

    for i, fname in enumerate(files, 1):
        filepath = os.path.join(NOTION_DIR, fname)
        chunks = chunk_file(filepath)
        all_chunks.extend(chunks)
        if i % 500 == 0:
            logger.info("  진행: %d/%d 파일 처리 (%d 청크)", i, len(files), len(all_chunks))

    logger.info("청크 분할 완료: %d개 파일 → %d개 청크", len(files), len(all_chunks))
    return all_chunks
