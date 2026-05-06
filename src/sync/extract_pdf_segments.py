"""
PDF inbox → hub.db demographic candidate support links (D-2).

워크플로우:
  data/pdf_inbox/*.pdf  → Vision 추출 → POLL/CANDIDATE/SEGMENT/MEASURES_IN_SEGMENT
  처리 완료 PDF는 data/pdf_processed/로 이동 (idempotent re-run 방지)

각 PDF가 1개 폴 보고서라고 가정. 메타에서 (agency, date, region) 추출하여
external_id = "pdf:{sha1(filename+date+agency)}" 로 POLL upsert.

비용 가드:
  --limit N: 최대 N개 PDF만 처리
  --dry-run: 추출만 하고 hub.db 미반영, 비용 추정만 출력
"""
import argparse
import hashlib
import json
import logging
import os
import shutil
import sqlite3
import sys
from typing import Any, Dict, Optional

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
sys.path.insert(0, PROJECT_ROOT)

from src.collectors.nesdc_deep_pdf import AnthropicVisionExtractor  # noqa: E402

logger = logging.getLogger("extract_pdf_segments")

DEFAULT_DB = os.environ.get("DB_PATH", "data/2026_local_election/hub.db")
DEFAULT_INBOX = "data/pdf_inbox"
DEFAULT_PROCESSED = "data/pdf_processed"
DEFAULT_REJECTED = "data/pdf_rejected"
SOURCE_NAME = "pdf_vision"
PDF_MAGIC = b"%PDF"
MAX_PDF_BYTES = 25 * 1024 * 1024  # 25MB — Anthropic API 한도 + 비용 가드


def _file_hash(path: str) -> str:
    """파일 내용 hash — idempotency key."""
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def _is_valid_pdf(path: str) -> bool:
    """첫 4바이트가 %PDF magic인지 + 크기 검사."""
    try:
        size = os.path.getsize(path)
        if size < 100 or size > MAX_PDF_BYTES:
            return False
        with open(path, "rb") as f:
            head = f.read(4)
        return head == PDF_MAGIC
    except OSError:
        return False


# DB helpers (transform_mirror·extract_segments와 동일 패턴)

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
    c.execute("SELECT id FROM objects WHERE obj_type=? AND external_id=?",
              (obj_type, external_id))
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


GROUP_CATEGORY = {
    "GENDER": "GENDER",
    "AGE": "AGE",
    "REGION": "REGION",
    "OCCUPATION": "OCCUPATION",
    "EDUCATION": "EDUCATION",
    "INCOME": "INCOME",
}


