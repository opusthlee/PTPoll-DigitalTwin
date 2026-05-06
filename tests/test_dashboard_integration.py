"""Dashboard /api/meta 통합 검증 — hub.db 직접 조회로 dashboard 호출 시뮬."""
import json
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.db.init_twin_db import init_db
from src.sync.transform_mirror import upsert_link, upsert_object


class TestDashboardMetaQuery(unittest.TestCase):
    """/api/meta가 사용하는 SQL 쿼리 검증 — segment 필터, dates dedupe."""

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

    def _setup_data(self):
        """REGION segment + MEASURES_IN_SEGMENT (대시보드 노출 대상),
        AGE segment + SAMPLED only (대시보드 비노출 대상)."""
        # POLL
        poll = upsert_object(self.c, "POLL", "p1", "Test Poll",
                             {"date": "2026-01-15"})
        # REGION segment + MEASURES_IN_SEGMENT (have data)
        region = upsert_object(self.c, "SEGMENT", "REGION:서울", "서울",
                               {"category": "REGION"})
        upsert_link(self.c, poll, region, "MEASURES_IN_SEGMENT",
                    {"a": 50.0, "b": 50.0})
        # AGE segment + SAMPLED only (no candidate data — should be hidden)
        age = upsert_object(self.c, "SEGMENT", "AGE:30대", "30대",
                            {"category": "AGE"})
        upsert_link(self.c, poll, age, "SAMPLED", {"n": 100})
        # POLL with duplicate + None date
        upsert_object(self.c, "POLL", "p2", "Same date", {"date": "2026-01-15"})
        upsert_object(self.c, "POLL", "p3", "No date", {"date": None})

    def test_segments_filtered_to_those_with_measures(self):
        """대시보드 /api/meta 쿼리: MEASURES_IN_SEGMENT가 있는 segment만 반환."""
        self._setup_data()
        self.c.execute("""
            SELECT s.name, s.properties FROM objects s
            WHERE s.obj_type='SEGMENT'
              AND EXISTS (SELECT 1 FROM links l
                          WHERE l.target_id = s.id AND l.link_type='MEASURES_IN_SEGMENT')
        """)
        rows = self.c.fetchall()
        names = [r[0] for r in rows]
        self.assertIn("서울", names, "MEASURES_IN_SEGMENT 있는 SEGMENT는 노출되어야")
        self.assertNotIn("30대", names, "SAMPLED만 있는 SEGMENT는 비노출")

    def test_dates_deduped_and_filtered_none(self):
        """대시보드 /api/meta dates: DISTINCT + None 제외 + sorted."""
        self._setup_data()
        self.c.execute("SELECT DISTINCT properties->>'date' FROM objects WHERE obj_type='POLL'")
        dates = sorted({d for (d,) in self.c.fetchall() if d})
        self.assertEqual(dates, ["2026-01-15"], "duplicate 제거 + None 필터")

    def test_dates_no_typeerror_with_nones(self):
        """과거 버그 회귀: None이 sort()에 들어가면 TypeError 발생했음."""
        self._setup_data()
        # 새 쿼리 (DISTINCT + WHERE 필터)는 None을 SQL에서 제외
        self.c.execute("""
            SELECT DISTINCT properties->>'date' FROM objects
            WHERE obj_type='POLL' AND properties->>'date' IS NOT NULL
        """)
        dates = sorted([d for (d,) in self.c.fetchall()])
        self.assertNotIn(None, dates)


class TestRealHubDb(unittest.TestCase):
    """실제 운영 hub.db 정합성 — 통합 환경 검사."""

    @classmethod
    def setUpClass(cls):
        cls.db = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data/2026_local_election/hub.db",
        )
        if not os.path.exists(cls.db):
            raise unittest.SkipTest(f"hub.db not found: {cls.db}")
        cls.conn = sqlite3.connect(cls.db)
        cls.c = cls.conn.cursor()

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "conn"):
            cls.conn.close()

    def test_pollagg_polls_have_date(self):
        """PollAgg 출처 POLL은 항상 date 있어야 — 대시보드 sort 안전 보장.
        PDF/NESDC 출처는 메타 추출 실패 시 None 가능 → SQL 필터 책임."""
        self.c.execute(
            "SELECT COUNT(*) FROM objects WHERE obj_type='POLL' "
            "AND external_id NOT LIKE 'nesdc:%' AND external_id NOT LIKE 'pdf:%' "
            "AND (properties->>'date' IS NULL OR properties->>'date' = '')"
        )
        self.assertEqual(self.c.fetchone()[0], 0,
                         "PollAgg POLL에 date 누락 — sync 결함")

    def test_no_orphan_links(self):
        self.c.execute("""
            SELECT COUNT(*) FROM links l
            WHERE NOT EXISTS (SELECT 1 FROM objects o WHERE o.id = l.source_id)
               OR NOT EXISTS (SELECT 1 FROM objects o WHERE o.id = l.target_id)
        """)
        self.assertEqual(self.c.fetchone()[0], 0, "고아 link 발견")

    def test_no_duplicate_object_keys(self):
        """UNIQUE 제약이 데이터 레벨에서 지켜졌는지."""
        self.c.execute("""
            SELECT obj_type, external_id, COUNT(*) FROM objects
            GROUP BY obj_type, external_id HAVING COUNT(*) > 1
        """)
        dups = self.c.fetchall()
        self.assertEqual(len(dups), 0, f"중복 객체: {dups}")

    def test_no_duplicate_links(self):
        self.c.execute("""
            SELECT source_id, target_id, link_type, COUNT(*) FROM links
            GROUP BY source_id, target_id, link_type HAVING COUNT(*) > 1
        """)
        dups = self.c.fetchall()
        self.assertEqual(len(dups), 0, f"중복 링크: {dups}")

    def test_sync_state_recent(self):
        """sync_state에 최근 sync 기록이 있어야."""
        self.c.execute("SELECT source, last_synced_at FROM sync_state")
        rows = self.c.fetchall()
        self.assertGreater(len(rows), 0, "sync_state 비어있음 — sync 실행 필요")
        sources = {r[0] for r in rows}
        self.assertIn("pollagg_rest", sources)

    def test_pollagg_polls_have_results(self):
        """PollAgg sync된 POLL에는 항상 MEASURES link이 있어야.
        NESDC HTML POLL과 PDF POLL은 MEASURES 없을 수 있음 (segment 단위만)."""
        self.c.execute("""
            SELECT COUNT(*) FROM objects o
            WHERE o.obj_type='POLL'
              AND o.external_id NOT LIKE 'nesdc:%'
              AND o.external_id NOT LIKE 'pdf:%'
              AND NOT EXISTS (SELECT 1 FROM links l
                              WHERE l.source_id=o.id AND l.link_type='MEASURES')
        """)
        orphans = self.c.fetchone()[0]
        self.assertEqual(orphans, 0,
                         "결과 없는 PollAgg POLL 발견 — sync 필터 누락")

    def test_segments_have_proper_categories(self):
        """SEGMENT는 모두 properties.category 설정되어야 (대시보드 분류용)."""
        self.c.execute("""
            SELECT name, properties FROM objects WHERE obj_type='SEGMENT'
        """)
        for name, props in self.c.fetchall():
            p = json.loads(props)
            self.assertIn(p.get("category"),
                          {"REGION", "REGION_FRAME", "AGE", "GENDER"},
                          f"SEGMENT '{name}' category 비정상: {p}")


if __name__ == "__main__":
    unittest.main()
