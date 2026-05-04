import sqlite3
import json
import os

SOURCE_DB = "/Users/up_main/Desktop/T_Antigravity/PollAgg/db/pollagg.db"
TARGET_DB = "/Users/up_main/Desktop/T_Antigravity/PTPoll/src/db/ptpoll_twin.db"

def get_or_create_object(cursor, obj_type, name, properties=None):
    """객체가 있으면 ID 반환, 없으면 생성 후 ID 반환"""
    cursor.execute("SELECT id FROM objects WHERE obj_type = ? AND name = ?", (obj_type, name))
    res = cursor.fetchone()
    if res:
        return res[0]
    
    props_json = json.dumps(properties) if properties else "{}"
    cursor.execute("INSERT INTO objects (obj_type, name, properties) VALUES (?, ?, ?)", 
                   (obj_type, name, props_json))
    return cursor.lastrowid

def create_link(cursor, source_id, target_id, link_type, properties=None):
    """객체 간 관계 생성"""
    props_json = json.dumps(properties) if properties else "{}"
    cursor.execute("INSERT INTO links (source_id, target_id, link_type, properties) VALUES (?, ?, ?, ?)",
                   (source_id, target_id, link_type, props_json))

def run_sync():
    if not os.path.exists(SOURCE_DB):
        print(f"Source DB not found at {SOURCE_DB}")
        return

    s_conn = sqlite3.connect(SOURCE_DB)
    t_conn = sqlite3.connect(TARGET_DB)
    s_cursor = s_conn.cursor()
    t_cursor = t_conn.cursor()

    try:
        # 1. PollAgg에서 데이터 읽기 (polls 테이블 기준)
        s_cursor.execute("SELECT id, agency, date, region, results, sample_size, method FROM polls")
        rows = s_cursor.fetchall()
    except Exception as e:
        print(f"Error reading source: {e}")
        return

    for row in rows:
        s_id, agency, p_date, region, results_json, sample_size, method = row
        results = json.loads(results_json)

        # A. POLLSTER 객체 생성/확인
        pollster_id = get_or_create_object(t_cursor, "POLLSTER", agency)

        # B. POLL 객체 생성
        poll_props = {
            "date": p_date,
            "region": region,
            "sample_size": sample_size,
            "method": method,
            "source_id": s_id
        }
        poll_obj_id = get_or_create_object(t_cursor, "POLL", f"{agency}_{p_date}_{region}", poll_props)

        # C. 관계 설정: POLLSTER --[CONDUCTED]--> POLL
        create_link(t_cursor, pollster_id, poll_obj_id, "CONDUCTED")

        # D. CANDIDATE 객체 및 지지율 관계 설정
        for cand_name, support_rate in results.items():
            candidate_id = get_or_create_object(t_cursor, "CANDIDATE", cand_name)
            
            # POLL --[MEASURES]--> CANDIDATE (속성으로 지지율 저장)
            create_link(t_cursor, poll_obj_id, candidate_id, "MEASURES", {"support_rate": support_rate})

        # E. Raw Mirror 저장 (Lineage)
        t_cursor.execute("INSERT INTO raw_mirror (source_table, source_pk, data) VALUES (?, ?, ?)",
                         ("polls", s_id, results_json))

    t_conn.commit()
    print(f"Sync complete. Processed {len(rows)} polls.")
    s_conn.close()
    t_conn.close()

if __name__ == "__main__":
    run_sync()
