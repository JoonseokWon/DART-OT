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
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
import tkinter.font as tkfont
from xml.etree import ElementTree


ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "outputs"
CONFIG_PATH = ROOT / ".dart_ot_config.json"
BORROWING_KEYWORDS = ["차입금", "사채", "금융부채", "이자율", "이율", "금리", "가중평균", "담보제공"]


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
    interest_rates: list[float]
    amounts: list[int]
    amount_unit: str
    max_amount: int
    source_file: str
    line_no: int
    context: str


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
            exact = [corp for corp in corps if corp.corp_name.lower() == needle]
            if exact:
                return sorted(exact, key=lambda c: (not bool(c.stock_code), c.corp_name))[0]
            for corp in corps:
                if needle in corp.corp_name.lower():
                    return corp

        return None

    def search_corps(self, company_name: str, stock_code: str = "", limit: int = 100) -> list[CorpInfo]:
        corps = self.get_corp_codes()
        if stock_code.strip():
            normalized = stock_code.strip().zfill(6)
            return [corp for corp in corps if corp.stock_code == normalized][:limit]

        needle = company_name.strip().lower()
        if not needle:
            return []

        matches = [corp for corp in corps if needle in corp.corp_name.lower()]
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

    def extract_financial_borrowing_line(self, corp_code: str, report: DartReport) -> BorrowingLine | None:
        bsns_year = report_business_year(report)
        reprt_code = report_code(report)
        if not bsns_year or not reprt_code:
            return None

        rows: list[dict] = []
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
                rows = data.get("list", [])
                break

        selected: list[tuple[str, int]] = []
        for row in rows:
            if row.get("sj_nm") != "재무상태표":
                continue
            account = normalize_text(row.get("account_nm", ""))
            if not is_financial_borrowing_account(account):
                continue
            amount = parse_dart_amount(row.get("thstrm_amount", ""))
            if amount is None or amount <= 0:
                continue
            selected.append((account, amount // 1_000_000))

        if not selected:
            return None

        total = sum(amount for _, amount in selected)
        detail = ", ".join(f"{name} {amount:,}" for name, amount in selected)
        context = f"재무제표API 차입금 합계 {total:,}백만원 ({detail})"
        return BorrowingLine(
            report.corp_name,
            report.report_name,
            report.receipt_date,
            report.receipt_no,
            "재무제표API",
            [],
            [total],
            "백만원",
            total,
            "fnlttSinglAcntAll.json",
            0,
            context,
        )

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
            for line_no, context, amount_unit in extract_text_records(text):
                if not context:
                    continue
                keyword = next((k for k in BORROWING_KEYWORDS if k in context), "")
                if not keyword:
                    continue
                rates = extract_rate_values(context)
                amounts = extract_amount_values(context)
                rows.append(
                    BorrowingLine(
                        report.corp_name,
                        report.report_name,
                        report.receipt_date,
                        report.receipt_no,
                        keyword,
                        rates,
                        amounts,
                        amount_unit,
                        max((abs(a) for a in amounts), default=0),
                        Path(source_file).name or f"{report.receipt_no}.xml",
                        line_no,
                        context[:1200],
                    )
                )
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
    begin_year = int(payload.get("beginYear") or now_year - 9)
    end_year = int(payload.get("endYear") or now_year)

    reports = client.get_reports(corp.corp_code, begin_year, end_year)
    borrowing_lines: list[BorrowingLine] = []
    for report in reports:
        try:
            financial_line = client.extract_financial_borrowing_line(corp.corp_code, report)
            if financial_line is not None:
                borrowing_lines.append(financial_line)
        except Exception:
            pass
        try:
            borrowing_lines.extend(client.extract_borrowing_lines(report))
        except Exception:
            continue

    tests = build_overall_tests(reports, borrowing_lines)
    OUTPUT_DIR.mkdir(exist_ok=True)
    file_name = f"DART_OT_{safe_filename(corp.corp_name)}_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
    save_workbook(OUTPUT_DIR / file_name, reports, borrowing_lines, tests)

    return {
        "ok": True,
        "message": f"{corp.corp_name} 정기보고서 {len(reports)}건, 차입금 관련 문맥 {len(borrowing_lines)}건을 정리했습니다.",
        "file": file_name,
        "reportCount": len(reports),
        "noteCount": len(borrowing_lines),
        "testCount": len(tests),
    }


def fail(message: str) -> dict:
    return {"ok": False, "message": message, "file": None, "reportCount": 0, "noteCount": 0, "testCount": 0}


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
    }
    if compact in exact_accounts:
        return True
    if any(excluded in compact for excluded in ("리스", "이자", "파생", "충당", "순확정")):
        return False
    return compact.endswith("차입금") or compact.endswith("사채")


def clean_context(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value)
    return normalize_text(value)


def extract_text_records(text: str) -> list[tuple[int, str, str]]:
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

    records: list[tuple[int, str, str]] = []
    current_unit = ""
    for _, line_no, context in sorted(raw_records, key=lambda item: item[0]):
        detected_unit = detect_amount_unit(context)
        if detected_unit:
            current_unit = detected_unit
        records.append((line_no, context, current_unit))
    return records


def detect_amount_unit(text: str) -> str:
    compact = re.sub(r"\s+", "", text)
    match = re.search(r"단위[:：]?(백만원|천원|억원|원|USD|천USD|백만USD|미화천달러|미화백만달러)", compact, flags=re.IGNORECASE)
    if match:
        return match.group(1)
    for unit in ("백만원", "천원", "억원"):
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


def build_overall_tests(reports: list[DartReport], lines: list[BorrowingLine]) -> list[dict]:
    by_receipt: dict[str, list[BorrowingLine]] = {}
    for line in lines:
        by_receipt.setdefault(line.receipt_no, []).append(line)

    rows: list[dict] = []
    prev_amount_sum: int | None = None
    for report in sorted(reports, key=lambda r: (r.receipt_date, r.report_name)):
        report_lines = by_receipt.get(report.receipt_no, [])
        comparison_rate_lines = [line for line in report_lines if is_comparison_rate_context(line.context)]
        test_lines = [line for line in report_lines if not is_comparison_rate_context(line.context)]
        target_rate_lines = [line for line in test_lines if is_valid_borrowing_rate_context(line.context)]
        avg_borrowing_rate_lines = [line for line in comparison_rate_lines if is_average_borrowing_rate_context(line.context)]
        wacc_lines = [line for line in comparison_rate_lines if is_wacc_context(line.context)]
        rates = [rate for line in target_rate_lines for rate in line.interest_rates if is_reasonable_interest_rate(rate)]
        avg_borrowing_rates = [rate for line in avg_borrowing_rate_lines for rate in line.interest_rates]
        wacc_rates = [rate for line in wacc_lines for rate in line.interest_rates]
        benchmark_rates = avg_borrowing_rates
        benchmark_label = "평균차입이자율" if avg_borrowing_rates else ""
        amount_sum, max_amount, amount_method, amount_used_lines = calculate_borrowing_amount(test_lines)
        amount_diff = amount_sum - prev_amount_sum if prev_amount_sum is not None else None
        amount_change = amount_diff / prev_amount_sum if amount_diff is not None and prev_amount_sum not in (None, 0) else None
        special_bond_lines = [line for line in amount_used_lines if is_special_bond_context(line.context)]
        special_bond_amount = sum(extract_current_amount(line) or 0 for line in special_bond_lines)
        special_bond_ratio = special_bond_amount / amount_sum if amount_sum else None
        min_rate = min(rates) if rates else None
        avg_rate = sum(rates) / len(rates) if rates else None
        max_rate = max(rates) if rates else None
        avg_benchmark_rate = sum(benchmark_rates) / len(benchmark_rates) if benchmark_rates else None
        benchmark_diff = avg_rate - avg_benchmark_rate if avg_rate is not None and avg_benchmark_rate is not None else None
        benchmark_error_rate = benchmark_diff / avg_benchmark_rate if benchmark_diff is not None and avg_benchmark_rate not in (None, 0) else None
        amount_units = sorted({line.amount_unit for line in amount_used_lines if line.max_amount and line.amount_unit})
        amount_unit = ""
        if len(amount_units) == 1:
            amount_unit = amount_units[0]
        elif len(amount_units) > 1:
            amount_unit = "혼합: " + ", ".join(amount_units)

        if avg_benchmark_rate is None:
            result = "비교불가: 비교기준(평균차입이자율) 정보 부족"
        elif avg_rate is None:
            result = "비교불가: 차입금 이자율 정보 부족"
        elif benchmark_error_rate is not None and abs(benchmark_error_rate) <= 0.05:
            result = "적정: 비교 기준 대비 ±5% 이내"
        else:
            result = "확인필요: 비교 기준 대비 오차범위 초과"

        caution_reasons: list[str] = []
        if amount_change is not None and abs(amount_change) >= 0.30:
            caution_reasons.append(f"전기 대비 검출금액합계가 {amount_change:.2%} 변동하여 30% 기준을 초과했습니다.")
        if special_bond_ratio is not None and special_bond_ratio > 0.30:
            caution_reasons.append(f"전환사채/신주인수권부사채 등 특수사채 비중이 {special_bond_ratio:.2%}로 30%를 초과했습니다.")
        caution_status = "주의요망" if caution_reasons else ""
        caution_reason = " ".join(caution_reasons)

        rows.append(
            {
                "corp_name": report.corp_name,
                "report_name": report.report_name,
                "receipt_date": report.receipt_date,
                "receipt_no": report.receipt_no,
                "context_count": len(test_lines),
                "rate_count": len(rates),
                "benchmark_type": benchmark_label if benchmark_rates else "",
                "benchmark_count": len(benchmark_rates),
                "wacc_count": len(wacc_rates),
                "amount_sum": amount_sum,
                "max_amount": max_amount,
                "amount_method": amount_method,
                "amount_diff": amount_diff,
                "amount_change": amount_change,
                "special_bond_amount": special_bond_amount,
                "special_bond_ratio": special_bond_ratio,
                "amount_unit": amount_unit,
                "min_rate": min_rate,
                "avg_rate": avg_rate,
                "max_rate": max_rate,
                "avg_benchmark_rate": avg_benchmark_rate,
                "benchmark_diff": benchmark_diff,
                "benchmark_error_rate": benchmark_error_rate,
                "result": result,
                "caution_status": caution_status,
                "caution_reason": caution_reason,
            }
        )
        prev_amount_sum = amount_sum

    return rows


def calculate_borrowing_amount(lines: list[BorrowingLine]) -> tuple[int, int, str, list[BorrowingLine]]:
    candidates: list[tuple[BorrowingLine, int]] = []
    seen_contexts: set[str] = set()
    for line in lines:
        if not is_borrowing_amount_context(line.context):
            continue
        amount = extract_current_amount(line)
        if amount is None or amount <= 0:
            continue
        key = normalize_text(line.context)
        if key in seen_contexts:
            continue
        seen_contexts.add(key)
        candidates.append((line, amount))

    if not candidates:
        return 0, 0, "차입금 잔액 후보 없음", []

    total_candidates = [(line, amount) for line, amount in candidates if is_total_amount_context(line.context)]
    if total_candidates:
        selected_line, selected_amount = max(total_candidates, key=lambda item: item[1])
        return selected_amount, selected_amount, "합계/총계 행 우선", [selected_line]

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
        return amount_sum, max_amount, "재무상태표 차입 항목 우선", [line for line, _ in selected]

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
    return amount_sum, max_amount, "차입 항목별 당기 금액 합산", [line for line, _ in selected]


def extract_current_amount(line: BorrowingLine) -> int | None:
    values = [abs(value) for value in line.amounts if abs(value) > 0]
    if not values:
        return None
    return values[0]


def is_borrowing_amount_context(text: str) -> bool:
    compact = re.sub(r"\s+", "", text).lower()
    if not any(keyword in compact for keyword in ("차입금", "단기차입", "장기차입", "사채", "유동성장기", "금융기관차입", "borrow", "debt", "bond", "loan")):
        return False
    if not re.search(r"\d{1,3}(?:,\d{3})+", text):
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
    if line.line_no > 2500:
        return False
    compact = re.sub(r"\s+", "", line.context)
    if not any(keyword in compact for keyword in ("단기차입금", "장기차입금", "유동성장기부채", "유동성사채", "사채")):
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


def is_valid_borrowing_rate_context(text: str) -> bool:
    compact = re.sub(r"\s+", "", text).lower()
    if not any(keyword in compact for keyword in ("차입금", "사채", "차입", "borrow", "debt", "bond")):
        return False
    if not any(keyword in compact for keyword in ("이자율", "이율", "금리", "interest", "rate")) and not has_rate_pattern(text):
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
        "리스부채",
        "증분차입",
        "할인율",
        "현금흐름할인",
        "공정가치",
        "조건부금융부채",
        "가중평균자본비용",
        "wacc",
    )
    return not any(keyword in compact for keyword in excluded)