def store_extraction(db_path: str, pdf_path: str, file_hash: str,
                     extraction: Dict[str, Any]) -> Dict[str, int]:
    """Vision 추출 결과 → hub.db UPSERT."""
    meta = extraction.get("meta", {})
    segments = extraction.get("segments", [])
    fname = os.path.basename(pdf_path)

    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    # POLL — file_hash 기반 external_id (재처리 시 동일 객체)
    poll_external = f"pdf:{file_hash}"
    poll_props = {
        "source": SOURCE_NAME,
        "filename": fname,
        "file_hash": file_hash,
        "agency": meta.get("agency"),
        "date": meta.get("date"),
        "region": meta.get("region"),
        "sample_size": meta.get("sample_size"),
        "method": meta.get("method"),
        "election": meta.get("election"),
        "extraction_cost_usd": extraction.get("cost_usd"),
    }
    poll_name = f"PDF_{meta.get('agency','?')}_{meta.get('date','?')}_{meta.get('region','?')}"
    poll_id = upsert_object(c, "POLL", poll_external, poll_name, poll_props)

    # POLLSTER + CONDUCTED
    agency = meta.get("agency")
    if agency:
        pollster_id = upsert_object(c, "POLLSTER", agency, agency, {})
        upsert_link(c, pollster_id, poll_id, "CONDUCTED", {})

    # 후보별 MEASURES (top-line: 모든 segment의 후보 합집합)
    all_candidates = set()
    for s in segments:
        all_candidates.update(s.get("results", {}).keys())

    measures_count = 0
    for cand in all_candidates:
        cand_id = upsert_object(c, "CANDIDATE", cand, cand, {})
        # PDF에는 top-line이 따로 있을 수 있지만 segment 단위 데이터만 받았으므로
        # MEASURES는 segment-aware 평균 또는 첫 segment값 — 일단 SAMPLED만 link 안 함
        # (top-line이 PDF에서 별도 추출되도록 prompt 보강 가능)
        # 여기선 후보 객체만 보장
        del cand_id  # placeholder, MEASURES skip

    # SEGMENT + MEASURES_IN_SEGMENT
    seg_count = 0
    for seg in segments:
        group = seg["group"]
        seg_name = seg["segment"]
        results = seg["results"]
        seg_external = f"{GROUP_CATEGORY[group]}:{seg_name}"
        seg_id = upsert_object(c, "SEGMENT", seg_external, seg_name,
                               {"category": GROUP_CATEGORY[group],
                                "source_field": f"pdf.vision.{group}"})
        upsert_link(c, poll_id, seg_id, "MEASURES_IN_SEGMENT", results)
        seg_count += 1
        # 추가: 후보별 SEGMENT_MEASURES 분리 link (분석 편의)
        for cand, rate in results.items():
            cand_id = upsert_object(c, "CANDIDATE", cand, cand, {})
            # candidate ↔ segment 직접 link는 graph 비대화 → MEASURES_IN_SEGMENT만 사용
            del cand_id
            measures_count += 1

    # raw_mirror — 전체 추출 결과 보존 (lineage)
    upsert_raw_mirror(c, SOURCE_NAME, file_hash, {
        "filename": fname,
        "meta": meta,
        "segments": segments,
        "cost_usd": extraction.get("cost_usd"),
        "usage": extraction.get("usage"),
    })

    conn.commit()
    conn.close()
    return {"poll_id": poll_id, "segments": seg_count, "measures": measures_count}


def already_processed(db_path: str, file_hash: str) -> bool:
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("SELECT 1 FROM raw_mirror WHERE source=? AND source_pk=? LIMIT 1",
              (SOURCE_NAME, file_hash))
    found = c.fetchone() is not None
    conn.close()
    return found


