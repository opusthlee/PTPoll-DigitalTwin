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

### 3. NESDC HTML demographic 추출 (D-1)

```bash
# 최근 ntt_id 10개의 sample demographic 분포 → SEGMENT(AGE/GENDER/REGION_FRAME)
python src/sync/extract_segments.py --limit 10

# 특정 ntt_id만
python src/sync/extract_segments.py --ntt-id 18544 --ntt-id 18543

# 이미 처리된 항목도 갱신
python src/sync/extract_segments.py --limit 5 --no-skip-existing
```

NESDC HTML은 sample 분포(N수)만 보유. 후보×demographic 지지율은 PDF (D-2).

### 3-2. PDF Vision 추출 (D-2)

NESDC PDF 첨부는 로그인 필수로 차단됨 (2026-05 정책). 우회 — **PDF inbox 패턴**:

```bash
# 1. 사용자가 어떤 경로로든 PDF 확보 (NESDC 로그인, pollster 사이트, 보도자료 등)
#    → data/pdf_inbox/ 디렉토리에 드롭
cp my_poll.pdf data/pdf_inbox/

# 2. ANTHROPIC_API_KEY .cron.env 설정 (1회)
echo 'ANTHROPIC_API_KEY=sk-ant-api03-...' > .cron.env
chmod 600 .cron.env

# 3. 추출 실행
./scripts/setup_venv.sh                  # 1회: venv + anthropic SDK 설치
source .cron.env
.venv/bin/python src/sync/extract_pdf_segments.py --dry-run        # 비용·결과 미리보기
.venv/bin/python src/sync/extract_pdf_segments.py --limit 5         # 5개까지만
.venv/bin/python src/sync/extract_pdf_segments.py --cost-cap 0.50   # 누적 $0.50까지
```

처리 완료 PDF는 `data/pdf_processed/`로, 유효하지 않은 PDF/0 segments PDF는
`data/pdf_rejected/`로 자동 분류. file hash 기반 idempotency — 동일 PDF 재처리 X.

**검증된 정확도**: 합성 한국어 폴 PDF, 22/22 셀 100% 정확 (이재명/윤석열 후보 × 11 segments).
**비용**: PDF당 ~$0.01-0.05 (claude-sonnet-4-6, 페이지 수 의존).
**자동 가드**: PDF magic 검증, 25MB 크기 제한, 누적 비용 cap, 0 segments 자동 거부.

### 4. 일일 cron 등록 (E)

```bash
# 매일 05:00 KST 자동 실행 (PollAgg 04:00 sync 1시간 후)
./scripts/install_cron.sh install

# 상태 확인
./scripts/install_cron.sh status

# 제거
./scripts/install_cron.sh uninstall

# 수동 실행
./scripts/cron_pipeline.sh
```

로그: `logs/pipeline.log`, 실패 시 `logs/alerts.log`.
알림 활성화: `.cron.env` 파일에 `SLACK_WEBHOOK=...` 또는 `NTFY_TOPIC=...` 설정.

### 5. 대시보드 실행

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
| ✅ D-1. NESDC HTML sample demographic | 완료 (`extract_segments.py` — AGE/GENDER/REGION_FRAME N수) |
| ✅ D-2. PDF 후보×demographic 지지율 | 완료 (`extract_pdf_segments.py` + Claude Vision). NESDC PDF 로그인 차단으로 PDF inbox 패턴 사용. 정확도 검증 22/22 100% |
| ✅ E. 일일 cron 파이프라인 | 완료 (`cron_pipeline.sh` — sync + HTML demo + PDF Vision 통합) |
| ⏳ F. 통합 운영 (모델 2) | PollAgg와 도메인·인프라 분리, sync만 공유 (`pt.dailyprizm.com`) |

## D-2 (PDF deep extraction) 셋업 가이드

PDF에서 후보×demographic 지지율을 뽑으려면 Vision API 필요:

```bash
# 1. 의존성 설치
pip install anthropic

# 2. API key 환경변수 설정 (~/.zshrc 또는 .cron.env)
export ANTHROPIC_API_KEY=sk-ant-...

# 3. (구현 예정) PDF 추출 스크립트
# python src/sync/extract_pdf_segments.py --limit 5
```

비용: PDF당 ~$0.01-0.05. 100개 PDF = $1~5 (1회성, 누적 안 됨).

## 백업

```bash
# 수동 백업
cp data/2026_local_election/hub.db \
   data/2026_local_election/backups/hub_$(date +%Y%m%d_%H%M%S).db
```

## 테스트

55개 단위·통합 테스트로 schema, sync, extract, dashboard 쿼리 검증:

```bash
./scripts/run_tests.sh                                   # 전체
python3 -m unittest tests.test_init_twin_db              # 모듈별
python3 -m unittest tests.test_dashboard_integration -v  # 상세 출력
```

