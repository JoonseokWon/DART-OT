import json
import os
import re
import socket
import threading
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tkinter import messagebox, ttk
import tkinter.font as tkfont
import urllib.parse
import zipfile

import app


ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "outputs"
CONFIG_PATH = ROOT / ".dart_ot_config.json"

ASSET_SECTIONS = {
    "유형자산 주석",
    "무형자산 주석",
    "사용권자산 주석",
    "투자부동산 주석",
}
EXPENSE_SECTIONS = {
    "비용의 성격별 분류 주석",
    "판매비와관리비 주석",
    "매출원가 주석",
    "금융수익 및 금융비용 주석",
    "기타수익 및 기타비용 주석",
}
ASSET_KEYWORDS = (
    "유형자산",
    "건물",
    "구축물",
    "기계장치",
    "차량운반구",
    "공구와기구",
    "비품",
    "시설장치",
    "건설중인자산",
    "무형자산",
    "소프트웨어",
    "산업재산권",
    "개발비",
    "회원권",
    "사용권자산",
    "투자부동산",
)
DEPRECIATION_KEYWORDS = (
    "감가상각비",
    "상각비",
    "무형자산상각비",
    "사용권자산상각비",
    "감가상각",
    "상각누계액",
)


@dataclass
class DepLine:
    corp_name: str
    report_name: str
    receipt_date: str
    receipt_no: str
    category: str
    section: str
    keyword: str
    amounts: list[int]
    amount_unit: str
    display_amount: int
    source_file: str
    line_no: int
    context: str


def run_depreciation_report(payload: dict) -> dict:
    api_key = payload.get("apiKey", "").strip()
    if not api_key:
        return app.fail("DART API 키를 입력해 주세요.")

    client = app.DartClient(api_key)
    corp = client.resolve_corp(
        payload.get("corpCode", ""),
        payload.get("stockCode", ""),
        payload.get("companyName", ""),
    )
    if corp is None:
        return app.fail("회사 정보를 찾지 못했습니다. 종목코드 또는 회사명을 다시 확인해 주세요.")

    now_year = datetime.now().year
    begin_year = int(payload.get("beginYear") or now_year - 9)
    end_year = int(payload.get("endYear") or now_year)

    reports = client.get_reports(corp.corp_code, begin_year, end_year)
    lines: list[DepLine] = []
    for report in reports:
        try:
            lines.extend(extract_depreciation_lines(client, report))
        except Exception:
            continue

    tests = build_depreciation_tests(reports, lines)
    OUTPUT_DIR.mkdir(exist_ok=True)
    file_name = f"DART_DEP_{app.safe_filename(corp.corp_name)}_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
    path = OUTPUT_DIR / file_name
    save_depreciation_workbook(path, reports, lines, tests)

    return {
        "ok": True,
        "message": f"{corp.corp_name} 정기보고서 {len(reports)}건, 감가상각 관련 문맥 {len(lines)}건을 정리했습니다.",
        "file": file_name,
        "reportCount": len(reports),
        "noteCount": len(lines),
        "testCount": len(tests),
    }


def extract_depreciation_lines(client: app.DartClient, report: app.DartReport) -> list[DepLine]:
    rows: list[DepLine] = []
    for source_file, text in client.get_document_texts(report.receipt_no):
        for line_no, context, amount_unit, section in extract_dep_text_records(text):
            category = classify_dep_context(context, section)
            if not category:
                continue
            amounts = app.extract_amount_values(context)
            if not amounts and category != "상각정책":
                continue
            keyword = dep_keyword_for_context(context, category)
            display_amount = display_amount_for_dep_line(context, amount_unit, amounts, category)
            rows.append(
                DepLine(
                    report.corp_name,
                    report.report_name,
                    report.receipt_date,
                    report.receipt_no,
                    category,
                    section,
                    keyword,
                    amounts,
                    amount_unit,
                    display_amount,
                    Path(source_file).name or f"{report.receipt_no}.xml",
                    line_no,
                    context[:1200],
                )
            )
    return rows


