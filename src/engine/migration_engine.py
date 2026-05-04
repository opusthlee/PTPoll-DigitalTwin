import sqlite3
import json

class VoterMigrationEngine:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def analyze_migration(self, poll_id_start: int, poll_id_end: int):
        """두 시점의 여론조사를 비교하여 지지율 전이 추론"""
        print(f"[*] Analyzing Voter Migration: Poll {poll_id_start} -> Poll {poll_id_end}")
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # 1. 두 조사의 세그먼트 데이터 가져오기
        query = '''
            SELECT o.name, l.properties 
            FROM links l 
            JOIN objects o ON l.target_id = o.id 
            WHERE l.source_id = ? AND l.link_type = 'MEASURES_IN_SEGMENT'
        '''
        cursor.execute(query, (poll_id_start,))
        start_data = {name: json.loads(props) for name, props in cursor.fetchall()}
        
        cursor.execute(query, (poll_id_end,))
        end_data = {name: json.loads(props) for name, props in cursor.fetchall()}

        # 2. 세그먼트별 비교 분석
        common_segments = set(start_data.keys()) & set(end_data.keys())
        
        for seg in common_segments:
            s_res = start_data[seg]
            e_res = end_data[seg]
            
            # 변화량 계산 (Net Change)
            diff = {cand: e_res.get(cand, 0) - s_res.get(cand, 0) for cand in set(s_res) | set(e_res)}
            
            # 하락한 후보(유출)와 상승한 후보(유입) 식별
            losers = {c: abs(v) for c, v in diff.items() if v < 0}
            winners = {c: v for c, v in diff.items() if v > 0}
            
            if losers and winners:
                print(f"  [Segment: {seg}] Detected Migration Signal")
                self._create_migration_links(cursor, seg, losers, winners)

        conn.commit()
        conn.close()

    def _create_migration_links(self, cursor, segment_name, losers, winners):
        """추론된 전이 관계를 온톨로지에 기록"""
        # 실제로는 복잡한 수식(Quadratic Programming 등)이 들어가야 하지만, 
        # 우선은 비례 배분 방식으로 전이 관계를 추정하여 링크를 생성합니다.
        total_loss = sum(losers.values())
        
        for l_cand, l_val in losers.items():
            for w_cand, w_val in winners.items():
                # 전이량 추정: (내 하락분) * (상대방의 상승 비중)
                migration_amount = round(l_val * (w_val / sum(winners.values())), 2)
                
                # 온톨로지 링크 생성: Candidate_A --[VOTER_MIGRATION]--> Candidate_B
                # 속성으로 세그먼트 정보와 추정 전이량을 저장
                cursor.execute(
                    "INSERT INTO links (source_id, target_id, link_type, properties) VALUES "
                    "((SELECT id FROM objects WHERE name=?), (SELECT id FROM objects WHERE name=?), ?, ?)",
                    (l_cand, w_cand, "VOTER_MIGRATION", json.dumps({
                        "segment": segment_name,
                        "amount": migration_amount,
                        "logic": "Proportional_Net_Change"
                    }))
                )
                print(f"    - {l_cand} -> {w_cand} ({migration_amount}%) in {segment_name}")

if __name__ == "__main__":
    DB_PATH = "/Users/up_main/Desktop/T_Antigravity/PTPoll/src/db/ptpoll_twin.db"
    engine = VoterMigrationEngine(DB_PATH)
    # 실제 데이터(Poll ID 2, 7번 등)를 기반으로 분석 실행
    # (참고: 리포트상 2번은 갤럽, 7번은 리얼미터 조사임)
    engine.analyze_migration(poll_id_start=2, poll_id_end=7)
