"""Shared, dependency-free OpenDART transport and response validation."""

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from io import BytesIO
from xml.etree import ElementTree


class DartAPIError(RuntimeError):
    def __init__(self, status: str, message: str):
        self.status = status
        super().__init__(message)


_STATUS_MESSAGES = {
    "010": "DART API 키를 확인해 주세요. OpenDART에서 발급받은 키를 입력한 뒤 다시 실행하세요.",
    "011": "DART API 키 사용이 중지됐습니다. OpenDART에서 키 상태를 확인해 주세요.",
    "012": "DART API 접근을 허용한 IP인지 OpenDART 설정을 확인해 주세요.",
    "013": "조회된 DART 자료가 없습니다. 회사와 조회기간을 확인해 주세요.",
    "014": "DART에서 원문파일을 찾지 못했습니다. 공시 원문을 직접 확인해 주세요.",
    "020": "DART 요청 한도를 초과했습니다. OpenDART 이용 한도를 확인한 뒤 다시 실행해 주세요.",
    "021": "DART 조회 가능한 회사 수를 초과했습니다. 조회 대상을 줄여 주세요.",
    "100": "DART 조회 조건을 확인해 주세요. 회사와 조회기간을 다시 선택하세요.",
    "101": "DART에서 허용한 접근 방식인지 OpenDART 설정을 확인해 주세요.",
    "800": "DART가 시스템 점검 중입니다. 잠시 후 다시 실행해 주세요.",
    "900": "DART에서 오류가 발생했습니다. 잠시 후 다시 실행해 주세요.",
    "901": "사용기간이 지난 DART API 키입니다. OpenDART에서 새 키를 발급받아 주세요.",
}
_REQUEST_LOCK = threading.Lock()
_LAST_REQUEST_AT = 0.0
_REQUEST_INTERVAL = 0.2
_MAX_ATTEMPTS = 3
_RETRY_HTTP_CODES = {429, 500, 502, 503, 504}


def check_dart_status(data: dict, *, allow_no_data: bool = False) -> None:
    status = str(data.get("status") or "")
    if status == "000" or (allow_no_data and status == "013"):
        return
    # Do not echo server messages or request URLs: they may contain the API key.
    raise DartAPIError(status, _STATUS_MESSAGES.get(status, "DART 응답을 확인할 수 없습니다. 잠시 후 다시 실행해 주세요."))


def _wait_for_request_slot() -> None:
    global _LAST_REQUEST_AT
    with _REQUEST_LOCK:
        delay = _REQUEST_INTERVAL - (time.monotonic() - _LAST_REQUEST_AT)
        if delay > 0:
            time.sleep(delay)
        _LAST_REQUEST_AT = time.monotonic()


def _retry_delay(attempt: int, retry_after: str | None = None) -> float:
    try:
        return max(0.0, min(10.0, float(retry_after)))
    except (TypeError, ValueError):
        return float(2 ** attempt)


def get_dart_bytes(url: str, params: dict[str, str], *, timeout: int, user_agent: str) -> bytes:
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(f"{url}?{query}", headers={"User-Agent": user_agent})
    for attempt in range(_MAX_ATTEMPTS):
        _wait_for_request_slot()
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            code = exc.code
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            exc.close()
            if code not in _RETRY_HTTP_CODES or attempt == _MAX_ATTEMPTS - 1:
                raise DartAPIError("http", f"DART에 연결하지 못했습니다(HTTP {code}). 잠시 후 다시 실행해 주세요.") from None
            time.sleep(_retry_delay(attempt, retry_after))
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == _MAX_ATTEMPTS - 1:
                raise DartAPIError("network", "DART에 연결하지 못했습니다. 인터넷 연결을 확인한 뒤 다시 실행해 주세요.") from None
            time.sleep(_retry_delay(attempt))
    raise AssertionError("unreachable")


def _check_xml_error(data: bytes) -> None:
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError:
        return
    if root.tag.rsplit("}", 1)[-1].lower() == "result":
        status_node = next((node for node in root if node.tag.rsplit("}", 1)[-1] == "status"), None)
        # CORPCODE.xml also uses <result>, but contains <list> rather than status.
        if status_node is not None:
            check_dart_status({"status": status_node.text})


def parse_dart_json(data: bytes) -> dict:
    try:
        result = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _check_xml_error(data)
        raise DartAPIError("format", "DART 응답 형식이 올바르지 않습니다. 잠시 후 다시 실행해 주세요.") from None
    if not isinstance(result, dict):
        raise DartAPIError("format", "DART 응답 형식이 올바르지 않습니다. 잠시 후 다시 실행해 주세요.")
    check_dart_status(result, allow_no_data=True)
    return result


def read_dart_xml_files(data: bytes) -> list[tuple[str, bytes]]:
    """Reject API error XML/HTML instead of caching it as disclosure text."""
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            files = [(name, archive.read(name)) for name in archive.namelist() if name.lower().endswith(".xml")]
    except (zipfile.BadZipFile, RuntimeError):
        _check_xml_error(data)
        raise DartAPIError("format", "DART 원문 압축파일을 읽지 못했습니다. 공시를 다시 조회해 주세요.") from None
    if not files:
        raise DartAPIError("format", "DART 원문 압축파일에 XML 문서가 없습니다. 공시를 다시 조회해 주세요.")
    for _, content in files:
        _check_xml_error(content)
    return files
