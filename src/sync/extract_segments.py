"""
NESDC HTML detail → PTPoll hub.db demographic SAMPLED links.

이 단계는 sample 분포(성별/연령/지역대 사례수)만 수집.
후보×demographic 지지율은 PDF에만 존재 → 별도 Vision API 단계 필요.

스키마 추가:
  SEGMENT(category=AGE)    — '18~29세', '30대', '40대' ...
  SEGMENT(category=GENDER) — '남', '여'
  SEGMENT(category=REGION_FRAME) — '1권역', '2권역' ... (NESDC 지역대 코드)
  Link type SAMPLED        — POLL --SAMPLED--> SEGMENT, properties={"n": 96}
                             POLL의 sample이 해당 segment에 어떻게 분포했는지

POLL 객체:
  external_id = "nesdc:{ntt_id}" (PollAgg sync POLL과 별도)
  properties.source = "nesdc_html"
  properties.ntt_id = ntt_id
  properties.meta = NESDC 등록 메타
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import time
from typing import Dict, Optional


def _extract_first_date(s: Optional[str]) -> Optional[str]:
    """NESDC '조사일시' 같은 노이즈 많은 텍스트에서 첫 YYYY-MM-DD 추출."""
    if not s:
        return None
    m = re.search(r"(\d{4}-\d{2}-\d{2})", s)
    return m.group(1) if m else None

# 패키지 경로
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
sys.path.insert(0, PROJECT_ROOT)

from src.collectors.nesdc_html import fetch_demographics, list_recent_ntt_ids  # noqa: E402

DEFAULT_DB = os.environ.get("DB_PATH", "data/2026_local_election/hub.db")
SOURCE_NAME = "nesdc_html"

# demographic group → SEGMENT category 매핑
GROUP_CATEGORY = {"AGE": "AGE", "GENDER": "GENDER", "REGION": "REGION_FRAME"}


def upsert_object(c, obj_type, external_id, name, properties=None):
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
    c.execute("SELECT id FROM objects WHERE obj_type=? AND external_id=?", (obj_type, external_id))
    return c.fetchone()[0]


def upsert_link(c, source_id, target_id, link_type, properties=None):
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


def upsert_raw_mirror(c, source, source_pk, data):
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


def update_sync_state(c, source, count, notes):
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


def already_processed(c, ntt_id) -> bool:
    """이미 raw_mirror에 저장된 ntt_id인지 확인 (skip 가능)."""
    c.execute("SELECT 1 FROM raw_mirror WHERE source=? AND source_pk=? LIMIT 1",
              (SOURCE_NAME, ntt_id))
    return c.fetchone() is not None


def process_ntt_id(c, ntt_id: str) -> Dict[str, int]:
    """단일 ntt_id 처리 — fetch + parse + upsert."""
    data = fetch_demographics(ntt_id)
    meta = data.get("meta", {})
    demos = data.get("demographics", {})

    # POLL 객체 (NESDC source)
    agency = meta.get("조사기관명", "Unknown")
    region = meta.get("조사지역", "")
    survey_date = _extract_first_date(meta.get("조사일시")) or _extract_first_date(meta.get("등록일"))
    poll_name = f"NESDC_{ntt_id}_{agency}"
    poll_props = {
        "source": SOURCE_NAME,
        "ntt_id": ntt_id,
        "agency": agency,
        "region": region,
        "date": survey_date,           # 대시보드 호환: dates 정렬에 사용됨
        "survey_date": survey_date,
        "election": meta.get("선거명"),
        "client": meta.get("조사의뢰자"),
        "subject": meta.get("조사대상"),
        "total_n": demos.get("TOTAL", {}).get("전체"),
    }
    poll_id = upsert_object(c, "POLL", f"nesdc:{ntt_id}", poll_name, poll_props)

    # POLLSTER 매핑 + CONDUCTED link
    if agency and agency != "Unknown":
        pollster_id = upsert_object(c, "POLLSTER", agency, agency, {})
        upsert_link(c, pollster_id, poll_id, "CONDUCTED", {})

    # SEGMENT + SAMPLED link
    sampled_count = 0
    for group, items in demos.items():
        if group == "TOTAL":
            continue
        category = GROUP_CATEGORY.get(group, group)
        for label, n in items.items():
            seg_external = f"{category}:{label}"
            seg_id = upsert_object(c, "SEGMENT", seg_external, label,
                                   {"category": category, "source_field": f"nesdc.demographics.{group}"})
            upsert_link(c, poll_id, seg_id, "SAMPLED", {"n": n})
            sampled_count += 1

    upsert_raw_mirror(c, SOURCE_NAME, ntt_id, data)

    return {"poll_id": poll_id, "sampled_links": sampled_count,
            "total_n": demos.get("TOTAL", {}).get("전체") or 0}


def run_extract(db_path: str, limit: int = 10, ntt_ids=None,
                skip_existing: bool = True, sleep_between: float = 1.0) -> Dict[str, int]:
    if ntt_ids is None:
        ntt_ids = list_recent_ntt_ids(page=1, limit=limit)
    print(f"[extract] target ntt_ids: {len(ntt_ids)}")

    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    processed = 0
    skipped = 0
    failed = 0
    sampled_total = 0
    failures = []

    for nid in ntt_ids:
        if skip_existing and already_processed(c, nid):
            skipped += 1
            continue
        try:
            r = process_ntt_id(c, nid)
            processed += 1
            sampled_total += r["sampled_links"]
            print(f"  [{nid}] OK — total_n={r['total_n']}, sampled_links={r['sampled_links']}")
            conn.commit()  # 부분 진척 보존 (네트워크 실패에 안전)
        except Exception as e:
            failed += 1
            failures.append(f"{nid}: {type(e).__name__}: {e}")
            print(f"  [{nid}] FAIL: {e}")
        time.sleep(sleep_between)  # NESDC 부하 방지

    notes = (f"limit={limit} processed={processed} skipped={skipped} "
             f"failed={failed} sampled_total={sampled_total}"
             + (f" failures={failures[:3]}" if failures else ""))
    update_sync_state(c, SOURCE_NAME, processed, notes)
    conn.commit()
    conn.close()

    print(f"[extract] OK: processed={processed} skipped={skipped} failed={failed} "
          f"sampled_links={sampled_total}")
    return {"processed": processed, "skipped": skipped, "failed": failed,
            "sampled_links": sampled_total}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="NESDC HTML → PTPoll demographic segments")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--limit", type=int, default=10, help="목록에서 가져올 ntt_id 수")
    ap.add_argument("--ntt-id", action="append", help="특정 ntt_id (반복 가능)")
    ap.add_argument("--no-skip-existing", action="store_true",
                    help="이미 처리한 ntt_id도 재처리")
    ap.add_argument("--sleep", type=float, default=1.0, help="요청 간 sleep (초)")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        print(f"[error] DB not found: {args.db}", file=sys.stderr)
        sys.exit(2)

    run_extract(args.db, limit=args.limit, ntt_ids=args.ntt_id,
                skip_existing=not args.no_skip_existing, sleep_between=args.sleep)
