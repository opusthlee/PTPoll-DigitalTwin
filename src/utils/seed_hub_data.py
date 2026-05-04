import sqlite3
import json
import os
import unicodedata

BASE_DIR = "/Users/up_main/Desktop/T_Antigravity/PTPoll"
DB_PATH = os.path.join(BASE_DIR, "data/2026_local_election/hub.db")

def normalize_ko(text):
    return unicodedata.normalize('NFC', text)

def seed_data():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.executescript('DROP TABLE IF EXISTS links; DROP TABLE IF EXISTS objects;')
    cursor.execute('CREATE TABLE objects (id INTEGER PRIMARY KEY AUTOINCREMENT, obj_type TEXT, name TEXT, properties TEXT)')
    cursor.execute('CREATE TABLE links (id INTEGER PRIMARY KEY AUTOINCREMENT, source_id INTEGER, target_id INTEGER, link_type TEXT, properties TEXT)')

    cands = {}
    for n in ["홍길동", "이순신", "세종대왕"]:
        cursor.execute("INSERT INTO objects (obj_type, name, properties) VALUES (?, ?, ?)", ("CANDIDATE", normalize_ko(n), json.dumps({})))
        cands[normalize_ko(n)] = cursor.lastrowid

    scenarios = {
        "전국": {"H": (30, 2), "L": (35, -1), "S": (10, 0)},
        "서울": {"H": (25, 4), "L": (40, -2), "S": (12, -0.5)},
        "종로구": {"H": (45, -2), "L": (25, 3), "S": (5, 1)},
        "20대": {"H": (20, 1), "L": (45, -3), "S": (15, 2)},
        "30대": {"H": (40, 3), "L": (30, 1), "S": (10, -2)},
        "남성": {"H": (35, 0.5), "L": (35, 0.5), "S": (10, 0)},
        "여성": {"H": (30, 5), "L": (30, -4), "S": (10, 0)},
        "전체 연령": {"H": (30, 2), "L": (35, -1), "S": (10, 0)},
        "전체 성별": {"H": (30, 2), "L": (35, -1), "S": (10, 0)}
    }

    dates = ["2026-04-01", "2026-04-10", "2026-04-20", "2026-04-30", "2026-05-04"]
    for name, trend in scenarios.items():
        norm_name = normalize_ko(name)
        cursor.execute("INSERT INTO objects (obj_type, name, properties) VALUES (?, ?, ?)", ("SEGMENT", norm_name, json.dumps({"category": "DYNAMIC"})))
        sid = cursor.lastrowid
        
        for i, date in enumerate(dates):
            cursor.execute("SELECT id FROM objects WHERE name=?", (f"P_{date}",))
            row = cursor.fetchone()
            if row: pid = row[0]
            else:
                cursor.execute("INSERT INTO objects (obj_type, name, properties) VALUES (?, ?, ?)", ("POLL", f"P_{date}", json.dumps({"date": date})))
                pid = cursor.lastrowid
            
            h = trend["H"][0] + trend["H"][1] * i
            l = trend["L"][0] + trend["L"][1] * i
            s = trend["S"][0] + trend["S"][1] * i
            res = {normalize_ko("홍길동"): round(h, 1), normalize_ko("이순신"): round(l, 1), normalize_ko("세종대왕"): round(s, 1)}
            
            cursor.execute("INSERT INTO links (source_id, target_id, link_type, properties) VALUES (?, ?, ?, ?)", (pid, sid, "MEASURES_IN_SEGMENT", json.dumps(res)))
            if name == "전국":
                for cand_name, rate in res.items():
                    cursor.execute("INSERT INTO links (source_id, target_id, link_type, properties) VALUES (?, ?, ?, ?)", (pid, cands[normalize_ko(cand_name)], "MEASURES", json.dumps({"support_rate": rate})))

    conn.commit(); conn.close()
    print("[*] NFC-Normalized Robust data seeded.")

if __name__ == "__main__":
    seed_data()