def extract_dep_text_records(text: str) -> list[tuple[int, str, str, str]]:
    raw_records: list[tuple[int, int, str]] = []
    seen: set[str] = set()

    for match in re.finditer(r"<TR\b.*?</TR>", text, flags=re.IGNORECASE | re.DOTALL):
        context = app.clean_context(match.group(0))
        if context and context not in seen:
            raw_records.append((match.start(), text.count("\n", 0, match.start()) + 1, context))
            seen.add(context)

    offset = 0
    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        line_start = offset
        offset += len(raw_line) + 1
        if "<TD" in raw_line.upper() or "<TR" in raw_line.upper() or "</TD" in raw_line.upper():
            continue
        context = app.clean_context(raw_line)
        if context and context not in seen:
            raw_records.append((line_start, line_no, context))
            seen.add(context)

    records: list[tuple[int, str, str, str]] = []
    current_unit = ""
    current_section = ""
    for _, line_no, context in sorted(raw_records, key=lambda item: item[0]):
        section = dep_note_section_for_context(context)
        if section:
            current_section = section
        detected_unit = app.detect_amount_unit(context)
        if detected_unit:
            current_unit = detected_unit
        records.append((line_no, context, current_unit, current_section))
    return records


def dep_note_section_for_context(text: str) -> str:
    compact = re.sub(r"\s+", "", app.normalize_text(text))
    if not re.match(r"^\d{1,3}\.", compact):
        return ""
    title = re.sub(r"^\d{1,3}\.", "", compact)
    title = re.sub(r"\([^)]*\)$", "", title)
    section_map = {
        "유형자산": "유형자산 주석",
        "무형자산": "무형자산 주석",
        "사용권자산": "사용권자산 주석",
        "투자부동산": "투자부동산 주석",
        "비용의성격별분류": "비용의 성격별 분류 주석",
        "판매비와관리비": "판매비와관리비 주석",
        "매출원가": "매출원가 주석",
        "금융수익및금융비용": "금융수익 및 금융비용 주석",
        "금융수익및금융원가": "금융수익 및 금융비용 주석",
        "기타수익및기타비용": "기타수익 및 기타비용 주석",
    }
    return section_map.get(title, "")


def classify_dep_context(text: str, section: str) -> str:
    compact = re.sub(r"\s+", "", text)
    if section in ASSET_SECTIONS and any(keyword in compact for keyword in ASSET_KEYWORDS + DEPRECIATION_KEYWORDS):
        return "자산주석"
    if section in EXPENSE_SECTIONS and any(keyword in compact for keyword in DEPRECIATION_KEYWORDS):
        return "감가상각비"
    if "내용연수" in compact or "정액법" in compact or "상각방법" in compact:
        return "상각정책"
    return ""


def dep_keyword_for_context(text: str, category: str) -> str:
    compact = re.sub(r"\s+", "", text)
    if category == "감가상각비":
        for keyword in ("감가상각비", "무형자산상각비", "사용권자산상각비", "상각비"):
            if keyword in compact:
                return keyword
    for keyword in ASSET_KEYWORDS + DEPRECIATION_KEYWORDS:
        if keyword in compact:
            return keyword
    return category


def display_amount_for_dep_line(text: str, unit: str, amounts: list[int], category: str) -> int:
    if category == "감가상각비":
        amount = extract_amount_after_dep_label(text, unit)
        if amount is not None:
            return amount
    return app.normalize_amount_to_million(max((abs(amount) for amount in amounts), default=0), unit)


def extract_amount_after_dep_label(text: str, unit: str) -> int | None:
    amount_pattern = r"\(?-?\d{1,3}(?:,\d{3})+\)?"
    label_patterns = (
        r"감가상각비",
        r"무형자산\s*상각비",
        r"무형자산상각비",
        r"사용권자산\s*상각비",
        r"사용권자산상각비",
        r"상각비",
    )
    for label_pattern in label_patterns:
        for match in re.finditer(label_pattern, text):
            tail = text[match.end() : match.end() + 160]
            amount_match = re.search(amount_pattern, tail)
            if not amount_match:
                continue
            raw = amount_match.group(0)
            value = int(raw.strip("()").replace(",", ""))
            return app.normalize_amount_to_million(abs(value), unit)
    return None


