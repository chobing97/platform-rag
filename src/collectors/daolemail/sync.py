"""DAOL 그룹웨어 메일 수집기 메인 스크립트."""

import hashlib
import logging
import os
import re
import sys
import time

from daolemail.client import AttachmentInfo, DaolMailClient, MailSummary
from daolemail.db import (
    finish_sync_run,
    get_synced_mail_idxs,
    start_sync_run,
    upsert_mail_state,
)
from daolemail.login import get_cookies

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
DATA_DIR = os.path.join(PROJECT_ROOT, "data", "raw", "daolemail")

# 기본 메일함
DEFAULT_MBOXES = {1: "받은메일함", 3: "보낸메일함"}
PAGE_SIZE = 50
REQUEST_DELAY = 0.3  # 서버 부하 방지


def _sanitize_filename(text: str, max_len: int = 80) -> str:
    """파일명에 사용할 수 없는 문자 제거."""
    text = re.sub(r'[\\/:*?"<>|\r\n]', "_", text)
    text = re.sub(r"_+", "_", text).strip("_. ")
    return text[:max_len] if text else "untitled"


def _bucket_dir(mail_idx: int) -> str:
    """mailIdx 기반 2자리 hex 버킷 디렉토리."""
    h = hashlib.md5(str(mail_idx).encode()).hexdigest()[:2]
    return os.path.join(DATA_DIR, h)


def _save_body_markdown(mail: MailSummary, body: str, mbox_idx: int, attachment_filenames: list[str]) -> str:
    """메일 본문을 YAML frontmatter + Markdown 파일로 저장. 파일 경로 반환."""
    bucket = _bucket_dir(mail.mail_idx)
    os.makedirs(bucket, exist_ok=True)

    filename = f"{_sanitize_filename(mail.subject)}_{mail.mail_idx}.md"
    filepath = os.path.join(bucket, filename)

    body = _strip_disclaimer(body)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("---\n")
        f.write(f"source: daolemail\n")
        f.write(f"content_type: email_body\n")
        f.write(f"mail_idx: {mail.mail_idx}\n")
        f.write(f"mbox_idx: {mbox_idx}\n")
        f.write(f'subject: "{mail.subject}"\n')
        f.write(f'sender: "{mail.sender}"\n')
        f.write(f'date: "{mail.date}"\n')
        if attachment_filenames:
            f.write(f"attachments: {attachment_filenames}\n")
        f.write("---\n\n")
        f.write(f"# {mail.subject}\n\n")
        f.write(f"**보낸사람**: {mail.sender}  \n")
        f.write(f"**날짜**: {mail.date}\n\n")
        f.write("---\n\n")
        f.write(body.strip())
        f.write("\n")

    return filepath


def _save_attachment(
    mail: MailSummary,
    mbox_idx: int,
    attachment: AttachmentInfo,
    data: bytes,
) -> str:
    """첨부파일 바이너리 저장 + 메타데이터 마크다운 생성. 마크다운 파일 경로 반환."""
    bucket = _bucket_dir(mail.mail_idx)
    attach_dir = os.path.join(bucket, "attachments")
    os.makedirs(attach_dir, exist_ok=True)

    # 원본 파일 저장
    safe_name = _sanitize_filename(attachment.filename, max_len=120)
    raw_path = os.path.join(attach_dir, f"{mail.mail_idx}_{safe_name}")
    with open(raw_path, "wb") as f:
        f.write(data)

    # OCR은 별도 ocr 커맨드로 실행. 기존 sidecar가 있으면 로드.
    extracted_text = ""
    sidecar = raw_path + ".txt"
    if os.path.exists(sidecar):
        try:
            with open(sidecar, "r", encoding="utf-8") as f:
                extracted_text = f.read().strip()
        except Exception:
            pass

    # 메타데이터 마크다운 (인덱싱용)
    md_filename = f"{_sanitize_filename(mail.subject)}_{mail.mail_idx}_att_{safe_name}.md"
    md_path = os.path.join(bucket, md_filename)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("---\n")
        f.write(f"source: daolemail\n")
        f.write(f"content_type: email_attachment\n")
        f.write(f"mail_idx: {mail.mail_idx}\n")
        f.write(f"mbox_idx: {mbox_idx}\n")
        f.write(f'subject: "{mail.subject}"\n')
        f.write(f'sender: "{mail.sender}"\n')
        f.write(f'date: "{mail.date}"\n')
        f.write(f'filename: "{attachment.filename}"\n')
        f.write(f'file_path: "{raw_path}"\n')
        f.write("---\n\n")
        f.write(f"# [첨부] {attachment.filename}\n\n")
        f.write(f"**원본 메일**: {mail.subject}  \n")
        f.write(f"**보낸사람**: {mail.sender}  \n")
        f.write(f"**날짜**: {mail.date}\n\n")
        if extracted_text:
            f.write("## 추출된 내용\n\n")
            f.write(extracted_text.strip())
            f.write("\n")
        else:
            f.write(f"첨부파일: `{attachment.filename}` (OCR 미실행 — `./platformagent ocr` 실행 필요)\n")

    return md_path


def _strip_disclaimer(text: str) -> str:
    """메일 하단 면책조항 제거."""
    patterns = [
        r"이 메시지\(첨부파일 포함\)는 보호대상.*?고객만족센터.*?\]",
        r"This message.*?intended recipient.*?delete it.*?\.",
    ]
    for pattern in patterns:
        text = re.sub(pattern, "", text, flags=re.DOTALL)
    return text.strip()


