"""PDF extractor 검증 — Vision API mock + DB store."""
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.collectors.nesdc_deep_pdf import (
    _parse_response,
    _validate_meta,
    _validate_segments,
)
from src.db.init_twin_db import init_db
from src.sync.extract_pdf_segments import (
    _file_hash,
    _is_valid_pdf,
    already_processed,
    run,
    store_extraction,
)


class TestResponseParser(unittest.TestCase):
    def test_parse_json_codeblock(self):
        text = '```json\n{"meta": {"agency": "X"}, "segments": []}\n```'
        r = _parse_response(text)
        self.assertEqual(r["meta"]["agency"], "X")

    def test_parse_bare_object(self):
        text = '{"meta": {}, "segments": [{"group":"AGE","segment":"30대","results":{"A":40}}]}'
        r = _parse_response(text)
        self.assertEqual(len(r["segments"]), 1)

    def test_parse_with_trailing_text(self):
        text = '여기 결과입니다:\n```json\n{"meta": {}, "segments": []}\n```\n끝'
        r = _parse_response(text)
        self.assertEqual(r["segments"], [])

    def test_parse_empty_returns_default(self):
        r = _parse_response("응답 못 함")
        self.assertEqual(r, {"meta": {}, "segments": []})


class TestValidateSegments(unittest.TestCase):
    def test_valid_segments(self):
        items = [
            {"group": "AGE", "segment": "30대", "results": {"A": 40.5, "B": 35.0}},
            {"group": "GENDER", "segment": "남", "results": {"A": 42, "B": 38}},
        ]
        r = _validate_segments(items)
        self.assertEqual(len(r), 2)

    def test_invalid_group_filtered(self):
        items = [{"group": "PETS", "segment": "강아지", "results": {"A": 40}}]
        self.assertEqual(_validate_segments(items), [])

    def test_string_numbers_coerced(self):
        items = [{"group": "AGE", "segment": "30대", "results": {"A": "40.5"}}]
        r = _validate_segments(items)
        self.assertEqual(r[0]["results"]["A"], 40.5)

    def test_non_numeric_dropped(self):
        items = [{"group": "AGE", "segment": "30대",
                  "results": {"A": 40, "B": "no data"}}]
        r = _validate_segments(items)
        self.assertEqual(r[0]["results"], {"A": 40.0})

    def test_empty_results_filtered(self):
        items = [{"group": "AGE", "segment": "30대", "results": {}}]
        self.assertEqual(_validate_segments(items), [])


class TestValidateMeta(unittest.TestCase):
    def test_extracts_iso_date(self):
        m = _validate_meta({"agency": "X", "date": "2026-05-04 14시"})
        self.assertEqual(m["date"], "2026-05-04")

    def test_drops_null_strings(self):
        m = _validate_meta({"agency": "X", "election": "null", "method": ""})
        self.assertNotIn("election", m)
        self.assertNotIn("method", m)
        self.assertEqual(m["agency"], "X")

    def test_sample_size_int(self):
        m = _validate_meta({"sample_size": "1000"})
        self.assertEqual(m["sample_size"], 1000)

    def test_invalid_sample_size_dropped(self):
        m = _validate_meta({"sample_size": "약 천명"})
        self.assertNotIn("sample_size", m)

    def test_drops_unparseable_date(self):
        m = _validate_meta({"date": "어제"})
        self.assertNotIn("date", m)


class TestFileHelpers(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_file_hash_consistent(self):
        p = os.path.join(self.tmpdir, "a.pdf")
        with open(p, "wb") as f:
            f.write(b"%PDF-1.4 fake content for hash test xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
        h1 = _file_hash(p)
        h2 = _file_hash(p)
        self.assertEqual(h1, h2)
        self.assertEqual(len(h1), 16)

    def test_valid_pdf_magic(self):
        p = os.path.join(self.tmpdir, "x.pdf")
        with open(p, "wb") as f:
            f.write(b"%PDF-1.4" + b"x" * 200)
        self.assertTrue(_is_valid_pdf(p))

    def test_invalid_html_disguised_as_pdf(self):
        p = os.path.join(self.tmpdir, "x.pdf")
        with open(p, "wb") as f:
            f.write(b"<!DOCTYPE html><html>fake</html>" + b"x" * 200)
        self.assertFalse(_is_valid_pdf(p))

    def test_too_small_rejected(self):
        p = os.path.join(self.tmpdir, "x.pdf")
        with open(p, "wb") as f:
            f.write(b"%PDF")  # 4 bytes
        self.assertFalse(_is_valid_pdf(p))


class TestStoreExtraction(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db = os.path.join(self.tmpdir, "t.db")
        init_db(self.db)
        # 합성 PDF 파일 (hash 계산용)
        self.pdf = os.path.join(self.tmpdir, "test.pdf")
        with open(self.pdf, "wb") as f:
            f.write(b"%PDF-1.4 dummy" + b"x" * 200)
        self.fhash = _file_hash(self.pdf)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_creates_pdf_poll(self):
        ext = {
            "meta": {"agency": "갤럽", "date": "2026-04-15", "region": "전국",
                     "sample_size": 1000},
            "segments": [
                {"group": "AGE", "segment": "30대",
                 "results": {"이재명": 50, "윤석열": 35}},
                {"group": "GENDER", "segment": "남",
                 "results": {"이재명": 45, "윤석열": 40}},
            ],
            "cost_usd": 0.02,
            "usage": {"input_tokens": 2000, "output_tokens": 400},
        }
        store_extraction(self.db, self.pdf, self.fhash, ext)
        conn = sqlite3.connect(self.db)
        c = conn.cursor()
        c.execute("SELECT external_id, name FROM objects WHERE obj_type='POLL'")
        rows = c.fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], f"pdf:{self.fhash}")
        # MEASURES_IN_SEGMENT 2개 (AGE 1 + GENDER 1)
        c.execute("SELECT COUNT(*) FROM links WHERE link_type='MEASURES_IN_SEGMENT'")
        self.assertEqual(c.fetchone()[0], 2)
        conn.close()

    def test_idempotent(self):
        ext = {
            "meta": {"agency": "X"},
            "segments": [{"group": "AGE", "segment": "30대",
                          "results": {"A": 40}}],
            "cost_usd": 0.01, "usage": {"input_tokens": 100, "output_tokens": 50},
        }
        store_extraction(self.db, self.pdf, self.fhash, ext)
        store_extraction(self.db, self.pdf, self.fhash, ext)
        conn = sqlite3.connect(self.db)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM objects WHERE external_id=?",
                  (f"pdf:{self.fhash}",))
        self.assertEqual(c.fetchone()[0], 1)
        c.execute("SELECT COUNT(*) FROM links WHERE link_type='MEASURES_IN_SEGMENT'")
        self.assertEqual(c.fetchone()[0], 1)
        conn.close()

    def test_already_processed_check(self):
        self.assertFalse(already_processed(self.db, self.fhash))
        ext = {"meta": {}, "segments": [{"group": "AGE", "segment": "30대",
                                          "results": {"A": 40}}],
               "cost_usd": 0.01, "usage": {"input_tokens": 100, "output_tokens": 50}}
        store_extraction(self.db, self.pdf, self.fhash, ext)
        self.assertTrue(already_processed(self.db, self.fhash))


