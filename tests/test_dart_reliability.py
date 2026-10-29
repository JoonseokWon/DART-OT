import json
import tempfile
import unittest
import urllib.error
import zipfile
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from xml.etree import ElementTree as ET

import app
import dart_api
import disclosure_viewer


REPORT = app.DartReport("검증회사", "분기보고서 (2026.03)", "20260515", "20260515000001", "000001")
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def xml_zip(content=b"<DOCUMENT><P>test</P></DOCUMENT>", name="report.xml"):
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(name, content)
    return stream.getvalue()


class ResponseValidationTests(unittest.TestCase):
    def test_key_and_limit_errors_are_explicit_and_do_not_echo_secrets(self):
        for code in ("010", "011", "020", "800", "900"):
            with self.subTest(code=code), self.assertRaises(dart_api.DartAPIError) as caught:
                dart_api.parse_dart_json(json.dumps({"status": code, "message": "secret-key"}).encode())
            self.assertEqual(caught.exception.status, code)
            self.assertNotIn("secret-key", str(caught.exception))

    def test_normal_no_data_remains_distinct(self):
        result = dart_api.parse_dart_json(b'{"status":"013"}')
        self.assertEqual(result["status"], "013")

    def test_missing_status_and_non_object_json_fail(self):
        for body in (b"{}", b"[]", b"null", b"<html>unavailable</html>"):
            with self.subTest(body=body), self.assertRaises(dart_api.DartAPIError):
                dart_api.parse_dart_json(body)

    def test_error_xml_is_not_a_document(self):
        for body in (
            b'<result><status>010</status><message>bad key</message></result>',
            b'<result xmlns="urn:dart"><status>020</status></result>',
        ):
            with self.subTest(body=body), self.assertRaises(dart_api.DartAPIError):
                dart_api.read_dart_xml_files(body)

    def test_xml_error_also_works_for_json_endpoint(self):
        with self.assertRaises(dart_api.DartAPIError) as caught:
            dart_api.parse_dart_json(b"<result><status>020</status></result>")
        self.assertEqual(caught.exception.status, "020")

    def test_document_errors_are_not_cached_in_either_client(self):
        disclosure_viewer._DOCUMENT_FILES.clear()
        for client, method in ((app.DartClient("test"), "get_document_texts"),
                               (disclosure_viewer.DartClient("test"), "get_document_files")):
            with patch.object(client, "_get_bytes", side_effect=[
                b"<result><status>020</status></result>", xml_zip(),
            ]) as download:
                with self.assertRaises(dart_api.DartAPIError):
                    getattr(client, method)(REPORT.receipt_no)
                self.assertEqual(getattr(client, method)(REPORT.receipt_no)[0][0], "report.xml")
                self.assertEqual(download.call_count, 2)
        disclosure_viewer._DOCUMENT_FILES.clear()

    def test_archive_without_xml_is_rejected(self):
        with self.assertRaises(dart_api.DartAPIError):
            dart_api.read_dart_xml_files(xml_zip(name="readme.txt"))

    def test_corporate_code_result_is_not_an_error_envelope(self):
        body = b"<result><list><corp_code>00000001</corp_code></list></result>"
        client = app.DartClient("test")
        with patch.object(client, "_get_bytes", return_value=xml_zip(body, "CORPCODE.xml")):
            self.assertEqual(client.get_corp_codes()[0].corp_code, "00000001")

    def test_error_inside_archive_is_also_rejected(self):
        with self.assertRaises(dart_api.DartAPIError):
            dart_api.read_dart_xml_files(xml_zip(b"<result><status>020</status></result>"))

    def test_financial_failure_is_not_cached_or_retried_as_standalone(self):
        client = app.DartClient("test")
        with patch.object(client, "_get_json", side_effect=[
            {"status": "010"}, {"status": "000", "list": [{"account_nm": "차입금"}]},
        ]) as download:
            with self.assertRaises(dart_api.DartAPIError):
                client.get_financial_statement_rows("00000001", REPORT)
            self.assertFalse(client._financial_rows_cache)
            self.assertEqual(len(client.get_financial_statement_rows("00000001", REPORT)), 1)
            self.assertEqual(download.call_count, 2)

    def test_no_consolidated_data_falls_back_to_standalone(self):
        client = app.DartClient("test")
        with patch.object(client, "_get_json", side_effect=[
            {"status": "013"}, {"status": "000", "list": [{"account_nm": "차입금"}]},
        ]) as download:
            self.assertEqual(len(client.get_financial_statement_rows("00000001", REPORT)), 1)
            self.assertEqual(download.call_args_list[1].args[1]["fs_div"], "OFS")


