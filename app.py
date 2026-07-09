import html
import ctypes
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.parse
import urllib.request
import webbrowser
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
import calendar
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
import tkinter.font as tkfont
from xml.etree import ElementTree


ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "outputs"
CONFIG_PATH = ROOT / ".dart_ot_config.json"
UI_DEBUG_PATH = OUTPUT_DIR / "ui_debug.log"
BORROWING_KEYWORDS = ["차입금", "사채", "금융부채", "이자율", "이율", "금리", "가중평균", "담보제공", "이자비용", "금융원가"]
DISPLAY_KEYWORDS = [
    "전환사채",
    "신주인수권부사채",
    "교환사채",
    "단기차입금",
    "장기차입금",
    "유동성장기차입금",
    "차입금",
    "회사채",
    "사채",
    "담보제공",
]
NOTE_INTEREST_EXPENSE_KEYWORD = "차입관련 이자비용"


def write_ui_debug(message: str) -> None:
    try:
        OUTPUT_DIR.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with UI_DEBUG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(f"[{timestamp}] {message}\n")
    except Exception:
        pass


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
class BorrowingLine:
    corp_name: str
    report_name: str
    receipt_date: str
    receipt_no: str
    keyword: str
    section: str
    interest_rates: list[float]
    amounts: list[int]
    amount_unit: str
    max_amount: int
    source_file: str
    line_no: int
    context: str


@dataclass
class FinancialExpense:
    receipt_no: str
    actual_interest_expense: int | None
    account_name: str
    memo: str


@dataclass
class ExtractionIssue:
    corp_name: str
    report_name: str
    receipt_date: str
    receipt_no: str
    step: str
    message: str