def is_reasonable_interest_rate(rate: float) -> bool:
    return 0 < rate <= 0.30


def has_rate_pattern(text: str) -> bool:
    if re.search(r"(?<![\d.])\d{1,2}(?:\.\d{1,4})?\s*%(?!\d)", text):
        return True
    return bool(re.search(r"(?<![\d,])\d{1,2}\.\d{1,4}\s*(?:~|-|∼|～)\s*\d{1,2}\.\d{1,4}(?![\d,])", text))


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
            if 0 < value <= 0.30 and value not in rates:
                rates.append(value)
    return rates


def is_decimal_rate_context(text: str) -> bool:
    compact = re.sub(r"\s+", "", text).lower()
    return any(keyword in compact for keyword in ("이자율", "이율", "금리", "wacc", "자본비용", "자본화이자율", "차입이자율", "차입금리", "interest", "rate"))


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
        raw = match.group(1)
        negative = raw.startswith("(") and raw.endswith(")")
        raw = raw.strip("()").replace(",", "")
        try:
            value = int(raw)
        except ValueError:
            continue
        values.append(-abs(value) if negative else value)
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


def save_workbook(path: Path, reports: list[DartReport], lines: list[BorrowingLine], tests: list[dict]) -> None:
    sheets = [
        (
            "정기보고서목록",
            ["회사명", "보고서명", "접수일", "접수번호", "종목코드"],
            [[r.corp_name, r.report_name, r.receipt_date, r.receipt_no, r.stock_code] for r in reports],
            {},
        ),
        (
            "차입금필터링",
            ["회사명", "보고서명", "접수일", "접수번호", "키워드", "이자율", "금액후보", "금액단위", "최대금액", "원문파일", "줄번호", "문맥"],
            [
                [
                    line.corp_name,
                    line.report_name,
                    line.receipt_date,
                    line.receipt_no,
                    line.keyword,
                    format_rates(line.interest_rates),
                    ", ".join(str(amount) for amount in line.amounts),
                    line.amount_unit,
                    line.max_amount,
                    line.source_file,
                    line.line_no,
                    line.context,
                ]
                for line in lines
            ],
            {9: 2, 11: 2},
        ),
        (
            "이자율오버롤테스트",
            [
                "회사명",
                "보고서명",
                "접수일",
                "접수번호",
                "차입금문맥수",
                "차입금이자율검출수",
                "비교기준",
                "비교기준이자율검출수",
                "WACC참고검출수",
                "검출금액합계",
                "최대라인금액",
                "금액산정방식",
                "금액단위",
                "전기대비증감",
                "전기대비변동률",
                "특수사채금액",
                "특수사채비중",
                "최저차입이자율",
                "평균차입이자율",
                "최고차입이자율",
                "평균비교기준이자율",
                "비교기준대비차이",
                "비교기준대비오차율",
                "결과",
                "주의여부",
                "주의사유",
            ],
            [
                [
                    t["corp_name"],
                    t["report_name"],
                    t["receipt_date"],
                    t["receipt_no"],
                    t["context_count"],
                    t["rate_count"],
                    t["benchmark_type"],
                    t["benchmark_count"],
                    t["wacc_count"],
                    t["amount_sum"],
                    t["max_amount"],
                    t["amount_method"],
                    t["amount_unit"],
                    t["amount_diff"],
                    t["amount_change"],
                    t["special_bond_amount"],
                    t["special_bond_ratio"],
                    t["min_rate"],
                    t["avg_rate"],
                    t["max_rate"],
                    t["avg_benchmark_rate"],
                    t["benchmark_diff"],
                    t["benchmark_error_rate"],
                    t["result"],
                    t["caution_status"],
                    t["caution_reason"],
                ]
                for t in tests
            ],
            {5: 2, 6: 2, 8: 2, 9: 2, 10: 2, 11: 2, 14: 2, 15: 3, 16: 2, 17: 3, 18: 3, 19: 3, 20: 3, 21: 3, 22: 3, 23: 3},
        ),
    ]

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types())
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
        self.entry_font = ("맑은 고딕", 12)

        self.api_key_var = tk.StringVar(value=self.config.get("api_key", ""))
        self.save_api_key_var = tk.BooleanVar(value=bool(self.config.get("api_key", "")))
        self.company_text: tk.Text | None = None
        self.stock_var = tk.StringVar()
        self.corp_code_var = tk.StringVar()
        self.begin_year_var = tk.StringVar(value=str(datetime.now().year - 9))
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
        self.company_text = tk.Text(
            container,
            height=1,
            width=20,
            font=self.entry_font,
            relief="solid",
            bd=1,
            highlightthickness=1,
            highlightbackground="#cbd5e1",
            highlightcolor="#0f766e",
            wrap="none",
            undo=False,
        )
        self.company_text.insert("1.0", "삼성전자")
        self.company_text.pack(fill="x", ipady=3)
        self.company_text.bind("<Return>", lambda _event: "break")
        self.company_text.bind("<Tab>", lambda _event: self.focus_next_company_widget())

    def focus_next_company_widget(self):
        if self.company_text is not None:
            self.company_text.tk_focusNext().focus()
        return "break"

    def get_company_name(self) -> str:
        if self.company_text is None:
            return ""
        return self.company_text.get("1.0", "end-1c").strip()

    def set_company_name(self, value: str) -> None:
        if self.company_text is None:
            return
        self.company_text.delete("1.0", "end")
        self.company_text.insert("1.0", value)

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
