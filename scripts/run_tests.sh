#!/bin/bash
# 모든 단위·통합 테스트 실행. CI/cron 후 health-check 용도로도 가능.
set -euo pipefail

PROJECT_DIR="${PTPOLL_DIR:-/Users/up_main/Desktop/T_Antigravity/PTPoll}"
cd "$PROJECT_DIR"

PYTHON="${PTPOLL_PYTHON:-python3}"
echo "[test] running unittest discover on tests/"
"$PYTHON" -m unittest discover -s tests "$@" 2>&1 | tail -10