@dataclass(frozen=True, slots=True)
class BorrowingMovementEstimate:
    average_balance: float
    method: str


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
        corps = self.get_corp_codes()
        if company_name.strip():
            needle = company_name.strip().lower()
            exact = [corp for corp in corps if corp.corp_name.lower() == needle]
            if exact:
                return sorted(exact, key=lambda c: (not bool(c.stock_code), c.corp_name))[0]
            for corp in corps:
                if needle in corp.corp_name.lower():
                    return corp

        if corp_code.strip():
            return CorpInfo(corp_code.strip(), company_name.strip() or corp_code.strip(), stock_code.strip())

        if stock_code.strip():
            normalized = stock_code.strip().zfill(6)
            for corp in corps:
                if corp.stock_code == normalized:
                    return corp

        return None

    def search_corps(self, company_name: str, stock_code: str = "", limit: int = 100) -> list[CorpInfo]:
        corps = self.get_corp_codes()
        needle = company_name.strip().lower()
        if needle:
            matches = [corp for corp in corps if needle in corp.corp_name.lower()]
        elif stock_code.strip():
            normalized = stock_code.strip().zfill(6)
            return [corp for corp in corps if corp.stock_code == normalized][:limit]
        else:
            return []

        return sorted(
            matches,
            key=lambda c: (
                c.corp_name.lower() != needle,
                not bool(c.stock_code),
                len(c.corp_name),
                c.corp_name,
            ),
        )[:limit]

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
            page_no = 1
            while True:
                data = self._get_json(
                    "https://opendart.fss.or.kr/api/list.json",
                    {
                        "crtfc_key": self.api_key,
                        "corp_code": corp_code,
                        "bgn_de": f"{year}0101",
                        "end_de": f"{year}1231",
                        "pblntf_ty": "A",
                        "page_count": "100",
                        "page_no": str(page_no),
                    },
                )
                status = data.get("status")
                if status not in (None, "000", "013"):
                    raise RuntimeError(f"DART list.json failed: {status} {data.get('message', '')}".strip())
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
                total_page = int(data.get("total_page") or 1)
                if page_no >= total_page:
                    break
                page_no += 1
        return sorted(reports, key=lambda r: (r.receipt_date, r.report_name), reverse=True)

    def get_financial_statement_rows(self, corp_code: str, report: DartReport) -> list[dict]:
        bsns_year = report_business_year(report)
        reprt_code = report_code(report)
        if not bsns_year or not reprt_code:
            return []
        for fs_div in ("CFS", "OFS"):
            data = self._get_json(
                "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json",
                {
                    "crtfc_key": self.api_key,
                    "corp_code": corp_code,
                    "bsns_year": bsns_year,
                    "reprt_code": reprt_code,
                    "fs_div": fs_div,
                },
            )
            if data.get("status") == "000" and data.get("list"):
                return data.get("list", [])
        return []

    def extract_financial_borrowing_line(self, corp_code: str, report: DartReport) -> BorrowingLine | None:
        rows = self.get_financial_statement_rows(corp_code, report)

        selected: list[tuple[str, int]] = []
        for row in rows:
            if row.get("sj_nm") != "재무상태표":
                continue
            account = normalize_text(row.get("account_nm", ""))
            if not is_financial_borrowing_account(account):
                continue
            amount = parse_dart_amount(row.get("thstrm_amount", ""))
            if amount is None:
                continue
            selected.append((account, amount // 1_000_000))

        if not selected:
            return None

        total = sum(amount for _, amount in selected)
        detail = ", ".join(f"{name} {amount:,}" for name, amount in selected)
        context = f"재무상태표 차입잔액 합계 {total:,}백만원 ({detail})"
        return BorrowingLine(
            report.corp_name,
            report.report_name,
            report.receipt_date,
            report.receipt_no,
            "재무상태표 차입잔액",
            "재무상태표 API",
            [],
            [total],
            "백만원",
            total,
            "fnlttSinglAcntAll.json",
            0,
            context,
        )

    def extract_revenue(self, corp_code: str, report: DartReport) -> int | None:
        rows = self.get_financial_statement_rows(corp_code, report)
        revenues: list[int] = []
        for row in rows:
            statement_name = normalize_text(row.get("sj_nm", ""))
            if not is_income_statement_name(statement_name):
                continue
            account = normalize_text(row.get("account_nm", ""))
            account_id = normalize_text(row.get("account_id", ""))
            if not is_revenue_account(account, account_id):
                continue
            amount = parse_dart_amount(row.get("thstrm_amount", ""))
            if amount is not None and amount > 0:
                revenues.append(amount // 1_000_000)
        return max(revenues) if revenues else None

    def extract_borrowing_note(self, report: DartReport) -> BorrowingNote | None:
        files = self.get_document_texts(report.receipt_no)
        plain = normalize_text(re.sub(r"<[^>]+>", " ", "\n".join(text for _, text in files)))
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

    def extract_borrowing_lines(self, report: DartReport) -> list[BorrowingLine]:
        files = self.get_document_texts(report.receipt_no)
        rows: list[BorrowingLine] = []
        for source_file, text in files:
            for line_no, context, amount_unit, section in extract_text_records(text):
                if not context:
                    continue
                keyword = display_keyword_for_context(context, section)
                if not keyword:
                    continue
                rates = extract_rate_values(context)
                amounts = extract_amount_values(context)
                if note_section_for_context(context) and not rates and not amounts:
                    continue
                if keyword == NOTE_INTEREST_EXPENSE_KEYWORD:
                    interest_amount = extract_amount_after_interest_expense_label(context, amount_unit)
                    if interest_amount is None:
                        continue
                    max_amount = interest_amount
                else:
                    explicit_amounts = extract_explicit_amounts_to_million(context)
                    max_amount = max(explicit_amounts, default=max((abs(a) for a in amounts), default=0))
                rows.append(
                    BorrowingLine(
                        report.corp_name,
                        report.report_name,
                        report.receipt_date,
                        report.receipt_no,
                        keyword,
                        section,
                        rates,
                        amounts,
                        amount_unit,
                        max_amount,
                        Path(source_file).name or f"{report.receipt_no}.xml",
                        line_no,
                        context[:1200],
                    )
                )
            rows.extend(extract_borrowing_table_rate_lines(report, source_file, text))
            rows.extend(extract_interest_expense_lines_from_document(report, source_file, text))
        return rows

    def get_document_texts(self, receipt_no: str) -> list[tuple[str, str]]:
        data = self._get_bytes(
            "https://opendart.fss.or.kr/api/document.xml",
            {"crtfc_key": self.api_key, "rcept_no": receipt_no},
        )

        files: list[tuple[str, str]] = []
        try:
            with zipfile.ZipFile(BytesIO(data)) as archive:
                for name in archive.namelist():
                    if name.lower().endswith(".xml"):
                        files.append((name, decode_dart_document(archive.read(name))))
        except zipfile.BadZipFile:
            files.append((f"{receipt_no}.xml", decode_dart_document(data)))
        return files

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
    begin_year = int(payload.get("beginYear") or now_year - 4)
    end_year = int(payload.get("endYear") or now_year)

    reports = client.get_reports(corp.corp_code, begin_year, end_year)
    borrowing_lines: list[BorrowingLine] = []
    revenues: dict[str, int] = {}
    issues: list[ExtractionIssue] = []
    for report in reports:
        try:
            financial_line = client.extract_financial_borrowing_line(corp.corp_code, report)
            if financial_line is not None:
                borrowing_lines.append(financial_line)
        except Exception as exc:
            issues.append(extraction_issue(report, "financial_statement", exc))
        try:
            revenue = client.extract_revenue(corp.corp_code, report)
            if revenue is not None:
                revenues[report.receipt_no] = revenue
        except Exception as exc:
            issues.append(extraction_issue(report, "revenue", exc))
        try:
            borrowing_lines.extend(client.extract_borrowing_lines(report))
        except Exception as exc:
            issues.append(extraction_issue(report, "borrowing_note", exc))

    tests = build_overall_tests(reports, borrowing_lines, revenues)
    OUTPUT_DIR.mkdir(exist_ok=True)
    file_name = f"DART_OT_{safe_filename(corp.corp_name)}_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
    save_workbook(OUTPUT_DIR / file_name, reports, borrowing_lines, tests, issues)

    return {
        "ok": True,
        "message": f"{corp.corp_name} 정기보고서 {len(reports)}건, 차입금 관련 문맥 {len(borrowing_lines)}건을 정리했습니다.",
        "file": file_name,
        "reportCount": len(reports),
        "noteCount": len(borrowing_lines),
        "testCount": len(tests),
        "issueCount": len(issues),
    }


def fail(message: str) -> dict:
    return {"ok": False, "message": message, "file": None, "reportCount": 0, "noteCount": 0, "testCount": 0}


def extraction_issue(report: DartReport, step: str, exc: Exception) -> ExtractionIssue:
    return ExtractionIssue(
        report.corp_name,
        report.report_name,
        report.receipt_date,
        report.receipt_no,
        step,
        f"{type(exc).__name__}: {exc}",
    )


def decode_dart_document(data: bytes) -> str:
    candidates: list[tuple[int, str]] = []
    for encoding in ("utf-8", "cp949", "euc-kr"):
        text = data.decode(encoding, errors="ignore")
        keyword_score = sum(text.count(keyword) for keyword in BORROWING_KEYWORDS) * 1000
        hangul_score = len(re.findall(r"[가-힣]", text))
        broken_score = text.count("\ufffd") * 100
        candidates.append((keyword_score + hangul_score - broken_score, text))
    return max(candidates, key=lambda item: item[0])[1]


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_config(config: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def text_of(node: ElementTree.Element, tag: str) -> str:
    child = node.find(tag)
    return (child.text or "").strip() if child is not None else ""


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def report_business_year(report: DartReport) -> str:
    match = re.search(r"\((\d{4})\.\d{2}\)", report.report_name)
    if match:
        return match.group(1)
    return (report.receipt_date or "")[:4]


def report_code(report: DartReport) -> str:
    name = report.report_name
    if "사업보고서" in name:
        return "11011"
    if "반기보고서" in name:
        return "11012"
    if "분기보고서" in name and ".03" in name:
        return "11013"
    if "분기보고서" in name and ".09" in name:
        return "11014"
    return ""


def report_period_months(report: DartReport) -> int:
    name = report.report_name
    if "분기보고서" in name:
        return 3
    if "반기보고서" in name:
        return 6
    return 12


def report_period_key(report: DartReport) -> tuple[int, int] | None:
    match = re.search(r"\((\d{4})\.(\d{2})\)", report.report_name)
    if match:
        return int(match.group(1)), int(match.group(2))
    year = report_business_year(report)
    if year.isdigit():
        return int(year), report_period_months(report)
    return None


def report_period_label(report: DartReport) -> str:
    key = report_period_key(report)
    if not key:
        return ""
    return f"{key[0]}.{key[1]:02d}"


def beginning_borrowing_amount(
    period_key: tuple[int, int] | None,
    amount_cache: dict[str, tuple[int, int, str, list[BorrowingLine]]],
    latest_report_by_period: dict[tuple[int, int], DartReport],
) -> tuple[int | None, str]:
    if not period_key:
        return None, "평균차입금: 보고기간 식별 실패"

    year, _ = period_key
    prior_year_end_report = latest_report_by_period.get((year - 1, 12))
    if prior_year_end_report:
        beginning_amount = amount_cache.get(prior_year_end_report.receipt_no, (None, 0, "", []))[0]
        if beginning_amount is not None:
            return (
                beginning_amount,
                f"평균차입금: 전기말 {report_period_label(prior_year_end_report)} 차입잔액 {beginning_amount:,}백만원과 당기말 차입잔액 평균",
            )

    return None, "평균차입금: 전기말 재무상태표 차입잔액 미검출"


def movement_adjusted_average_borrowing_balance(
    beginning_amount: int | None,
    ending_amount: int,
    lines: list[BorrowingLine],
) -> BorrowingMovementEstimate | None:
    if beginning_amount is None or beginning_amount <= 0 or ending_amount < 0:
        return None

    estimates: list[tuple[float, BorrowingMovementEstimate]] = []
    for source_file, source_lines in borrowing_movement_lines_by_source(lines).items():
        increases = sum(amount for amount in borrowing_movement_amounts(source_lines) if amount > 0)
        decreases = sum(abs(amount) for amount in borrowing_movement_amounts(source_lines) if amount < 0)
        if increases == 0 and decreases == 0:
            continue

        implied_ending = beginning_amount + increases - decreases
        difference = abs(implied_ending - ending_amount)
        tolerance = max(5_000, max(beginning_amount, ending_amount) * 0.20)
        if difference > tolerance:
            continue

        average_balance = beginning_amount + (increases * 0.5) - (decreases * 0.5)
        if average_balance <= 0:
            continue
        method = (
            "평균차입금: 전기말 재무상태표 차입잔액에 당기 차입금 변동내역을 반기 평균 가정으로 반영"
            f" (증가 {increases:,}백만원, 감소 {decreases:,}백만원, 출처 {source_file})"
        )
        estimates.append((difference, BorrowingMovementEstimate(average_balance, method)))

    if not estimates:
        return None
    return min(estimates, key=lambda item: item[0])[1]


def borrowing_movement_lines_by_source(lines: list[BorrowingLine]) -> dict[str, list[BorrowingLine]]:
    grouped: dict[str, list[BorrowingLine]] = {}
    seen: set[tuple[str, str]] = set()
    for line in lines:
        if not is_borrowing_movement_context(line.context):
            continue
        key = (line.source_file, normalize_text(line.context))
        if key in seen:
            continue
        seen.add(key)
        grouped.setdefault(line.source_file, []).append(line)
    return grouped


def borrowing_movement_amounts(lines: list[BorrowingLine]) -> list[int]:
    amounts: list[int] = []
    for line in lines:
        sign = borrowing_movement_sign(line.context)
        if sign == 0:
            continue
        values = [abs(value) for value in line.amounts if abs(value) > 0]
        if not values:
            continue
        amount = normalize_movement_amount_to_million(values[0], line.amount_unit)
        if amount > 0:
            amounts.append(sign * amount)
    return amounts


def is_borrowing_movement_context(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    if any(keyword in compact for keyword in ("할인발행차금", "담보", "주주총회", "동등배당", "정관")):
        return False
    return borrowing_movement_sign(text) != 0 and bool(re.search(r"\d{1,3}(?:,\d{3})+", text))


def borrowing_movement_sign(text: str) -> int:
    compact = re.sub(r"\s+", "", text)
    if re.search(r"(?:단기차입금|장기차입금|차입금|사채)의(?:감소|상환)", compact):
        return -1
    if re.search(r"(?:단기차입금|장기차입금|차입금|사채)의(?:증가|발행)", compact) or "신규차입" in compact:
        return 1
    return 0


def normalize_movement_amount_to_million(value: int, unit: str) -> int:
    compact = re.sub(r"\s+", "", unit or "")
    if compact in ("", "원") and value >= 100_000:
        return round(value / 1_000)
    return normalize_amount_to_million(value, compact)


def parse_dart_amount(value: str) -> int | None:
    cleaned = str(value or "").replace(",", "").strip()
    if not cleaned or cleaned == "-":
        return None
    try:
        return abs(int(cleaned))
    except ValueError:
        return None


def is_financial_borrowing_account(account: str) -> bool:
    compact = re.sub(r"\s+", "", account)
    exact_accounts = {
        "단기차입금",
        "장기차입금",
        "사채",
        "유동성장기부채",
        "유동성장기차입금",
        "유동성사채",
        "차입부채",
    }
    if compact in exact_accounts:
        return True
    if any(excluded in compact for excluded in ("리스", "이자", "파생", "충당", "순확정")):
        return False
    return compact.endswith("차입금") or compact.endswith("사채") or compact.endswith("차입부채")


def is_exact_interest_expense_account(account: str) -> bool:
    compact = re.sub(r"\s+", "", account)
    return (
        compact
        in {
            "이자비용",
            "이자비용(금융원가)",
            "이자비용(금융비용)",
            "차입금이자비용",
            "사채이자비용",
            "기타금융부채이자비용",
            "차입/금융부채이자비용",
        }
        or "상각후원가측정금융부채이자비용" in compact
    )

def is_income_statement_name(statement_name: str) -> bool:
    compact = re.sub(r"\s+", "", statement_name)
    return "손익계산서" in compact or "포괄손익계산서" in compact


def is_revenue_account(account: str, account_id: str = "") -> bool:
    if account_id == "ifrs-full_Revenue":
        return True
    compact = re.sub(r"\s+", "", account)
    if any(keyword in compact for keyword in ("매출원가", "금융수익", "이자수익", "기타수익", "영업외수익")):
        return False
    return (
        compact in {"매출", "매출액", "영업수익", "수익", "수익(매출액)"}
        or compact.endswith("매출액")
        or compact.startswith(("매출액(", "수익("))
    )


def clean_context(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value)
    return normalize_text(value)


def display_keyword_for_context(text: str, section: str = "") -> str:
    compact = re.sub(r"\s+", "", text)
    if any(keyword in compact for keyword in ("사채처분손실", "사채처분이익")):
        return ""
    if section in ("금융비용 주석", "금융수익 및 금융비용 주석"):
        return NOTE_INTEREST_EXPENSE_KEYWORD if is_borrowing_interest_expense_context(text) else ""
    if section == "차입금/사채 주석" and is_borrowing_interest_expense_context(text):
        return ""

    if is_borrowing_interest_expense_context(text):
        return NOTE_INTEREST_EXPENSE_KEYWORD
    for keyword in DISPLAY_KEYWORDS:
        if keyword in compact:
            return keyword
    return ""


def extract_text_records(text: str) -> list[tuple[int, str, str, str]]:
    raw_records: list[tuple[int, int, str]] = []
    seen: set[str] = set()

    for match in re.finditer(r"<TR\b.*?</TR>", text, flags=re.IGNORECASE | re.DOTALL):
        context = clean_context(match.group(0))
        if context and context not in seen:
            line_no = text.count("\n", 0, match.start()) + 1
            raw_records.append((match.start(), line_no, context))
            seen.add(context)

    offset = 0
    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        line_start = offset
        offset += len(raw_line) + 1
        if "<TD" in raw_line.upper() or "<TR" in raw_line.upper() or "</TD" in raw_line.upper():
            continue
        context = clean_context(raw_line)
        if context and context not in seen:
            raw_records.append((line_start, line_no, context))
            seen.add(context)

    records: list[tuple[int, str, str, str]] = []
    current_unit = ""
    current_section = ""
    for _, line_no, context in sorted(raw_records, key=lambda item: item[0]):
        section = note_section_for_context(context)
        if section:
            current_section = section
        detected_unit = detect_amount_unit(context)
        if detected_unit:
            current_unit = detected_unit
        records.append((line_no, context, current_unit, current_section))
    return records


def extract_interest_expense_lines_from_document(report: DartReport, source_file: str, text: str) -> list[BorrowingLine]:
    plain = clean_context(text)
    rows: list[BorrowingLine] = []
    seen_contexts: set[str] = set()

    def append_interest_line(section: str, context: str, raw_amount: str, unit: str, line_no: int) -> None:
        context = context.strip()
        if not context or context in seen_contexts:
            return
        seen_contexts.add(context)
        negative = raw_amount.startswith("(") and raw_amount.endswith(")")
        amount = int(raw_amount.strip("()").replace(",", ""))
        if negative:
            amount = -abs(amount)
        amount = normalize_interest_amount_to_million(abs(amount), unit)
        rows.append(
            BorrowingLine(
                report.corp_name,
                report.report_name,
                report.receipt_date,
                report.receipt_no,
                NOTE_INTEREST_EXPENSE_KEYWORD,
                section,
                [],
                [amount],
                unit,
                amount,
                Path(source_file).name or f"{report.receipt_no}.xml",
                line_no,
                context[:1200],
            )
        )

    section_pattern = re.compile(
        r"\d{1,3}\.\s*(?:금융수익\s*(?:및|과)\s*)?금융(?:비용|원가)(?:의\s*내역|내역)?"
    )
    for section_match in section_pattern.finditer(plain):
        start = section_match.start()
        next_match = re.search(r"\s\d{1,3}\.\s*[가-힣A-Za-z]", plain[section_match.end() :])
        end = section_match.end() + next_match.start() if next_match else min(len(plain), start + 6000)
        section_text = plain[start:end]
        unit = detect_amount_unit(section_text)
        for amount_match in re.finditer(r"이자비용\s+(\(?-?\d{1,3}(?:,\d{3})+\)?)", section_text):
            context_start = max(0, amount_match.start() - 220)
            context_end = min(len(section_text), amount_match.end() + 420)
            context = section_text[context_start:context_end].strip()
            raw = amount_match.group(1)
            line_no = text.count("\n", 0, max(0, text.find(section_text[:80]))) + 1
            append_interest_line("금융비용 주석", context, raw, unit, line_no)

    liability_pattern = re.compile(
        r"상각후원가\s*(?:측정\s*)?금융부채\s*:?\s*(?:[^\d.,()]{0,80}?)이자비용\s+(\(?-?\d{1,3}(?:,\d{3})+\)?)"
    )
    for amount_match in liability_pattern.finditer(plain):
        context_start = max(0, amount_match.start() - 220)
        context_end = min(len(plain), amount_match.end() + 420)
        context = plain[context_start:context_end].strip()
        unit_context = plain[max(0, amount_match.start() - 1500) : amount_match.end() + 200]
        unit = detect_amount_unit(unit_context)
        source_pos = text.find(plain[max(0, amount_match.start() - 40) : amount_match.start() + 40])
        line_no = text.count("\n", 0, source_pos if source_pos >= 0 else 0) + 1
        append_interest_line(
            "상각후원가 금융부채 이자비용 주석",
            context,
            amount_match.group(1),
            unit,
            line_no,
        )
    return rows


def extract_borrowing_table_rate_lines(report: DartReport, source_file: str, text: str) -> list[BorrowingLine]:
    rows: list[BorrowingLine] = []
    seen: set[str] = set()
    for table_match in re.finditer(r"<TABLE\b.*?</TABLE>", text, flags=re.IGNORECASE | re.DOTALL):
        table_html = table_match.group(0)
        table_text = clean_context(table_html)
        section = borrowing_section_for_table(text, table_match.start(), table_text)
        unit = detect_amount_unit(table_text) or detect_amount_unit(clean_context(text[max(0, table_match.start() - 800) : table_match.start()]))
        table_rows = parse_table_rows(table_html)
        rows.extend(borrowing_lines_from_table_rows(report, source_file, text, table_match.start(), table_rows, table_text, section, unit, seen))

    for group_start, group_html in parse_tr_groups(text):
        table_text = clean_context(group_html)
        section = borrowing_section_for_table(text, group_start, table_text)
        unit = detect_amount_unit(table_text) or detect_amount_unit(clean_context(text[max(0, group_start - 800) : group_start]))
        table_rows = parse_table_rows(group_html)
        rows.extend(borrowing_lines_from_table_rows(report, source_file, text, group_start, table_rows, table_text, section, unit, seen))
    return rows


def borrowing_lines_from_table_rows(
    report: DartReport,
    source_file: str,
    text: str,
    table_start: int,
    table_rows: list[list[str]],
    table_text: str,
    section: str,
    unit: str,
    seen: set[str],
) -> list[BorrowingLine]:
    rows: list[BorrowingLine] = []
    if not table_rows:
        return rows
    if not is_borrowing_note_section(section) and not is_borrowing_table_text(table_text):
        return rows

    rate_rows = [(idx, cells) for idx, cells in enumerate(table_rows) if is_table_rate_row(cells)]
    amount_rows = [(idx, cells) for idx, cells in enumerate(table_rows) if is_table_borrowing_amount_row(cells)]
    if not rate_rows or not amount_rows:
        return rows

    date_by_col = table_date_values(table_rows)
    total_cols = table_total_columns(table_rows)
    for rate_idx, rate_cells in rate_rows:
        if is_prior_period_table_segment(table_rows, rate_idx):
            continue
        rates_by_col = table_rates_by_column(rate_cells)
        if not rates_by_col:
            continue
        excluded_cols = total_cols | table_excluded_columns_near_rate(table_rows, rate_idx)
        next_rate_idx = min((idx for idx, _ in rate_rows if idx > rate_idx), default=len(table_rows))
        nearby_amount_rows = [
            (idx, cells)
            for idx, cells in amount_rows
            if rate_idx < idx < next_rate_idx and idx - rate_idx <= 8
        ]
        for amount_idx, amount_cells in nearby_amount_rows:
            label = first_text_cell(amount_cells)
            if not label:
                continue
            for col, amount in table_amounts_by_column(amount_cells, unit).items():
                if col in excluded_cols:
                    continue
                rates = rates_by_col.get(col)
                if not rates:
                    rates = nearest_rate_column_value(rates_by_col, col)
                if not rates:
                    continue
                context_parts = [section or "차입금/사채 주석", label, f"금액 {amount:,}백만원"]
                context_parts.append("이자율 " + ", ".join(f"{rate:.4%}" for rate in rates))
                if col in date_by_col:
                    context_parts.append(" ".join(date_by_col[col]))
                context = " ".join(context_parts)
                key = f"{report.receipt_no}:{source_file}:{table_start}:{rate_idx}:{amount_idx}:{col}:{amount}:{rates}"
                if key in seen:
                    continue
                seen.add(key)
                line_no = text.count("\n", 0, table_start) + 1
                rows.append(
                    BorrowingLine(
                        report.corp_name,
                        report.report_name,
                        report.receipt_date,
                        report.receipt_no,
                        display_keyword_for_context(label, section) or borrowing_table_keyword(label),
                        section or "차입금/사채 주석",
                        rates,
                        [amount],
                        "백만원",
                        amount,
                        Path(source_file).name or f"{report.receipt_no}.xml",
                        line_no,
                        context[:1200],
                    )
                )
    return rows


def parse_table_rows(table_html: str) -> list[list[str]]:
    parsed_rows: list[list[str]] = []
    for row_match in re.finditer(r"<TR\b.*?</TR>", table_html, flags=re.IGNORECASE | re.DOTALL):
        cells: list[str] = []
        for cell_match in re.finditer(r"<T[HDE]\b([^>]*)>(.*?)</T[HDE]>", row_match.group(0), flags=re.IGNORECASE | re.DOTALL):
            attrs = cell_match.group(1)
            colspan_match = re.search(r"COLSPAN\s*=\s*[\"']?(\d+)", attrs, flags=re.IGNORECASE)
            colspan = int(colspan_match.group(1)) if colspan_match else 1
            value = clean_context(cell_match.group(2))
            cells.extend([value] * max(1, min(colspan, 20)))
        if any(cells):
            parsed_rows.append(cells)
    return parsed_rows


def parse_tr_groups(text: str) -> list[tuple[int, str]]:
    matches = list(re.finditer(r"<TR\b.*?</TR>", text, flags=re.IGNORECASE | re.DOTALL))
    groups: list[tuple[int, str]] = []
    current_start: int | None = None
    current_parts: list[str] = []
    previous_end = 0
    for match in matches:
        gap = clean_context(text[previous_end : match.start()])
        starts_new_group = current_start is None or bool(
            re.search(r"(?:^|\s)(?:당기|당분기|당반기|전기|전분기|전반기)말?\s*(?:\(단위|$)", gap)
        )
        if starts_new_group:
            if current_start is not None and current_parts:
                groups.append((current_start, "\n".join(current_parts)))
            current_start = match.start()
            current_parts = []
        current_parts.append(match.group(0))
        previous_end = match.end()
    if current_start is not None and current_parts:
        groups.append((current_start, "\n".join(current_parts)))
    return groups


def borrowing_section_for_table(text: str, table_start: int, table_text: str) -> str:
    section = note_section_for_context(table_text)
    if section:
        return section
    prefix = clean_context(text[max(0, table_start - 2500) : table_start])
    headings = re.findall(r"(?:^|\s)(\d{1,3}\.\s*[가-힣A-Za-z0-9()/ㆍ·\s]{1,80})", prefix)
    for heading in reversed(headings):
        section = note_section_for_context(heading.strip())
        if section:
            return section
    if is_borrowing_table_text(table_text):
        return "차입금/사채 주석"
    return ""


def is_borrowing_table_text(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    return any(keyword in compact for keyword in ("차입금", "단기차입", "장기차입", "사채", "회사채", "borrow", "debt", "bond"))


def is_table_rate_row(cells: list[str]) -> bool:
    row_text = " ".join(cells)
    compact = re.sub(r"\s+", "", row_text)
    return any(keyword in compact for keyword in ("이자율", "이율", "금리", "연이자율", "interestrate")) and bool(table_rates_by_column(cells))


def is_table_borrowing_amount_row(cells: list[str]) -> bool:
    label = first_text_cell(cells)
    compact = re.sub(r"\s+", "", label)
    if not compact:
        return False
    if any(keyword in compact for keyword in ("이자율", "이율", "금리", "할인", "할증", "차감", "기술", "기초통화", "상환일", "발행일")):
        return False
    return any(keyword in compact for keyword in ("차입금", "단기차입", "장기차입", "유동성장기차입", "사채", "회사채", "은행차입"))


def first_text_cell(cells: list[str]) -> str:
    for cell in cells:
        if re.search(r"[가-힣A-Za-z]", cell):
            return cell
    return cells[0] if cells else ""


def borrowing_table_keyword(label: str) -> str:
    compact = re.sub(r"\s+", "", label)
    for keyword in ("단기차입금", "장기차입금", "유동성장기차입금", "회사채", "사채", "차입금"):
        if keyword in compact:
            return keyword
    return "차입금"


def table_rates_by_column(cells: list[str]) -> dict[int, list[float]]:
    rates_by_col: dict[int, list[float]] = {}
    for idx, cell in enumerate(cells):
        rates = extract_rate_values("이자율 " + cell)
        if not rates and re.fullmatch(r"\s*0(?:\.0{1,5})?\s*", cell):
            rates = [0.0]
        rates = [rate for rate in rates if 0 <= rate <= 0.50]
        if rates or re.fullmatch(r"\s*0(?:\.0{1,5})?\s*", cell):
            rates_by_col[idx] = rates
    return rates_by_col


def table_amounts_by_column(cells: list[str], unit: str) -> dict[int, int]:
    amounts: dict[int, int] = {}
    for idx, cell in enumerate(cells):
        if re.search(r"[가-힣A-Za-z]", cell):
            continue
        value = parse_table_amount_cell(cell)
        if value is None or value <= 0:
            continue
        amount = normalize_amount_to_million(value, unit)
        if amount > 0:
            amounts[idx] = amount
    return amounts


def nearest_rate_column_value(values_by_col: dict[int, list[float]], col: int) -> list[float] | None:
    if not values_by_col:
        return None
    nearest = min(values_by_col, key=lambda idx: abs(idx - col))
    if nearest < col:
        left_pair_cols = [idx for idx in values_by_col if idx < nearest and nearest - idx <= 2]
        if left_pair_cols:
            pair_col = max(left_pair_cols)
            pair_values = values_by_col[pair_col] + values_by_col[nearest]
            pair_values = [value for value in pair_values if 0 <= value <= 0.50]
            if pair_values and any(value > 0 for value in pair_values):
                return [sum(pair_values) / len(pair_values)]
    if abs(nearest - col) <= 3:
        return values_by_col[nearest]
    return None


def parse_table_amount_cell(cell: str) -> int | None:
    matches = re.findall(r"\(?-?\d{1,3}(?:,\d{3})+\)?|\(?-?\d{1,12}\)?", cell)
    if not matches:
        return None
    values: list[int] = []
    for raw in matches:
        value = parse_signed_amount(raw)
        if value is not None:
            values.append(abs(value))
    return max(values) if values else None


def table_date_values(table_rows: list[list[str]]) -> dict[int, list[str]]:
    values: dict[int, list[str]] = {}
    for cells in table_rows:
        label = first_text_cell(cells)
        if not any(keyword in label for keyword in ("발행일", "만기", "상환일")):
            continue
        for idx, cell in enumerate(cells):
            if extract_dates(cell):
                values.setdefault(idx, []).append(f"{label} {cell}")
    return values


def table_total_columns(table_rows: list[list[str]]) -> set[int]:
    cols: set[int] = set()
    for cells in table_rows[:8]:
        for idx, cell in enumerate(cells):
            compact = re.sub(r"\s+", "", cell)
            if is_excluded_total_header(compact):
                cols.add(idx)
    return cols


def table_excluded_columns_near_rate(table_rows: list[list[str]], rate_idx: int) -> set[int]:
    cols: set[int] = set()
    for cells in table_rows[max(0, rate_idx - 8) : rate_idx]:
        for idx, cell in enumerate(cells):
            compact = re.sub(r"\s+", "", cell)
            if is_excluded_total_header(compact) or any(keyword in compact for keyword in ("리스부채", "리스 부채")):
                cols.add(idx)
    return cols


def is_excluded_total_header(compact: str) -> bool:
    if "범위합계" in compact:
        return False
    return "총계" in compact or any(
        keyword in compact
        for keyword in ("차입금합계", "차입금명칭합계", "명칭합계", "사채합계", "유동성차입금합계", "장기차입금합계", "단기차입금합계")
    )


def is_prior_period_table_segment(table_rows: list[list[str]], rate_idx: int) -> bool:
    markers: list[str] = []
    for cells in table_rows[max(0, rate_idx - 12) : rate_idx]:
        row_text = re.sub(r"\s+", "", " ".join(cells))
        for marker in ("당분기말", "당반기말", "당기말", "당분기", "당반기", "당기", "전분기말", "전반기말", "전기말", "전분기", "전반기", "전기"):
            if marker in row_text:
                markers.append(marker)
    if not markers:
        return False
    return markers[-1].startswith("전")


def note_section_for_context(text: str) -> str:
    normalized = normalize_text(text)
    compact = re.sub(r"\s+", "", normalized)
    if not re.match(r"^\d{1,3}\.", compact):
        return ""
    title = re.sub(r"^\d{1,3}\.", "", compact)
    title = re.sub(r"\([^)]*\)$", "", title)
    if title.startswith("차입금") or title.startswith("사채"):
        return "차입금/사채 주석"
    if title.startswith(
        (
            "금융비용",
            "금융원가",
            "금융수익및금융비용",
            "금융수익과금융비용",
            "금융수익및금융원가",
            "금융수익과금융원가",
        )
    ):
        return "금융비용 주석"
    return ""


def detect_amount_unit(text: str) -> str:
    compact = re.sub(r"\s+", "", text)
    match = re.search(r"단위[:：]?(십억원|백만원|천원|억원|원|USD|천USD|백만USD|미화천달러|미화백만달러)", compact, flags=re.IGNORECASE)
    if match:
        return match.group(1)
    for unit in ("십억원", "백만원", "천원", "억원"):
        if unit in text:
            return unit
    return ""


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


def interest_judgment_basis(
    expected: float,
    actual: float,
    diff: float | None,
    average_balance: float,
    avg_rate: float,
    period_months: int,
    revenue: int,
    materiality_threshold: float,
    verdict_text: str,
) -> str:
    parts = [
        verdict_text,
        f"계산 {expected:,.0f}백만원 = 평균차입금 {average_balance:,.0f} × 적용이자율 {avg_rate:.2%} × {period_months}/12.",
        f"실제 {actual:,.0f}백만원.",
    ]
    if diff is not None:
        parts.append(f"차이 {diff:,.0f}백만원.")
        parts.append(materiality_comparison_basis(diff, revenue, materiality_threshold))
    return " ".join(parts)


def materiality_threshold_from_revenue(revenue: int) -> float:
    return revenue * 0.01 * 0.25


def absolute_amount_diff(left: float, right: float) -> float:
    return abs(abs(left) - abs(right))


def materiality_comparison_basis(diff: float, revenue: int, materiality_threshold: float) -> str:
    operator = "<=" if abs(diff) <= materiality_threshold else ">"
    return (
        f"기준 |차이| {abs(diff):,.0f}백만원 {operator} 중요성 {materiality_threshold:,.0f}백만원"
        f"(매출액 {revenue:,.0f} × 1% × 25%)."
    )


def disclosure_interest_judgment_basis(
    reference: float,
    actual: float,
    diff: float,
    revenue: int,
    materiality_threshold: float,
    verdict_text: str,
) -> str:
    return (
        f"{verdict_text} 금융비용 주석 {reference:,.0f}백만원 vs 상각후원가 금융부채 주석 {actual:,.0f}백만원. "
        f"차이 {diff:,.0f}백만원. {materiality_comparison_basis(diff, revenue, materiality_threshold)}"
    )

def build_overall_tests(
    reports: list[DartReport],
    lines: list[BorrowingLine],
    revenues: dict[str, int] | None = None,
) -> list[dict]:
    revenues = revenues or {}
    by_receipt: dict[str, list[BorrowingLine]] = {}
    for line in lines:
        by_receipt.setdefault(line.receipt_no, []).append(line)

    amount_cache: dict[str, tuple[int, int, str, list[BorrowingLine]]] = {}
    for report in reports:
        report_lines = by_receipt.get(report.receipt_no, [])
        test_lines = [line for line in report_lines if not is_comparison_rate_context(line.context)]
        amount_cache[report.receipt_no] = calculate_borrowing_amount(test_lines)

    latest_report_by_period: dict[tuple[int, int], DartReport] = {}
    for report in sorted(reports, key=lambda r: (r.receipt_date, r.report_name)):
        key = report_period_key(report)
        if key:
            latest_report_by_period[key] = report

    rows: list[dict] = []
    for report in sorted(reports, key=lambda r: (r.receipt_date, r.report_name)):
        report_lines = by_receipt.get(report.receipt_no, [])
        comparison_rate_lines = [line for line in report_lines if is_comparison_rate_context(line.context)]
        test_lines = [line for line in report_lines if not is_comparison_rate_context(line.context)]
        borrowing_context_lines = [line for line in test_lines if line.keyword != NOTE_INTEREST_EXPENSE_KEYWORD]
        target_rate_lines = [line for line in test_lines if is_valid_borrowing_rate_line(line)]
        avg_borrowing_rate_lines = [line for line in comparison_rate_lines if is_average_borrowing_rate_context(line.context)]
        wacc_lines = [line for line in comparison_rate_lines if is_wacc_context(line.context)]
        rates = [rate for line in target_rate_lines for rate in estimation_interest_rates(line)]
        avg_borrowing_rates = [rate for line in avg_borrowing_rate_lines for rate in line.interest_rates]
        wacc_rates = [rate for line in wacc_lines for rate in line.interest_rates]
        benchmark_rates = avg_borrowing_rates
        if avg_borrowing_rates:
            benchmark_label = "평균차입이자율"
        elif wacc_rates:
            benchmark_label = "평균차입이자율 미공시(WACC 참고검출)"
        else:
            benchmark_label = "평균차입이자율 미공시"
        amount_sum, max_amount, amount_method, amount_used_lines = amount_cache.get(report.receipt_no, (0, 0, "차입금 잔액 후보 없음", []))
        period_months = report_period_months(report)
        period_key = report_period_key(report)
        weighted_rate = weighted_average_interest_rate(target_rate_lines, amount_sum, period_key, period_months)
        has_amount_candidate = bool(amount_used_lines)
        yoy_report = latest_report_by_period.get((period_key[0] - 1, period_key[1])) if period_key else None
        yoy_amount_sum = amount_cache.get(yoy_report.receipt_no, (None, 0, "", []))[0] if yoy_report else None
        amount_diff = amount_sum - yoy_amount_sum if yoy_amount_sum is not None else None
        amount_change = amount_diff / yoy_amount_sum if amount_diff is not None and yoy_amount_sum not in (None, 0) else None
        amount_comparison_label = f"전년동기 {report_period_label(yoy_report)}" if yoy_report else "전년동기 비교대상 없음"
        min_rate = min(rates) if rates else None
        avg_rate = weighted_rate if weighted_rate is not None else (sum(rates) / len(rates) if rates else None)
        max_rate = max(rates) if rates else None
        avg_benchmark_rate = sum(benchmark_rates) / len(benchmark_rates) if benchmark_rates else None
        benchmark_diff = avg_rate - avg_benchmark_rate if avg_rate is not None and avg_benchmark_rate is not None else None
        benchmark_error_rate = benchmark_diff / avg_benchmark_rate if benchmark_diff is not None and avg_benchmark_rate not in (None, 0) else None
        beginning_amount, average_method = beginning_borrowing_amount(period_key, amount_cache, latest_report_by_period)
        movement_estimate = movement_adjusted_average_borrowing_balance(beginning_amount, amount_sum, test_lines)
        if movement_estimate is not None:
            average_borrowing_balance = movement_estimate.average_balance
            average_method = movement_estimate.method
        elif amount_sum > 0 and beginning_amount is not None and beginning_amount > 0:
            average_borrowing_balance = (beginning_amount + amount_sum) / 2
        elif amount_sum > 0:
            average_borrowing_balance = amount_sum
            average_method = "평균차입금: 전기말 잔액을 찾지 못해 당기말 잔액 사용"
        elif has_amount_candidate and beginning_amount is not None and beginning_amount > 0:
            average_borrowing_balance = beginning_amount / 2
            average_method = f"평균차입금: 당기말 잔액 0, 전기말 잔액 {beginning_amount:,}백만원의 절반 사용"
        else:
            average_borrowing_balance = None
            average_method = "평균차입금: 전기말 및 당기말 차입잔액 부족"
        period_factor = period_months / 12
        revenue = revenues.get(report.receipt_no, 0)
        materiality_threshold = materiality_threshold_from_revenue(revenue) if revenue > 0 else 0
        financial_expense = extract_note_interest_expense(report_lines) or FinancialExpense(report.receipt_no, None, "", "금융비용 주석 이자비용 정보 없음")
        actual_interest_expense = financial_expense.actual_interest_expense
        actual_interest_comparable = actual_interest_expense is not None and is_exact_interest_expense_account(financial_expense.account_name)
        disclosure_comparison = note_interest_disclosure_comparison(report_lines)
        expected_interest_expense = average_borrowing_balance * avg_rate * period_factor if avg_rate is not None and average_borrowing_balance else None
        interest_expense_diff = absolute_amount_diff(actual_interest_expense, expected_interest_expense) if actual_interest_comparable and expected_interest_expense is not None else None
        interest_expense_error_rate = interest_expense_diff / expected_interest_expense if interest_expense_diff is not None and expected_interest_expense not in (None, 0) else None
        amount_units = sorted({line.amount_unit for line in amount_used_lines if line.max_amount and line.amount_unit})
        amount_unit = ""
        if len(amount_units) == 1:
            amount_unit = amount_units[0]
        elif len(amount_units) > 1:
            amount_unit = "혼합: " + ", ".join(amount_units)

        if disclosure_comparison is not None and actual_interest_expense is None:
            reference_interest, disclosed_interest, disclosure_diff, disclosure_error_rate = disclosure_comparison
            expected_interest_expense = reference_interest
            actual_interest_expense = disclosed_interest
            interest_expense_diff = disclosure_diff
            interest_expense_error_rate = disclosure_error_rate
            financial_expense = FinancialExpense(
                report.receipt_no,
                disclosed_interest,
                "주석 간 이자비용 대사",
                "금융비용 주석 이자비용과 상각후원가 금융부채 이자비용 대사",
            )
            actual_interest_comparable = True
            if abs(disclosure_diff) <= materiality_threshold:
                judgment = "적정"
                judgment_basis = disclosure_interest_judgment_basis(
                    reference_interest,
                    disclosed_interest,
                    disclosure_diff,
                    revenue,
                    materiality_threshold,
                    "주석 간 대사 차이 PM/3 이하임.",
                )
            else:
                judgment = "확인필요"
                judgment_basis = disclosure_interest_judgment_basis(
                    reference_interest,
                    disclosed_interest,
                    disclosure_diff,
                    revenue,
                    materiality_threshold,
                    "주석 간 대사 차이 PM/3 초과함.",
                )
        elif not has_amount_candidate or average_borrowing_balance in (None, 0):
            judgment = "판단불가"
            if has_amount_candidate:
                judgment_basis = f"당기말 차입금/사채 0, 전기 비교잔액 없음. 예상이자 산정불가. 금액산정: {amount_method}."
            else:
                judgment_basis = f"차입금/사채 잔액 후보 미검출. 예상이자 산정불가. 금액산정: {amount_method}."
        elif actual_interest_expense is None:
            judgment = "판단불가"
            judgment_basis = "금융비용 주석 내 비교 가능한 이자비용 미검출."
        elif not is_exact_interest_expense_account(financial_expense.account_name):
            judgment = "판단불가"
            judgment_basis = "금융비용 대체값 사용. 외환손익 등 혼재 가능하여 판정 제외함."
        elif avg_rate is None:
            judgment = "확인필요"
            judgment_basis = f"금융비용 주석의 이자비용 {actual_interest_expense:,.0f}백만원을 실제 이자비용으로 사용. 차입금/사채 주석의 이자율 표기 확인 필요."
        elif expected_interest_expense is None:
            judgment = "판단불가"
            judgment_basis = "평균차입금 또는 이자율 부족. 예상이자 산정불가."
        elif interest_expense_diff is not None and abs(interest_expense_diff) <= materiality_threshold:
            judgment = "적정"
            judgment_basis = interest_judgment_basis(
                expected_interest_expense,
                actual_interest_expense,
                interest_expense_diff,
                average_borrowing_balance,
                avg_rate,
                period_months,
                revenue,
                materiality_threshold,
                "계산이자와 실제이자 차이 PM/3 이하임.",
            )
        else:
            judgment = "확인필요"
            judgment_basis = interest_judgment_basis(
                expected_interest_expense,
                actual_interest_expense,
                interest_expense_diff,
                average_borrowing_balance,
                avg_rate,
                period_months,
                revenue,
                materiality_threshold,
                "계산이자와 실제이자 차이 PM/3 초과함.",
            )

        result = judgment

        caution_reasons: list[str] = []
        if amount_change is not None and abs(amount_change) >= 0.30:
            caution_reasons.append(f"전년동기 대비 검출금액합계가 {amount_change:.2%} 변동하여 30% 기준을 초과했습니다.")
        caution_status = "주의요망" if caution_reasons else ""
        caution_reason = " ".join(caution_reasons)

        rows.append(
            {
                "corp_name": report.corp_name,
                "report_name": report.report_name,
                "receipt_date": report.receipt_date,
                "receipt_no": report.receipt_no,
                "context_count": len(borrowing_context_lines),
                "rate_count": len(rates),
                "benchmark_type": benchmark_label,
                "benchmark_count": len(benchmark_rates),
                "wacc_count": len(wacc_rates),
                "amount_sum": amount_sum,
                "max_amount": max_amount,
                "amount_method": f"{amount_method}; {average_method}",
                "amount_comparison_label": amount_comparison_label,
                "amount_diff": amount_diff,
                "amount_change": amount_change,
                "amount_unit": amount_unit,
                "min_rate": min_rate,
                "avg_rate": avg_rate,
                "max_rate": max_rate,
                "avg_benchmark_rate": avg_benchmark_rate,
                "benchmark_diff": benchmark_diff,
                "benchmark_error_rate": benchmark_error_rate,
                "average_borrowing_balance": average_borrowing_balance,
                "period_months": report_period_months(report),
                "period_factor": period_factor,
                "expected_interest_expense": expected_interest_expense,
                "actual_interest_expense": actual_interest_expense,
                "interest_expense_account": financial_expense.account_name,
                "interest_expense_diff": interest_expense_diff,
                "interest_expense_error_rate": interest_expense_error_rate,
                "interest_expense_memo": financial_expense.memo,
                "judgment": judgment,
                "judgment_basis": judgment_basis,
                "result": result,
                "caution_status": caution_status,
                "caution_reason": caution_reason,
            }
        )
    return rows


def calculate_borrowing_amount(lines: list[BorrowingLine]) -> tuple[int, int, str, list[BorrowingLine]]:
    candidates: list[tuple[BorrowingLine, int]] = []
    seen_contexts: set[str] = set()
    financial_api_lines = [line for line in lines if line.source_file == "fnlttSinglAcntAll.json"]
    note_lines = [line for line in lines if line.source_file != "fnlttSinglAcntAll.json"]
    for line in note_lines:
        if line.interest_rates:
            continue
        if not is_borrowing_amount_context(line.context):
            continue
        amount = extract_current_amount(line)
        if amount is None:
            continue
        if amount < 0:
            continue
        if amount == 0 and not is_current_period_zero_amount(line.context):
            continue
        key = normalize_text(line.context)
        if key in seen_contexts:
            continue
        seen_contexts.add(key)
        candidates.append((line, amount))

    total_candidates = [(line, amount) for line, amount in candidates if is_total_amount_context(line.context)]
    if total_candidates:
        selected_line, selected_amount = max(total_candidates, key=lambda item: item[1])
        return selected_amount, selected_amount, "차입금/사채 주석 합계/총계 행 우선", [selected_line]

    statement_candidates = [(line, amount) for line, amount in candidates if is_statement_borrowing_row(line)]
    if statement_candidates:
        by_category: dict[str, tuple[BorrowingLine, int]] = {}
        for line, amount in statement_candidates:
            category = borrowing_amount_category(line.context)
            if category not in by_category or amount > by_category[category][1]:
                by_category[category] = (line, amount)
        selected = list(by_category.values())
        amount_sum = sum(amount for _, amount in selected)
        max_amount = max((amount for _, amount in selected), default=0)
        return amount_sum, max_amount, "차입금/사채 주석 항목 우선", [line for line, _ in selected]

    selected: list[tuple[BorrowingLine, int]] = []
    seen_amount_keys: set[tuple[str, int]] = set()
    for line, amount in candidates:
        label = borrowing_line_label(line.context)
        key = (label, amount)
        if key in seen_amount_keys:
            continue
        seen_amount_keys.add(key)
        selected.append((line, amount))

    amount_sum = sum(amount for _, amount in selected)
    max_amount = max((amount for _, amount in selected), default=0)
    if selected:
        financial_amount, financial_line = financial_statement_borrowing_amount(financial_api_lines)
        if financial_amount > 0 and amount_sum > financial_amount * 1.5:
            return (
                financial_amount,
                financial_amount,
                f"주석 합산액 {amount_sum:,}백만원이 재무상태표 API 차입잔액 {financial_amount:,}백만원 대비 과대하여 재무상태표 API 사용",
                [financial_line] if financial_line else [],
            )
        return amount_sum, max_amount, "차입금/사채 주석 항목별 당기 금액 합산", [line for line, _ in selected]

    if financial_api_lines:
        amount, selected_line = financial_statement_borrowing_amount(financial_api_lines)
        return amount, amount, "차입금/사채 주석 미검출: 재무상태표 차입 항목 사용", [selected_line]

    return 0, 0, "차입금 잔액 후보 없음", []


def financial_statement_borrowing_amount(lines: list[BorrowingLine]) -> tuple[int, BorrowingLine | None]:
    if not lines:
        return 0, None
    selected_line = max(lines, key=lambda line: line.max_amount)
    amount = normalize_amount_to_million(selected_line.amounts[0], selected_line.amount_unit) if selected_line.amounts else 0
    return amount, selected_line


def extract_current_amount(line: BorrowingLine) -> int | None:
    if is_current_period_zero_amount(line.context):
        return 0
    explicit_amounts = extract_explicit_amounts_to_million(line.context)
    if explicit_amounts:
        return max(explicit_amounts)
    values = [abs(value) for value in line.amounts if abs(value) > 0]
    if not values:
        return None
    compact = re.sub(r"\s+", "", line.context)
    if "차입금명칭" in compact and len(values) > 1:
        return sum(normalize_amount_to_million(value, borrowing_amount_unit_for_context(value, line.amount_unit, line.context)) for value in values)
    value = max(values)
    return normalize_amount_to_million(value, borrowing_amount_unit_for_context(value, line.amount_unit, line.context))


def borrowing_amount_unit_for_context(value: int, unit: str, context: str) -> str:
    compact_unit = re.sub(r"\s+", "", unit or "")
    compact_context = re.sub(r"\s+", "", context or "")
    if compact_unit in ("억원", "원") and compact_unit not in compact_context and value >= 100_000:
        return "백만원"
    return compact_unit


def is_current_period_zero_amount(text: str) -> bool:
    return bool(re.search(r"(?:총차입금|차입금(?:및사채)?|단기차입금|장기차입금|사채)\s*[-－]\s*\d{1,3}(?:,\d{3})+", text))


def normalize_amount_to_million(value: int, unit: str) -> int:
    compact = re.sub(r"\s+", "", unit or "")
    if "십억원" in compact:
        return value * 1_000
    if "천원" in compact:
        return round(value / 1_000)
    if compact == "원":
        return round(value / 1_000_000)
    if "억원" in compact:
        return value * 100
    return value


def normalize_interest_amount_to_million(value: int, unit: str) -> int:
    compact = re.sub(r"\s+", "", unit or "")
    if compact == "":
        return value
    if compact == "원":
        if value >= 10_000_000:
            return round(value / 1_000_000)
        return value
    return normalize_amount_to_million(value, compact)


def parse_signed_amount(raw: str) -> int | None:
    negative = raw.startswith("(") and raw.endswith(")")
    cleaned = raw.strip("()").replace(",", "")
    try:
        value = int(cleaned)
    except ValueError:
        return None
    return -abs(value) if negative else value


def extract_explicit_amounts_to_million(text: str) -> list[int]:
    amounts: list[int] = []
    amount_pattern = r"\(?-?\d{1,3}(?:,\d{3})+\)?|\(?-?\d{1,12}\)?"
    for match in re.finditer(rf"({amount_pattern})\s*(백만원|억원|천원|원)", text):
        value = parse_signed_amount(match.group(1))
        if value is None:
            continue
        if value == 0:
            continue
        amount = normalize_amount_to_million(abs(value), match.group(2))
        amounts.append(-amount if value < 0 else amount)
    return amounts


def calculate_special_bond_amount(lines: list[BorrowingLine], reference_amount: int | None = None) -> int:
    financial_total = 0
    report_context_totals: list[int] = []
    seen_contexts: set[str] = set()
    for line in lines:
        if line.source_file == "fnlttSinglAcntAll.json":
            financial_total += extract_special_bond_amount_from_financial_context(line.context)
            continue

        key = normalize_text(line.context)
        if key in seen_contexts or not is_special_bond_amount_context(line.context):
            continue
        seen_contexts.add(key)
        amount = extract_special_bond_amount_from_report_context(line)
        if amount > 0:
            report_context_totals.append(amount)

    if reference_amount and reference_amount > 0:
        report_context_totals = [amount for amount in report_context_totals if amount <= reference_amount * 1.2]

    if financial_total > 0 and (not reference_amount or financial_total <= reference_amount * 1.2):
        return financial_total
    return max(report_context_totals, default=0)


def extract_special_bond_amount_from_financial_context(text: str) -> int:
    total = 0
    for account, raw_amount in re.findall(r"([^,()]+?)\s+(\d{1,3}(?:,\d{3})*)", text):
        compact = re.sub(r"\s+", "", account)
        if any(keyword in compact for keyword in ("전환사채", "신주인수권부사채", "교환사채")):
            total += int(raw_amount.replace(",", ""))
    return total


def is_special_bond_amount_context(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    if not is_special_bond_context(text):
        return False
    if not re.search(r"\d{1,3}(?:,\d{3})+", text):
        return False
    title_only_keywords = (
        "◆click◆",
        ".dsl",
        "정관",
        "발행및배정",
        "삽입",
        "변경",
        "미상환신주인수권부사채등발행현황.dsl",
    )
    if any(keyword in compact for keyword in title_only_keywords):
        return False
    exclusion_keywords = (
        "전환가액",
        "행사가액",
        "전환가격",
        "행사기간",
        "청구기간",
        "전환청구기간",
        "주식수",
        "보통주식",
        "희석",
        "주당이익",
        "가정(단위:주)",
        "단위:주",
        "전환가정",
        "공정가치",
        "시장가격",
    )
    if any(keyword in compact for keyword in exclusion_keywords):
        return False
    if "단위" in compact and "주" in compact:
        return False
    amount_markers = (
        "장부금액",
        "액면금액",
        "권면총액",
        "미상환잔액",
        "잔액중",
        "발행금액",
        "발행가액",
        "발행하였",
        "사채발행",
        "상계하는방법",
    )
    return any(keyword in compact for keyword in amount_markers)


def extract_special_bond_amount_from_report_context(line: BorrowingLine) -> int:
    values = [normalize_amount_to_million(abs(value), line.amount_unit) for value in line.amounts if abs(value) > 0]
    if not values:
        return 0
    compact = re.sub(r"\s+", "", line.context)
    if any(keyword in compact for keyword in ("보통주식", "전환청구기간", "행사기간", "주식수", "단위:주")):
        return max(values)
    unique_values = list(dict.fromkeys(values))
    if len(unique_values) >= 3:
        largest = max(unique_values)
        rest_sum = sum(unique_values) - largest
        if rest_sum and abs(largest - rest_sum) <= max(1, largest * 0.01):
            return largest
    return sum(unique_values)


def extract_note_interest_expense(lines: list[BorrowingLine]) -> FinancialExpense | None:
    candidates: list[tuple[BorrowingLine, int]] = []
    seen_contexts: set[str] = set()
    for line in lines:
        if not is_borrowing_interest_expense_context(line.context):
            continue
        if is_explanatory_interest_expense_context(line.context):
            continue
        if line.context in seen_contexts:
            continue
        amount = extract_interest_expense_amount(line)
        if amount is not None and amount > 0:
            candidates.append((line, amount))
            seen_contexts.add(line.context)
    if not candidates:
        return None

    total_candidates = [(line, amount) for line, amount in candidates if is_total_interest_expense_candidate(line.context)]
    if total_candidates:
        selected_line, amount = max(total_candidates, key=lambda item: finance_cost_interest_candidate_score(item[0], item[1]))
        return FinancialExpense(
            selected_line.receipt_no,
            amount,
            "이자비용(금융원가)",
            "금융비용 주석의 당기 이자비용 우선 사용",
        )

    groups: list[list[tuple[BorrowingLine, int]]] = []
    for line, amount in sorted(candidates, key=lambda item: (item[0].source_file, item[0].line_no)):
        if not groups:
            groups.append([(line, amount)])
            continue
        prev_line = groups[-1][-1][0]
        if line.source_file == prev_line.source_file and 0 <= line.line_no - prev_line.line_no <= 8:
            groups[-1].append((line, amount))
        else:
            groups.append([(line, amount)])

    preferred_groups = [group for group in groups if any(is_amortized_financial_liability_interest(line.context) for line, _ in group)]
    if preferred_groups:
        selected_group = max(preferred_groups, key=lambda group: interest_expense_group_amount(group)[0])
    else:
        group_sums = [interest_expense_group_amount(group)[0] for group in groups]
        largest_sum = max(group_sums)
        selected_group = next(group for group, total in zip(groups, group_sums) if total >= largest_sum * 0.5)
    amount, used_total_row = interest_expense_group_amount(selected_group)

    if any(is_amortized_financial_liability_interest(line.context) for line, _ in selected_group):
        memo = "상각후원가 금융부채 표의 이자비용 우선 사용"
    elif used_total_row:
        memo = "주석 금융원가 표의 차입 관련 이자비용 총계 행 사용"
    elif len(selected_group) == 1:
        memo = "주석 금융원가 표의 차입 관련 이자비용 사용"
    elif all(is_financial_expense_summary_context(line.context) for line, _ in selected_group):
        memo = "연결/별도 금융비용 주석 후보 중 큰 이자비용 금액 사용"
    else:
        memo = f"주석 금융원가 표의 차입 관련 이자비용 {len(selected_group)}개 항목 합산"
    receipt_no = lines[0].receipt_no if lines else ""
    return FinancialExpense(receipt_no, amount, "이자비용(금융원가)", memo)


def note_interest_disclosure_comparison(lines: list[BorrowingLine]) -> tuple[int, int, int, float] | None:
    component_comparisons: list[tuple[int, int, tuple[int, int, int, float]]] = []
    seen_contexts: set[str] = set()
    context_index = 0
    for line in lines:
        if line.keyword != NOTE_INTEREST_EXPENSE_KEYWORD:
            continue
        if is_explanatory_interest_expense_context(line.context):
            continue
        if line.context in seen_contexts:
            continue
        seen_contexts.add(line.context)
        component_comparison = note_interest_component_comparison(line)
        if component_comparison is not None:
            component_comparisons.append((interest_context_period_score(line.context), -context_index, component_comparison))
        context_index += 1

    if component_comparisons:
        return max(component_comparisons, key=lambda item: (item[0], item[1]))[2]
    return None


def is_explanatory_interest_expense_context(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    return "이자비용" in compact and any(
        keyword in compact
        for keyword in (
            "이자비용절감",
            "절감을위한",
            "조기상환",
            "상환에따라",
        )
    )


def is_total_interest_expense_candidate(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    if is_amortized_financial_liability_interest(text):
        return False
    if any(keyword in compact for keyword in ("기타금융부채이자비용", "차입금이자비용", "사채이자비용", "금융부채이자비용")):
        return False
    return "이자비용" in compact and any(keyword in compact for keyword in ("금융비용", "금융원가", "금융수익과금융비용", "금융수익및금융비용"))


def interest_expense_candidate_score(line: BorrowingLine, amount: int) -> tuple[int, int, int, int, int]:
    period_score = interest_context_period_score(line.context)
    consolidated_score = consolidated_context_score(line.context)
    full_context_score = 1 if line.line_no == 1 else 0
    return period_score, consolidated_score, -line.line_no, full_context_score, -amount


def finance_cost_interest_candidate_score(line: BorrowingLine, amount: int) -> tuple[int, int, int, int, int, int, int, int]:
    compact = re.sub(r"\s+", "", line.context)
    section_score = 2 if line.section == "금융비용 주석" else 0
    finance_cost_note_score = 1 if any(keyword in compact for keyword in ("금융비용", "금융원가", "금융비용의내역", "금융원가의내역")) else 0
    exact_interest_row_score = 1 if any(keyword in compact for keyword in ("이자비용(금융비용)", "이자비용(금융원가)", "구분당기전기이자비용")) else 0
    standalone_note_score = 1 if "연결회사" not in compact and "연결회사는" not in compact else 0
    source_detail_score = 1 if "_" in line.source_file else 0
    period_score = interest_context_period_score(line.context)
    full_context_score = 1 if line.line_no == 1 else 0
    return (
        section_score,
        finance_cost_note_score,
        exact_interest_row_score,
        standalone_note_score,
        source_detail_score,
        period_score,
        full_context_score,
        -amount,
    )


def consolidated_context_score(text: str) -> int:
    compact = re.sub(r"\s+", "", text)
    if "별도" in compact:
        return 0
    if "연결" in compact:
        return 2
    return 1


def note_interest_component_comparison(line: BorrowingLine) -> tuple[int, int, int, float] | None:
    total_matches = extract_interest_total_amount_matches(line.context, line.amount_unit)
    if not total_matches:
        return None

    comparisons: list[tuple[int, int, tuple[int, int, int, float]]] = []
    for match_index, (start, totals) in enumerate(total_matches):
        end = total_matches[match_index + 1][0] if match_index + 1 < len(total_matches) else len(line.context)
        segment = line.context[start:end]
        components = extract_interest_component_amount_groups(segment, line.amount_unit)
        if len(components) < 2:
            continue
        index = interest_amount_column_index(line.context, totals)
        if index >= len(totals):
            continue
        component_values = [amounts[index] for amounts in components if index < len(amounts)]
        if len(component_values) < 2:
            continue

        reference = totals[index]
        actual = sum(component_values)
        if reference <= 0 or abs(reference - actual) > max(1, reference * 0.05):
            continue
        diff = round(absolute_amount_diff(actual, reference))
        error_rate = diff / reference
        comparison = (reference, actual, diff, error_rate)
        comparisons.append((interest_context_period_score_at(line.context, start), -match_index, comparison))
    if not comparisons:
        return None
    return max(comparisons, key=lambda item: (item[0], item[1]))[2]


def interest_amount_column_index(text: str, totals: list[int]) -> int:
    compact = re.sub(r"\s+", "", text)
    if len(totals) >= 2 and "3개월" in compact and "누적" in compact:
        return 1
    return 0


def interest_context_period_score(text: str) -> int:
    compact = re.sub(r"\s+", "", text)
    total_index = compact.find("이자비용")
    prefix = compact[:total_index] if total_index >= 0 else compact
    return interest_period_prefix_score(prefix)


def interest_context_period_score_at(text: str, total_index: int) -> int:
    prefix = re.sub(r"\s+", "", text[:total_index])
    return interest_period_prefix_score(prefix)


def interest_period_prefix_score(prefix: str) -> int:
    markers: list[tuple[int, bool]] = []
    for marker in ("당분기", "당반기", "당기"):
        marker_index = prefix.rfind(marker)
        if marker_index >= 0:
            markers.append((marker_index, True))
    for marker in ("전분기", "전반기", "전기", "전년"):
        marker_index = prefix.rfind(marker)
        if marker_index >= 0:
            markers.append((marker_index, False))
    if markers:
        return 2 if max(markers)[1] else 0
    return 1


def extract_interest_total_amounts(text: str, unit: str) -> list[int]:
    return [amount for _, amounts in extract_interest_total_amount_matches(text, unit) for amount in amounts]


def extract_interest_total_amount_matches(text: str, unit: str) -> list[tuple[int, list[int]]]:
    compact_unit = re.sub(r"\s+", "", unit or "")
    amount_matches: list[tuple[int, list[int]]] = []
    for match in re.finditer(r"이자비용\s*(?:\(\s*(?:금융원가|금융비용)\s*\))?\s+((?:\(?-?\d{1,3}(?:,\d{3})+\)?\s*){1,3})", text):
        prefix = text[max(0, match.start() - 24) : match.start()]
        compact_prefix = re.sub(r"\s+", "", prefix)
        if any(keyword in compact_prefix for keyword in ("상각후원가", "기타금융부채", "차입금", "사채", "금융부채")):
            continue
        amounts = extract_amount_sequence(match.group(1), compact_unit)
        if amounts:
            amount_matches.append((match.start(), amounts))
    return amount_matches


def extract_interest_component_amount_groups(text: str, unit: str) -> list[list[int]]:
    compact_unit = re.sub(r"\s+", "", unit or "")
    label_patterns = (
        r"상각후원가\s*(?:측정\s*)?금융부채\s*이자비용",
        r"기타\s*금융부채\s*이자비용",
        r"차입금\s*이자비용",
        r"사채\s*이자비용",
        r"금융부채\s*이자비용",
    )
    groups: list[list[int]] = []
    seen_groups: set[tuple[int, ...]] = set()
    amount_sequence = r"((?:\(?-?\d{1,3}(?:,\d{3})+\)?\s*){1,3})"
    for label_pattern in label_patterns:
        for match in re.finditer(label_pattern + r"\s+" + amount_sequence, text):
            amounts = extract_amount_sequence(match.group(1), compact_unit)
            key = tuple(amounts)
            if amounts and key not in seen_groups:
                groups.append(amounts)
                seen_groups.add(key)
    return groups


def extract_amount_sequence(text: str, unit: str) -> list[int]:
    values: list[int] = []
    for raw in re.findall(r"\(?-?\d{1,3}(?:,\d{3})+\)?", text):
        value = parse_signed_amount(raw)
        if value is None:
            continue
        values.append(normalize_interest_amount_to_million(abs(value), unit))
    return values


def interest_expense_group_amount(group: list[tuple[BorrowingLine, int]]) -> tuple[int, bool]:
    amounts = [amount for _, amount in group]
    if len(amounts) >= 2:
        for amount in sorted(amounts, reverse=True):
            others = amounts.copy()
            others.remove(amount)
            if sum(others) and abs(amount - sum(others)) <= 1:
                return amount, True
        if all(is_financial_expense_summary_context(line.context) for line, _ in group):
            return max(amounts), False
    return sum(amounts), False


def is_financial_expense_summary_context(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    return "금융수익" in compact and "금융비용" in compact and "이자비용" in compact and "합계" in compact


def is_amortized_financial_liability_interest(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    return "상각후원가" in compact and "금융부채" in compact and "이자비용" in compact


def extract_interest_expense_amount(line: BorrowingLine) -> int | None:
    compact_context = re.sub(r"\s+", "", line.context)
    if is_total_interest_expense_candidate(line.context):
        amount = extract_total_interest_expense_amount(line.context, line.amount_unit, line.report_name)
        if amount is not None:
            return amount
    amount = extract_amount_after_interest_expense_label(line.context, line.amount_unit, line.report_name)
    if amount is not None:
        return amount
    if "이자비용" in compact_context and len([value for value in line.amounts if abs(value) > 0]) == 1:
        return extract_current_amount(line)
    return extract_current_amount(line)


def extract_total_interest_expense_amount(text: str, unit: str, report_name: str = "") -> int | None:
    compact_unit = re.sub(r"\s+", "", unit or "")
    explicit_unit = detect_amount_unit(text)
    if explicit_unit:
        compact_unit = re.sub(r"\s+", "", explicit_unit)
    amount_pattern = r"\(?-?\d{1,3}(?:,\d{3})+\)?"
    for match in re.finditer(r"(?:^|\s|[:：;])이자비용\s*(?:\(\s*(?:금융원가|금융비용)\s*\))?", text):
        prefix = re.sub(r"\s+", "", text[max(0, match.start() - 20) : match.start()])
        if any(keyword in prefix for keyword in ("상각후원가", "기타금융부채", "차입금", "사채", "금융부채")):
            continue
        tail = text[match.end() : match.end() + 160]
        amount_matches = re.findall(amount_pattern, tail)
        if not amount_matches:
            continue
        raw = amount_matches[interest_expense_amount_index(report_name, len(amount_matches))]
        value = parse_signed_amount(raw)
        if value is None:
            continue
        return normalize_interest_amount_to_million(abs(value), compact_unit)
    return None


def extract_amount_after_interest_expense_label(text: str, unit: str, report_name: str = "") -> int | None:
    compact_unit = re.sub(r"\s+", "", unit or "")
    label_patterns = (
        r"이자비용\s*\(\s*금융원가\s*\)",
        r"이자비용\s*\(\s*금융비용\s*\)",
        r"상각후원가\s*측정\s*금융부채\s*이자비용",
        r"기타\s*금융부채\s*이자비용",
        r"차입금\s*이자비용",
        r"사채\s*이자비용",
        r"금융부채\s*이자비용",
        r"이자비용",
    )
    amount_pattern = r"\(?-?\d{1,3}(?:,\d{3})+\)?"
    for label_pattern in label_patterns:
        for match in re.finditer(label_pattern, text):
            tail = text[match.end() : match.end() + 160]
            amount_matches = re.findall(amount_pattern, tail)
            if not amount_matches:
                continue
            raw = amount_matches[interest_expense_amount_index(report_name, len(amount_matches))]
            value = parse_signed_amount(raw)
            if value is None:
                continue
            return normalize_interest_amount_to_million(abs(value), compact_unit)
    return None


def interest_expense_amount_index(report_name: str, amount_count: int) -> int:
    if amount_count <= 1:
        return 0
    compact = re.sub(r"\s+", "", report_name)
    if "분기보고서" in compact or "반기보고서" in compact:
        return 1
    return 0


def is_borrowing_interest_expense_context(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    if any(keyword in compact for keyword in ("사채처분손실", "사채처분이익")):
        return False
    if "이자비용" not in compact:
        return False
    if any(keyword in compact for keyword in ("리스부채", "확정급여", "순확정", "충당부채", "복구충당", "계약부채")):
        return False
    exact_phrases = (
        "상각후원가측정금융부채이자비용",
        "기타금융부채이자비용",
        "차입금이자비용",
        "사채이자비용",
        "금융부채이자비용",
        "이자비용(금융원가)",
        "이자비용(금융비용)",
    )
    if any(phrase in compact for phrase in exact_phrases):
        return True
    return any(keyword in compact for keyword in ("차입금", "사채", "금융부채", "금융원가", "금융비용"))


def is_borrowing_amount_context(text: str) -> bool:
    compact = re.sub(r"\s+", "", text).lower()
    if not any(keyword in compact for keyword in ("차입금", "단기차입", "장기차입", "사채", "유동성장기", "금융기관차입", "borrow", "debt", "bond", "loan")):
        return False
    if not re.search(r"\d{1,3}(?:,\d{3})+", text):
        return False
    if is_current_period_zero_amount(text):
        return True
    if not any(keyword in compact for keyword in ("장부금액", "액면금액", "권면총액", "미상환잔액", "유동성", "비유동성", "차입금명칭")):
        return False
    exclusion_keywords = (
        "이자비용",
        "차입원가",
        "담보제공",
        "담보설정",
        "약정",
        "한도",
        "리스부채",
        "증분차입",
        "wacc",
        "가중평균자본비용",
        "netcash",
        "유동자금",
        "자산총액",
        "자산총계",
        "유동자산",
        "비유동자산",
        "전환가액",
        "전환권",
        "주식수",
        "스톡옵션",
        "상환",
    )
    return not any(keyword in compact for keyword in exclusion_keywords)


def is_total_amount_context(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    return any(keyword in compact for keyword in ("합계", "총계", "총차입금", "차입금계", "사채계"))


def is_statement_borrowing_row(line: BorrowingLine) -> bool:
    if line.source_file == "fnlttSinglAcntAll.json":
        return True
    if line.line_no > 2500:
        return False
    compact = re.sub(r"\s+", "", line.context)
    if not any(keyword in compact for keyword in ("단기차입금", "장기차입금", "유동성장기부채", "유동성사채", "차입부채", "사채")):
        return False
    return bool(re.search(r"\b\d{1,2}(?:,\s*\d{1,2}){1,8}\b", line.context))


def borrowing_amount_category(text: str) -> str:
    compact = re.sub(r"\s+", "", text)
    if "단기차입" in compact:
        return "단기차입금"
    if "유동성장기" in compact:
        return "유동성장기부채"
    if "장기차입" in compact:
        return "장기차입금"
    if "유동성사채" in compact:
        return "유동성사채"
    if "사채" in compact:
        return "사채"
    if "차입부채" in compact:
        return "차입부채"
    return borrowing_line_label(text)


def borrowing_line_label(text: str) -> str:
    cleaned = normalize_text(text)
    cleaned = re.sub(r"\d{1,3}(?:,\d{3})+", "", cleaned)
    cleaned = re.sub(r"\d{1,2}(?:\.\d{1,4})?\s*%", "", cleaned)
    return cleaned[:80]


def is_wacc_context(text: str) -> bool:
    upper_text = text.upper()
    return "WACC" in upper_text or "가중평균자본비용" in text or "가중평균 자본비용" in text


def is_average_borrowing_rate_context(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    if any(keyword in compact for keyword in ("자본비용", "증분차입", "리스부채", "자본화", "차입원가")):
        return False
    return (
        "평균차입이자율" in compact
        or "평균차입금리" in compact
        or "가중평균차입이자율" in compact
        or "가중평균차입금리" in compact
        or "평균사채이자율" in compact
        or "평균사채금리" in compact
    )


def is_comparison_rate_context(text: str) -> bool:
    return is_wacc_context(text) or is_average_borrowing_rate_context(text)


def is_borrowing_target_context(text: str) -> bool:
    return any(keyword in text for keyword in ("차입금", "사채", "금융부채", "담보제공"))


def is_valid_borrowing_rate_line(line: BorrowingLine) -> bool:
    if not line.interest_rates:
        return False
    if not is_borrowing_note_section(line.section):
        return False
    if not is_borrowing_target_context(line.context):
        return False
    return is_valid_borrowing_rate_context(line.context, allow_mixed_lease=True)


def is_borrowing_note_section(section: str) -> bool:
    compact = re.sub(r"\s+", "", section or "")
    return "차입금" in compact or "사채" in compact


def is_valid_borrowing_rate_context(text: str, allow_mixed_lease: bool = False) -> bool:
    compact = re.sub(r"\s+", "", text).lower()
    if is_non_interest_percent_context(text):
        return False
    if not any(keyword in compact for keyword in ("차입금", "사채", "차입", "borrow", "debt", "bond")):
        return False
    excluded = (
        "cashcoverage",
        "coverage",
        "유동자금/차입금",
        "자기자본",
        "담보제공",
        "이자율변동",
        "민감도",
        "가정하",
        "금융손익변동",
        "자본화",
        "차입원가",
        "건설중인자산",
        "증분차입",
        "할인율",
        "현금흐름할인",
        "공정가치",
        "조건부금융부채",
        "가중평균자본비용",
        "wacc",
    )
    if any(keyword in compact for keyword in excluded):
        return False
    if "리스부채" in compact and not allow_mixed_lease:
        return False
    return True


def is_non_interest_percent_context(text: str) -> bool:
    compact = re.sub(r"\s+", "", text).lower()
    if any(keyword in compact for keyword in ("표면", "만기이자", "액면이자", "연이자", "이자율", "이율", "금리", "interest", "rate")):
        return False
    non_interest_keywords = (
        "옵션",
        "조기상환청구권",
        "매도청구권",
        "전환청구",
        "전환가액",
        "전환가격",
        "행사가액",
        "전환권",
        "상환권",
        "액면금액의",
        "발행금액",
        "유상증자",
        "자본총계",
        "소유지분율",
        "지분율",
        "보통주",
        "우선주",
    )
    return "%" in text and any(keyword in compact for keyword in non_interest_keywords)


def is_reasonable_interest_rate(rate: float) -> bool:
    return 0 < rate <= 0.50


def estimation_interest_rates(line: BorrowingLine) -> list[float]:
    if any(rate > 0.50 for rate in line.interest_rates):
        return []
    return [rate for rate in line.interest_rates if is_reasonable_interest_rate(rate)]


def weighted_average_interest_rate(
    lines: list[BorrowingLine],
    reference_amount: int,
    period_key: tuple[int, int] | None,
    default_months: int,
) -> float | None:
    weighted_sum = 0.0
    weight_total = 0.0
    amount_total = 0
    default_days = report_period_days(period_key, default_months)
    seen_contexts: set[str] = set()
    for line in lines:
        key = normalize_text(line.context)
        if key in seen_contexts:
            continue
        seen_contexts.add(key)
        line_rate = representative_interest_rate_for_weighting(line)
        if line_rate is None:
            continue
        amount = extract_current_amount(line)
        if amount is None or amount <= 0:
            continue
        days = interest_rate_weight_days(line.context, period_key, default_days)
        if days <= 0:
            continue
        weighted_sum += line_rate * amount * days
        weight_total += amount * days
        amount_total += amount
    if not weight_total:
        return None
    if reference_amount > 0 and amount_total < reference_amount * 0.5:
        return None
    return weighted_sum / weight_total


def report_period_days(period_key: tuple[int, int] | None, default_months: int) -> int:
    if not period_key:
        return round(365 * max(1, min(default_months, 12)) / 12)
    start, end = report_period_dates(period_key)
    return (end - start).days + 1


def report_period_dates(period_key: tuple[int, int]) -> tuple[date, date]:
    year, month = period_key
    month = max(1, min(month, 12))
    end_day = calendar.monthrange(year, month)[1]
    return date(year, 1, 1), date(year, month, end_day)


def interest_rate_weight_days(text: str, period_key: tuple[int, int] | None, default_days: int) -> int:
    if period_key:
        period_start, period_end = report_period_dates(period_key)
        dates = extract_dates(text)
        if dates:
            start = max(period_start, min(dates))
            end = min(period_end, max(dates))
            if start <= end:
                return (end - start).days + 1
            return 0
    months = interest_rate_weight_months(text, max(1, round(default_days / 30.4)))
    return round(365 * months / 12)


def extract_dates(text: str) -> list[date]:
    dates: list[date] = []
    for year, month, day in re.findall(r"(\d{4})[-./년]\s*(\d{1,2})[-./월]\s*(\d{1,2})", text):
        try:
            dates.append(date(int(year), int(month), int(day)))
        except ValueError:
            continue
    return dates


def interest_rate_weight_months(text: str, default_months: int) -> int:
    compact = re.sub(r"\s+", "", text)
    month_matches = [int(value) for value in re.findall(r"(\d{1,2})개월", compact)]
    month_matches = [value for value in month_matches if 1 <= value <= 12]
    if month_matches:
        return max(month_matches)
    if "당분기" in compact or "3개월" in compact:
        return 3
    if "당반기" in compact or "6개월" in compact:
        return 6
    if "3분기" in compact or "9개월" in compact:
        return 9
    return max(1, min(default_months, 12))


def representative_interest_rate_for_weighting(line: BorrowingLine) -> float | None:
    if any(rate > 0.50 for rate in line.interest_rates):
        return None
    rates = [rate for rate in line.interest_rates if 0 <= rate <= 0.50]
    if not rates or not any(rate > 0 for rate in rates):
        return None
    return (min(rates) + max(rates)) / 2

def is_special_bond_context(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    return any(
        keyword in compact
        for keyword in (
            "전환사채",
            "전환상환우선",
            "신주인수권부사채",
            "신주인수권",
            "교환사채",
            "교환권",
            "CB",
            "BW",
            "EB",
        )
    )


def is_special_bond_amount_context(text: str) -> bool:
    if not is_special_bond_context(text):
        return False
    compact = re.sub(r"\s+", "", text)
    if not re.search(r"\d{1,3}(?:,\d{3})+", text):
        return False
    exclusions = (
        "미상환전환사채발행현황",
        "미상환신주인수권부사채",
        "삽입",
        ".dsl",
        "표시되어야할권리",
        "정기주주총회",
        "전환가액",
        "전환권",
        "주식수",
        "스톡옵션",
    )
    return not any(keyword in compact for keyword in exclusions)


def special_bond_review_memo(mention_count: int, amount: int) -> str:
    if amount > 0:
        return "전환사채/신주인수권부사채 등 특수사채 금액 후보 검출"
    if mention_count > 0:
        return "특수사채 관련 표제목/정관 문구는 검출되었으나 잔액 후보 없음"
    return "특수사채 관련 문구 없음"


def extract_rate(text: str) -> float | None:
    rates = [float(x) for x in re.findall(r"(\d{1,2}(?:\.\d{1,4})?)\s*%", text)]
    return sum(rates) / len(rates) if rates else None


def extract_rate_values(text: str) -> list[float]:
    rates: list[float] = []
    for raw in re.findall(r"(?<![\d.])(\d{1,2}(?:\.\d{1,4})?)\s*%(?!\d)", text):
        try:
            rates.append(float(raw) / 100)
        except ValueError:
            continue
    for left, right in re.findall(r"(?<![\d,])(\d{1,2}\.\d{1,4})\s*(?:~|-|∼|～)\s*(\d{1,2}\.\d{1,4})(?![\d,])", text):
        for raw in (left, right):
            try:
                value = float(raw) / 100
            except ValueError:
                continue
            if value not in rates:
                rates.append(value)
    if is_decimal_rate_context(text):
        for raw in re.findall(r"(?<![\d,])0\.\d{2,5}(?![\d,])", text):
            try:
                value = float(raw)
            except ValueError:
                continue
            if is_reasonable_interest_rate(value) and value not in rates:
                rates.append(value)
    return rates


def is_decimal_rate_context(text: str) -> bool:
    compact = re.sub(r"\s+", "", text).lower()
    return any(
        keyword in compact
        for keyword in (
            "이자율",
            "이율",
            "금리",
            "사채",
            "차입금",
            "차입",
            "wacc",
            "자본비용",
            "자본화이자율",
            "차입이자율",
            "차입금리",
            "interest",
            "rate",
        )
    )


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


def extract_amount_values(text: str) -> list[int]:
    values: list[int] = []
    for match in re.finditer(r"(?<![\d.])(\(?-?\d{1,3}(?:,\d{3})+\)?)(?!\s*%)", text):
        value = parse_signed_amount(match.group(1))
        if value is None:
            continue
        values.append(value)
    if values:
        return values

    if detect_amount_unit(text):
        for match in re.finditer(r"(?<![\d.])(\(?-?\d{1,12}\)?)(?![\d.]|\s*%)", text):
            value = parse_signed_amount(match.group(1))
            if value is None:
                continue
            if value == 0:
                continue
            values.append(value)
    return values


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


def note_reference(line: BorrowingLine) -> str:
    if line.source_file == "fnlttSinglAcntAll.json":
        return "재무제표 API 항목 참조"

    normalized = normalize_text(line.context)
    match = re.search(r"(?:^|\s)(\d{1,3})\.\s*([가-힣A-Za-z0-9()/·ㆍ\s]{1,40})", normalized)
    if match:
        note_no = match.group(1)
        title = normalize_text(match.group(2)).strip()
        return f"연결재무제표 주석 {note_no}번 {title} 항목 참조"

    reference = line.section or line.keyword or "관련 주석"
    return f"연결재무제표 주석 {reference} 참조"


def save_workbook(
    path: Path,
    reports: list[DartReport],
    lines: list[BorrowingLine],
    tests: list[dict],
    issues: list[ExtractionIssue] | None = None,
) -> None:
    issues = issues or []
    financial_statement_lines = [line for line in lines if line.source_file == "fnlttSinglAcntAll.json"]
    borrowing_filter_lines = [
        line
        for line in lines
        if line.keyword != NOTE_INTEREST_EXPENSE_KEYWORD and line.source_file != "fnlttSinglAcntAll.json"
    ]
    interest_expense_lines = [line for line in lines if line.keyword == NOTE_INTEREST_EXPENSE_KEYWORD]
    filter_headers = ["회사명", "보고서명", "키워드", "주석구분", "이자율", "원문검출금액", "금액단위", "적용금액", "문맥"]

    def filter_row(line: BorrowingLine) -> list:
        return [
            line.corp_name,
            line.report_name,
            line.keyword,
            line.section,
            format_rates(line.interest_rates),
            ", ".join(str(amount) for amount in line.amounts),
            line.amount_unit,
            line.max_amount,
            note_reference(line),
        ]

    sheets = [
        (
            "정기보고서목록",
            ["회사명", "보고서명", "종목코드"],
            [[r.corp_name, r.report_name, r.stock_code] for r in reports],
            {},
        ),
        (
            "차입금필터링",
            filter_headers,
            [filter_row(line) for line in borrowing_filter_lines],
            {8: 2},
        ),
        (
            "재무상태표차입잔액",
            filter_headers,
            [filter_row(line) for line in financial_statement_lines],
            {8: 2},
        ),
        (
            "이자비용필터링",
            filter_headers,
            [filter_row(line) for line in interest_expense_lines],
            {8: 2},
        ),
        (
            "이자율오버롤테스트",
            [
                "회사명",
                "보고서명",
                "판정",
                "판정근거",
                "예상이자비용",
                "실제이자비용",
                "이자비용차이",
                "이자비용계정",
                "평균차입금",
                "검출평균이자율",
                "최저차입이자율",
                "최고차입이자율",
                "대상기간(개월)",
                "검출금액합계",
                "증감비교대상",
                "전년동기대비변동률",
                "전년동기대비증감",
                "금액단위",
                "금액산정방식",
                "차입금문맥수",
                "이자비용산정메모",
            ],
            [
                [
                    t["corp_name"],
                    t["report_name"],
                    t["judgment"],
                    t["judgment_basis"],
                    t["expected_interest_expense"],
                    t["actual_interest_expense"],
                    t["interest_expense_diff"],
                    t["interest_expense_account"],
                    t["average_borrowing_balance"],
                    t["avg_rate"],
                    t["min_rate"],
                    t["max_rate"],
                    t["period_months"],
                    t["amount_sum"],
                    t["amount_comparison_label"],
                    t["amount_change"],
                    t["amount_diff"],
                    t["amount_unit"],
                    t["amount_method"],
                    t["context_count"],
                    t["interest_expense_memo"],
                ]
                for t in tests
            ],
            {5: 2, 6: 2, 7: 2, 9: 2, 10: 3, 11: 3, 12: 3, 13: 2, 14: 2, 16: 3, 17: 2, 20: 2},
        ),
    ]
    sheets.append(
        (
            "추출오류",
            ["회사명", "보고서명", "접수일", "접수번호", "단계", "오류"],
            [[i.corp_name, i.report_name, i.receipt_date, i.receipt_no, i.step, i.message] for i in issues],
            {},
        )
    )

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types(len(sheets)))
        archive.writestr("_rels/.rels", root_rels())
        archive.writestr("xl/workbook.xml", workbook_xml([s[0] for s in sheets]))
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels(len(sheets)))
        archive.writestr("xl/styles.xml", styles_xml())
        for index, (_, headers, rows, column_styles) in enumerate(sheets, start=1):
            archive.writestr(f"xl/worksheets/sheet{index}.xml", sheet_xml(headers, rows, column_styles))


def sheet_xml(headers: list[str], rows: list[list[str]], column_styles: dict[int, int] | None = None) -> str:
    column_styles = column_styles or {}
    lines = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>']
    widths = [16, 28, 12, 16, 14, 18, 14, 18, 16, 16, 16, 12, 16, 16, 16, 16, 16, 16, 16, 16, 14, 16, 34, 12, 80]
    cols = "".join(
        f'<col min="{idx}" max="{idx}" width="{width}" customWidth="1"/>'
        for idx, width in enumerate(widths[: max(len(headers), 1)], start=1)
    )
    lines.append(f'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><cols>{cols}</cols><sheetData>')
    lines.append(row_xml(1, headers, True, column_styles))
    for idx, row in enumerate(rows, start=2):
        lines.append(row_xml(idx, row, False, column_styles))
    lines.append("</sheetData></worksheet>")
    return "".join(lines)


def row_xml(row_no: int, values: list[str], header: bool, column_styles: dict[int, int]) -> str:
    cells = []
    for idx, value in enumerate(values, start=1):
        ref = f"{column_name(idx)}{row_no}"
        style_id = 1 if header else column_styles.get(idx, 0)
        style = f' s="{style_id}"' if style_id else ""
        if not header and isinstance(value, (int, float)) and value is not None:
            cells.append(f'<c r="{ref}"{style}><v>{value}</v></c>')
        elif value is None:
            cells.append(f'<c r="{ref}"{style}/>')
        else:
            safe = html.escape(str(value or ""), quote=False)
            cells.append(f'<c r="{ref}" t="inlineStr"{style}><is><t xml:space="preserve">{safe}</t></is></c>')
    return f'<row r="{row_no}">{"".join(cells)}</row>'


def column_name(index: int) -> str:
    name = ""
    while index:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name


def content_types(sheet_count: int = 3) -> str:
    worksheet_overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for i in range(1, sheet_count + 1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        f"{worksheet_overrides}"
        "</Types>"
    )


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
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><numFmts count="2"><numFmt numFmtId="164" formatCode="#,##0"/><numFmt numFmtId="165" formatCode="0.00%"/></numFmts><fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts><fills count="1"><fill><patternFill patternType="none"/></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="4"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/><xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/><xf numFmtId="165" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs></styleSheet>"""


def money(value: float | None) -> str:
    return str(round(value)) if value is not None else ""


def percent(value: float | None) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".") if value is not None else ""


def format_rates(values: list[float]) -> str:
    return ", ".join(f"{value * 100:.2f}%" for value in values)


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


def configure_windows_dpi() -> None:
    if os.name != "nt":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


class DartOtApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.config = load_config()
        self.title("DART-OT")
        self.geometry("1280x820")
        self.minsize(1180, 760)
        self.selected_corp: CorpInfo | None = None
        self.search_results: list[CorpInfo] = []
        self.output_file: Path | None = None
        self.tk.call("tk", "scaling", 1.35)
        self.entry_font = tkfont.Font(self, family="맑은 고딕", size=12)
        write_ui_debug(f"app_start scaling={self.tk.call('tk', 'scaling')} entry_font={self.entry_font.actual()}")

        self.api_key_var = tk.StringVar(value=self.config.get("api_key", ""))
        self.save_api_key_var = tk.BooleanVar(value=bool(self.config.get("api_key", "")))
        self.company_var = tk.StringVar(value="삼성전자")
        self.company_entry: tk.Entry | None = None
        self.stock_var = tk.StringVar()
        self.corp_code_var = tk.StringVar()
        self.begin_year_var = tk.StringVar(value=str(datetime.now().year - 4))
        self.end_year_var = tk.StringVar(value=str(datetime.now().year))
        self.status_var = tk.StringVar(value="DART API 키와 회사명을 입력한 뒤 회사 검색을 눌러 주세요.")
        self.summary_var = tk.StringVar(value="정기보고서: -    차입금 공시: -    오버롤 테스트: -")

        self._build()

    def _build(self) -> None:
        self.configure(bg="#f6f8fb")
        for font_name in ("TkDefaultFont", "TkTextFont", "TkFixedFont", "TkMenuFont"):
            try:
                tkfont.nametofont(font_name).configure(family="맑은 고딕", size=12)
            except tk.TclError:
                pass
        style = ttk.Style(self)
        style.configure("TFrame", background="#f6f8fb")
        style.configure("Panel.TFrame", background="#ffffff")
        style.configure("TLabel", background="#f6f8fb", font=("맑은 고딕", 12))
        style.configure("Panel.TLabel", background="#ffffff", font=("맑은 고딕", 12))
        style.configure("Title.TLabel", background="#f6f8fb", font=("맑은 고딕", 22, "bold"))
        style.configure("TButton", font=("맑은 고딕", 12), padding=(8, 6))
        style.configure("Accent.TButton", font=("맑은 고딕", 12, "bold"), padding=(8, 8))
        style.configure("TCheckbutton", background="#ffffff", font=("맑은 고딕", 12))
        style.configure("Treeview", font=("맑은 고딕", 11), rowheight=30)
        style.configure("Treeview.Heading", font=("맑은 고딕", 11, "bold"))

        root = ttk.Frame(self, padding=24)
        root.pack(fill="both", expand=True)

        ttk.Label(root, text="DART-OT", style="Title.TLabel").pack(anchor="w")
        ttk.Label(root, text="DART 공시 기반 차입금 필터링 및 이자율 오버롤 테스트 파일 생성 도구").pack(anchor="w", pady=(4, 20))

        body = ttk.Frame(root)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=0)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        left = ttk.Frame(body, style="Panel.TFrame", padding=20)
        left.grid(row=0, column=0, sticky="ns", padx=(0, 18))
        right = ttk.Frame(body, style="Panel.TFrame", padding=20)
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(1, weight=1)
        right.columnconfigure(0, weight=1)

        self._entry(left, "DART API 키", self.api_key_var, show="*")
        ttk.Checkbutton(left, text="API 키 저장", variable=self.save_api_key_var).pack(anchor="w", pady=(0, 8))
        self._company_entry(left)
        self._entry(left, "종목코드", self.stock_var)
        self._entry(left, "DART 고유번호", self.corp_code_var)

        year_frame = ttk.Frame(left, style="Panel.TFrame")
        year_frame.pack(fill="x", pady=(4, 0))
        year_frame.columnconfigure(0, weight=1)
        year_frame.columnconfigure(1, weight=1)
        self._entry(year_frame, "시작연도", self.begin_year_var, width=12, grid_col=0)
        self._entry(year_frame, "종료연도", self.end_year_var, width=12, grid_col=1)

        ttk.Button(left, text="회사 검색", command=self.search_company, style="Accent.TButton").pack(fill="x", pady=(18, 8))
        ttk.Button(left, text="엑셀 파일 생성", command=self.run_export, style="Accent.TButton").pack(fill="x")

        ttk.Label(right, text="회사 선택", style="Panel.TLabel", font=("맑은 고딕", 14, "bold")).grid(row=0, column=0, sticky="w")
        columns = ("corp_name", "stock_code", "corp_code")
        self.tree = ttk.Treeview(right, columns=columns, show="headings", height=14)
        self.tree.heading("corp_name", text="회사명")
        self.tree.heading("stock_code", text="종목코드")
        self.tree.heading("corp_code", text="DART 고유번호")
        self.tree.column("corp_name", width=430)
        self.tree.column("stock_code", width=140, anchor="center")
        self.tree.column("corp_code", width=170, anchor="center")
        self.tree.grid(row=1, column=0, sticky="nsew", pady=(10, 12))
        self.tree.bind("<<TreeviewSelect>>", self.select_company)

        status = ttk.Label(right, textvariable=self.status_var, style="Panel.TLabel", wraplength=820, justify="left")
        status.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(right, textvariable=self.summary_var, style="Panel.TLabel").grid(row=3, column=0, sticky="w")

        buttons = ttk.Frame(right, style="Panel.TFrame")
        buttons.grid(row=4, column=0, sticky="ew", pady=(14, 0))
        ttk.Button(buttons, text="결과 파일 열기", command=self.open_output).pack(side="left")
        ttk.Button(buttons, text="저장 폴더 열기", command=self.open_output_dir).pack(side="left", padx=(8, 0))

    def _entry(self, parent, label: str, variable: tk.StringVar, show: str | None = None, width: int | None = None, grid_col: int | None = None) -> None:
        container = ttk.Frame(parent, style="Panel.TFrame")
        if grid_col is None:
            container.pack(fill="x", pady=(0, 8))
        else:
            container.grid(row=0, column=grid_col, sticky="ew", padx=(0 if grid_col == 0 else 6, 6 if grid_col == 0 else 0))
        ttk.Label(container, text=label, style="Panel.TLabel").pack(anchor="w", pady=(0, 4))
        entry = tk.Entry(
            container,
            textvariable=variable,
            show=show or "",
            width=width or 20,
            font=self.entry_font,
            relief="solid",
            bd=1,
            highlightthickness=1,
            highlightbackground="#cbd5e1",
            highlightcolor="#0f766e",
            insertwidth=1,
        )
        entry.pack(fill="x", ipady=5)

    def _company_entry(self, parent) -> None:
        container = ttk.Frame(parent, style="Panel.TFrame")
        container.pack(fill="x", pady=(0, 8))
        ttk.Label(container, text="회사명", style="Panel.TLabel").pack(anchor="w", pady=(0, 4))
        self.company_entry = tk.Entry(
            container,
            textvariable=self.company_var,
            width=20,
            font=self.entry_font,
            relief="solid",
            bd=1,
            highlightthickness=1,
            highlightbackground="#cbd5e1",
            highlightcolor="#0f766e",
            insertwidth=1,
        )
        self.company_entry.pack(fill="x", ipady=5)
        self.company_entry.bind("<Return>", lambda _event: self.search_company())
        self.company_entry.bind("<KeyRelease>", self.log_company_entry_state)
        self.log_company_entry_state()

    def log_company_entry_state(self, _event=None) -> None:
        if self.company_entry is None:
            return
        try:
            write_ui_debug(
                "company_entry "
                f"class={self.company_entry.winfo_class()} "
                f"height={self.company_entry.winfo_height()} "
                f"font={self.entry_font.actual()} "
                f"scaling={self.tk.call('tk', 'scaling')} "
                f"text_len={len(self.company_var.get())}"
            )
        except Exception:
            pass

    def focus_next_company_widget(self):
        if self.company_entry is not None:
            self.company_entry.tk_focusNext().focus()
        return "break"

    def get_company_name(self) -> str:
        return self.company_var.get().strip()

    def set_company_name(self, value: str) -> None:
        self.company_var.set(value)

    def search_company(self) -> None:
        api_key = self.api_key_var.get().strip()
        if not api_key:
            messagebox.showwarning("입력 필요", "DART API 키를 입력해 주세요.")
            return

        self.selected_corp = None
        self.corp_code_var.set("")
        if self.get_company_name():
            self.stock_var.set("")
        self.persist_api_key()
        self.status_var.set("회사 목록을 조회하고 있습니다.")
        self._set_buttons_state("disabled")
        threading.Thread(target=self._search_company_worker, args=(api_key,), daemon=True).start()

    def _search_company_worker(self, api_key: str) -> None:
        try:
            client = DartClient(api_key)
            results = client.search_corps(self.get_company_name(), self.stock_var.get())
            self.after(0, lambda: self._show_search_results(results))
        except Exception as exc:
            self.after(0, lambda: self._show_error(f"회사 검색 중 오류가 발생했습니다: {exc}"))

    def _show_search_results(self, results: list[CorpInfo]) -> None:
        self._set_buttons_state("normal")
        self.search_results = results
        self.selected_corp = None
        for item in self.tree.get_children():
            self.tree.delete(item)
        for idx, corp in enumerate(results):
            self.tree.insert("", "end", iid=str(idx), values=(corp.corp_name, corp.stock_code, corp.corp_code))

        if results:
            self.tree.selection_set("0")
            self.tree.focus("0")
            self.select_company()
            self.status_var.set(f"검색 결과 {len(results)}건이 있습니다. 정확한 회사를 선택한 뒤 엑셀 파일 생성을 눌러 주세요.")
        else:
            self.status_var.set("검색 결과가 없습니다. 회사명 또는 종목코드를 다시 확인해 주세요.")

    def select_company(self, _event=None) -> None:
        selected = self.tree.selection()
        if not selected:
            return
        index = int(selected[0])
        self.selected_corp = self.search_results[index]
        self.set_company_name(self.selected_corp.corp_name)
        self.stock_var.set(self.selected_corp.stock_code)
        self.corp_code_var.set(self.selected_corp.corp_code)

    def run_export(self) -> None:
        api_key = self.api_key_var.get().strip()
        if not api_key:
            messagebox.showwarning("입력 필요", "DART API 키를 입력해 주세요.")
            return
        if self.selected_corp is not None and self.get_company_name() != self.selected_corp.corp_name:
            self.selected_corp = None
            self.corp_code_var.set("")
        if self.selected_corp is None and not self.corp_code_var.get().strip():
            messagebox.showwarning("회사 선택 필요", "회사 검색 후 목록에서 회사를 선택해 주세요.")
            return

        self.persist_api_key()
        self.status_var.set("DART 공시를 조회하고 엑셀 파일을 생성하고 있습니다. 보고서 수에 따라 시간이 걸릴 수 있습니다.")
        self._set_buttons_state("disabled")
        threading.Thread(target=self._run_export_worker, daemon=True).start()

    def persist_api_key(self) -> None:
        if self.save_api_key_var.get():
            save_config({"api_key": self.api_key_var.get().strip()})
        elif CONFIG_PATH.exists():
            try:
                CONFIG_PATH.unlink()
            except OSError:
                pass

    def _run_export_worker(self) -> None:
        payload = {
            "apiKey": self.api_key_var.get(),
            "companyName": self.get_company_name(),
            "stockCode": self.stock_var.get(),
            "corpCode": self.corp_code_var.get(),
            "beginYear": self.begin_year_var.get(),
            "endYear": self.end_year_var.get(),
        }
        try:
            result = run_report(payload)
            self.after(0, lambda: self._show_export_result(result))
        except Exception as exc:
            self.after(0, lambda: self._show_error(f"실행 중 오류가 발생했습니다: {exc}"))

    def _show_export_result(self, result: dict) -> None:
        self._set_buttons_state("normal")
        self.status_var.set(result.get("message", "작업이 완료되었습니다."))
        self.summary_var.set(
            f"정기보고서: {result.get('reportCount', 0)}    "
            f"차입금 공시: {result.get('noteCount', 0)}    "
            f"오버롤 테스트: {result.get('testCount', 0)}"
        )
        if result.get("ok") and result.get("file"):
            self.output_file = OUTPUT_DIR / result["file"]
            messagebox.showinfo("완료", f"엑셀 파일을 생성했습니다.\n{self.output_file}")

    def _show_error(self, message: str) -> None:
        self._set_buttons_state("normal")
        self.status_var.set(message)
        messagebox.showerror("오류", message)

    def _set_buttons_state(self, state: str) -> None:
        for child in self.winfo_children():
            self._set_state_recursive(child, state)

    def _set_state_recursive(self, widget, state: str) -> None:
        if isinstance(widget, ttk.Button):
            widget.configure(state=state)
        for child in widget.winfo_children():
            self._set_state_recursive(child, state)

    def open_output(self) -> None:
        if self.output_file and self.output_file.exists():
            os.startfile(self.output_file)
        else:
            messagebox.showinfo("결과 없음", "아직 생성된 결과 파일이 없습니다.")

    def open_output_dir(self) -> None:
        OUTPUT_DIR.mkdir(exist_ok=True)
        os.startfile(OUTPUT_DIR)


def main() -> None:
    configure_windows_dpi()
    app = DartOtApp()
    app.mainloop()


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
        <div class="step">1. 최근 5년치 정기보고서 목록을 수집합니다.</div>
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
