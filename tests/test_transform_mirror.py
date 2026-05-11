"""transform_mirror 검증 — UPSERT 멱등성, JSON 핸들링, 멤버 함수."""
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.db.init_twin_db import init_db
from src.engine.simulation_engine import ScenarioSimulator
from src.sync.transform_mirror import (
    run_sync,
    update_sync_state,
    upsert_link,
    upsert_object,
    upsert_raw_mirror,
)
from src.utils.candidates import normalize_candidate_name, normalize_results
from src.utils.history import load_segment_history


class TestCandidateNormalization(unittest.TestCase):
    def test_aliases_to_korean_party_names(self):
        self.assertEqual(normalize_candidate_name("DP_lead"), "더불어민주당")
        self.assertEqual(normalize_candidate_name("PPP"), "국민의힘")
        self.assertEqual(normalize_candidate_name("Others"), "기타정당")
        self.assertEqual(normalize_candidate_name("기타"), "기타정당")

    def test_duplicate_aliases_keep_largest_value(self):
        results = normalize_results({
            "DP": 41.0,
            "DP_lead": 42.0,
            "PPP": "35.5",
            "bad": "n/a",
        })
        self.assertEqual(results, {"더불어민주당": 42.0, "국민의힘": 35.5})


class TestUpsertObject(unittest.TestCase):
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

    def test_creates_new_object(self):
        obj_id = upsert_object(self.c, "POLLSTER", "갤럽", "한국갤럽", {"k": "v"})
        self.assertIsInstance(obj_id, int)
        self.c.execute("SELECT name, properties FROM objects WHERE id=?", (obj_id,))
        name, props = self.c.fetchone()
        self.assertEqual(name, "한국갤럽")
        self.assertEqual(json.loads(props), {"k": "v"})

    def test_updates_on_conflict(self):
        id1 = upsert_object(self.c, "POLLSTER", "갤럽", "한국갤럽", {"k": "v1"})
        id2 = upsert_object(self.c, "POLLSTER", "갤럽", "한국갤럽 NEW", {"k": "v2"})
        self.assertEqual(id1, id2, "UPSERT가 동일 id 반환해야 함")
        self.c.execute("SELECT name, properties FROM objects WHERE id=?", (id1,))
        name, props = self.c.fetchone()
        self.assertEqual(name, "한국갤럽 NEW")
        self.assertEqual(json.loads(props), {"k": "v2"})

    def test_multiple_external_id_separate(self):
        id1 = upsert_object(self.c, "POLLSTER", "갤럽", "갤럽")
        id2 = upsert_object(self.c, "POLLSTER", "리얼미터", "리얼미터")
        self.assertNotEqual(id1, id2)

    def test_korean_unicode_preserved(self):
        upsert_object(self.c, "CANDIDATE", "더불어민주당", "더불어민주당",
                      {"한글키": "한글값"})
        self.c.execute("SELECT name, properties FROM objects WHERE external_id='더불어민주당'")
        name, props = self.c.fetchone()
        self.assertEqual(name, "더불어민주당")
        self.assertEqual(json.loads(props), {"한글키": "한글값"})


