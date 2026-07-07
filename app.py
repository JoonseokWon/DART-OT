import html
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
import zipfile
from dataclasses import dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree


ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "outputs"
BORROWING_KEYWORDS = ["차입금", "사채", "이자율", "이자비용", "유동성장기", "단기차입", "장기차입"]


@dataclass
class CorpInfo:
    corp_code: str
    corp_name: str
    stock_code: str


@dataclass
class DartReport:
    corp_name: str
    report_name: str
    receipt_date: str
    receipt_no: str
    stock_code: str


@dataclass
class BorrowingNote:
    corp_name: str
    report_name: str
    receipt_date: str
    receipt_no: str
    stock_code: str
    summary: str
    special_matter: str


@dataclass
class InterestTest:
    corp_name: str
    report_name: str
    receipt_no: str
    borrowing_amount: float | None
    interest_rate: float | None
    expected_interest: float | None
    actual_interest: float | None
    error_rate: float | None
    result: str


class DartClient:
    def __init__(self, api_key: str):
        self.api_key = api_key.strip()

    def resolve_corp(self, corp_code: str, stock_code: str, company_name: str) -> CorpInfo | None:
        if corp_code.strip():
            return CorpInfo(corp_code.strip(), company_name.strip() or corp_code.strip(), stock_code.strip())

        corps = self.get_corp_codes()
        if stock_code.strip():
            normalized = stock_code.strip().zfill(6)
            for corp in corps:
                if corp.stock_code == normalized:
                    return corp

        if company_name.strip():
            needle = company_name.strip().lower()
            for corp in corps:
                if needle in corp.corp_name.lower():
                    return corp

        return None

    def get_corp_codes(self) -> list[CorpInfo]:
        data = self._get_bytes("https://opendart.fss.or.kr/api/corpCode.xml", {"crtfc_key": self.api_key})
        with zipfile.ZipFile(BytesIO(data)) as archive:
            name = next(n for n in archive.namelist() if n.lower().endswith(".xml"))
            xml_data = archive.read(name)

        root = ElementTree.fromstring(xml_data)
        corps: list[CorpInfo] = []
        for node in root.findall("list"):
            corp_code = text_of(node, "corp_code")
            corp_name = text_of(node, "corp_name")
            stock_code = text_of(node, "stock_code")
            if corp_code:
                corps.append(CorpInfo(corp_code, corp_name, stock_code))
        return corps

    def get_reports(self, corp_code: str, begin_year: int, end_year: int) -> list[DartReport]:
        reports: list[DartReport] = []
        for year in range(begin_year, end_year + 1):
            data = self._get_json(
                "https://opendart.fss.or.kr/api/list.json",
                {
                    "crtfc_key": self.api_key,
                    "corp_code": corp_code,
                    "bgn_de": f"{year}0101",
                    "end_de": f"{year}1231",
                    "pblntf_ty": "A",
                    "page_count": "100",
                },
            )
            for item in data.get("list", []):
                reports.append(
                    DartReport(
                        item.get("corp_name", ""),
                        item.get("report_nm", ""),
                        item.get("rcept_dt", ""),
                        item.get("rcept_no", ""),
                        item.get("stock_code", ""),
                    )
                )
        return sorted(reports, key=lambda r: (r.receipt_date, r.report_name), reverse=True)

    def extract_borrowing_note(self, report: DartReport) -> BorrowingNote | None:
        data = self._get_bytes(
            "https://opendart.fss.or.kr/api/document.xml",
            {"crtfc_key": self.api_key, "rcept_no": report.receipt_no},
        )

        chunks: list[str] = []
        try:
            with zipfile.ZipFile(BytesIO(data)) as archive:
                for name in archive.namelist():
                    if name.lower().endswith(".xml"):
                        chunks.append(archive.read(name).decode("utf-8", errors="ignore"))
        except zipfile.BadZipFile:
            chunks.append(data.decode("utf-8", errors="ignore"))

        plain = normalize_text(re.sub(r"<[^>]+>", " ", "\n".join(chunks)))
        snippets = extract_snippets(plain)
        if not snippets:
            return None

        summary = "\n\n".join(snippets[:8])
        return BorrowingNote(
            report.corp_name,
            report.report_name,
            report.receipt_date,
            report.receipt_no,
            report.stock_code,
            summary,
            build_special_matter(summary),
        )

    def _get_json(self, url: str, params: dict[str, str]) -> dict:
        return json.loads(self._get_bytes(url, params).decode("utf-8", errors="ignore"))

    def _get_bytes(self, url: str, params: dict[str, str]) -> bytes:
        query = urllib.parse.urlencode(params)
        request = urllib.request.Request(f"{url}?{query}", headers={"User-Agent": "DART-OT/1.0"})
        with urllib.request.urlopen(request, timeout=40) as response:
            return response.read()


