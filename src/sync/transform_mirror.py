"""
PollAgg REST API → PTPoll hub.db sync.

특징:
- prod REST(`https://api-poll.dailyprizm.com/api/data`)에서 polls 가져옴
- UPSERT 기반 → 같은 sync 여러번 실행해도 안전 (블로커 #2 해결)
- raw_mirror에 원본 JSON 보존 (lineage 추적)
- sync_state에 마지막 sync 시점·건수 기록
- PollAgg의 region 컬럼을 PTPoll의 SEGMENT(category=REGION)로 자동 매핑
  → 별도 PDF 추출 없어도 "전국·서울·영남 등" 단위 trend/simulation 작동 (블로커 #5 부분 해결)
- AGE/GENDER segment는 PDF 추출 구현 후 별도 추가 (이 sync 범위 밖)

사용법:
    python src/sync/transform_mirror.py                                 # 전체 카테고리
    python src/sync/transform_mirror.py --category local_election       # 특정 카테고리만
    python src/sync/transform_mirror.py --since 2026-01-01               # 기간 필터
"""
import argparse
import gzip
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

from src.utils.candidates import normalize_results

DEFAULT_API_BASE = os.environ.get("POLLAGG_API", "https://api-poll.dailyprizm.com/api")
DEFAULT_DB = os.environ.get("DB_PATH", "data/2026_local_election/hub.db")
SOURCE_NAME = "pollagg_rest"


