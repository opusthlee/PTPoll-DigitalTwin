"""Schema 초기화 + 멱등성 + 인덱스 검증."""
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.db.init_twin_db import init_db, reset_db


class TestInitTwinDb(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "test_hub.db")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_init_creates_all_tables(self):
        init_db(self.db_path)
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in c.fetchall()}
        for required in ("objects", "links", "raw_mirror", "sync_state"):
            self.assertIn(required, tables, f"missing table: {required}")
        conn.close()

    def test_init_creates_indexes(self):
        init_db(self.db_path)
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT name FROM sqlite_master WHERE type='index'")
        indexes = {row[0] for row in c.fetchall()}
        for required in ("idx_objects_type_name", "idx_links_type_source",
                         "idx_links_type_target", "idx_raw_mirror_source"):
            self.assertIn(required, indexes, f"missing index: {required}")
        conn.close()

    def test_init_is_idempotent(self):
        """두 번 init 해도 에러 없이 동일 schema 유지."""
        init_db(self.db_path)
        # 데이터 삽입 후
        conn = sqlite3.connect(self.db_path)
        conn.execute("INSERT INTO objects (obj_type, external_id, name) VALUES ('TEST','x','x')")
        conn.commit()
        conn.close()
        # 재 init — 데이터 보존되어야 함
        init_db(self.db_path)
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM objects WHERE obj_type='TEST'")
        self.assertEqual(c.fetchone()[0], 1, "기존 데이터가 init 재실행으로 손실됨")
        conn.close()

    def test_reset_clears_data(self):
        init_db(self.db_path)
        conn = sqlite3.connect(self.db_path)
        conn.execute("INSERT INTO objects (obj_type, external_id, name) VALUES ('TEST','x','x')")
        conn.commit()
        conn.close()
        reset_db(self.db_path)
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM objects")
        self.assertEqual(c.fetchone()[0], 0, "reset이 데이터를 비우지 못함")
        conn.close()

    def test_unique_constraint_objects(self):
        """동일 (obj_type, external_id) 두 번 INSERT 시 IntegrityError."""
        init_db(self.db_path)
        conn = sqlite3.connect(self.db_path)
        conn.execute("INSERT INTO objects (obj_type, external_id, name) VALUES ('A','1','a')")
        conn.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO objects (obj_type, external_id, name) VALUES ('A','1','b')")
            conn.commit()
        conn.close()

    def test_unique_constraint_links(self):
        """동일 (source, target, link_type) 두 번 INSERT 시 IntegrityError."""
        init_db(self.db_path)
        conn = sqlite3.connect(self.db_path)
        conn.execute("INSERT INTO objects (obj_type, external_id, name) VALUES ('A','1','a')")
        conn.execute("INSERT INTO objects (obj_type, external_id, name) VALUES ('B','1','b')")
        conn.execute("INSERT INTO links (source_id, target_id, link_type) VALUES (1, 2, 'X')")
        conn.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO links (source_id, target_id, link_type) VALUES (1, 2, 'X')")
            conn.commit()
        conn.close()


if __name__ == "__main__":
    unittest.main()