def _collect_mail(client: DaolMailClient, mail: MailSummary, mbox_idx: int) -> str:
    """단일 메일 수집 (본문 + 첨부파일). 본문 파일 경로 반환."""
    # 본문 수집
    body = client.get_mail_body(mbox_idx, mail.mail_idx)
    time.sleep(REQUEST_DELAY)

    # 첨부파일 수집
    attachment_filenames: list[str] = []
    try:
        attachments = client.get_attachment_info(mbox_idx, mail.mail_idx)
        time.sleep(REQUEST_DELAY)

        for att in attachments:
            try:
                data = client.download_attachment(mbox_idx, mail.mail_idx, att)
                _save_attachment(mail, mbox_idx, att, data)
                attachment_filenames.append(att.filename)
                logger.info(f"  첨부파일: {att.filename} ({len(data)} bytes)")
                time.sleep(REQUEST_DELAY)
            except Exception as e:
                logger.warning(f"  첨부파일 다운로드 실패 [{att.filename}]: {e}")
    except Exception as e:
        logger.warning(f"  첨부파일 정보 조회 실패 [{mail.mail_idx}]: {e}")

    # 본문 마크다운 저장
    filepath = _save_body_markdown(mail, body, mbox_idx, attachment_filenames)
    return filepath


def _sync_mailbox(client: DaolMailClient, mbox_idx: int, mbox_name: str, full: bool) -> tuple[int, int]:
    """단일 메일함 수집. (총 메일 수, 신규 수집 수) 반환."""
    synced_idxs = set() if full else get_synced_mail_idxs()
    synced_count = 0

    total_mails, first_page = client.get_mail_list(mbox_idx, PAGE_SIZE, 0)
    logger.info(f"[{mbox_name}] 총 메일 수: {total_mails} (이미 수집: {len(synced_idxs)})")

    offset = 0
    while offset < total_mails:
        if offset == 0:
            mails = first_page
        else:
            _, mails = client.get_mail_list(mbox_idx, PAGE_SIZE, offset)
            time.sleep(REQUEST_DELAY)

        if not mails:
            break

        for mail in mails:
            if mail.mail_idx in synced_idxs:
                logger.debug(f"스킵 (수집완료): [{mail.mail_idx}] {mail.subject}")
                continue

            try:
                filepath = _collect_mail(client, mail, mbox_idx)
            except Exception as e:
                logger.warning(f"수집 실패 [{mail.mail_idx}]: {e}")
                continue

            upsert_mail_state(
                mail_idx=mail.mail_idx,
                mbox_idx=mbox_idx,
                subject=mail.subject,
                sender=mail.sender,
                date=mail.date,
                file_path=filepath,
            )
            synced_count += 1
            logger.info(f"수집 [{synced_count}]: [{mail.mail_idx}] {mail.subject}")

        offset += PAGE_SIZE

    return total_mails, synced_count


def sync(mbox_idx: int | None = None, full: bool = False) -> None:
    """메일 수집 실행. mbox_idx=None이면 전체 메일함 순회."""
    # 1. 쿠키 확보
    cookies = get_cookies()
    if not cookies:
        logger.error("로그인 실패 — 수집 중단")
        sys.exit(1)

    client = DaolMailClient(cookies)
    if not client.is_session_valid():
        logger.error("세션 무효 — 수집 중단")
        sys.exit(1)

    # 2. 수집 대상 메일함 결정
    if mbox_idx is not None:
        mailboxes = {mbox_idx: DEFAULT_MBOXES.get(mbox_idx, f"메일함({mbox_idx})")}
    else:
        # 기본 메일함 + 사용자 정의 메일함
        mailboxes = dict(DEFAULT_MBOXES)
        try:
            custom = client.get_mailboxes()
            for mb in custom:
                key = int(mb["key"])
                # title에서 읽지 않은 수 제거: "IBKR Project <span ...>114</span> " → "IBKR Project"
                import re as _re
                name = _re.sub(r"<[^>]+>", "", mb.get("title", "")).strip()
                mailboxes[key] = name
        except Exception as e:
            logger.warning(f"사용자 정의 메일함 조회 실패: {e}")

    # 3. 동기화 시작
    run_id = start_sync_run()
    total_all = 0
    synced_all = 0

    try:
        for idx, name in mailboxes.items():
            logger.info(f"=== 메일함 수집 시작: {name} (mboxIdx={idx}) ===")
            total, synced = _sync_mailbox(client, idx, name, full)
            total_all += total
            synced_all += synced

        finish_sync_run(run_id, total_all, synced_all)
        logger.info(f"전체 수집 완료 — {len(mailboxes)}개 메일함, 총 {total_all}건 중 {synced_all}건 신규 수집")

    except Exception as e:
        finish_sync_run(run_id, total_all, synced_all, str(e))
        logger.error(f"수집 중 오류: {e}")
        raise


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="DAOL 그룹웨어 메일 수집")
    parser.add_argument("--full", action="store_true", help="전체 재수집 (delta 무시)")
    parser.add_argument("--mbox", type=int, default=None, help="메일함 idx (미지정 시 전체 메일함)")
    args = parser.parse_args()

    sync(mbox_idx=args.mbox, full=args.full)