def run(db_path: str, inbox_dir: str, processed_dir: str,
        rejected_dir: str = DEFAULT_REJECTED,
        limit: Optional[int] = None, dry_run: bool = False,
        skip_existing: bool = True,
        cost_cap_usd: float = 0.50) -> Dict[str, Any]:
    pdfs = sorted(p for p in os.listdir(inbox_dir)
                  if p.lower().endswith(".pdf"))
    if limit:
        pdfs = pdfs[:limit]
    logger.info(f"[pdf-extract] inbox: {len(pdfs)} PDFs (limit={limit}, "
                f"dry_run={dry_run}, cost_cap=${cost_cap_usd})")

    if not pdfs:
        return {"processed": 0, "skipped": 0, "failed": 0, "rejected": 0,
                "total_cost_usd": 0.0}

    extractor = None  # 첫 호출 시점에 lazy init
    if not dry_run:
        os.makedirs(processed_dir, exist_ok=True)
        os.makedirs(rejected_dir, exist_ok=True)

    processed = skipped = failed = rejected = 0
    total_cost = 0.0
    failures = []

    for fname in pdfs:
        path = os.path.join(inbox_dir, fname)

        # 1. PDF magic 검사 (HTML/기타 파일 예외 처리)
        if not _is_valid_pdf(path):
            logger.warning(f"[pdf-extract] {fname}: 유효하지 않은 PDF (magic/size) — rejected")
            if not dry_run:
                try:
                    shutil.move(path, os.path.join(rejected_dir, fname))
                except OSError:
                    pass
            rejected += 1
            continue

        try:
            fhash = _file_hash(path)
        except OSError as e:
            logger.warning(f"[pdf-extract] {fname}: hash 실패 {e}")
            failed += 1
            continue

        if skip_existing and already_processed(db_path, fhash):
            logger.info(f"[pdf-extract] {fname}: 이미 처리됨 (hash={fhash}) — skip")
            skipped += 1
            continue

        # 2. 비용 cap — 누적 비용이 한도 초과 시 중단
        if total_cost >= cost_cap_usd:
            logger.warning(f"[pdf-extract] cost cap ${cost_cap_usd} 도달 — 중단")
            break

        if extractor is None:
            extractor = AnthropicVisionExtractor()

        try:
            r = extractor.extract(path)
        except Exception as e:
            failed += 1
            failures.append(f"{fname}: {type(e).__name__}: {str(e)[:100]}")
            logger.error(f"[pdf-extract] {fname} FAIL: {e}")
            continue

        total_cost += r["cost_usd"]

        # 3. segment 0개면 polling PDF가 아닌 것으로 간주 → rejected
        if not r["segments"]:
            logger.warning(f"[pdf-extract] {fname}: 0 segments — rejected "
                           f"(${r['cost_usd']:.4f} 소비)")
            rejected += 1
            if not dry_run:
                try:
                    shutil.move(path, os.path.join(rejected_dir, fname))
                except OSError:
                    pass
            continue

        if dry_run:
            logger.info(f"[pdf-extract] {fname} DRY-RUN: "
                        f"meta={r['meta']}, {len(r['segments'])} segments, "
                        f"${r['cost_usd']:.4f}")
            processed += 1
            continue

        store_extraction(db_path, path, fhash, r)
        processed += 1

        # processed/로 이동
        try:
            shutil.move(path, os.path.join(processed_dir, fname))
        except OSError as e:
            logger.warning(f"[pdf-extract] {fname}: move 실패 {e}")

    notes = (f"limit={limit} processed={processed} skipped={skipped} "
             f"failed={failed} rejected={rejected} total_cost=${total_cost:.4f}"
             + (" [DRY-RUN]" if dry_run else "")
             + (f" failures={failures[:3]}" if failures else ""))

    if not dry_run:
        conn = sqlite3.connect(db_path)
        update_sync_state(conn.cursor(), SOURCE_NAME, processed, notes)
        conn.commit(); conn.close()

    logger.info(f"[pdf-extract] OK: processed={processed} skipped={skipped} "
                f"failed={failed} rejected={rejected} total_cost=${total_cost:.4f}")
    return {"processed": processed, "skipped": skipped, "failed": failed,
            "rejected": rejected, "total_cost_usd": total_cost, "failures": failures}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="PDF inbox → hub.db demographic links")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--inbox", default=DEFAULT_INBOX)
    ap.add_argument("--processed", default=DEFAULT_PROCESSED)
    ap.add_argument("--rejected", default=DEFAULT_REJECTED,
                    help="유효하지 않은 PDF 또는 0 segments PDF 이동 폴더")
    ap.add_argument("--limit", type=int, default=None,
                    help="최대 처리 수 (비용 가드)")
    ap.add_argument("--cost-cap", type=float, default=0.50,
                    help="누적 비용 USD 한도 (기본 $0.50)")
    ap.add_argument("--dry-run", action="store_true",
                    help="hub.db 미반영, 비용 추정만")
    ap.add_argument("--no-skip-existing", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    if not os.path.exists(args.inbox):
        logger.error(f"inbox 디렉토리 없음: {args.inbox}")
        sys.exit(2)

    run(args.db, args.inbox, args.processed, args.rejected,
        limit=args.limit, dry_run=args.dry_run,
        skip_existing=not args.no_skip_existing,
        cost_cap_usd=args.cost_cap)
