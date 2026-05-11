"""Shared segment history aggregation for dashboard and simulation."""
import json
import re
import sqlite3
from collections import defaultdict
from typing import Dict, List, Tuple

from src.utils.candidates import normalize_results


EXCLUDED_RESULT_KEYS = {"positive", "negative"}
EXCLUDED_CATEGORIES = {"approval_rating"}


def load_segment_history(db_path: str, segment: str) -> Tuple[List[str], Dict[str, List[float]]]:
    """Return date-aligned average candidate support for a segment.

    Multiple polls on the same date are averaged per candidate. Approval-rating
    rows are excluded so party support and approval sentiment do not share axes.
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    if segment == "전국" or segment == "TOTAL":
        cursor.execute(
            """
            SELECT p.properties, l.properties
            FROM links l
            JOIN objects p ON l.source_id = p.id
            JOIN objects o ON l.target_id = o.id
            WHERE l.link_type = 'MEASURES_IN_SEGMENT' AND o.name = '전국'
            """
        )
    else:
        cursor.execute(
            """
            SELECT p.properties, l.properties
            FROM links l
            JOIN objects p ON l.source_id = p.id
            JOIN objects o ON l.target_id = o.id
            WHERE l.link_type = 'MEASURES_IN_SEGMENT' AND o.name = ?
            """,
            (segment,),
        )

    bucket = defaultdict(lambda: defaultdict(lambda: [0.0, 0]))
    candidates = set()
    for poll_props, link_props in cursor.fetchall():
        poll = json.loads(poll_props)
        date = poll.get("date")
        if not date or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            continue
        if poll.get("category") in EXCLUDED_CATEGORIES:
            continue

        results = {
            key: value
            for key, value in normalize_results(json.loads(link_props)).items()
            if key not in EXCLUDED_RESULT_KEYS
        }
        if not results:
            continue

        for candidate, rate in results.items():
            bucket[date][candidate][0] += rate
            bucket[date][candidate][1] += 1
            candidates.add(candidate)

    conn.close()

    dates = sorted(bucket.keys())
    aligned = {}
    for candidate in sorted(candidates):
        values = []
        for date in dates:
            total, count = bucket[date].get(candidate, [0.0, 0])
            values.append(round(total / count, 2) if count else None)
        aligned[candidate] = values
    return dates, aligned
