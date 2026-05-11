from src.utils.history import load_segment_history

class ScenarioSimulator:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def run_simulation(self, target_segment: str, impact_factor: float, start_date: str = None):
        dates, candidates = load_segment_history(self.db_path, target_segment)
        if not dates:
            return {"error": f"No data found for segment: {target_segment}"}

        start_idx = 0
        if start_date:
            while start_idx < len(dates) and dates[start_idx] < start_date:
                start_idx += 1
        sorted_dates = dates[start_idx:]
        if not sorted_dates:
            return {"error": "No data matches the selected time range"}

        values_by_candidate = {name: values[start_idx:] for name, values in candidates.items()}

        latest_res = {}
        for cand, values in values_by_candidate.items():
            if values and values[-1] is not None:
                latest_res[cand] = values[-1]
        
        # 3. 누적 추세(Overall Momentum) 계산
        overall_momentum = {}
        for cand, values in values_by_candidate.items():
            deltas = []
            last_value = None
            for value in values:
                if value is None:
                    continue
                if last_value is not None:
                    deltas.append(value - last_value)
                last_value = value
            overall_momentum[cand] = sum(deltas) / len(deltas) if deltas else 0

        # 4. 미래 예측
        simulated_res = {}
        for cand, val in latest_res.items():
            projected_val = val + overall_momentum[cand] + (val * impact_factor)
            simulated_res[cand] = round(max(0, projected_val), 2)

        return {
            "target": target_segment,
            "original": latest_res,
            "simulated": simulated_res,
            "momentum": {c: round(v, 2) for c, v in overall_momentum.items()},
            "dates_analyzed": sorted_dates
        }
