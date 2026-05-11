#!/bin/bash
# PTPoll 일일 파이프라인 — 1) PollAgg sync 2) NESDC HTML segment 추출
#
# 운영 흐름:
#   PollAgg 04:00 KST cron이 NESDC raw poll → Postgres 저장
#   ↓
#   PTPoll 05:00 KST cron 이 스크립트 실행 → hub.db 갱신
#
# flock 단일 실행 + timeout + 로그 + Slack/ntfy 옵션 알림.
set -uo pipefail

# cron 환경은 PATH가 빈약해서 /opt/homebrew/bin (mac) /usr/local/bin (mac intel) 필요
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${PTPOLL_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
LOG_DIR="$PROJECT_DIR/logs"
LOG="$LOG_DIR/pipeline.log"
ALERT="$LOG_DIR/alerts.log"
LOCK="/tmp/ptpoll-pipeline.lock"
TS=$(date "+%Y-%m-%d %H:%M:%S %Z")

# Python 우선순위: 환경변수 > venv > system python3
if [[ -n "${PTPOLL_PYTHON:-}" ]]; then
  PYTHON="$PTPOLL_PYTHON"
elif [[ -x "$PROJECT_DIR/.venv/bin/python" ]]; then
  PYTHON="$PROJECT_DIR/.venv/bin/python"
else
  PYTHON="/usr/bin/env python3"
fi

# webhook env 옵션 (.cron.env가 있으면 source)
if [[ -f "$PROJECT_DIR/.cron.env" ]]; then
  set -a; source "$PROJECT_DIR/.cron.env"; set +a
fi

mkdir -p "$LOG_DIR"

# Portable timeout (macOS는 기본 timeout 없음, brew coreutils의 gtimeout 사용)
if command -v gtimeout >/dev/null 2>&1; then
  TIMEOUT_CMD=(gtimeout 600)
elif command -v timeout >/dev/null 2>&1; then
  TIMEOUT_CMD=(timeout 600)
else
  TIMEOUT_CMD=()  # fallback: timeout 없이 실행
fi

# Portable flock — macOS 기본 미설치
if ! command -v flock >/dev/null 2>&1; then
  # 간단한 PID 기반 lock 대체
  if [[ -f "$LOCK" ]] && kill -0 "$(cat "$LOCK")" 2>/dev/null; then
    echo "[$TS] another pipeline already running (pid=$(cat $LOCK)) — skip" >> "$LOG"
    exit 0
  fi
  echo $$ > "$LOCK"
  trap "rm -f $LOCK" EXIT
else
  exec 9>"$LOCK"
  if ! flock -n 9; then
    echo "[$TS] another pipeline already running — skip" >> "$LOG"
    exit 0
  fi
fi

cd "$PROJECT_DIR" || { echo "[$TS] cd failed" >> "$ALERT"; exit 2; }

echo "════════════ $TS START ════════════" >> "$LOG"

run_step() {
  local name="$1"; shift
  echo "  → $name" >> "$LOG"
  # set -u 하에서 빈 배열 안전 확장
  if "${TIMEOUT_CMD[@]+${TIMEOUT_CMD[@]}}" "$@" >> "$LOG" 2>&1; then
    return 0
  else
    local ec=$?
    echo "  ✗ $name FAILED (exit=$ec)" >> "$LOG"
    echo "[$TS] $name FAILED (exit=$ec)" >> "$ALERT"
    return $ec
  fi
}

OVERALL_OK=true

run_step "transform_mirror (PollAgg → hub.db)" \
  $PYTHON src/sync/transform_mirror.py || OVERALL_OK=false

run_step "extract_segments (NESDC HTML → demographics)" \
  $PYTHON src/sync/extract_segments.py --limit 20 || OVERALL_OK=false

# D-2 PDF Vision: ANTHROPIC_API_KEY 있고 inbox에 PDF 있을 때만 실행.
# 비용 가드: 1회 실행당 최대 $0.50. limit으로 PDF 수도 제한.
if [[ -n "${ANTHROPIC_API_KEY:-}" ]] && [[ -d "$PROJECT_DIR/data/pdf_inbox" ]] \
   && ls "$PROJECT_DIR/data/pdf_inbox"/*.pdf >/dev/null 2>&1; then
  run_step "extract_pdf_segments (PDF Vision → MEASURES_IN_SEGMENT)" \
    $PYTHON src/sync/extract_pdf_segments.py --limit 10 --cost-cap 0.50 || OVERALL_OK=false
else
  echo "  → extract_pdf_segments skip (no API key or empty inbox)" >> "$LOG"
fi

if $OVERALL_OK; then
  echo "════════════ $TS OK ════════════" >> "$LOG"
  exit 0
fi

MSG="[PTPoll cron FAIL] at $TS — see $LOG"
echo "════════════ $TS FAIL ════════════" >> "$LOG"
echo "$MSG" >> "$ALERT"

# best-effort 알림
if [[ -n "${SLACK_WEBHOOK:-}" ]]; then
  curl -fsS -X POST -H "Content-Type: application/json" \
       --data "{\"text\":\"$MSG\"}" "$SLACK_WEBHOOK" >/dev/null 2>&1 || true
fi
if [[ -n "${NTFY_TOPIC:-}" ]]; then
  curl -fsS -d "$MSG" "https://ntfy.sh/$NTFY_TOPIC" >/dev/null 2>&1 || true
fi

exit 1
