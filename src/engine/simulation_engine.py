import sqlite3
import json

class ScenarioSimulator:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def run_simulation(self, target_segment: str, impact_factor: float, start_date: str = None):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # 1. 원시 데이터 가져오기 (SQL 가공 최소화)
        if target_segment == "전국" or target_segment == "TOTAL":
            # 세그먼트 테이블에서 '전국'을 찾거나, 없으면 전체 지지율(MEASURES)에서 가져옴
            cursor.execute('''
                SELECT p.properties, l.properties, 'SEGMENT'
                FROM links l JOIN objects p ON l.source_id = p.id JOIN objects o ON l.target_id = o.id
                WHERE l.link_type = 'MEASURES_IN_SEGMENT' AND o.name = '전국'
            ''')
        else:
            cursor.execute('''
                SELECT p.properties, l.properties, 'SEGMENT'
                FROM links l JOIN objects p ON l.source_id = p.id JOIN objects o ON l.target_id = o.id
                WHERE l.link_type = 'MEASURES_IN_SEGMENT' AND o.name = ?
            ''', (target_segment,))
        
        rows = cursor.fetchall()
        if not rows:
            conn.close()
            return {"error": f"No data found for segment: {target_segment}"}

        # 2. 파이썬에서 데이터 정렬 및 파싱
        history = {}
        for p_props, l_props, _ in rows:
            p, l = json.loads(p_props), json.loads(l_props)
            date = p.get('date')
            if not date: continue
            if start_date and date < start_date: continue
            history[date] = {k: float(v) for k, v in l.items()}

        if not history:
            conn.close()
            return {"error": "No data matches the selected time range"}

        sorted_dates = sorted(history.keys())
        latest_res = history[sorted_dates[-1]]
        
        # 3. 누적 추세(Overall Momentum) 계산
        overall_momentum = {}
        for cand in latest_res:
            deltas = []
            for i in range(1, len(sorted_dates)):
                v1 = history[sorted_dates[i-1]].get(cand, 0)
                v2 = history[sorted_dates[i]].get(cand, 0)
                deltas.append(v2 - v1)
            overall_momentum[cand] = sum(deltas) / len(deltas) if deltas else 0

        # 4. 미래 예측
        simulated_res = {}
        for cand, val in latest_res.items():
            projected_val = val + overall_momentum[cand] + (val * impact_factor)
            simulated_res[cand] = round(max(0, projected_val), 2)

        conn.close()
        return {
            "target": target_segment,
            "original": latest_res,
            "simulated": simulated_res,
            "momentum": {c: round(v, 2) for c, v in overall_momentum.items()},
            "dates_analyzed": sorted_dates
        }
