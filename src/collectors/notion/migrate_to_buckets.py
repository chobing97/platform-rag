"""기존 flat 구조의 Notion 데이터를 hash bucket 구조로 마이그레이션.

기존: data/notion/{title}_{page_id[:8]}.md, data/notion/media/{page_id[:8]}_{block_id[:8]}{ext}
변경: data/notion/{page_id[:2]}/{title}_{page_id[:8]}.md, data/notion/{page_id[:2]}/media/{...}

사용법: cd src/collectors && .venv/bin/python -m notion.migrate_to_buckets [--dry-run]
"""

import logging
import os
import re
import shutil
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
DATA_DIR = os.path.join(PROJECT_ROOT, "data", "raw", "notion")
MEDIA_DIR = os.path.join(DATA_DIR, "media")

# 파일명 끝의 _{page_id[:8]}.md 패턴
_PAGE_ID_RE = re.compile(r"_([0-9a-f]{8})\.md$")
# 미디어 파일명: {page_id[:8]}_{block_id[:8]}{ext}
_MEDIA_RE = re.compile(r"^([0-9a-f]{8})_")


def migrate(dry_run: bool = False):
    if not os.path.isdir(DATA_DIR):
        logger.error("데이터 디렉토리 없음: %s", DATA_DIR)
        return

    # 1. 마크다운 파일 이동
    md_files = [f for f in os.listdir(DATA_DIR) if f.endswith(".md") and os.path.isfile(os.path.join(DATA_DIR, f))]
    logger.info("마크다운 파일 %d개 발견", len(md_files))

    moved_md = 0
    for fname in md_files:
        m = _PAGE_ID_RE.search(fname)
        if not m:
            logger.warning("page_id 추출 실패, 건너뜀: %s", fname)
            continue

        page_id_prefix = m.group(1)[:2]
        bucket = os.path.join(DATA_DIR, page_id_prefix)
        src = os.path.join(DATA_DIR, fname)
        dst = os.path.join(bucket, fname)

        if dry_run:
            logger.info("[DRY-RUN] %s → %s", fname, os.path.relpath(dst, DATA_DIR))
        else:
            os.makedirs(bucket, exist_ok=True)
            shutil.move(src, dst)
            moved_md += 1

    # 2. 미디어 파일 이동
    media_files = []
    if os.path.isdir(MEDIA_DIR):
        media_files = [f for f in os.listdir(MEDIA_DIR) if os.path.isfile(os.path.join(MEDIA_DIR, f))]
    logger.info("미디어 파일 %d개 발견", len(media_files))

    moved_media = 0
    for fname in media_files:
        m = _MEDIA_RE.match(fname)
        if not m:
            logger.warning("page_id 추출 실패, 건너뜀: %s", fname)
            continue

        page_id_prefix = m.group(1)[:2]
        bucket_media = os.path.join(DATA_DIR, page_id_prefix, "media")
        src = os.path.join(MEDIA_DIR, fname)
        dst = os.path.join(bucket_media, fname)

        # sidecar .txt 도 함께 이동
        sidecar_src = src + ".txt"

        if dry_run:
            logger.info("[DRY-RUN] media/%s → %s", fname, os.path.relpath(dst, DATA_DIR))
        else:
            os.makedirs(bucket_media, exist_ok=True)
            shutil.move(src, dst)
            if os.path.exists(sidecar_src):
                shutil.move(sidecar_src, dst + ".txt")
            moved_media += 1

    # 3. 빈 media/ 디렉토리 제거
    if not dry_run and os.path.isdir(MEDIA_DIR) and not os.listdir(MEDIA_DIR):
        os.rmdir(MEDIA_DIR)
        logger.info("빈 media/ 디렉토리 제거")

    if dry_run:
        logger.info("[DRY-RUN] 완료 — md: %d, media: %d", len(md_files), len(media_files))
    else:
        logger.info("마이그레이션 완료 — md: %d, media: %d", moved_md, moved_media)


if __name__ == "__main__":
    migrate(dry_run="--dry-run" in sys.argv)
