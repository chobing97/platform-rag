"""DAOL 그룹웨어 메일 API 클라이언트."""

import html
import re
import logging
import urllib.parse
from dataclasses import dataclass, field

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://groupware.daolsecurities.com"


@dataclass
class AttachmentInfo:
    """첨부파일 정보."""
    attach_id: str
    filename: str
    eml_path: str  # URL 인코딩 전 원본 경로


@dataclass
class MailDetail:
    """메일 상세 정보 (수신자, 참조자, 첨부파일)."""
    recipients: list[str] = field(default_factory=list)   # ["이름 <email>", ...]
    cc: list[str] = field(default_factory=list)            # ["이름 <email>", ...]
    attachments: list[AttachmentInfo] = field(default_factory=list)


@dataclass
class MailSummary:
    """메일 목록에서 파싱한 메일 요약 정보."""
    mail_idx: int
    subject: str
    sender: str
    date: str
    size: str
    has_attachment: bool = False


class DaolMailClient:
    """Postian 그룹웨어 메일 HTTP 클라이언트."""

    def __init__(self, cookies: dict):
        self.session = requests.Session()
        self.session.cookies.update(cookies)

    def _get(self, path: str, **kwargs) -> requests.Response:
        return self.session.get(f"{BASE_URL}/{path}", **kwargs)

    def _post(self, path: str, **kwargs) -> requests.Response:
        return self.session.post(f"{BASE_URL}/{path}", **kwargs)

    # ── 메일함 목록 ──────────────────────────────────────────

    def get_mailboxes(self) -> list[dict]:
        """사용자 정의 메일함 목록 조회. [{key, title}, ...]"""
        resp = self._post(
            "mailbox.ds?act=refreshMyMbox&menu=1",
            headers={"Content-Type": "text/plain;charset=UTF-8"},
        )
        return resp.json()

    # ── 메일 목록 ────────────────────────────────────────────

    def get_mail_list(self, mbox_idx: int = 1, limit: int = 50, offset: int = 0) -> tuple[int, list[MailSummary]]:
        """메일 목록 조회. (총 메일 수, [MailSummary, ...]) 반환."""
        resp = self._get(
            "maillist.ds",
            params={
                "act": "list",
                "mboxIdx": mbox_idx,
                "limit": limit,
                "offset": offset,
                "order": 0,
                "search": "",
                "filter": 0,
                "detailSearch": "",
                "detailMboxName": "받은메일함",
                "detailMboxIdx": "",
                "sender": "",
                "receiver": "",
                "bodyContent": "",
                "operator": "AND",
            },
        )
        resp.encoding = "utf-8"
        return self._parse_mail_list(resp.text)

    def _parse_mail_list(self, html_text: str) -> tuple[int, list[MailSummary]]:
        """메일 목록 HTML 파싱."""
        # 총 메일 수
        total_match = re.search(r'전체메일\s*<span class="num2">(\d+)</span>', html_text)
        total = int(total_match.group(1)) if total_match else 0

        # 메일 항목 파싱
        mails: list[MailSummary] = []

        # mailIdx + 제목 추출
        subjects = re.findall(
            r'name="list_subject(\d+)"\s+value="([^"]*)"',
            html_text,
        )

        # 보낸사람 추출 (sender td 안의 title 속성)
        senders = re.findall(
            r'<td\s+class="sender"[^>]*>.*?title="([^"]*)"',
            html_text,
            re.DOTALL,
        )

        # 날짜 추출 (time2 td)
        dates = re.findall(
            r'<td\s+class="time2">\s*(?:<!--[^>]*-->)?\s*(?:<!--[^>]*-->)?\s*([\d/:\s]+?)\s*(?:<!--)',
            html_text,
        )

        # 용량 추출
        sizes = re.findall(
            r'<td\s+class="size">\s*([\d.]+ [A-Z]+)',
            html_text,
            re.IGNORECASE,
        )

        for i, (mail_idx_str, subject) in enumerate(subjects):
            sender = html.unescape(senders[i]) if i < len(senders) else ""
            date = dates[i].strip() if i < len(dates) else ""
            size = sizes[i].strip() if i < len(sizes) else ""

            mails.append(MailSummary(
                mail_idx=int(mail_idx_str),
                subject=html.unescape(subject),
                sender=sender,
                date=date,
                size=size,
            ))

        return total, mails

    # ── 메일 본문 ────────────────────────────────────────────

    def get_mail_body(self, mbox_idx: int, mail_idx: int) -> str:
        """메일 본문 프리뷰 (plain text)."""
        resp = self._post(
            "mailread.ds?act=getBodyPreview",
            headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
            data={"mboxIdx": mbox_idx, "mailIdx": mail_idx},
        )
        resp.encoding = "utf-8"
        return resp.text

    # ── 메일 상세 (수신자/참조자/첨부파일) ────────────────────

    def _fetch_detail_html(self, mbox_idx: int, mail_idx: int) -> str:
        """메일 상세 페이지 HTML을 가져온다."""
        resp = self._get(
            "mailread.ds",
            params={
                "act": "basic",
                "mboxIdx": mbox_idx,
                "mailIdx": mail_idx,
                "mailNum": 0,
                "limit": 10,
                "offset": 0,
                "order": 0,
                "filter": 0,
                "search": "",
                "useActiveX": 0,
                "detailSearch": "",
                "detailMboxIdx": mbox_idx,
                "detailMboxName": "받은메일함",
                "operator": "AND",
                "startDate": "",
                "endDate": "",
                "calEndDate": "",
                "_sender": "",
                "_receiver": "",
                "_bodyContent": "",
                "_subject": "",
            },
        )
        resp.encoding = "utf-8"
        return resp.text

    def get_mail_detail(self, mbox_idx: int, mail_idx: int) -> MailDetail:
        """메일 상세 정보 조회 (수신자, 참조자, 첨부파일)."""
        html_text = self._fetch_detail_html(mbox_idx, mail_idx)
        recipients = self._parse_address_field(html_text, "받는사람")
        cc = self._parse_address_field(html_text, "참조")
        attachments = self._parse_attachment_info(html_text)
        return MailDetail(recipients=recipients, cc=cc, attachments=attachments)

    def get_attachment_info(self, mbox_idx: int, mail_idx: int) -> list[AttachmentInfo]:
        """메일 상세 페이지에서 첨부파일 정보 추출. (하위 호환용)"""
        return self.get_mail_detail(mbox_idx, mail_idx).attachments

    @staticmethod
    def _parse_address_field(html_text: str, label: str) -> list[str]:
        """메일 상세 HTML에서 특정 필드(받는사람/참조)의 주소 목록을 파싱한다."""
        # 전략 1: label이 포함된 <th>/<td> 행에서 title 속성의 "이름 <email>" 추출
        pattern = rf'{label}.*?</t[hd]>\s*<td[^>]*>(.*?)</td>'
        match = re.search(pattern, html_text, re.DOTALL | re.IGNORECASE)
        if not match:
            return []

        section = match.group(1)
        # title="이름 &lt;email&gt;" 패턴
        titles = re.findall(r'title="([^"]*)"', section)
        if titles:
            return [html.unescape(t) for t in titles if "@" in html.unescape(t)]

        # 전략 2: title 없으면 텍스트에서 직접 "이름 <email>" 패턴 추출
        decoded = html.unescape(section)
        # HTML 태그 제거
        decoded = re.sub(r"<[^>]+>", " ", decoded)
        addresses = re.findall(r'[^,;]+?\s*<[^>]+@[^>]+>', decoded)
        return [a.strip() for a in addresses]

    def _parse_attachment_info(self, html_text: str) -> list[AttachmentInfo]:
        """메일 상세 HTML에서 첨부파일 목록 파싱."""
        # emlPath 추출
        eml_match = re.search(r'name="emlPath"\s+value="([^"]*)"', html_text)
        if not eml_match:
            return []
        eml_path = eml_match.group(1)

        # attachIdxs 추출 (예: ",2." 또는 ",2.,3.")
        idx_match = re.search(r"attachIdxs\s*=\s*'([^']*)'", html_text)
        if not idx_match or not idx_match.group(1).strip(","):
            return []
        attach_ids = [aid for aid in idx_match.group(1).split(",") if aid]

        # 파일명 추출: downloadAttach('2.') 뒤의 링크 텍스트
        filenames = re.findall(
            r"downloadAttach\('([^']+)'\).*?>([^<]+)</a>",
            html_text,
        )
        id_to_name = {aid: name.strip() for aid, name in filenames}

        attachments = []
        for aid in attach_ids:
            filename = id_to_name.get(aid, f"attachment_{aid}")
            attachments.append(AttachmentInfo(
                attach_id=aid,
                filename=filename,
                eml_path=eml_path,
            ))

        return attachments

    def download_attachment(self, mbox_idx: int, mail_idx: int, attachment: AttachmentInfo) -> bytes:
        """첨부파일 바이너리 다운로드."""
        encoded_path = urllib.parse.quote(attachment.eml_path, safe="")
        resp = self._get(
            "mailread.ds",
            params={
                "act": "attachDownload",
                "mailIdx": mail_idx,
                "mboxIdx": mbox_idx,
                "path": encoded_path,
                "attachId": attachment.attach_id,
                "mrm": "sdk",
            },
        )
        resp.raise_for_status()
        return resp.content

    # ── 세션 유효성 확인 ─────────────────────────────────────

    def is_session_valid(self) -> bool:
        """쿠키가 유효한지 확인."""
        try:
            mailboxes = self.get_mailboxes()
            return isinstance(mailboxes, list)
        except Exception:
            return False
