"""
PTPoll Twin DB schema 초기화. UNIQUE 제약 + sync_state 테이블 포함.
멱등(idempotent) 실행 가능 — IF NOT EXISTS 사용.

DB_PATH 환경변수로 경로 override 가능. 기본은 PROJECT 단위 hub.db.
"""
import argparse
import os
import sqlite3
import sys

DEFAULT_DB = "data/2026_local_election/hub.db"


def init_db(db_path: str) -> None:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    # 1. Objects: 모든 실체 (POLLSTER, POLL, CANDIDATE, SEGMENT, EVENT 등)
    #    UNIQUE(obj_type, external_id)로 idempotent UPSERT 가능
    c.execute("""
        CREATE TABLE IF NOT EXISTS objects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            obj_type TEXT NOT NULL,
            external_id TEXT,
            name TEXT NOT NULL,
            properties JSON DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(obj_type, external_id)
        )
    """)

    # 2. Links: 객체 간 관계 (CONDUCTED, MEASURES, MEASURES_IN_SEGMENT, IMPACTED 등)
    #    UNIQUE(source, target, link_type)로 동일 관계 중복 방지
    c.execute("""
        CREATE TABLE IF NOT EXISTS links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER NOT NULL,
            target_id INTEGER NOT NULL,
            link_type TEXT NOT NULL,
            properties JSON DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source_id, target_id, link_type),
            FOREIGN KEY(source_id) REFERENCES objects(id),
            FOREIGN KEY(target_id) REFERENCES objects(id)
        )
    """)

    # 3. raw_mirror: 외부 소스 원본 데이터 보관 (lineage 추적)
    c.execute("""
        CREATE TABLE IF NOT EXISTS raw_mirror (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            source_pk TEXT NOT NULL,
            data JSON,
            synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source, source_pk)
        )
    """)

    # 4. sync_state: 외부 소스별 최종 sync 시점·통계
    c.execute("""
        CREATE TABLE IF NOT EXISTS sync_state (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL UNIQUE,
            last_synced_at TIMESTAMP,
            last_record_count INTEGER,
            notes TEXT
        )
    """)

    # 인덱스
    c.execute("CREATE INDEX IF NOT EXISTS idx_objects_type_name ON objects(obj_type, name)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_links_type_source ON links(link_type, source_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_links_type_target ON links(link_type, target_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_raw_mirror_source ON raw_mirror(source, source_pk)")

    conn.commit()
    conn.close()
    print(f"[init] PTPoll hub.db schema ready at {db_path}")


def reset_db(db_path: str) -> None:
    if os.path.exists(db_path):
        os.remove(db_path)
        print(f"[reset] removed {db_path}")
    init_db(db_path)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.environ.get("DB_PATH", DEFAULT_DB))
    ap.add_argument("--reset", action="store_true", help="기존 DB 삭제 후 재생성")
    args = ap.parse_args()
    if args.reset:
        reset_db(args.db)
    else:
        init_db(args.db)