def run_report(payload: dict) -> dict:
    api_key = payload.get("apiKey", "").strip()
    if not api_key:
        return fail("DART API 키를 입력해 주세요.")

    client = DartClient(api_key)
    corp = client.resolve_corp(
        payload.get("corpCode", ""),
        payload.get("stockCode", ""),
        payload.get("companyName", ""),
    )
    if corp is None:
        return fail("회사 정보를 찾지 못했습니다. 종목코드 또는 회사명을 다시 확인해 주세요.")

    now_year = datetime.now().year
    begin_year = int(payload.get("beginYear") or now_year - 9)
    end_year = int(payload.get("endYear") or now_year)

    reports = client.get_reports(corp.corp_code, begin_year, end_year)
    notes: list[BorrowingNote] = []
    for report in reports:
        try:
            note = client.extract_borrowing_note(report)
            if note:
                notes.append(note)
        except Exception:
            continue

    tests = [interest_test_from_note(note) for note in notes]
    OUTPUT_DIR.mkdir(exist_ok=True)
    file_name = f"DART_OT_{safe_filename(corp.corp_name)}_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
    save_workbook(OUTPUT_DIR / file_name, reports, notes, tests)

    return {
        "ok": True,
        "message": f"{corp.corp_name} 정기보고서 {len(reports)}건, 차입금 관련 공시 {len(notes)}건을 정리했습니다.",
        "file": file_name,
        "reportCount": len(reports),
        "noteCount": len(notes),
        "testCount": len(tests),
    }


def fail(message: str) -> dict:
    return {"ok": False, "message": message, "file": None, "reportCount": 0, "noteCount": 0, "testCount": 0}


def text_of(node: ElementTree.Element, tag: str) -> str:
    child = node.find(tag)
    return (child.text or "").strip() if child is not None else ""


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def extract_snippets(plain: str) -> list[str]:
    snippets: list[str] = []
    for keyword in BORROWING_KEYWORDS:
        index = 0
        while True:
            index = plain.find(keyword, index)
            if index < 0:
                break
            start = max(0, index - 260)
            snippet = plain[start : start + 820].strip()
            if len(snippet) > 80 and all(snippet[:60] not in old for old in snippets):
                snippets.append(snippet)
            index += len(keyword)
            if len(snippets) >= 12:
                return snippets
    return snippets


def build_special_matter(text: str) -> str:
    flags: list[str] = []
    checks = [
        ("유동성", "유동성 대체 관련 문구 확인"),
        ("특수관계", "특수관계자 차입 여부 확인"),
        ("전환사채", "전환사채 조건 확인"),
        ("신주인수권", "신주인수권부사채 조건 확인"),
        ("담보", "담보 제공 조건 확인"),
        ("만기", "만기 구조 확인"),
    ]
    for needle, label in checks:
        if needle in text and label not in flags:
            flags.append(label)
    return ", ".join(flags) if flags else "특이사항 자동 식별 없음. 원문 주석 확인 필요."