테스트는 임시 sqlite로 격리되며, 통합 테스트는 실제 hub.db도 검사 (없으면 skip).
네트워크 호출은 mock 처리되어 외부 의존성 없음.

## 운영 점검 기록

### 2026-05-12

- `main`/`origin/main` 최신 커밋 `dd58b2b` 기준으로 D-2 PDF Vision inbox 파이프라인 점검.
- 로컬 경로가 `/Users/up_main/...`에 고정되어 있던 운영 스크립트와 대시보드 서버를 실행 위치 기준 경로 계산으로 수정.
- `cron_pipeline.sh` 실제 실행 확인: PollAgg 9028 polls sync, NESDC HTML 10건 처리, PDF inbox 샘플은 이미 처리되어 skip.
- 대시보드 API 확인: `/api/project`, `/api/trends?segment=서울`, `/api/simulate?segment=서울&impact=1` 응답 정상.
- `/api/trends`에서 date 없는 POLL이 섞이면 정렬 중 `TypeError`가 나던 문제를 수정하고 회귀 테스트 추가.
- 검증: `./scripts/run_tests.sh -v`, `.venv/bin/python -m unittest tests.test_dashboard_integration -v`.

### 2026-05-12 대시보드 가독성·수치 정합성 보강

- Trend/Projection 그래프를 누적 막대 기반으로 정리하고 기본 화면은 최근 24개 날짜만 표시.
- 기본 그래프는 주요 정당 중심으로 표시하고, 소수·기타/전체 항목은 토글로 확인하도록 변경.
- 막대 내부 수치 라벨을 추가하되 `%` 기호는 생략하고 작은 조각은 숨겨 겹침을 방지.
- `DP`, `DP_lead`, `PPP`, `PPP_lead`, `Others`, `기타` 같은 PollAgg alias를 표준 정당명으로 정규화.
- 같은 날짜의 여러 poll은 후보별 평균으로 집계하고, `approval_rating`의 positive/negative는 선거 지지율 계산에서 제외.
- Simulation `original`은 최신 날짜에 실제 값이 있는 항목만 사용하도록 수정해 Trend 최신값과 Projection 현재값을 일치시킴.
- 검증: `./scripts/run_tests.sh -v`, localhost `/api/trends`, `/api/simulate` 응답 대조.

## 트러블슈팅

| 증상 | 원인 | 조치 |
|------|------|------|
| sync 실패 "API HTTP 404" | API URL 오타 또는 prod 다운 | `curl https://api-poll.dailyprizm.com/api/data\|head -c 200` 로 prod 상태 확인 |
| "DB not found" | hub.db 미생성 | `python src/db/init_twin_db.py --reset` 선행 |
| /api/trends 응답 빈 | segment 미생성 또는 이름 mismatch | `sqlite3 hub.db "SELECT * FROM objects WHERE obj_type='SEGMENT'"` 로 확인 |
| 대시보드 차트 안 그려짐 | hub.db 갱신 후 브라우저 cache | 강력 새로고침 (Cmd+Shift+R) |
| AGE/GENDER 드롭다운에 안 나옴 | MEASURES_IN_SEGMENT link 없음 (정상) | D-2 PDF 추출 구현 후 자동 노출됨 |
| cron 등록했는데 안 실행됨 | macOS Full Disk Access 권한 필요할 수 있음 | 시스템 설정 → 개인정보 보호 → 전체 디스크 접근에 `cron` 추가 |
| pipeline.log 비대해짐 | log rotation 미구현 | 수동 truncate 또는 logrotate 설정 (향후 작업) |

## 알려진 제약

| 항목 | 현재 동작 | 개선 시점 |
|------|---------|----------|
| schema 마이그레이션 | `IF NOT EXISTS` 기반 — 새 컬럼은 자동 추가 안 됨 | 향후 alembic 도입 검토 |
| raw_mirror 누적 | UPSERT라 무한 증가 안 함 (poll 수 = mirror 수). PollAgg 9028 → 4MB | 1년 후 archive 정책 |
| AGE/GENDER candidate 지지율 | NESDC PDF 정책상 HTML 미공개 → SAMPLED N수만 | D-2 (Vision API) 활성화 후 |
| PollAgg ↔ NESDC POLL cross-reference | 별도 객체로 저장 (`nesdc:{id}` vs `{id}`). agency+date 매칭 미구현 | 향후 graph build 단계 |
| sqlite WAL mode | OFF (기본) | 동시성 이슈 발생 시 활성화 |
| log rotation | 없음 | logrotate 또는 size 기반 archive |

## 라이선스 / 책임

내부 운영 도구. 수집 데이터는 NESDC 공개 자료 기반.