def build_depreciation_tests(reports: list[app.DartReport], lines: list[DepLine]) -> list[dict]:
    by_receipt: dict[str, list[DepLine]] = {}
    for line in lines:
        by_receipt.setdefault(line.receipt_no, []).append(line)

    rows: list[dict] = []
    for report in sorted(reports, key=lambda r: (r.receipt_date, r.report_name)):
        report_lines = by_receipt.get(report.receipt_no, [])
        asset_lines = [line for line in report_lines if line.category == "자산주석"]
        expense_lines = [line for line in report_lines if line.category == "감가상각비"]
        policy_lines = [line for line in report_lines if line.category == "상각정책"]
        dep_expense = select_depreciation_expense(expense_lines)
        asset_amount = max((line.display_amount for line in asset_lines), default=None)
        judgment = "확인필요" if dep_expense else "판단불가"
        basis = "감가상각비/상각비 항목을 주석에서 검출했습니다." if dep_expense else "비용 주석에서 감가상각비 또는 상각비 항목을 찾지 못했습니다."
        rows.append(
            {
                "corp_name": report.corp_name,
                "report_name": report.report_name,
                "receipt_date": report.receipt_date,
                "receipt_no": report.receipt_no,
                "judgment": judgment,
                "basis": basis,
                "asset_amount": asset_amount,
                "depreciation_expense": dep_expense,
                "asset_context_count": len(asset_lines),
                "expense_context_count": len(expense_lines),
                "policy_context_count": len(policy_lines),
            }
        )
    return rows


def select_depreciation_expense(lines: list[DepLine]) -> int | None:
    amounts = [line.display_amount for line in lines if line.display_amount > 0]
    if not amounts:
        return None
    unique_amounts = sorted(set(amounts), reverse=True)
    return unique_amounts[0]


def save_depreciation_workbook(path: Path, reports: list[app.DartReport], lines: list[DepLine], tests: list[dict]) -> None:
    asset_lines = [line for line in lines if line.category in {"자산주석", "상각정책"}]
    expense_lines = [line for line in lines if line.category == "감가상각비"]
    filter_headers = ["회사명", "보고서명", "접수일", "접수번호", "구분", "주석구분", "키워드", "금액후보", "금액단위", "표시금액", "원문파일", "줄번호", "문맥"]

    def line_row(line: DepLine) -> list:
        return [
            line.corp_name,
            line.report_name,
            line.receipt_date,
            line.receipt_no,
            line.category,
            line.section,
            line.keyword,
            ", ".join(str(amount) for amount in line.amounts),
            line.amount_unit,
            line.display_amount,
            line.source_file,
            line.line_no,
            line.context,
        ]

    sheets = [
        (
            "정기보고서목록",
            ["회사명", "보고서명", "접수일", "접수번호", "종목코드"],
            [[r.corp_name, r.report_name, r.receipt_date, r.receipt_no, r.stock_code] for r in reports],
            {},
        ),
        (
            "자산주석필터링",
            filter_headers,
            [line_row(line) for line in asset_lines],
            {10: 2, 12: 2},
        ),
        (
            "감가상각비필터링",
            filter_headers,
            [line_row(line) for line in expense_lines],
            {10: 2, 12: 2},
        ),
        (
            "감가상각비오버롤",
            [
                "회사명",
                "보고서명",
                "접수일",
                "접수번호",
                "판정",
                "판정근거",
                "자산주석최대금액",
                "감가상각비",
                "자산주석문맥수",
                "감가상각비문맥수",
                "상각정책문맥수",
            ],
            [
                [
                    row["corp_name"],
                    row["report_name"],
                    row["receipt_date"],
                    row["receipt_no"],
                    row["judgment"],
                    row["basis"],
                    row["asset_amount"],
                    row["depreciation_expense"],
                    row["asset_context_count"],
                    row["expense_context_count"],
                    row["policy_context_count"],
                ]
                for row in tests
            ],
            {7: 2, 8: 2},
        ),
    ]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", app.content_types())
        archive.writestr("_rels/.rels", app.root_rels())
        archive.writestr("xl/workbook.xml", app.workbook_xml([s[0] for s in sheets]))
        archive.writestr("xl/_rels/workbook.xml.rels", app.workbook_rels(len(sheets)))
        archive.writestr("xl/styles.xml", app.styles_xml())
        for index, (_, headers, rows, column_styles) in enumerate(sheets, start=1):
            archive.writestr(f"xl/worksheets/sheet{index}.xml", app.sheet_xml(headers, rows, column_styles))


class DepHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:
        return

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            self.respond(200, DEPRECIATION_HTML.encode("utf-8"), "text/html; charset=utf-8")
            return
        if parsed.path == "/download":
            params = urllib.parse.parse_qs(parsed.query)
            name = params.get("file", [""])[0]
            file_path = (OUTPUT_DIR / name).resolve()
            if file_path.parent != OUTPUT_DIR.resolve() or not file_path.exists():
                self.respond(404, b"not found", "text/plain; charset=utf-8")
                return
            self.respond(200, file_path.read_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            return
        self.respond(404, b"not found", "text/plain; charset=utf-8")

    def do_POST(self) -> None:
        if self.path != "/api/run":
            self.respond(404, b"not found", "text/plain; charset=utf-8")
            return
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        try:
            result = run_depreciation_report(payload)
        except Exception as exc:
            result = app.fail(f"실행 중 오류가 발생했습니다: {exc}")
        self.respond(200, json.dumps(result, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def respond(self, status: int, data: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class DepreciationApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.config_data = app.load_config()
        self.title("DART-DEP")
        self.geometry("1040x680")
        self.minsize(980, 640)
        self.tk.call("tk", "scaling", 1.35)
        self.entry_font = ("맑은 고딕", 12)
        self.selected_corp: app.CorpInfo | None = None
        self.search_results: list[app.CorpInfo] = []
        self.output_file: Path | None = None

        self.api_key_var = tk.StringVar(value=self.config_data.get("api_key", ""))
        self.save_api_key_var = tk.BooleanVar(value=bool(self.config_data.get("api_key", "")))
        self.company_var = tk.StringVar(value="삼성전자")
        self.stock_var = tk.StringVar()
        self.corp_code_var = tk.StringVar()
        self.begin_year_var = tk.StringVar(value=str(datetime.now().year - 9))
        self.end_year_var = tk.StringVar(value=str(datetime.now().year))
        self.status_var = tk.StringVar(value="DART API 키와 회사명을 입력한 뒤 회사 검색을 눌러 주세요.")
        self.summary_var = tk.StringVar(value="정기보고서: -    감가상각 관련 공시: -    오버롤 테스트: -")
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
        style.configure("Panel.TLabel", background="#ffffff", font=("맑은 고딕", 12))
        style.configure("Title.TLabel", background="#f6f8fb", font=("맑은 고딕", 22, "bold"))
        style.configure("TButton", font=("맑은 고딕", 12), padding=(8, 6))
        style.configure("Accent.TButton", font=("맑은 고딕", 12, "bold"), padding=(8, 8))
        style.configure("Treeview", font=("맑은 고딕", 11), rowheight=30)
        style.configure("Treeview.Heading", font=("맑은 고딕", 11, "bold"))

        root = ttk.Frame(self, padding=24)
        root.pack(fill="both", expand=True)
        ttk.Label(root, text="DART-DEP", style="Title.TLabel").pack(anchor="w")
        ttk.Label(root, text="DART 공시 기반 감가상각비 오버롤 테스트 파일 생성 도구").pack(anchor="w", pady=(4, 20))

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
        self._entry(left, "회사명", self.company_var)
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
        self.tree.column("corp_name", width=390)
        self.tree.column("stock_code", width=130, anchor="center")
        self.tree.column("corp_code", width=160, anchor="center")
        self.tree.grid(row=1, column=0, sticky="nsew", pady=(10, 12))
        self.tree.bind("<<TreeviewSelect>>", self.select_company)
        ttk.Label(right, textvariable=self.status_var, style="Panel.TLabel", wraplength=720, justify="left").grid(row=2, column=0, sticky="ew", pady=(0, 10))
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
        entry = tk.Entry(container, textvariable=variable, show=show or "", width=width or 20, font=self.entry_font, relief="solid", bd=1, highlightthickness=1, highlightbackground="#cbd5e1", highlightcolor="#0f766e", insertwidth=1)
        entry.pack(fill="x", ipady=5)
        if label == "회사명":
            entry.bind("<Return>", lambda _event: self.search_company())

    def search_company(self) -> None:
        api_key = self.api_key_var.get().strip()
        if not api_key:
            messagebox.showwarning("입력 필요", "DART API 키를 입력해 주세요.")
            return
        self.status_var.set("회사 목록을 검색하고 있습니다.")
        threading.Thread(target=self._search_company_worker, args=(api_key,), daemon=True).start()

    def _search_company_worker(self, api_key: str) -> None:
        try:
            results = app.DartClient(api_key).search_corps(self.company_var.get(), self.stock_var.get())
        except Exception as exc:
            self.after(0, lambda: messagebox.showerror("검색 오류", str(exc)))
            return
        self.after(0, lambda: self._show_search_results(results))

    def _show_search_results(self, results: list[app.CorpInfo]) -> None:
        self.search_results = results
        for item in self.tree.get_children():
            self.tree.delete(item)
        for idx, corp in enumerate(results):
            self.tree.insert("", "end", iid=str(idx), values=(corp.corp_name, corp.stock_code, corp.corp_code))
        self.status_var.set(f"검색 결과 {len(results)}건이 조회되었습니다." if results else "검색 결과가 없습니다.")

    def select_company(self, _event=None) -> None:
        selected = self.tree.selection()
        if not selected:
            return
        index = int(selected[0])
        if index >= len(self.search_results):
            return
        self.selected_corp = self.search_results[index]
        self.company_var.set(self.selected_corp.corp_name)
        self.stock_var.set(self.selected_corp.stock_code)
        self.corp_code_var.set(self.selected_corp.corp_code)

    def run_export(self) -> None:
        api_key = self.api_key_var.get().strip()
        if not api_key:
            messagebox.showwarning("입력 필요", "DART API 키를 입력해 주세요.")
            return
        if self.save_api_key_var.get():
            app.save_config({"api_key": api_key})
        payload = {
            "apiKey": api_key,
            "companyName": self.company_var.get(),
            "stockCode": self.stock_var.get(),
            "corpCode": self.corp_code_var.get(),
            "beginYear": self.begin_year_var.get(),
            "endYear": self.end_year_var.get(),
        }
        self.status_var.set("DART 공시를 조회하고 감가상각비 엑셀 파일을 생성하고 있습니다.")
        threading.Thread(target=self._run_export_worker, args=(payload,), daemon=True).start()

    def _run_export_worker(self, payload: dict) -> None:
        result = run_depreciation_report(payload)
        self.after(0, lambda: self._finish_export(result))

    def _finish_export(self, result: dict) -> None:
        self.status_var.set(result.get("message", "작업이 완료되었습니다."))
        self.summary_var.set(f"정기보고서: {result.get('reportCount', 0)}    감가상각 관련 공시: {result.get('noteCount', 0)}    오버롤 테스트: {result.get('testCount', 0)}")
        if result.get("ok") and result.get("file"):
            self.output_file = OUTPUT_DIR / result["file"]

    def open_output(self) -> None:
        if self.output_file and self.output_file.exists():
            os.startfile(self.output_file)

    def open_output_dir(self) -> None:
        OUTPUT_DIR.mkdir(exist_ok=True)
        os.startfile(OUTPUT_DIR)


DEPRECIATION_HTML = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><title>DART-DEP</title></head>
<body><h1>DART-DEP</h1><p>데스크톱 앱에서 실행해 주세요.</p></body></html>"""


def find_port() -> int:
    for port in range(51801, 51860):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return 0


def main() -> None:
    app.configure_windows_dpi()
    DepreciationApp().mainloop()


if __name__ == "__main__":
    main()
