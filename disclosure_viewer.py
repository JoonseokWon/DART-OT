import html
import json
import os
import re
import socket
import threading
import urllib.parse
import urllib.request
import webbrowser
import zipfile
from datetime import datetime
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from html.parser import HTMLParser
from xml.etree import ElementTree


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / ".dart_ot_config.json"
CORP_CACHE_PATH = ROOT / ".dart_corp_cache.json"
DOCUMENT_PREVIEW_CHARS = 220_000
SEARCH_CONTEXT_CHARS = 4500
SEARCH_MAX_RESULTS = 80
_CORP_CODES: list["CorpInfo"] | None = None
_DOCUMENT_FILES: dict[str, list[tuple[str, str]]] = {}


@dataclass
class CorpInfo:
    corp_code: str
    corp_name: str
    stock_code: str


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_config(config: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def decode_dart_document(data: bytes) -> str:
    candidates: list[tuple[int, str]] = []
    for encoding in ("utf-8", "cp949", "euc-kr"):
        text = data.decode(encoding, errors="ignore")
        keyword_score = sum(text.count(keyword) for keyword in ("차입", "사채", "이자", "금리", "재무제표")) * 1000
        hangul_score = len(re.findall(r"[가-힣]", text))
        broken_score = text.count("\ufffd") * 100
        candidates.append((keyword_score + hangul_score - broken_score, text))
    return max(candidates, key=lambda item: item[0])[1]


def clean_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def highlight_text(value: str, keyword: str) -> str:
    escaped = html.escape(value)
    needle = keyword.strip()
    if not needle:
        return escaped
    pattern = re.compile(re.escape(html.escape(needle)), re.IGNORECASE)
    return pattern.sub(lambda match: f"<mark>{match.group(0)}</mark>", escaped)


class DartDocumentRenderer(HTMLParser):
    block_tags = {"document", "cover", "section", "part", "chapter", "body"}
    paragraph_tags = {"p", "div", "span"}
    title_tags = {"title", "subtitle"}
    table_tags = {"table", "thead", "tbody", "tfoot", "tr", "td", "th"}

    def __init__(self, keyword: str = ""):
        super().__init__(convert_charrefs=True)
        self.keyword = keyword
        self.parts: list[str] = []
        self.open_blocks: list[str] = []

    def render(self, text: str) -> str:
        prepared = re.sub(r"<\?xml[^>]*>", "", text, flags=re.IGNORECASE)
        prepared = re.sub(r"<!DOCTYPE[^>]*>", "", prepared, flags=re.IGNORECASE)
        prepared = prepared.replace("&cr;", "<br>").replace("&#13;", "<br>")
        self.feed(prepared)
        self.close()
        while self.open_blocks:
            self.parts.append(f"</{self.open_blocks.pop()}>")
        return "".join(self.parts).strip()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attrs_dict = {name.lower(): (value or "") for name, value in attrs}
        if tag in self.block_tags:
            self.parts.append('<div class="dart-block">')
            self.open_blocks.append("div")
            return
        if tag in self.title_tags:
            self.parts.append('<h2 class="dart-title">')
            self.open_blocks.append("h2")
            return
        if tag in self.paragraph_tags:
            self.parts.append("<p>")
            self.open_blocks.append("p")
            return
        if tag == "br":
            self.parts.append("<br>")
            return
        if tag == "table":
            self.parts.append('<table class="dart-table">')
            self.open_blocks.append("table")
            return
        if tag in {"thead", "tbody", "tfoot"}:
            self.parts.append(f"<{tag}>")
            self.open_blocks.append(tag)
            return
        if tag == "tr":
            self.parts.append("<tr>")
            self.open_blocks.append("tr")
            return
        if tag in {"td", "th"}:
            safe_attrs = []
            for attr_name in ("colspan", "rowspan"):
                raw = attrs_dict.get(attr_name, "")
                if raw.isdigit() and 1 <= int(raw) <= 100:
                    safe_attrs.append(f'{attr_name}="{raw}"')
            align = attrs_dict.get("align", "").lower()
            if align in {"left", "center", "right"}:
                safe_attrs.append(f'style="text-align:{align}"')
            suffix = " " + " ".join(safe_attrs) if safe_attrs else ""
            self.parts.append(f"<{tag}{suffix}>")
            self.open_blocks.append(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        target = None
        if tag in self.block_tags:
            target = "div"
        elif tag in self.title_tags:
            target = "h2"
        elif tag in self.paragraph_tags:
            target = "p"
        elif tag in self.table_tags:
            target = tag
        if target and target in self.open_blocks:
            while self.open_blocks:
                current = self.open_blocks.pop()
                self.parts.append(f"</{current}>")
                if current == target:
                    break

    def handle_data(self, data: str) -> None:
        if not data:
            return
        cleaned = data.replace("\r\n", "\n").replace("\r", "\n")
        if not cleaned.strip():
            return
        self.parts.append(highlight_text(cleaned, self.keyword).replace("\n", "<br>"))

    def handle_entityref(self, name: str) -> None:
        self.handle_data(html.unescape(f"&{name};"))

    def handle_charref(self, name: str) -> None:
        self.handle_data(html.unescape(f"&#{name};"))


def render_dart_document_html(files: list[tuple[str, str]], keyword: str = "", max_chars: int | None = None) -> str:
    rendered: list[str] = []
    remaining = max_chars
    for source, text in files:
        truncated = False
        if remaining is not None:
            if remaining <= 0:
                break
            truncated = len(text) > remaining
            text = text[:remaining]
            remaining -= len(text)
        body = DartDocumentRenderer(keyword).render(text)
        if body:
            notice = '<div class="preview-notice">빠른 미리보기입니다. 필요한 경우 상단의 전체 원문 버튼을 눌러 전체 내용을 불러오세요.</div>' if truncated else ""
            rendered.append(
                f'<article class="dart-document">'
                f'<div class="source-name">{html.escape(source)}</div>'
                f"{notice}{body}</article>"
            )
    return "".join(rendered)


def render_dart_search_html(files: list[tuple[str, str]], keyword: str) -> tuple[str, int, bool]:
    needle = keyword.strip()
    if not needle:
        return "", 0, False

    pattern = re.compile(re.escape(needle), re.IGNORECASE)
    rendered: list[str] = []
    total_count = 0
    shown = 0
    for source, text in files:
        raw_matches = list(pattern.finditer(text))
        total_count += len(raw_matches)
        if not raw_matches:
            continue

        ranges: list[tuple[int, int]] = []
        for match in raw_matches:
            start = max(0, match.start() - SEARCH_CONTEXT_CHARS)
            end = min(len(text), match.end() + SEARCH_CONTEXT_CHARS)
            if ranges and start <= ranges[-1][1]:
                ranges[-1] = (ranges[-1][0], max(ranges[-1][1], end))
            else:
                ranges.append((start, end))

        for start, end in ranges:
            if shown >= SEARCH_MAX_RESULTS:
                break
            snippet = text[start:end]
            prefix = "..." if start > 0 else ""
            suffix = "..." if end < len(text) else ""
            body = DartDocumentRenderer(needle).render(prefix + snippet + suffix)
            if body:
                shown += 1
                rendered.append(
                    f'<article class="dart-document search-result">'
                    f'<div class="source-name">{html.escape(source)} / 검색 결과 {shown}</div>'
                    f"{body}</article>"
                )
        if shown >= SEARCH_MAX_RESULTS:
            break

    return "".join(rendered), total_count, shown >= SEARCH_MAX_RESULTS


def text_of(node: ElementTree.Element, tag: str) -> str:
    child = node.find(tag)
    return (child.text or "").strip() if child is not None else ""


class DartClient:
    def __init__(self, api_key: str):
        self.api_key = api_key.strip()

    def resolve_corp(self, corp_code: str, stock_code: str, company_name: str) -> CorpInfo | None:
        corps = self.get_corp_codes()
        needle = company_name.strip().lower()
        if needle:
            exact = [corp for corp in corps if corp.corp_name.lower() == needle]
            if exact:
                return sorted(exact, key=lambda c: (not bool(c.stock_code), c.corp_name))[0]
            matches = [corp for corp in corps if needle in corp.corp_name.lower()]
            if matches:
                return sorted(matches, key=lambda c: (not bool(c.stock_code), len(c.corp_name), c.corp_name))[0]

        if corp_code.strip():
            return CorpInfo(corp_code.strip(), company_name.strip() or corp_code.strip(), stock_code.strip())

        if stock_code.strip():
            normalized = stock_code.strip().zfill(6)
            for corp in corps:
                if corp.stock_code == normalized:
                    return corp
        return None

    def search_corps(self, company_name: str, stock_code: str = "", limit: int = 80) -> list[dict]:
        corps = self.get_corp_codes()
        needle = company_name.strip().lower()
        if needle:
            matches = [corp for corp in corps if needle in corp.corp_name.lower()]
        elif stock_code.strip():
            normalized = stock_code.strip().zfill(6)
            matches = [corp for corp in corps if corp.stock_code == normalized]
        else:
            matches = []
        matches = sorted(matches, key=lambda c: (c.corp_name.lower() != company_name.strip().lower(), not bool(c.stock_code), len(c.corp_name), c.corp_name))
        return [{"corpCode": c.corp_code, "corpName": c.corp_name, "stockCode": c.stock_code} for c in matches[:limit]]

    def get_corp_codes(self) -> list[CorpInfo]:
        global _CORP_CODES
        if _CORP_CODES is not None:
            return _CORP_CODES
        today = datetime.now().strftime("%Y%m%d")
        if CORP_CACHE_PATH.exists():
            try:
                cached = json.loads(CORP_CACHE_PATH.read_text(encoding="utf-8"))
                if cached.get("date") == today and cached.get("items"):
                    _CORP_CODES = [CorpInfo(item["corp_code"], item["corp_name"], item.get("stock_code", "")) for item in cached["items"]]
                    return _CORP_CODES
            except Exception:
                pass

        data = self._get_bytes("https://opendart.fss.or.kr/api/corpCode.xml", {"crtfc_key": self.api_key})
        with zipfile.ZipFile(BytesIO(data)) as archive:
            name = next(n for n in archive.namelist() if n.lower().endswith(".xml"))
            root = ElementTree.fromstring(archive.read(name))
        corps: list[CorpInfo] = []
        for node in root.findall("list"):
            corp_code = text_of(node, "corp_code")
            if corp_code:
                corps.append(CorpInfo(corp_code, text_of(node, "corp_name"), text_of(node, "stock_code")))
        _CORP_CODES = corps
        try:
            CORP_CACHE_PATH.write_text(
                json.dumps(
                    {
                        "date": today,
                        "items": [{"corp_code": c.corp_code, "corp_name": c.corp_name, "stock_code": c.stock_code} for c in corps],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except Exception:
            pass
        return _CORP_CODES

    def get_disclosures(self, corp_code: str, begin_date: str, end_date: str, pblntf_ty: str = "") -> list[dict]:
        rows: list[dict] = []
        page = 1
        while True:
            params = {
                "crtfc_key": self.api_key,
                "corp_code": corp_code,
                "bgn_de": begin_date,
                "end_de": end_date,
                "page_no": str(page),
                "page_count": "100",
            }
            if pblntf_ty:
                params["pblntf_ty"] = pblntf_ty
            data = self._get_json("https://opendart.fss.or.kr/api/list.json", params)
            status = data.get("status")
            if status not in ("000", "013"):
                raise RuntimeError(data.get("message") or "DART 공시 조회에 실패했습니다.")
            items = data.get("list", [])
            for item in items:
                receipt_no = item.get("rcept_no", "")
                rows.append(
                    {
                        "corpName": item.get("corp_name", ""),
                        "stockCode": item.get("stock_code", ""),
                        "reportName": item.get("report_nm", ""),
                        "receiptDate": item.get("rcept_dt", ""),
                        "receiptNo": receipt_no,
                        "submitter": item.get("flr_nm", ""),
                        "remark": item.get("rm", ""),
                        "dartUrl": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt_no}",
                    }
                )
            total_page = int(data.get("total_page") or 1)
            if page >= total_page or not items:
                break
            page += 1
        return sorted(rows, key=lambda r: (r["receiptDate"], r["receiptNo"]), reverse=True)

    def get_document_files(self, receipt_no: str) -> list[tuple[str, str]]:
        if receipt_no in _DOCUMENT_FILES:
            return _DOCUMENT_FILES[receipt_no]
        data = self._get_bytes("https://opendart.fss.or.kr/api/document.xml", {"crtfc_key": self.api_key, "rcept_no": receipt_no})
        files: list[tuple[str, str]] = []
        try:
            with zipfile.ZipFile(BytesIO(data)) as archive:
                for name in archive.namelist():
                    if name.lower().endswith(".xml"):
                        files.append((Path(name).name, decode_dart_document(archive.read(name))))
        except zipfile.BadZipFile:
            files.append((f"{receipt_no}.xml", decode_dart_document(data)))
        _DOCUMENT_FILES[receipt_no] = files
        return files

    def get_document_html(self, receipt_no: str, keyword: str = "", full: bool = False) -> dict:
        files = self.get_document_files(receipt_no)
        keyword_count = 0
        if keyword.strip():
            if not full:
                rendered, keyword_count, limited = render_dart_search_html(files, keyword)
                total_chars = sum(len(text) for _, text in files)
                return {
                    "html": rendered,
                    "fileCount": len(files),
                    "keywordCount": keyword_count,
                    "preview": False,
                    "searchMode": True,
                    "limited": limited,
                    "totalChars": total_chars,
                }
            needle = re.compile(re.escape(keyword.strip()), re.IGNORECASE)
            keyword_count = sum(len(needle.findall(clean_text(text))) for _, text in files)
        rendered = render_dart_document_html(files, keyword, None if full else DOCUMENT_PREVIEW_CHARS)
        total_chars = sum(len(text) for _, text in files)
        return {
            "html": rendered,
            "fileCount": len(files),
            "keywordCount": keyword_count,
            "preview": not full and total_chars > DOCUMENT_PREVIEW_CHARS,
            "searchMode": False,
            "limited": False,
            "totalChars": total_chars,
        }

    def _get_json(self, url: str, params: dict[str, str]) -> dict:
        return json.loads(self._get_bytes(url, params).decode("utf-8", errors="ignore"))

    def _get_bytes(self, url: str, params: dict[str, str]) -> bytes:
        query = urllib.parse.urlencode(params)
        request = urllib.request.Request(f"{url}?{query}", headers={"User-Agent": "DART-Disclosure-Viewer/1.0"})
        with urllib.request.urlopen(request, timeout=50) as response:
            return response.read()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        return

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            self.respond(200, HTML_PAGE.encode("utf-8"), "text/html; charset=utf-8")
            return
        if parsed.path == "/api/config":
            config = load_config()
            self.json({"apiKey": config.get("api_key", "")})
            return
        if parsed.path == "/api/document":
            query = urllib.parse.parse_qs(parsed.query)
            api_key = query.get("apiKey", [""])[0] or load_config().get("api_key", "")
            receipt_no = query.get("receiptNo", [""])[0]
            keyword = query.get("keyword", [""])[0]
            full = query.get("full", ["0"])[0] == "1"
            if not api_key or not receipt_no:
                self.json({"ok": False, "message": "API 키와 접수번호가 필요합니다."})
                return
            try:
                document = DartClient(api_key).get_document_html(receipt_no, keyword, full)
                self.json({"ok": True, **document})
            except Exception as exc:
                self.json({"ok": False, "message": str(exc)})
            return
        self.respond(404, b"not found", "text/plain; charset=utf-8")

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        api_key = payload.get("apiKey", "").strip() or load_config().get("api_key", "")
        if payload.get("saveApiKey") and api_key:
            save_config({"api_key": api_key})
        if not api_key:
            self.json({"ok": False, "message": "DART API 키를 입력해 주세요."})
            return
        try:
            client = DartClient(api_key)
            if self.path == "/api/search-corps":
                self.json({"ok": True, "items": client.search_corps(payload.get("companyName", ""), payload.get("stockCode", ""))})
                return
            if self.path == "/api/disclosures":
                corp = client.resolve_corp(payload.get("corpCode", ""), payload.get("stockCode", ""), payload.get("companyName", ""))
                if corp is None:
                    self.json({"ok": False, "message": "회사 정보를 찾지 못했습니다."})
                    return
                rows = client.get_disclosures(
                    corp.corp_code,
                    payload.get("beginDate", ""),
                    payload.get("endDate", ""),
                    payload.get("pblntfType", ""),
                )
                self.json({"ok": True, "corp": corp.__dict__, "items": rows, "count": len(rows)})
                return
            self.json({"ok": False, "message": "알 수 없는 요청입니다."})
        except Exception as exc:
            self.json({"ok": False, "message": str(exc)})

    def json(self, value: dict) -> None:
        self.respond(200, json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def respond(self, status: int, data: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def find_port() -> int:
    for port in range(51801, 51900):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return 0


def main() -> None:
    port = find_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"DART 공시 뷰어 실행 중: {url}")
    threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    server.serve_forever()


HTML_PAGE = r"""
<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>DART 공시 뷰어</title>
  <style>
    :root { --ink:#17202a; --muted:#667085; --line:#d6dce7; --bg:#f5f7fb; --panel:#fff; --accent:#0f766e; }
    * { box-sizing:border-box; }
    body { margin:0; font-family:"Segoe UI","Malgun Gothic",Arial,sans-serif; background:var(--bg); color:var(--ink); font-size:15px; }
    header { height:64px; display:flex; align-items:center; padding:0 22px; background:#fff; border-bottom:1px solid var(--line); }
    h1 { margin:0; font-size:20px; }
    main { display:grid; grid-template-columns:340px 1fr; gap:16px; padding:16px; height:calc(100vh - 64px); }
    aside, section { background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:16px; overflow:auto; }
    label { display:block; font-weight:700; margin:12px 0 6px; }
    input, select { width:100%; height:38px; border:1px solid #b9c3d3; border-radius:6px; padding:0 10px; font-size:15px; }
    .row { display:grid; grid-template-columns:1fr 1fr; gap:8px; }
    button { height:40px; border:0; border-radius:6px; padding:0 12px; background:var(--accent); color:#fff; font-weight:800; cursor:pointer; }
    button.secondary { background:#243447; }
    button.light { background:#eef2f7; color:#1f2937; border:1px solid #cbd5e1; }
    button:disabled { opacity:.55; cursor:wait; }
    .actions { display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-top:14px; }
    .status { margin-top:12px; padding:10px; background:#eef7f5; color:#0b4f49; border-radius:6px; white-space:pre-wrap; }
    .tabs { display:flex; gap:8px; margin-bottom:12px; }
    .tabs button { background:#e9eef5; color:#1f2937; }
    .tabs button.active { background:#17202a; color:#fff; }
    table { width:100%; border-collapse:collapse; font-size:14px; }
    th, td { border-bottom:1px solid var(--line); padding:8px; text-align:left; vertical-align:top; }
    th { position:sticky; top:0; background:#f8fafc; z-index:1; }
    tr:hover td { background:#f7fbfa; }
    .nowrap { white-space:nowrap; }
    .muted { color:var(--muted); }
    .doc { background:#fff; border:1px solid var(--line); border-radius:8px; padding:18px; overflow:auto; line-height:1.55; }
    .dart-document { max-width:1180px; margin:0 auto 28px; color:#111827; }
    .source-name { position:sticky; top:0; z-index:2; margin:-18px -18px 16px; padding:10px 18px; background:#f8fafc; border-bottom:1px solid var(--line); color:var(--muted); font-size:13px; font-weight:700; }
    .dart-title { margin:22px 0 12px; font-size:20px; line-height:1.35; border-bottom:2px solid #111827; padding-bottom:8px; }
    .dart-block { margin:8px 0; }
    .dart-document p { margin:8px 0; min-height:1em; }
    .dart-table { width:100%; border-collapse:collapse; table-layout:auto; margin:12px 0 18px; font-size:13px; background:#fff; }
    .dart-table th, .dart-table td { border:1px solid #9ca3af; padding:6px 8px; vertical-align:top; white-space:pre-wrap; }
    .dart-table th { position:static; background:#f3f4f6; font-weight:800; }
    .dart-document mark { background:#fff3a3; padding:0 2px; }
    .preview-notice { margin:0 0 14px; padding:10px 12px; border:1px solid #f3c96b; background:#fff8e1; color:#694a05; border-radius:6px; font-weight:700; }
    .search-result { border-top:3px solid #0f766e; padding-top:12px; }
    .search-summary { margin:0 0 14px; padding:10px 12px; border:1px solid #99d6cf; background:#eefaf8; color:#064e47; border-radius:6px; font-weight:700; }
    .viewer-tools { display:flex; gap:8px; align-items:center; margin-bottom:12px; }
    .viewer-tools input { max-width:260px; }
    @media (max-width:900px) { main { grid-template-columns:1fr; height:auto; } }
  </style>
</head>
<body>
  <header><h1>DART 공시 뷰어</h1></header>
  <main>
    <aside>
      <label>DART API 키</label>
      <input id="apiKey" type="password">
      <label><input id="saveApiKey" type="checkbox" style="width:auto;height:auto"> API 키 저장</label>
      <label>회사명</label>
      <input id="companyName" value="디앤디파마텍">
      <label>종목코드</label>
      <input id="stockCode" placeholder="예: 347850">
      <label>DART 고유번호</label>
      <input id="corpCode">
      <div class="row">
        <div><label>시작일</label><input id="beginDate" value="20160101"></div>
        <div><label>종료일</label><input id="endDate"></div>
      </div>
      <label>공시 유형</label>
      <select id="pblntfType">
        <option value="">전체</option>
        <option value="A">정기공시</option>
        <option value="B">주요사항보고</option>
        <option value="C">발행공시</option>
        <option value="D">지분공시</option>
        <option value="E">기타공시</option>
        <option value="F">외부감사관련</option>
        <option value="G">펀드공시</option>
        <option value="H">자산유동화</option>
        <option value="I">거래소공시</option>
        <option value="J">공정위공시</option>
      </select>
      <div class="actions">
        <button id="searchCorp">회사 찾기</button>
        <button id="loadDisclosure" class="secondary">공시 조회</button>
      </div>
      <div class="status" id="status">회사명과 기간을 입력한 뒤 공시 조회를 누르세요.</div>
    </aside>
    <section>
      <div class="tabs">
        <button id="tabList" class="active">공시 목록</button>
        <button id="tabDoc">원문 보기</button>
      </div>
      <div id="listPane">
        <table>
          <thead><tr><th>접수일</th><th>보고서명</th><th>제출인</th><th>접수번호</th><th>보기</th></tr></thead>
          <tbody id="disclosureRows"></tbody>
        </table>
      </div>
      <div id="docPane" style="display:none">
        <div class="viewer-tools">
          <input id="docKeyword" placeholder="원문 내 검색어 예: 차입금">
          <button id="reloadDoc">원문 검색</button>
          <button id="loadFullDoc" class="light">전체 원문</button>
          <a id="dartLink" target="_blank"></a>
        </div>
        <div class="doc" id="docRecords"></div>
      </div>
    </section>
  </main>
  <script>
    const $ = (id) => document.getElementById(id);
    let selectedReceiptNo = "";
    let selectedDartUrl = "";
    function payload() {
      return {
        apiKey: $("apiKey").value,
        saveApiKey: $("saveApiKey").checked,
        companyName: $("companyName").value,
        stockCode: $("stockCode").value,
        corpCode: $("corpCode").value,
        beginDate: $("beginDate").value,
        endDate: $("endDate").value,
        pblntfType: $("pblntfType").value,
      };
    }
    function status(text) { $("status").textContent = text; }
    function showTab(name) {
      $("listPane").style.display = name === "list" ? "" : "none";
      $("docPane").style.display = name === "doc" ? "" : "none";
      $("tabList").classList.toggle("active", name === "list");
      $("tabDoc").classList.toggle("active", name === "doc");
    }
    async function post(url, body) {
      const res = await fetch(url, { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body) });
      return await res.json();
    }
    async function loadConfig() {
      $("endDate").value = new Date().toISOString().slice(0,10).replaceAll("-","");
      const data = await fetch("/api/config").then(r => r.json());
      if (data.apiKey) { $("apiKey").value = data.apiKey; $("saveApiKey").checked = true; }
    }
    $("searchCorp").onclick = async () => {
      status("회사 후보를 조회하고 있습니다.");
      const data = await post("/api/search-corps", payload());
      if (!data.ok) { status(data.message); return; }
      const rows = data.items.map(x => `${x.corpName} / ${x.stockCode || "-"} / ${x.corpCode}`).join("\n");
      status(rows || "검색 결과가 없습니다.");
      if (data.items.length) {
        $("companyName").value = data.items[0].corpName;
        $("stockCode").value = data.items[0].stockCode || "";
        $("corpCode").value = data.items[0].corpCode;
      }
    };
    $("loadDisclosure").onclick = async () => {
      status("공시 목록을 조회하고 있습니다.");
      $("disclosureRows").innerHTML = "";
      const data = await post("/api/disclosures", payload());
      if (!data.ok) { status(data.message); return; }
      $("companyName").value = data.corp.corp_name || $("companyName").value;
      $("stockCode").value = data.corp.stock_code || "";
      $("corpCode").value = data.corp.corp_code || "";
      status(`${data.corp.corp_name} 공시 ${data.count}건을 불러왔습니다.`);
      $("disclosureRows").innerHTML = data.items.map(x => `
        <tr>
          <td class="nowrap">${x.receiptDate}</td>
          <td>${x.reportName}</td>
          <td class="nowrap">${x.submitter || ""}</td>
          <td class="nowrap">${x.receiptNo}</td>
          <td class="nowrap">
            <button class="light" onclick="viewDoc('${x.receiptNo}', '${x.dartUrl}')">앱에서 보기</button>
            <a href="${x.dartUrl}" target="_blank">DART</a>
          </td>
        </tr>`).join("");
      showTab("list");
    };
    async function viewDoc(receiptNo, dartUrl) {
      selectedReceiptNo = receiptNo;
      selectedDartUrl = dartUrl;
      $("dartLink").href = dartUrl;
      $("dartLink").textContent = `DART 원문 열기 (${receiptNo})`;
      showTab("doc");
      await reloadDoc();
    }
    async function reloadDoc() {
      if (!selectedReceiptNo) return;
      $("docRecords").innerHTML = "원문을 불러오는 중입니다.";
      const q = new URLSearchParams({ apiKey:$("apiKey").value, receiptNo:selectedReceiptNo, keyword:$("docKeyword").value });
      const data = await fetch(`/api/document?${q}`).then(r => r.json());
      if (!data.ok) { $("docRecords").textContent = data.message; return; }
      $("docRecords").innerHTML = data.records.map(r => `
        <div class="record">
          <div class="meta">${r.source} / line ${r.lineNo}</div>
          <div>${r.text.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")}</div>
        </div>`).join("") || "표시할 원문이 없습니다. 검색어를 바꿔보세요.";
    }
    reloadDoc = async function(full = false) {
      if (!selectedReceiptNo) return;
      $("docRecords").innerHTML = full ? "전체 원문을 불러오는 중입니다." : "빠른 미리보기를 불러오는 중입니다.";
      const q = new URLSearchParams({ apiKey:$("apiKey").value, receiptNo:selectedReceiptNo, keyword:$("docKeyword").value, full: full ? "1" : "0" });
      const data = await fetch(`/api/document?${q}`).then(r => r.json());
      if (!data.ok) { $("docRecords").textContent = data.message; return; }
      $("docRecords").innerHTML = data.html || "표시할 원문이 없습니다. 검색어를 바꿔보세요.";
      if (data.searchMode) {
        const limitText = data.limited ? " 상위 결과만 표시했습니다." : "";
        $("docRecords").insertAdjacentHTML("afterbegin", `<div class="search-summary">원문 전체에서 ${data.keywordCount}건을 찾았습니다.${limitText}</div>`);
      }
      if (data.preview) {
        $("docRecords").insertAdjacentHTML("afterbegin", `<div class="preview-notice">큰 공시는 먼저 일부만 표시했습니다. 전체가 필요하면 상단의 전체 원문 버튼을 누르세요.</div>`);
      }
      if ($("docKeyword").value && data.keywordCount === 0) {
        $("docRecords").insertAdjacentHTML("afterbegin", `<div class="status">검색어가 원문 전체에서 발견되지 않았습니다.</div>`);
      }
    };
    $("reloadDoc").onclick = () => reloadDoc(false);
    $("loadFullDoc").onclick = () => reloadDoc(true);
    $("tabList").onclick = () => showTab("list");
    $("tabDoc").onclick = () => showTab("doc");
    loadConfig();
  </script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
