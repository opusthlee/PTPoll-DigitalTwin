"""Candidate/party label normalization shared by sync and dashboard code."""
from typing import Any, Dict, Optional


CANDIDATE_ALIASES = {
    "DP": "더불어민주당",
    "DP_lead": "더불어민주당",
    "dp": "더불어민주당",
    "dp_lead": "더불어민주당",
    "PPP": "국민의힘",
    "PPP_lead": "국민의힘",
    "ppp": "국민의힘",
    "ppp_lead": "국민의힘",
    "Others": "기타정당",
    "others": "기타정당",
    "기타": "기타정당",
}


def normalize_candidate_name(name: Any) -> Optional[str]:
    if name is None:
        return None
    text = str(name).strip()
    if not text:
        return None
    return CANDIDATE_ALIASES.get(text, text)


def normalize_results(results: Any) -> Dict[str, float]:
    """Normalize result keys and coerce numeric values.

    If aliases for the same candidate appear in one payload, keep the largest
    value instead of summing. Aliases are alternate labels, not additive fields.
    """
    if not isinstance(results, dict):
        return {}

    normalized: Dict[str, float] = {}
    for raw_name, raw_rate in results.items():
        name = normalize_candidate_name(raw_name)
        if not name:
            continue
        try:
            rate = float(raw_rate)
        except (TypeError, ValueError):
            continue
        if name in normalized:
            normalized[name] = max(normalized[name], rate)
        else:
            normalized[name] = rate
    return normalized
