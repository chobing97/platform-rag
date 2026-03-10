"""PaddleOCR 기반 이미지·PDF 텍스트 추출."""

import logging
import os
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_ocr_instance = None
_ocr_available: bool | None = None


def _get_ocr():
    """PaddleOCR 인스턴스를 싱글톤으로 반환한다."""
    global _ocr_instance, _ocr_available
    if _ocr_available is False:
        return None
    if _ocr_instance is None:
        try:
            from paddleocr import PaddleOCR

            _ocr_instance = PaddleOCR(lang="korean", use_angle_cls=True, show_log=False)
            _ocr_available = True
            logger.info("PaddleOCR 초기화 완료 (lang=korean)")
        except ImportError:
            _ocr_available = False
            logger.warning(
                "PaddleOCR가 설치되지 않았습니다. OCR 기능이 비활성화됩니다. "
                "설치: pip install paddlepaddle paddleocr PyMuPDF"
            )
            return None
    return _ocr_instance


# ─── 파일 다운로드 ──────────────────────────────────

def download_file(url: str, save_path: str, timeout: int = 120) -> str | None:
    """URL에서 파일을 다운로드한다. 성공 시 저장 경로, 실패 시 None."""
    import requests

    try:
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        resp = requests.get(url, timeout=timeout, stream=True)
        resp.raise_for_status()
        with open(save_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        size_kb = os.path.getsize(save_path) / 1024
        logger.info("다운로드 완료: %s (%.1f KB)", os.path.basename(save_path), size_kb)
        return save_path
    except Exception as e:
        logger.warning("다운로드 실패 (%s): %s", url[:80], e)
        return None


# ─── 텍스트 추출 ──────────────────────────────────

def extract_text_from_image(image_path: str) -> str:
    """이미지에서 OCR로 텍스트를 추출한다."""
    ocr = _get_ocr()
    if ocr is None:
        return ""
    try:
        result = ocr.ocr(image_path, cls=True)
        if not result or not result[0]:
            return ""
        lines = [line[1][0] for line in result[0] if line[1][1] > 0.5]
        return "\n".join(lines)
    except Exception as e:
        logger.warning("이미지 OCR 실패 (%s): %s", os.path.basename(image_path), e)
        return ""


def extract_text_from_pdf(pdf_path: str) -> str:
    """PDF에서 OCR로 텍스트를 추출한다."""
    ocr = _get_ocr()
    if ocr is None:
        return ""
    try:
        result = ocr.ocr(pdf_path, cls=True)
        if not result:
            return ""
        lines = []
        for page in result:
            if page:
                for line in page:
                    if line[1][1] > 0.5:
                        lines.append(line[1][0])
        return "\n".join(lines)
    except Exception as e:
        logger.warning("PDF OCR 실패 (%s): %s", os.path.basename(pdf_path), e)
        return ""


# ─── 유틸리티 ──────────────────────────────────

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
PDF_EXTENSIONS = {".pdf"}


def get_block_url(data: dict) -> str:
    """Notion 블록 데이터에서 파일 URL을 추출한다."""
    if "file" in data and isinstance(data["file"], dict):
        return data["file"].get("url", "")
    if "external" in data and isinstance(data["external"], dict):
        return data["external"].get("url", "")
    return ""


def guess_extension(url: str, block_type: str) -> str:
    """URL에서 파일 확장자를 추측한다."""
    parsed = urlparse(url)
    ext = Path(parsed.path).suffix.lower()
    if ext in (
        ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg",
        ".pdf", ".docx", ".xlsx", ".pptx", ".doc", ".xls", ".ppt",
    ):
        return ext
    defaults = {"image": ".png", "pdf": ".pdf", "file": ""}
    return defaults.get(block_type, "")


def get_filename_from_url(url: str) -> str:
    """URL에서 파일명을 추출한다."""
    parsed = urlparse(url)
    name = Path(parsed.path).name
    return name if name else ""


# ─── 블록 미디어 처리 ──────────────────────────────

def process_media_blocks(blocks: list[dict], page_id: str, media_dir: str) -> int:
    """블록 목록에서 미디어를 다운로드하고 텍스트를 추출한다.

    추출된 텍스트는 각 블록의 '_extracted_text' 키에 저장된다.
    다운로드된 파일 경로는 '_local_path' 키에 저장된다.

    Returns:
        처리된 미디어 블록 수.
    """
    count = 0
    for block in blocks:
        block_type = block.get("type", "")
        block_id = block.get("id", "unknown")

        if block_type in ("image", "file", "pdf"):
            data = block.get(block_type, {})
            url = get_block_url(data)
            if not url:
                continue

            ext = guess_extension(url, block_type)
            filename = f"{page_id[:8]}_{block_id[:8]}{ext}"
            save_path = os.path.join(media_dir, filename)

            downloaded = download_file(url, save_path)
            if not downloaded:
                continue

            block["_local_path"] = save_path
            count += 1

            # 텍스트 추출
            ext_lower = ext.lower()
            if ext_lower in IMAGE_EXTENSIONS:
                text = extract_text_from_image(save_path)
            elif ext_lower in PDF_EXTENSIONS or block_type == "pdf":
                text = extract_text_from_pdf(save_path)
            else:
                text = ""

            if text:
                block["_extracted_text"] = text.strip()
                logger.info(
                    "텍스트 추출 완료: %s (%d자)", os.path.basename(save_path), len(text)
                )

        # 하위 블록 재귀 처리
        if block.get("children"):
            count += process_media_blocks(block["children"], page_id, media_dir)

    return count
