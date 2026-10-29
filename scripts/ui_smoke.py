"""Exercise the real desktop UI with deterministic data and no saved API key.

Run from the project directory: python scripts/ui_smoke.py
Search for 오류검증 to show a recoverable API error; any other name succeeds.
"""

import sys
import time
import json
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app
from dart_api import DartAPIError


REPORT = app.DartReport("검증회사", "분기보고서 (2026.03)", "20260515", "20260515000001", "000001")


def line(keyword, section, rates, amounts, source, context):
    return app.BorrowingLine(
        REPORT.corp_name, REPORT.report_name, REPORT.receipt_date, REPORT.receipt_no,
        keyword, section, rates, amounts, "백만원", amounts[0], source, 42, context,
    )


class DemoClient:
    def __init__(self, api_key):
        pass

    def search_corps(self, company_name, stock_code=""):
        time.sleep(0.3)
        if "오류" in company_name:
            raise DartAPIError("010", "DART API 키를 확인해 주세요. 입력으로 돌아가 키를 바꾼 뒤 다시 실행하세요.")
        return [app.CorpInfo("00000001", "검증회사", "000001")]

    def resolve_corp(self, *args):
        return app.CorpInfo("00000001", "검증회사", "000001")

    def get_reports(self, *args):
        time.sleep(0.3)
        return [REPORT]

    def extract_financial_borrowing_line(self, *args):
        return line("재무상태표 차입잔액", "재무상태표 API", [], [10000],
                    "fnlttSinglAcntAll.json", "재무상태표 차입잔액 합계 10,000백만원")

    def extract_financial_benchmarks(self, *args):
        return app.FinancialBenchmarks(revenue=50000)

    def extract_borrowing_lines(self, *args):
        time.sleep(0.3)
        return [
            line("단기차입금", "차입금 주석", [0.05], [10000], "demo.xml",
                 "차입금 주석 단기차입금 10,000 금리 5.0%"),
            line(app.NOTE_INTEREST_EXPENSE_KEYWORD, "금융비용 주석", [], [125], "demo.xml",
                 "금융비용 주석 이자비용 125"),
        ]


if __name__ == "__main__":
    app.configure_windows_dpi()
    test_failed = False
    with patch("app.load_config", return_value={}), patch("app.save_config"), \
         patch("app.DartClient", DemoClient), patch.object(app.DartOtApp, "_enable_source_reload"):
        window = app.DartOtApp()
        window.title("DART-OT · 화면 검증 (모의 데이터)")
        window.api_key_var.set("ui-check")
        if "--automated" in sys.argv:
            checks = []

            def wait_until(predicate, callback, remaining=100):
                if predicate():
                    callback()
                elif remaining:
                    window.after(100, wait_until, predicate, callback, remaining - 1)
                else:
                    global test_failed
                    test_failed = True
                    print(json.dumps({"ok": False, "error": "UI workflow timed out"}), flush=True)
                    window.destroy()

            def check_error():
                assert window.get_company_name() == "오류검증"
                assert str(window.result_open_button.cget("state")) == "disabled"
                checks.append("오류 안내와 입력 보존")
                window._show_setup_page()
                window.update_idletasks()
                assert window.companies_card.winfo_rootx() + window.companies_card.winfo_width() <= window.winfo_rootx() + window.winfo_width()
                assert window.preset_combo.winfo_ismapped(), (window.winfo_geometry(), window.conditions_card.winfo_geometry(), window.conditions_card.body.winfo_geometry(), window.conditions_card.body.winfo_reqheight())
                assert window.preset_combo.winfo_rooty() + window.preset_combo.winfo_height() <= window.conditions_card.winfo_rooty() + window.conditions_card.winfo_height()
                window.set_company_name("검증회사")
                window.search_company()
                wait_until(lambda: window.selected_corp is not None, check_search)

            def check_search():
                assert window.selected_corp.corp_name == "검증회사"
                assert window.export_button.cget("state") == "normal"
                window.update_idletasks()
                assert window.export_button.winfo_ismapped()
                assert window.export_button.winfo_rooty() + window.export_button.winfo_height() <= window.companies_card.winfo_rooty() + window.companies_card.winfo_height()
                checks.append("오류 후 회사 재검색")
                window.export_button.invoke()
                wait_until(lambda: window.output_file is not None, check_export)

            def check_export():
                assert window.output_file.exists()
                assert window.result_metric_vars["tests"].get() == "1"
                assert str(window.result_open_button.cget("state")) == "normal"
                assert "원문추적" in window.result_summary_var.get()
                checks.append("실제 계산·Excel 생성·결과 안내")
                window.geometry("1180x760")
                window.after(200, check_layout)

            def check_layout():
                window.update_idletasks()
                assert window.result_tree.winfo_ismapped()
                assert window.result_tree.winfo_height() >= 90, window.result_tree.winfo_geometry()
                for button in (window.result_back_button, window.result_open_button):
                    assert button.winfo_ismapped(), (str(button), window.winfo_geometry(), button.winfo_geometry(), button.master.winfo_geometry())
                    assert button.winfo_rooty() + button.winfo_height() <= window.winfo_rooty() + window.winfo_height()
                checks.append("최소 창 크기에서 주요 버튼 노출")
                checks.append("결과 표의 제목과 데이터 행 노출")
                # JSON escapes keep CI logs readable on non-Korean Windows consoles.
                print(json.dumps({"ok": True, "checks": checks}), flush=True)
                if "--preview-result" in sys.argv:
                    window.title("DART-OT · 결과 화면 검증 (모의 데이터)")
                else:
                    window.destroy()

            def report_test_error(exc_type, exc, tb):
                global test_failed
                test_failed = True
                import traceback
                traceback.print_exception(exc_type, exc, tb)
                window.destroy()

            window.report_callback_exception = report_test_error
            window.set_company_name("오류검증")
            window.geometry("1180x760")
            window.after(300, window.search_company)
            window.after(400, wait_until, lambda: "입력으로 돌아가" in window.result_status_var.get(), check_error)
        window.mainloop()
    if "--automated" in sys.argv and test_failed:
        raise SystemExit(1)
