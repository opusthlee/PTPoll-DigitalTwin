import os
import json
import sqlite3
import base64
from typing import List, Dict

class NESDCPDFProcessor:
    def __init__(self, db_path: str, api_key: str = None):
        self.db_path = db_path
        self.api_key = api_key

    def process_and_save(self, poll_obj_id: int):
        """AI Vision을 통한 추출 시뮬레이션 및 DB 저장 실행"""
        print(f"[*] Starting Deep Analysis for Poll ID: {poll_obj_id}")
        
        # 1. AI 가상 추출 (실제 구현 시 Vision API 호출)
        extracted_data = self._mock_ai_table_extraction()
        
        # 2. 온톨로지 DB 저장
        self._save_to_ontology(poll_obj_id, extracted_data)

    def _save_to_ontology(self, poll_obj_id: int, data: List[Dict]):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        try:
            for item in data:
                group = item["group"]
                segment = item["segment"]
                results = item["results"]

                seg_name = f"{group}_{segment}"
                
                # SEGMENT 객체 생성 또는 ID 획득
                cursor.execute("SELECT id FROM objects WHERE obj_type='SEGMENT' AND name=?", (seg_name,))
                res = cursor.fetchone()
                
                if res:
                    segment_id = res[0]
                else:
                    cursor.execute(
                        "INSERT INTO objects (obj_type, name, properties) VALUES (?, ?, ?)",
                        ("SEGMENT", seg_name, json.dumps({"category": group, "label": segment}))
                    )
                    segment_id = cursor.lastrowid

                # 관계 생성: POLL --[MEASURES_IN_SEGMENT]--> SEGMENT
                cursor.execute(
                    "INSERT INTO links (source_id, target_id, link_type, properties) VALUES (?, ?, ?, ?)",
                    (poll_obj_id, segment_id, "MEASURES_IN_SEGMENT", json.dumps(results))
                )
            
            conn.commit()
            print(f"[+] Successfully saved {len(data)} cross-tab segments.")
        except Exception as e:
            print(f"[-] Error: {e}")
            conn.rollback()
        finally:
            conn.close()

    def _mock_ai_table_extraction(self) -> List[Dict]:
        return [
            {"group": "AGE", "segment": "20s", "results": {"홍길동": 28.5, "이순신": 41.2}},
            {"group": "AGE", "segment": "30s", "results": {"홍길동": 35.0, "이순신": 38.5}},
            {"group": "REGION", "segment": "Seoul", "results": {"홍길동": 45.2, "이순신": 32.1}},
            {"group": "GENDER", "segment": "Female", "results": {"홍길동": 30.5, "이순신": 44.8}}
        ]

if __name__ == "__main__":
    DB_PATH = "/Users/up_main/Desktop/T_Antigravity/PTPoll/src/db/ptpoll_twin.db"
    processor = NESDCPDFProcessor(DB_PATH)
    # 테스트 데이터 주입
    processor.process_and_save(poll_obj_id=2)