def interest_test_from_note(note: BorrowingNote) -> InterestTest:
    amount = extract_amount(note.summary)
    rate = extract_rate(note.summary)
    actual = extract_actual_interest(note.summary)
    expected = amount * rate / 100 if amount is not None and rate is not None else None
    error = (actual - expected) / expected * 100 if actual is not None and expected not in (None, 0) else None
    if error is None:
        result = "계산 정보 부족"
    elif abs(error) <= 5:
        result = "적정"
    else:
        result = "검토 필요"
    return InterestTest(note.corp_name, note.report_name, note.receipt_no, amount, rate, expected, actual, error, result)


def extract_rate(text: str) -> float | None:
    rates = [float(x) for x in re.findall(r"(\d{1,2}(?:\.\d{1,4})?)\s*%", text)]
    return sum(rates) / len(rates) if rates else None


def extract_amount(text: str) -> float | None:
    max_value = 0.0
    for raw, unit in re.findall(r"(\d{1,3}(?:,\d{3})+|\d+)\s*(백만원|억원|원)", text):
        value = float(raw.replace(",", ""))
        if unit == "억원":
            value *= 100_000_000
        elif unit == "백만원":
            value *= 1_000_000
        max_value = max(max_value, value)
    return max_value or None


def extract_actual_interest(text: str) -> float | None:
    match = re.search(r"이자비용.{0,80}?(\d{1,3}(?:,\d{3})+|\d+)\s*(백만원|억원|원)", text)
    if not match:
        return None
    value = float(match.group(1).replace(",", ""))
    if match.group(2) == "억원":
        return value * 100_000_000
    if match.group(2) == "백만원":
        return value * 1_000_000
    return value


def save_workbook(path: Path, reports: list[DartReport], notes: list[BorrowingNote], tests: list[InterestTest]) -> None:
    sheets = [
        (
            "정기보고서 목록",
            ["회사명", "보고서명", "접수일", "접수번호", "종목코드"],
            [[r.corp_name, r.report_name, r.receipt_date, r.receipt_no, r.stock_code] for r in reports],
        ),
        (
            "차입금 필터링",
            ["회사명", "보고서명", "접수일", "접수번호", "종목코드", "차입금 주석공시 내용", "특이사항"],
            [[n.corp_name, n.report_name, n.receipt_date, n.receipt_no, n.stock_code, n.summary, n.special_matter] for n in notes],
        ),
        (
            "이자율 오버롤 테스트",
            ["회사명", "보고서명", "접수번호", "차입금 잔액 추정", "이자율 추정(%)", "기대 이자비용", "실제 이자비용 추정", "오차율(%)", "판정"],
            [
                [
                    t.corp_name,
                    t.report_name,
                    t.receipt_no,
                    money(t.borrowing_amount),
                    percent(t.interest_rate),
                    money(t.expected_interest),
                    money(t.actual_interest),
                    percent(t.error_rate),
                    t.result,
                ]
                for t in tests
            ],
        ),
    ]

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types())
        archive.writestr("_rels/.rels", root_rels())
        archive.writestr("xl/workbook.xml", workbook_xml([s[0] for s in sheets]))
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels(len(sheets)))
        archive.writestr("xl/styles.xml", styles_xml())
        for index, (_, headers, rows) in enumerate(sheets, start=1):
            archive.writestr(f"xl/worksheets/sheet{index}.xml", sheet_xml(headers, rows))


def sheet_xml(headers: list[str], rows: list[list[str]]) -> str:
    lines = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>']
    lines.append('<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>')
    lines.append(row_xml(1, headers, True))
    for idx, row in enumerate(rows, start=2):
        lines.append(row_xml(idx, row, False))
    lines.append("</sheetData></worksheet>")
    return "".join(lines)


