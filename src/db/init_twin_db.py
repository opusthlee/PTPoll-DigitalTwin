import sqlite3
import os

DB_PATH = "src/db/ptpoll_twin.db"

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # 1. Objects Table: 모든 실체 (Pollster, Candidate, District 등)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS objects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            obj_type TEXT NOT NULL,       -- 'POLLSTER', 'CANDIDATE', 'EVENT' 등
            external_id TEXT UNIQUE,      -- 원본 DB의 ID 또는 고유 식별자
            name TEXT NOT NULL,
            properties JSON,              -- 객체의 세부 속성 (JSON 형식)
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # 2. Links Table: 객체 간의 관계
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER,
            target_id INTEGER,
            link_type TEXT NOT NULL,      -- 'CONDUCTED', 'MEASURES', 'IMPACTED' 등
            properties JSON,              -- 관계의 속성 (예: 지지율 수치)
            FOREIGN KEY(source_id) REFERENCES objects(id),
            FOREIGN KEY(target_id) REFERENCES objects(id)
        )
    ''')

    # 3. Mirroring Buffer: PollAgg에서 가져온 원본 로우 데이터 보관 (Lineage용)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS raw_mirror (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_table TEXT,
            source_pk INTEGER,
            data JSON,
            synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    conn.commit()
    conn.close()
    print(f"PTPoll Twin DB initialized at {DB_PATH}")

if __name__ == "__main__":
    init_db()
