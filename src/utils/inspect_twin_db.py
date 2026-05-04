import sqlite3
import json
import os

DB_PATH = "src/db/ptpoll_twin.db"
REPORT_PATH = "db_review.md"

def generate_report():
    if not os.path.exists(DB_PATH):
        print(f"DB not found at {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write("# PTPoll Twin Database Review Report\n\n")
        
        # 1. 요약 통계
        f.write("## 1. Summary Statistics\n")
        cursor.execute("SELECT obj_type, COUNT(*) FROM objects GROUP BY obj_type")
        obj_stats = cursor.fetchall()
        f.write("- **Objects Count:**\n")
        for t, c in obj_stats:
            f.write(f"  - {t}: {c}\n")
            
        cursor.execute("SELECT link_type, COUNT(*) FROM links GROUP BY link_type")
        link_stats = cursor.fetchall()
        f.write("- **Links Count:**\n")
        for t, c in link_stats:
            f.write(f"  - {t}: {c}\n")
        f.write("\n---\n")

        # 2. 객체 리뷰 (Objects)
        f.write("## 2. Objects Detail (Sample)\n")
        f.write("| ID | Type | Name | Properties |\n")
        f.write("| :--- | :--- | :--- | :--- |\n")
        cursor.execute("SELECT id, obj_type, name, properties FROM objects LIMIT 20")
        for row in cursor.fetchall():
            props = json.loads(row[3])
            f.write(f"| {row[0]} | {row[1]} | {row[2]} | `{props}` |\n")
        f.write("\n---\n")

        # 3. 관계 리뷰 (Links & Knowledge Graph)
        f.write("## 3. Relationships & Values (Knowledge Graph)\n")
        f.write("| Source (From) | Relation (Link) | Target (To) | Values/Properties |\n")
        f.write("| :--- | :--- | :--- | :--- |\n")
        
        query = '''
            SELECT o1.name, l.link_type, o2.name, l.properties
            FROM links l
            JOIN objects o1 ON l.source_id = o1.id
            JOIN objects o2 ON l.target_id = o2.id
            LIMIT 50
        '''
        cursor.execute(query)
        for row in cursor.fetchall():
            props = json.loads(row[3])
            f.write(f"| {row[0]} | **{row[1]}** | {row[2]} | `{props}` |\n")

    conn.close()
    print(f"[*] Review report generated: {REPORT_PATH}")

if __name__ == "__main__":
    generate_report()
