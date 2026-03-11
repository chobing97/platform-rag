"""DAOL 그룹웨어 로그인 및 쿠키 관리."""

import getpass
import json
from pathlib import Path

import requests

BASE_URL = "https://groupware.daolsecurities.com"
LOGIN_URL = f"{BASE_URL}/login.ds"
COOKIE_PATH = Path(__file__).resolve().parents[3] / "data" / "raw" / "daolemail_cookies.json"


def login(user_id: str, password: str) -> dict | None:
    """로그인 후 쿠키를 반환한다. 실패 시 None."""
    data = {
        "csrf": "",
        "act": "",
        "domain": "daolfn.com",
        "language": "kr",
        "mode": "",
        "type": "",
        "notiIdx": "",
        "notiPopType": "",
        "pkey": "",
        "passwd": "",
        "email": "1111",
        "chglang": "",
        "domainIndex": "3",
        "id": user_id,
        "password": password,
        "domainList": "daolfn.com",
        "input2": "",
    }

    session = requests.Session()
    resp = session.post(
        LOGIN_URL,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        allow_redirects=False,
    )

    cookies = session.cookies.get_dict()
    usk = cookies.get("usk")
    uap = cookies.get("uap")

    if usk and uap:
        print(f"[OK] 로그인 성공")
        print(f"  usk = {usk}")
        print(f"  uap = {uap[:30]}...")
        return cookies

    set_cookies = resp.headers.get("Set-Cookie", "")
    print(f"[FAIL] 로그인 실패")
    print(f"  HTTP Status: {resp.status_code}")
    print(f"  Set-Cookie: {set_cookies[:100]}")
    print(f"  Location: {resp.headers.get('Location', 'N/A')}")
    return None


def save_cookies(cookies: dict) -> None:
    """쿠키를 파일에 저장."""
    COOKIE_PATH.parent.mkdir(parents=True, exist_ok=True)
    COOKIE_PATH.write_text(json.dumps(cookies, ensure_ascii=False))
    print(f"[OK] 쿠키 저장 → {COOKIE_PATH}")


def load_cookies() -> dict | None:
    """저장된 쿠키를 로드. 없으면 None."""
    if not COOKIE_PATH.exists():
        return None
    try:
        return json.loads(COOKIE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def verify_cookies(cookies: dict) -> bool:
    """쿠키가 유효한지 메일함 목록 조회로 확인."""
    resp = requests.post(
        f"{BASE_URL}/mailbox.ds?act=refreshMyMbox&menu=1",
        headers={"Content-Type": "text/plain;charset=UTF-8"},
        cookies=cookies,
    )
    try:
        data = resp.json()
        print(f"[OK] 쿠키 유효 — 메일함 {len(data)}개 확인")
        return True
    except Exception:
        print(f"[FAIL] 쿠키 만료")
        return False


def get_cookies() -> dict | None:
    """유효한 쿠키 반환. 저장된 쿠키가 만료되었으면 재로그인."""
    cookies = load_cookies()
    if cookies and verify_cookies(cookies):
        return cookies

    print("로그인이 필요합니다.")
    user_id = input("ID: ")
    password = getpass.getpass("Password: ")

    cookies = login(user_id, password)
    if cookies:
        save_cookies(cookies)
        return cookies
    return None


if __name__ == "__main__":
    cookies = get_cookies()
    if cookies:
        verify_cookies(cookies)
