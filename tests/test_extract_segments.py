"""extract_segments 검증 — 날짜 추출, NESDC POLL 생성, idempotency."""
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.db.init_twin_db import init_db
from src.sync.extract_segments import (
    _extract_first_date,
    already_processed,
    process_ntt_id,
    run_extract,
)


class TestExtractFirstDate(unittest.TestCase):
    def test_extracts_iso_date(self):
        self.assertEqual(_extract_first_date("2026-05-04"), "2026-05-04")

    def test_extracts_from_messy_text(self):
        s = "2026-05-04\r\n\t\t\t14시 ~ 21시\r\n\t2026-05-05"
        self.assertEqual(_extract_first_date(s), "2026-05-04")

    def test_returns_none_when_no_date(self):
        self.assertIsNone(_extract_first_date("어제"))
        self.assertIsNone(_extract_first_date(""))
        self.assertIsNone(_extract_first_date(None))


class TestProcessNttId(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db = os.path.join(self.tmpdir, "t.db")
        init_db(self.db)
        self.conn = sqlite3.connect(self.db)
        self.c = self.conn.cursor()

    def tearDown(self):
        self.conn.close()
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _stub_demographics(self, ntt_id="X1"):
        return {
            "ntt_id": ntt_id,
            "meta": {
                "조사기관명": "조원씨앤아이",
                "조사지역": "경기도",
                "조사일시": "2026-05-04 14시 ~ 2026-05-05",
                "선거명": "지방선거",
                "조사대상": "만 18세 이상",
            },
            "demographics": {
                "TOTAL": {"전체": 802},
                "GENDER": {"남": 476, "여": 326},
                "AGE": {"30대": 93, "40대": 143},
                "REGION": {"1권역": 132},
            },
            "table_count": 29,
        }

    @mock.patch("src.sync.extract_segments.fetch_demographics")
    def test_creates_poll_with_date(self, m):
        m.return_value = self._stub_demographics()
        process_ntt_id(self.c, "X1")
        self.c.execute("SELECT name, properties FROM objects WHERE external_id='nesdc:X1'")
        name, props = self.c.fetchone()
        p = json.loads(props)
        self.assertEqual(p["date"], "2026-05-04")
        self.assertEqual(p["agency"], "조원씨앤아이")
        self.assertEqual(p["total_n"], 802)

    @mock.patch("src.sync.extract_segments.fetch_demographics")
    def test_creates_segments_for_each_demographic(self, m):
        m.return_value = self._stub_demographics()
        process_ntt_id(self.c, "X1")
        self.c.execute("SELECT obj_type, name, properties FROM objects WHERE obj_type='SEGMENT'")
        rows = self.c.fetchall()
        names = sorted(r[1] for r in rows)
        self.assertEqual(names, sorted(["남", "여", "30대", "40대", "1권역"]))

    @mock.patch("src.sync.extract_segments.fetch_demographics")
    def test_segment_categories_correct(self, m):
        m.return_value = self._stub_demographics()
        process_ntt_id(self.c, "X1")
        self.c.execute("SELECT name, properties FROM objects WHERE obj_type='SEGMENT'")
        cats = {r[0]: json.loads(r[1])["category"] for r in self.c.fetchall()}
        self.assertEqual(cats["남"], "GENDER")
        self.assertEqual(cats["여"], "GENDER")
        self.assertEqual(cats["30대"], "AGE")
        self.assertEqual(cats["1권역"], "REGION_FRAME")

    @mock.patch("src.sync.extract_segments.fetch_demographics")
    def test_creates_sampled_links(self, m):
        m.return_value = self._stub_demographics()
        process_ntt_id(self.c, "X1")
        self.c.execute("SELECT COUNT(*) FROM links WHERE link_type='SAMPLED'")
        # 5 segments × 1 link
        self.assertEqual(self.c.fetchone()[0], 5)

    @mock.patch("src.sync.extract_segments.fetch_demographics")
    def test_sampled_link_has_n_value(self, m):
        m.return_value = self._stub_demographics()
        process_ntt_id(self.c, "X1")
        self.c.execute("""
            SELECT s.name, l.properties FROM links l
            JOIN objects s ON l.target_id = s.id
            WHERE l.link_type='SAMPLED' AND s.name='30대'
        """)
        name, props = self.c.fetchone()
        self.assertEqual(json.loads(props)["n"], 93)

    @mock.patch("src.sync.extract_segments.fetch_demographics")
    def test_idempotent_reprocess(self, m):
        m.return_value = self._stub_demographics()
        process_ntt_id(self.c, "X1")
        process_ntt_id(self.c, "X1")
        self.c.execute("SELECT COUNT(*) FROM objects WHERE external_id='nesdc:X1'")
        self.assertEqual(self.c.fetchone()[0], 1)
        self.c.execute("SELECT COUNT(*) FROM links WHERE link_type='SAMPLED'")
        self.assertEqual(self.c.fetchone()[0], 5, "SAMPLED link 중복")

    @mock.patch("src.sync.extract_segments.fetch_demographics")
    def test_handles_missing_meta_fields(self, m):
        m.return_value = {
            "ntt_id": "X2", "meta": {}, "demographics": {"TOTAL": {"전체": 100}},
        }
        process_ntt_id(self.c, "X2")
        self.c.execute("SELECT properties FROM objects WHERE external_id='nesdc:X2'")
        p = json.loads(self.c.fetchone()[0])
        self.assertEqual(p["agency"], "Unknown")
        self.assertIsNone(p["date"])

    @mock.patch("src.sync.extract_segments.fetch_demographics")
    def test_already_processed_check(self, m):
        m.return_value = self._stub_demographics()
        self.assertFalse(already_processed(self.c, "X1"))
        process_ntt_id(self.c, "X1")
        self.assertTrue(already_processed(self.c, "X1"))


class TestRunExtract(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db = os.path.join(self.tmpdir, "t.db")
        init_db(self.db)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @mock.patch("src.sync.extract_segments.fetch_demographics")
    @mock.patch("src.sync.extract_segments.list_recent_ntt_ids")
    def test_run_extract_processes_all(self, m_list, m_fetch):
        m_list.return_value = ["A1", "A2"]
        m_fetch.return_value = {
            "ntt_id": "A1", "meta": {"조사기관명": "X"},
            "demographics": {"TOTAL": {"전체": 100}, "GENDER": {"남": 50, "여": 50}},
        }
        result = run_extract(self.db, limit=10, sleep_between=0)
        self.assertEqual(result["processed"], 2)
        self.assertEqual(result["failed"], 0)
        self.assertGreater(result["sampled_links"], 0)

    @mock.patch("src.sync.extract_segments.fetch_demographics")
    @mock.patch("src.sync.extract_segments.list_recent_ntt_ids")
    def test_skip_existing_skips_already_processed(self, m_list, m_fetch):
        m_list.return_value = ["A1"]
        m_fetch.return_value = {
            "ntt_id": "A1", "meta": {"조사기관명": "X"},
            "demographics": {"TOTAL": {"전체": 100}, "GENDER": {"남": 50}},
        }
        run_extract(self.db, limit=10, sleep_between=0)
        m_fetch.reset_mock()
        result = run_extract(self.db, limit=10, sleep_between=0)
        self.assertEqual(result["processed"], 0)
        self.assertEqual(result["skipped"], 1)
        m_fetch.assert_not_called()

    @mock.patch("src.sync.extract_segments.fetch_demographics")
    @mock.patch("src.sync.extract_segments.list_recent_ntt_ids")
    def test_no_skip_existing_reprocesses(self, m_list, m_fetch):
        m_list.return_value = ["A1"]
        m_fetch.return_value = {
            "ntt_id": "A1", "meta": {"조사기관명": "X"},
            "demographics": {"TOTAL": {"전체": 100}, "GENDER": {"남": 50}},
        }
        run_extract(self.db, limit=10, sleep_between=0)
        result = run_extract(self.db, limit=10, sleep_between=0,
                             skip_existing=False)
        self.assertEqual(result["processed"], 1)

    @mock.patch("src.sync.extract_segments.fetch_demographics")
    @mock.patch("src.sync.extract_segments.list_recent_ntt_ids")
    def test_continues_on_individual_failure(self, m_list, m_fetch):
        m_list.return_value = ["A1", "A2", "A3"]

        def side_effect(nid):
            if nid == "A2":
                raise Exception("network blip")
            return {"ntt_id": nid, "meta": {"조사기관명": "X"},
                    "demographics": {"TOTAL": {"전체": 100}}}
        m_fetch.side_effect = side_effect
        result = run_extract(self.db, limit=10, sleep_between=0)
        self.assertEqual(result["processed"], 2)
        self.assertEqual(result["failed"], 1)


if __name__ == "__main__":
    unittest.main()