class TestRunOrchestrator(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db = os.path.join(self.tmpdir, "t.db")
        init_db(self.db)
        self.inbox = os.path.join(self.tmpdir, "inbox")
        self.processed = os.path.join(self.tmpdir, "processed")
        self.rejected = os.path.join(self.tmpdir, "rejected")
        os.makedirs(self.inbox)
        # valid PDF
        with open(os.path.join(self.inbox, "good.pdf"), "wb") as f:
            f.write(b"%PDF-1.4" + b"x" * 200)
        # invalid PDF (HTML)
        with open(os.path.join(self.inbox, "bad.pdf"), "wb") as f:
            f.write(b"<html>not a pdf</html>" + b"x" * 200)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @mock.patch("src.sync.extract_pdf_segments.AnthropicVisionExtractor")
    def test_invalid_pdf_rejected_without_api_call(self, M_ex):
        # 추출기 인스턴스도 만들지 않아야 (lazy init)
        instance = mock.MagicMock()
        instance.extract.return_value = {
            "meta": {}, "segments": [{"group": "AGE", "segment": "30대",
                                       "results": {"A": 40}}],
            "cost_usd": 0.01, "usage": {"input_tokens": 100, "output_tokens": 50},
        }
        M_ex.return_value = instance

        result = run(self.db, self.inbox, self.processed, self.rejected,
                     limit=10)
        self.assertEqual(result["processed"], 1)  # good.pdf
        self.assertEqual(result["rejected"], 1)  # bad.pdf — magic 검사 실패
        # 좋은 PDF만 API 호출
        self.assertEqual(instance.extract.call_count, 1)

    @mock.patch("src.sync.extract_pdf_segments.AnthropicVisionExtractor")
    def test_zero_segments_rejected(self, M_ex):
        instance = mock.MagicMock()
        instance.extract.return_value = {
            "meta": {"agency": "X"}, "segments": [],
            "cost_usd": 0.05, "usage": {"input_tokens": 5000, "output_tokens": 100},
        }
        M_ex.return_value = instance

        result = run(self.db, self.inbox, self.processed, self.rejected,
                     limit=10)
        # good.pdf는 0 segments라서 rejected, bad.pdf는 magic 실패라서 rejected
        self.assertEqual(result["processed"], 0)
        self.assertEqual(result["rejected"], 2)
        # 비용은 발생 (good.pdf 1회)
        self.assertEqual(result["total_cost_usd"], 0.05)

    @mock.patch("src.sync.extract_pdf_segments.AnthropicVisionExtractor")
    def test_cost_cap_stops_processing(self, M_ex):
        # inbox에 valid PDF 3개 추가
        for i in range(3):
            with open(os.path.join(self.inbox, f"valid{i}.pdf"), "wb") as f:
                f.write(b"%PDF-1.4" + bytes(str(i), 'ascii') + b"x" * 200)

        instance = mock.MagicMock()
        instance.extract.return_value = {
            "meta": {"agency": "X"},
            "segments": [{"group": "AGE", "segment": "30대",
                          "results": {"A": 40}}],
            "cost_usd": 0.20, "usage": {"input_tokens": 10000, "output_tokens": 1000},
        }
        M_ex.return_value = instance

        result = run(self.db, self.inbox, self.processed, self.rejected,
                     limit=10, cost_cap_usd=0.30)
        # 첫 PDF 후 0.20 누적, 두번째 직전에도 0.20<0.30 통과,
        # 세번째 직전에 0.40>0.30 → break
        self.assertLessEqual(result["processed"], 2)
        self.assertLessEqual(result["total_cost_usd"], 0.40)


if __name__ == "__main__":
    unittest.main()