def row_xml(row_no: int, values: list[str], header: bool) -> str:
    cells = []
    style = ' s="1"' if header else ""
    for idx, value in enumerate(values, start=1):
        ref = f"{column_name(idx)}{row_no}"
        safe = html.escape(str(value or ""), quote=False)
        cells.append(f'<c r="{ref}" t="inlineStr"{style}><is><t xml:space="preserve">{safe}</t></is></c>')
    return f'<row r="{row_no}">{"".join(cells)}</row>'


def column_name(index: int) -> str:
    name = ""
    while index:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name


def content_types() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/worksheets/sheet3.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>"""


def root_rels() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>"""


def workbook_rels(count: int) -> str:
    rels = [f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1, count + 1)]
    rels.append(f'<Relationship Id="rId{count + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>')
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{''.join(rels)}</Relationships>"""


def workbook_xml(names: list[str]) -> str:
    sheets = "".join(f'<sheet name="{html.escape(name)}" sheetId="{idx}" r:id="rId{idx}"/>' for idx, name in enumerate(names, start=1))
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>{sheets}</sheets></workbook>"""


def styles_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts><fills count="1"><fill><patternFill patternType="none"/></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs></styleSheet>"""


def money(value: float | None) -> str:
    return str(round(value)) if value is not None else ""


def percent(value: float | None) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".") if value is not None else ""


def safe_filename(value: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "_", value.strip() or "company")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        return

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            self.respond(200, HTML_PAGE.encode("utf-8"), "text/html; charset=utf-8")
            return
        if parsed.path == "/download":
            query = urllib.parse.parse_qs(parsed.query)
            filename = Path(query.get("file", [""])[0]).name
            path = OUTPUT_DIR / filename
            if not path.exists():
                self.respond(404, b"file not found", "text/plain; charset=utf-8")
                return
            data = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.respond(404, b"not found", "text/plain; charset=utf-8")

    def do_POST(self) -> None:
        if self.path != "/api/run":
            self.respond(404, b"not found", "text/plain; charset=utf-8")
            return
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        try:
            result = run_report(payload)
        except Exception as exc:
            result = fail(f"실행 중 오류가 발생했습니다: {exc}")
        self.respond(200, json.dumps(result, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def respond(self, status: int, data: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def find_port() -> int:
    for port in range(51731, 51800):
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
    print(f"DART-OT 실행 중: {url}")
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    server.serve_forever()


HTML_PAGE = r"""
<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>DART-OT</title>
  <style>
    :root { color-scheme: light; --ink:#17202a; --muted:#637083; --line:#d8dee8; --accent:#0f766e; --bg:#f6f8fb; --panel:#ffffff; }
    * { box-sizing: border-box; }
    body { margin:0; font-family:"Segoe UI","Malgun Gothic",Arial,sans-serif; color:var(--ink); background:var(--bg); }
    header { background:#fff; border-bottom:1px solid var(--line); padding:22px 28px; }
    h1 { margin:0 0 6px; font-size:24px; letter-spacing:0; }
    p { margin:0; color:var(--muted); line-height:1.55; }
    main { max-width:1120px; margin:0 auto; padding:28px; display:grid; grid-template-columns:360px 1fr; gap:22px; }
    section, aside { background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:20px; }
    label { display:block; font-weight:700; margin:14px 0 7px; }
    input { width:100%; height:40px; border:1px solid #cbd5e1; border-radius:6px; padding:0 11px; font-size:14px; }
    .row { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
    button { width:100%; height:44px; margin-top:18px; border:0; border-radius:6px; background:var(--accent); color:#fff; font-weight:800; cursor:pointer; }
    button:disabled { opacity:.55; cursor:wait; }
    h2 { margin:0 0 12px; font-size:18px; }
    .steps { display:grid; gap:10px; margin-top:14px; }
    .step { padding:12px; border:1px solid var(--line); border-radius:7px; background:#fbfcfe; }
    .status { min-height:64px; padding:14px; border-radius:7px; background:#eef7f5; color:#0b4f49; white-space:pre-wrap; }
    .download { display:inline-flex; align-items:center; justify-content:center; height:40px; min-width:160px; margin-top:14px; padding:0 14px; border-radius:6px; background:#17202a; color:#fff; text-decoration:none; font-weight:800; }
    table { width:100%; border-collapse:collapse; margin-top:12px; font-size:14px; }
    th, td { text-align:left; border-bottom:1px solid var(--line); padding:10px 8px; }
    th { color:#415066; }
    @media (max-width:860px) { main { grid-template-columns:1fr; padding:18px; } }
  </style>
</head>
<body>
  <header>
    <h1>DART-OT</h1>
    <p>DART 공시 기반 차입금 필터링 및 이자율 오버롤 테스트 자동화 도구</p>
  </header>
  <main>
    <aside>
      <h2>조회 조건</h2>
      <form id="form">
        <label for="apiKey">DART API 키</label>
        <input id="apiKey" name="apiKey" type="password" autocomplete="off" required>
        <label for="stockCode">종목코드</label>
        <input id="stockCode" name="stockCode" placeholder="예: 005930">
        <label for="companyName">회사명</label>
        <input id="companyName" name="companyName" placeholder="예: 삼성전자">
        <label for="corpCode">DART 고유번호</label>
        <input id="corpCode" name="corpCode" placeholder="선택 입력">
        <div class="row">
          <div><label for="beginYear">시작연도</label><input id="beginYear" name="beginYear" type="number" min="1999"></div>
          <div><label for="endYear">종료연도</label><input id="endYear" name="endYear" type="number" min="1999"></div>
        </div>
        <button id="run" type="submit">오버롤 테스트 실행</button>
      </form>
    </aside>
    <section>
      <h2>실행 결과</h2>
      <div id="status" class="status">조회 조건을 입력하고 실행해 주세요.</div>
      <div id="link"></div>
      <div class="steps">
        <div class="step">1. 최근 10년치 정기보고서 목록을 수집합니다.</div>
        <div class="step">2. 차입금, 사채, 이자율 관련 주석을 필터링합니다.</div>
        <div class="step">3. ±5% 기준으로 이자율 오버롤 테스트 결과를 산출합니다.</div>
      </div>
      <table>
        <thead><tr><th>항목</th><th>건수</th></tr></thead>
        <tbody>
          <tr><td>정기보고서</td><td id="reportCount">-</td></tr>
          <tr><td>차입금 관련 공시</td><td id="noteCount">-</td></tr>
          <tr><td>오버롤 테스트</td><td id="testCount">-</td></tr>
        </tbody>
      </table>
    </section>
  </main>
  <script>
    const form = document.querySelector('#form');
    const statusBox = document.querySelector('#status');
    const button = document.querySelector('#run');
    const link = document.querySelector('#link');
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      button.disabled = true;
      link.innerHTML = '';
      statusBox.textContent = 'DART 공시를 조회하고 있습니다. 보고서 수에 따라 시간이 걸릴 수 있습니다.';
      const payload = Object.fromEntries(new FormData(form).entries());
      payload.beginYear = payload.beginYear ? Number(payload.beginYear) : null;
      payload.endYear = payload.endYear ? Number(payload.endYear) : null;
      try {
        const response = await fetch('/api/run', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload) });
        const data = await response.json();
        statusBox.textContent = data.message || '작업이 완료되었습니다.';
        document.querySelector('#reportCount').textContent = data.reportCount ?? '-';
        document.querySelector('#noteCount').textContent = data.noteCount ?? '-';
        document.querySelector('#testCount').textContent = data.testCount ?? '-';
        if (data.ok && data.file) {
          link.innerHTML = `<a class="download" href="/download?file=${encodeURIComponent(data.file)}">엑셀 다운로드</a>`;
        }
      } catch (error) {
        statusBox.textContent = '실행 중 오류가 발생했습니다: ' + error.message;
      } finally {
        button.disabled = false;
      }
    });
  </script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
