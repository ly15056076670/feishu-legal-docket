import datetime as dt
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from pypdf import PdfWriter


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import court_download
from calc_deadline import calculate
from mac_sync import prepare
from normalize_date import normalize
from sms_delivery import identify


LINK = ("https://zxfw.court.gov.cn/zxfw/#/pagesAjkj/app/wssd/index"
        "?qdbh=synthetic-one&sdbh=synthetic-two&sdsin=synthetic-sign")
SMS = f"【合成法院】请查收：{LINK}。"


class MockResponse(io.BytesIO):
    def __init__(self, content, url, content_type):
        super().__init__(content)
        self.url = url
        self.headers = {"Content-Type": content_type}

    def geturl(self):
        return self.url


class MockCourt:
    def __init__(self, files):
        self.files = files

    def open(self, request, timeout):
        url = request.full_url
        if url == court_download.API:
            assert json.loads(request.data)["sdsin"] == "synthetic-sign"
            rows = [
                {"c_wsmc": f"示例文书{index}", "wjlj": f"https://court.example/{index}", "dt_cjsj": "未确认"}
                for index in range(len(self.files))
            ]
            return MockResponse(json.dumps({"code": 200, "data": rows}).encode(), url, "application/json")
        content = self.files[int(url.rsplit("/", 1)[1])]
        return MockResponse(content, url, "application/pdf")


class DocketTests(unittest.TestCase):
    def test_sms_recognizes_only_known_hosts_and_does_not_print_signature(self):
        unrelated = LINK.replace("zxfw.court.gov.cn", "not-the-court.example")
        self.assertEqual(identify(SMS + "\n" + unrelated), {LINK: ("zxfw.court.gov.cn", "api_first")})
        result = subprocess.run([sys.executable, str(SCRIPTS / "sms_delivery.py")],
                                input=SMS, capture_output=True, text=True, check=True)
        self.assertNotIn("synthetic-sign", result.stdout)

    def test_sms_cli_passes_only_selected_url_to_download(self):
        with patch.object(sys, "argv", ["court_download.py", "--sms-stdin", "--output-dir", "demo"]), \
             patch.object(sys, "stdin", io.StringIO(SMS)), \
             patch.object(sys, "stdout", io.StringIO()), \
             patch.object(court_download, "fetch", return_value=[]) as fetch:
            court_download.main()
        fetch.assert_called_once_with(LINK, Path("demo"), None)

    def test_download_checks_entire_batch_and_reuses_existing_identical_pdfs(self):
        raw = io.BytesIO()
        pdf = PdfWriter()
        pdf.add_blank_page(180, 100)
        pdf.write(raw)
        content = raw.getvalue()
        with tempfile.TemporaryDirectory() as temp, patch.object(court_download, "safe_url", side_effect=lambda url: url):
            destination = Path(temp)
            with self.assertRaises(court_download.DownloadError):
                court_download.fetch(LINK, destination, opener=MockCourt([content, b"<html>login</html>"]))
            self.assertEqual(list(destination.iterdir()), [])
            first = court_download.fetch(LINK, destination, opener=MockCourt([content, content]))
            self.assertEqual(len(first), 2)
            self.assertFalse(first[0]["received_date_confirmed"])
            inode = Path(first[0]["file"]).stat().st_ino
            repeated = court_download.fetch(LINK, destination, opener=MockCourt([content, content]))
            self.assertEqual(first, repeated)
            self.assertEqual(Path(repeated[0]["file"]).stat().st_ino, inode)

    def test_dates_and_estimate_do_not_infer_receipt(self):
        self.assertEqual(normalize("二○二六年十2月二十五日"), "2026-12-25")
        with self.assertRaises(ValueError):
            normalize("二〇二六年2十一月二十五日")
        result = calculate({"category": "银行存款", "ruling_date": "2024-02-29"})
        self.assertEqual(result["estimated_end_date"], "2025-02-28")
        self.assertIn("待核实", result["start_basis"])

    def test_mac_cancel_requires_confirmation_and_stays_scoped(self):
        action = {"kind": "hearing", "case_no": "示例A", "node_key": "hearing-1", "status": "cancelled"}
        self.assertEqual(prepare([action])["calendar"], [])
        plan = prepare([{**action, "confirmed": True}, {**action, "confirmed": True}],
                       as_of=dt.datetime(2027, 1, 1))
        self.assertEqual(len(plan["calendar"]), 1)
        self.assertIn("|||CANCEL|||", plan["calendar"][0])


if __name__ == "__main__":
    unittest.main()
