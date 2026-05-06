# PTPoll Digital Twin

여론조사 데이터를 **Knowledge Graph (objects + links)** 모델로 다루는 트윈 시스템.
PollAgg(`https://api-poll.dailyprizm.com`)의 raw poll 데이터를 받아
세그먼트(권역·연령·성별) 기반 분석 + 시나리오 시뮬레이션을 제공.

## 아키텍처

```
PollAgg (Postgres, prod)
   │  GET /api/data
   ▼
transform_mirror.py  ── upsert ──▶  hub.db (SQLite)
                                    ├── objects   (POLLSTER, POLL, CANDIDATE, SEGMENT)
                                    ├── links     (CONDUCTED, MEASURES, MEASURES_IN_SEGMENT)
                                    ├── raw_mirror (lineage)
                                    └── sync_state (마지막 sync 메타)
                                    ▲
                            dashboard_server.py (port 8000)
                            simulation_engine.py
```

PollAgg의 `region` 컬럼이 PTPoll의 `SEGMENT`(category=REGION)로 자동 매핑되어,
PDF 추출 없이도 권역별 trend / simulation이 작동함.

연령·성별 segment는 NESDC PDF deep extraction 구현 후 채워질 예정 (현재 mock).

## 운영 절차

### 0. 의존성
- Python 3.10+
- 표준 라이브러리만 사용 (sqlite3, urllib, gzip, json) — 외부 패키지 없음

### 1. DB 초기화

```bash
# 빈 schema 새로 만들기 (기존 DB 삭제)
python src/db/init_twin_db.py --reset

# 기존 유지하며 누락 테이블만 추가
python src/db/init_twin_db.py
```

### 2. PollAgg에서 sync

```bash
# 전체 카테고리
python src/sync/transform_mirror.py

# 카테고리별
python src/sync/transform_mirror.py --category local_election
python src/sync/transform_mirror.py --category election --since 2026-01-01

# DB 경로 / API base 변경
DB_PATH=data/2026_local_election/hub.db \
POLLAGG_API=https://api-poll.dailyprizm.com/api \
  python src/sync/transform_mirror.py
```

UPSERT 기반이라 **재실행 안전**. sync_state 테이블에 마지막 실행 시점·건수 기록됨.

### 3. 대시보드 실행

```bash
python src/api/dashboard_server.py
# → http://localhost:8000
```

### 4. sync 상태 확인

```bash
sqlite3 data/2026_local_election/hub.db \
  "SELECT * FROM sync_state; SELECT obj_type, COUNT(*) FROM objects GROUP BY obj_type;"
```

## 데이터 모델

### objects (UNIQUE: obj_type + external_id)
| obj_type | external_id 예 | name 예 |
|----------|---------------|---------|
| POLLSTER | "리얼미터" | 리얼미터 |
| POLL | "13727" (PollAgg id) | Gallup_2026-04-23_서울 |
| CANDIDATE | "더불어민주당" | 더불어민주당 |
| SEGMENT | "REGION:서울" | 서울 |

### links (UNIQUE: source + target + link_type)
| link_type | source → target | properties |
|-----------|-----------------|------------|
| CONDUCTED | POLLSTER → POLL | {} |
| MEASURES | POLL → CANDIDATE | {"support_rate": 42.5} |
| MEASURES_IN_SEGMENT | POLL → SEGMENT | {"더불어민주당": 42.5, "국민의힘": 38.0, …} |

### sync_state
- 외부 소스(`pollagg_rest`) 별 1행
- last_synced_at, last_record_count, notes

### raw_mirror (lineage)
- PollAgg의 원본 JSON 그대로 보관
- (source, source_pk) UNIQUE — 재sync 시 갱신

## 향후 작업

| 단계 | 내용 |
|------|------|
| ✅ A. Reset & schema | 완료 (UNIQUE 제약 + sync_state) |
| ✅ B. REST sync | 완료 (idempotent UPSERT) |
| ✅ C. Region segment 자동 생성 | 완료 (대시보드 차트·시뮬 작동) |
| ⏳ D. NESDC PDF deep extraction | `src/collectors/nesdc_deep_pdf.py` mock → 실 Vision API |
| ⏳ E. 일일 cron sync | crontab 등록, 실패 알림 |
| ⏳ F. 통합 운영 (모델 2) | PollAgg와 도메인·인프라 분리, sync만 공유 |

## 백업

```bash
# 수동 백업
cp data/2026_local_election/hub.db \
   data/2026_local_election/backups/hub_$(date +%Y%m%d_%H%M%S).db
```

## 트러블슈팅

| 증상 | 원인 | 조치 |
|------|------|------|
| sync 실패 "API HTTP 404" | API URL 오타 또는 prod 다운 | `curl https://api-poll.dailyprizm.com/api/data\|head -c 200` 로 prod 상태 확인 |
| "DB not found" | hub.db 미생성 | `python src/db/init_twin_db.py --reset` 선행 |
| /api/trends 응답 빈 | segment 미생성 또는 이름 mismatch | `sqlite3 hub.db "SELECT * FROM objects WHERE obj_type='SEGMENT'"` 로 확인 |
| 대시보드 차트 안 그려짐 | hub.db 갱신 후 브라우저 cache | 강력 새로고침 (Cmd+Shift+R) |

## 라이선스 / 책임

내부 운영 도구. 수집 데이터는 NESDC 공개 자료 기반.