class TestUpsertLink(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db = os.path.join(self.tmpdir, "t.db")
        init_db(self.db)
        self.conn = sqlite3.connect(self.db)
        self.c = self.conn.cursor()
        self.poll_id = upsert_object(self.c, "POLL", "1", "p1")
        self.cand_id = upsert_object(self.c, "CANDIDATE", "c1", "c1")

    def tearDown(self):
        self.conn.close()
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_creates_link(self):
        upsert_link(self.c, self.poll_id, self.cand_id, "MEASURES",
                    {"support_rate": 42.5})
        self.c.execute("SELECT properties FROM links")
        row = self.c.fetchone()
        self.assertEqual(json.loads(row[0]), {"support_rate": 42.5})

    def test_idempotent(self):
        upsert_link(self.c, self.poll_id, self.cand_id, "MEASURES", {"v": 1})
        upsert_link(self.c, self.poll_id, self.cand_id, "MEASURES", {"v": 2})
        self.c.execute("SELECT COUNT(*) FROM links")
        self.assertEqual(self.c.fetchone()[0], 1)
        self.c.execute("SELECT properties FROM links")
        self.assertEqual(json.loads(self.c.fetchone()[0]), {"v": 2})


class TestRunSyncWithMockedAPI(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db = os.path.join(self.tmpdir, "t.db")
        init_db(self.db)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _polls(self):
        return [
            {
                "id": 100, "agency": "한국갤럽", "date": "2026-05-01",
                "survey_date": "2026-05-01", "survey_year": 2026, "survey_week": None,
                "region": "전국", "district": None, "sample_size": 1000,
                "method": "전화면접", "response_rate": 0.15,
                "category": "election",
                "results": {"더불어민주당": 42.5, "국민의힘": 38.0, "정의당": 5.0},
            },
            {
                "id": 101, "agency": "리얼미터", "date": "2026-05-03",
                "survey_date": "2026-05-03", "survey_year": 2026, "survey_week": None,
                "region": "서울", "district": None, "sample_size": 2500,
                "method": "ARS", "response_rate": 0.04,
                "category": "election",
                "results": {"더불어민주당": 39.8, "국민의힘": 41.5},
            },
        ]

    @mock.patch("src.sync.transform_mirror.fetch_polls")
    def test_sync_creates_expected_objects(self, m):
        m.return_value = self._polls()
        stats = run_sync("http://test/api", self.db)
        self.assertEqual(stats["polls"], 2)
        self.assertEqual(stats["pollsters"], 2)
        self.assertEqual(stats["candidates"], 3)
        self.assertEqual(stats["measures"], 5)  # 3 + 2
        self.assertEqual(stats["seg_measures"], 2)
        self.assertEqual(stats["regions"], 2)

    @mock.patch("src.sync.transform_mirror.fetch_polls")
    def test_sync_idempotent_re_run(self, m):
        m.return_value = self._polls()
        run_sync("http://test/api", self.db)
        run_sync("http://test/api", self.db)  # 두 번째 실행
        conn = sqlite3.connect(self.db)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM objects WHERE obj_type='POLL'")
        self.assertEqual(c.fetchone()[0], 2, "POLL 중복 생성됨")
        c.execute("SELECT COUNT(*) FROM links")
        # 2 CONDUCTED + 5 MEASURES + 2 MEASURES_IN_SEGMENT = 9
        self.assertEqual(c.fetchone()[0], 9, "link 중복 생성됨")
        conn.close()

    @mock.patch("src.sync.transform_mirror.fetch_polls")
    def test_sync_skips_polls_without_results(self, m):
        m.return_value = [
            {"id": 1, "agency": "X", "results": {}, "region": "전국"},  # 빈 results
            {"id": 2, "agency": "Y", "results": None, "region": "전국"},  # None
        ]
        stats = run_sync("http://test/api", self.db)
        self.assertEqual(stats["polls"], 0)

    @mock.patch("src.sync.transform_mirror.fetch_polls")
    def test_sync_handles_non_numeric_support_rate(self, m):
        m.return_value = [{
            "id": 1, "agency": "X", "date": "2026-01-01",
            "region": "전국",
            "results": {"a": 30.0, "b": "invalid", "c": None, "d": 25.0},
        }]
        stats = run_sync("http://test/api", self.db)
        self.assertEqual(stats["measures"], 2, "유효 숫자만 measures 생성되어야")

    @mock.patch("src.sync.transform_mirror.fetch_polls")
    def test_sync_normalizes_candidate_aliases(self, m):
        m.return_value = [{
            "id": 1, "agency": "X", "date": "2026-01-01",
            "region": "전국",
            "results": {"DP_lead": 42.0, "PPP": 35.0, "Others": 3.0},
        }]
        run_sync("http://test/api", self.db)
        conn = sqlite3.connect(self.db)
        c = conn.cursor()
        c.execute("SELECT external_id FROM objects WHERE obj_type='CANDIDATE' ORDER BY external_id")
        self.assertEqual([r[0] for r in c.fetchall()], ["국민의힘", "기타정당", "더불어민주당"])
        c.execute("SELECT properties FROM links WHERE link_type='MEASURES_IN_SEGMENT'")
        props = json.loads(c.fetchone()[0])
        self.assertEqual(props, {"더불어민주당": 42.0, "국민의힘": 35.0, "기타정당": 3.0})
        conn.close()

    @mock.patch("src.sync.transform_mirror.fetch_polls")
    def test_history_averages_same_date_and_matches_simulation_original(self, m):
        m.return_value = [
            {
                "id": 1, "agency": "A", "date": "2026-01-01",
                "region": "전국", "category": "election",
                "results": {"DP": 40.0, "PPP": 30.0},
            },
            {
                "id": 2, "agency": "B", "date": "2026-01-01",
                "region": "전국", "category": "election",
                "results": {"더불어민주당": 44.0, "국민의힘": 34.0},
            },
            {
                "id": 3, "agency": "C", "date": "2026-01-02",
                "region": "전국", "category": "approval_rating",
                "results": {"positive": 60.0, "negative": 30.0},
            },
        ]
        run_sync("http://test/api", self.db)
        dates, candidates = load_segment_history(self.db, "전국")
        self.assertEqual(dates, ["2026-01-01"])
        self.assertEqual(candidates["더불어민주당"], [42.0])
        self.assertEqual(candidates["국민의힘"], [32.0])

        sim = ScenarioSimulator(self.db).run_simulation("전국", 0)
        self.assertEqual(sim["original"]["더불어민주당"], 42.0)
        self.assertEqual(sim["original"]["국민의힘"], 32.0)

    @mock.patch("src.sync.transform_mirror.fetch_polls")
    def test_sync_state_recorded(self, m):
        m.return_value = self._polls()
        run_sync("http://test/api", self.db)
        conn = sqlite3.connect(self.db)
        c = conn.cursor()
        c.execute("SELECT source, last_record_count FROM sync_state")
        row = c.fetchone()
        self.assertEqual(row[0], "pollagg_rest")
        self.assertEqual(row[1], 2)
        conn.close()


if __name__ == "__main__":
    unittest.main()