def fetch_polls(api_base: str, params: Dict[str, Optional[str]]) -> list:
    """REST GET /api/data?... — PollAgg 공식 API"""
    qs = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    url = f"{api_base}/data" + (f"?{qs}" if qs else "")
    req = urllib.request.Request(url, headers={"Accept-Encoding": "gzip"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                data = gzip.decompress(data)
            return json.loads(data)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"API HTTP {e.code}: {e.reason} — {url}") from e


def upsert_object(c: sqlite3.Cursor, obj_type: str, external_id: str,
                  name: str, properties: Optional[Dict[str, Any]] = None) -> int:
    """객체 UPSERT. (obj_type, external_id) 기준. id 반환."""
    props = json.dumps(properties or {}, ensure_ascii=False)
    c.execute(
        """
        INSERT INTO objects (obj_type, external_id, name, properties)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(obj_type, external_id) DO UPDATE SET
            name = excluded.name,
            properties = excluded.properties,
            updated_at = CURRENT_TIMESTAMP
        """,
        (obj_type, external_id, name, props),
    )
    c.execute("SELECT id FROM objects WHERE obj_type=? AND external_id=?",
              (obj_type, external_id))
    return c.fetchone()[0]


def upsert_link(c: sqlite3.Cursor, source_id: int, target_id: int,
                link_type: str, properties: Optional[Dict[str, Any]] = None) -> None:
    """링크 UPSERT. (source, target, link_type) 기준."""
    props = json.dumps(properties or {}, ensure_ascii=False)
    c.execute(
        """
        INSERT INTO links (source_id, target_id, link_type, properties)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(source_id, target_id, link_type) DO UPDATE SET
            properties = excluded.properties,
            updated_at = CURRENT_TIMESTAMP
        """,
        (source_id, target_id, link_type, props),
    )


def upsert_raw_mirror(c: sqlite3.Cursor, source: str, source_pk: str,
                      data: Dict[str, Any]) -> None:
    c.execute(
        """
        INSERT INTO raw_mirror (source, source_pk, data)
        VALUES (?, ?, ?)
        ON CONFLICT(source, source_pk) DO UPDATE SET
            data = excluded.data,
            synced_at = CURRENT_TIMESTAMP
        """,
        (source, source_pk, json.dumps(data, ensure_ascii=False)),
    )


def update_sync_state(c: sqlite3.Cursor, source: str, count: int, notes: str) -> None:
    c.execute(
        """
        INSERT INTO sync_state (source, last_synced_at, last_record_count, notes)
        VALUES (?, CURRENT_TIMESTAMP, ?, ?)
        ON CONFLICT(source) DO UPDATE SET
            last_synced_at = CURRENT_TIMESTAMP,
            last_record_count = excluded.last_record_count,
            notes = excluded.notes
        """,
        (source, count, notes),
    )


def run_sync(api_base: str, db_path: str, category: Optional[str] = None,
             since: Optional[str] = None, until: Optional[str] = None) -> Dict[str, int]:
    polls = fetch_polls(api_base, {"category": category, "since": since, "until": until})
    print(f"[sync] fetched {len(polls)} polls from {api_base}/data "
          f"(category={category}, since={since}, until={until})")

    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    pollsters: set = set()
    candidates: set = set()
    regions: set = set()
    poll_count = 0
    measures = 0
    seg_measures = 0

    for p in polls:
        poll_id = str(p["id"])
        agency = (p.get("agency") or "Unknown").strip()
        results = normalize_results(p.get("results") or {})
        if not results:
            continue  # 결과 없는 폴은 graph에 의미 없음
        region = (p.get("region") or "전국").strip() or "전국"
        district = p.get("district")

        # POLLSTER
        pollster_id = upsert_object(c, "POLLSTER", agency, agency, {})
        pollsters.add(agency)

        # POLL
        poll_props = {
            "date": p.get("date"),
            "survey_date": p.get("survey_date"),
            "survey_year": p.get("survey_year"),
            "survey_week": p.get("survey_week"),
            "region": region,
            "district": district,
            "sample_size": p.get("sample_size"),
            "method": p.get("method"),
            "response_rate": p.get("response_rate"),
            "category": p.get("category"),
            "pollagg_id": p["id"],
        }
        poll_name = f"{agency}_{p.get('date') or 'no_date'}_{region}"
        poll_obj_id = upsert_object(c, "POLL", poll_id, poll_name, poll_props)

        # POLLSTER --[CONDUCTED]--> POLL
        upsert_link(c, pollster_id, poll_obj_id, "CONDUCTED", {})

        # 후보별 MEASURES + 후보 객체
        for cand_name, rate in results.items():
            cand_id = upsert_object(c, "CANDIDATE", cand_name, cand_name, {})
            upsert_link(c, poll_obj_id, cand_id, "MEASURES", {"support_rate": rate})
            measures += 1
            candidates.add(cand_name)

        # REGION SEGMENT 자동 생성 + MEASURES_IN_SEGMENT
        # 대시보드 /api/trends가 MEASURES_IN_SEGMENT를 조회하므로
        # PollAgg region을 그대로 segment로 매핑 → segment 데이터 없어도 차트 작동
        seg_external_id = f"REGION:{region}"
        seg_id = upsert_object(c, "SEGMENT", seg_external_id, region,
                               {"category": "REGION", "source_field": "polls.region"})
        upsert_link(c, poll_obj_id, seg_id, "MEASURES_IN_SEGMENT", results)
        seg_measures += 1
        regions.add(region)

        # raw_mirror
        upsert_raw_mirror(c, SOURCE_NAME, poll_id, p)
        poll_count += 1

    notes = (f"category={category} since={since} until={until} | "
             f"polls={poll_count} pollsters={len(pollsters)} "
             f"candidates={len(candidates)} measures={measures} "
             f"seg_measures={seg_measures} regions={len(regions)}")
    update_sync_state(c, SOURCE_NAME, poll_count, notes)
    conn.commit()
    conn.close()

    print(f"[sync] OK: polls={poll_count} pollsters={len(pollsters)} "
          f"candidates={len(candidates)} measures={measures} "
          f"seg_measures={seg_measures} regions={len(regions)}")
    return {
        "polls": poll_count,
        "pollsters": len(pollsters),
        "candidates": len(candidates),
        "measures": measures,
        "seg_measures": seg_measures,
        "regions": len(regions),
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="PollAgg → PTPoll hub.db sync")
    ap.add_argument("--api", default=DEFAULT_API_BASE,
                    help="PollAgg API base URL (env: POLLAGG_API)")
    ap.add_argument("--db", default=DEFAULT_DB, help="hub.db 경로 (env: DB_PATH)")
    ap.add_argument("--category", default=None,
                    help="필터: election, local_election, by_election, approval_rating 등")
    ap.add_argument("--since", default=None, help="필터: ISO YYYY-MM-DD")
    ap.add_argument("--until", default=None, help="필터: ISO YYYY-MM-DD")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        print(f"[error] DB not found: {args.db}. Run init_twin_db.py first.",
              file=sys.stderr)
        sys.exit(2)

    run_sync(args.api, args.db, args.category, args.since, args.until)