class TransportTests(unittest.TestCase):
    def request(self):
        return dart_api.get_dart_bytes("https://opendart.fss.or.kr/api/list.json",
                                       {"crtfc_key": "never-print-this"}, timeout=1, user_agent="test")

    @patch("dart_api._wait_for_request_slot")
    @patch("dart_api.time.sleep")
    def test_transient_http_retries_then_succeeds(self, sleep, slot):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b"ok"
        error = urllib.error.HTTPError("https://x/?key=secret", 503, "busy", {"Retry-After": "1"}, None)
        with patch("dart_api.urllib.request.urlopen", side_effect=[error, response]) as download:
            self.assertEqual(self.request(), b"ok")
            self.assertEqual(download.call_count, 2)
            self.assertEqual(slot.call_count, 2)
            sleep.assert_called_once_with(1.0)

    @patch("dart_api._wait_for_request_slot")
    @patch("dart_api.time.sleep")
    def test_network_retries_are_bounded_and_redacted(self, sleep, slot):
        with patch("dart_api.urllib.request.urlopen", side_effect=urllib.error.URLError("never-print-this")) as download:
            with self.assertRaises(dart_api.DartAPIError) as caught:
                self.request()
            self.assertEqual(download.call_count, 3)
            self.assertNotIn("never-print-this", str(caught.exception))

    @patch("dart_api._wait_for_request_slot")
    @patch("dart_api.time.sleep")
    def test_permanent_http_error_is_not_retried(self, sleep, slot):
        error = urllib.error.HTTPError("https://x/?key=secret", 403, "secret", {}, None)
        with patch("dart_api.urllib.request.urlopen", side_effect=error) as download:
            with self.assertRaises(dart_api.DartAPIError):
                self.request()
            self.assertEqual(download.call_count, 1)
            sleep.assert_not_called()

    def test_retry_after_is_bounded(self):
        self.assertEqual(dart_api._retry_delay(0, "99999"), 10)
        self.assertEqual(dart_api._retry_delay(1, "invalid"), 2)


class ExportEvidenceTests(unittest.TestCase):
    def test_trace_and_manifest_preserve_source_identifiers(self):
        line = app.BorrowingLine(
            REPORT.corp_name, REPORT.report_name, REPORT.receipt_date, REPORT.receipt_no,
            "단기차입금", "별도재무제표 주석", [5.0], [1000], "백만원", 1000,
            "source.xml", 42, "별도재무제표 <차입금> & 금리 5.0% 1,000\n근거",
        )
        app.OUTPUT_DIR.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=app.OUTPUT_DIR) as folder:
            path = Path(folder) / "evidence.xlsx"
            no_lines_report = app.DartReport("검증회사", "사업보고서 (2025.12)", "20260301", "20260301000001", "000001")
            app.save_workbook(path, [REPORT, no_lines_report], [line], [], run_metadata={"조회 시작연도": 2025})
            with zipfile.ZipFile(path) as z:
                for name in z.namelist():
                    if name.endswith(".xml"):
                        ET.fromstring(z.read(name))
                names = [n.attrib["name"] for n in ET.fromstring(z.read("xl/workbook.xml")).findall("m:sheets/m:sheet", NS)]
                self.assertEqual(names[:5], ["정기보고서목록", "차입금필터링", "이자비용필터링", "이자율오버롤테스트", "추출오류"])
                trace = ET.fromstring(z.read(f"xl/worksheets/sheet{names.index('원문추적') + 1}.xml"))
                cells = trace.findall("m:sheetData/m:row", NS)[1].findall("m:c", NS)
                values = ["".join(c.itertext()) for c in cells]
                self.assertEqual(values[3], REPORT.receipt_no)
                self.assertEqual(values[5:7], ["source.xml", "42"])
                self.assertEqual(values[13], line.context)
                manifest = z.read(f"xl/worksheets/sheet{names.index('보고서식별') + 1}.xml").decode()
                self.assertIn(no_lines_report.receipt_no, manifest)

    def test_empty_query_does_not_create_workbook(self):
        with patch("app.DartClient") as cls, patch("app.save_workbook") as save:
            cls.return_value.resolve_corp.return_value = app.CorpInfo("00000001", "검증회사", "000001")
            cls.return_value.get_reports.return_value = []
            result = app.run_report({"apiKey": "test", "beginYear": 2025, "endYear": 2026})
            self.assertFalse(result["ok"])
            self.assertIn("조회기간", result["message"])
            save.assert_not_called()


class DelayedUIErrorTests(unittest.TestCase):
    def test_error_survives_exception_scope_for_both_workers(self):
        for method in (app.DartOtApp._search_company_worker, app.DartOtApp._run_export_worker):
            callbacks = []
            fake = SimpleNamespace(
                api_key_var=Mock(), stock_var=Mock(), corp_code_var=Mock(), begin_year_var=Mock(),
                end_year_var=Mock(), materiality_preset_var=Mock(), get_company_name=Mock(return_value="검증회사"),
                _show_error=Mock(), _queue_progress=Mock(),
                after=lambda delay, callback, *args: callbacks.append((callback, args)),
            )
            with patch("app.DartClient", side_effect=RuntimeError("키 확인 안내")), patch("app.run_report", side_effect=RuntimeError("키 확인 안내")):
                if method is app.DartOtApp._search_company_worker:
                    method(fake, "test")
                else:
                    method(fake)
            callback, args = callbacks[0]
            callback(*args)
            self.assertIn("키 확인 안내", fake._show_error.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
