"""이메일 첨부파일 텍스트 추출.

Notion OCR 모듈(notion/ocr.py)의 PaddleOCR를 재사용하여
이미지·PDF에서 텍스트를 추출하고, 원본 파일과 같은 경로에 .txt로 저장한다.
"""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
PDF_EXTENSIONS = {".pdf"}
TEXT_EXTENSIONS = {".txt", ".csv", ".log", ".json", ".xml", ".html", ".htm", ".md"}

_ocr_instance = None

# PaddleOCR 3.4.0+ 결과에서 텍스트 추출 시 최소 신뢰도
_MIN_CONFIDENCE = 0.5


def _get_ocr():
    """PaddleOCR 싱글톤."""
    global _ocr_instance
    if _ocr_instance is None:
        from paddleocr import PaddleOCR
        _ocr_instance = PaddleOCR(lang="korean")
        logger.info("PaddleOCR 초기화 완료 (lang=korean)")
    return _ocr_instance


def _extract_texts_from_result(result: list) -> list[str]:
    """PaddleOCR 3.4.0+ OCRResult 리스트에서 텍스트 추출."""
    lines = []
    for page in result:
        if not page:
            continue
        texts = page.get("rec_texts", []) if hasattr(page, "get") else getattr(page, "rec_texts", [])
        scores = page.get("rec_scores", []) if hasattr(page, "get") else getattr(page, "rec_scores", [])
        for text, score in zip(texts, scores):
            if score > _MIN_CONFIDENCE:
                lines.append(text)
    return lines


def _extract_from_image(file_path: str) -> str:
    ocr = _get_ocr()
    try:
        result = ocr.predict(file_path)
        if not result:
            return ""
        text = "\n".join(_extract_texts_from_result(result))
        if text:
            logger.info("이미지 OCR 완료 (%s): %d자", os.path.basename(file_path), len(text))
        return text
    except Exception as e:
        logger.warning("이미지 OCR 실패 (%s): %s", os.path.basename(file_path), e)
        return ""


def _extract_from_pdf(file_path: str) -> str:
    ocr = _get_ocr()
    try:
        result = ocr.predict(file_path)
        if not result:
            return ""
        text = "\n".join(_extract_texts_from_result(result))
        if text:
            logger.info("PDF OCR 완료 (%s): %d자", os.path.basename(file_path), len(text))
        return text
    except Exception as e:
        logger.warning("PDF OCR 실패 (%s): %s", os.path.basename(file_path), e)
        return ""


def _extract_from_text_file(file_path: str) -> str:
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception as e:
        logger.warning("텍스트 파일 읽기 실패 (%s): %s", os.path.basename(file_path), e)
        return ""


def extract_text(file_path: str) -> str:
    """파일에서 텍스트를 추출한다. 지원하지 않는 형식이면 빈 문자열."""
    ext = Path(file_path).suffix.lower()

    if ext in IMAGE_EXTENSIONS:
        return _extract_from_image(file_path)
    elif ext in PDF_EXTENSIONS:
        return _extract_from_pdf(file_path)
    elif ext in TEXT_EXTENSIONS:
        return _extract_from_text_file(file_path)
    else:
        logger.debug("텍스트 추출 미지원 확장자: %s", ext)
        return ""


def extract_and_save(file_path: str) -> str:
    """텍스트 추출 후 같은 경로에 .txt로 저장. 추출된 텍스트 반환."""
    text = extract_text(file_path)
    if not text:
        return ""

    txt_path = file_path + ".txt"
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(text)

    logger.info("텍스트 추출 저장: %s (%d자)", os.path.basename(txt_path), len(text))
    return text
