#!/bin/bash
# PTPoll venv 셋업 (1회 실행). Homebrew Python의 PEP 668 격리 회피.
set -euo pipefail

PROJECT_DIR="${PTPOLL_DIR:-/Users/up_main/Desktop/T_Antigravity/PTPoll}"
cd "$PROJECT_DIR"

if [[ ! -d .venv ]]; then
  echo "[setup] creating .venv …"
  python3 -m venv .venv
fi

echo "[setup] upgrading pip …"
.venv/bin/pip install --quiet --upgrade pip

if [[ -f requirements.txt ]]; then
  echo "[setup] installing requirements.txt …"
  .venv/bin/pip install -r requirements.txt
fi

echo "[setup] done. python: $(.venv/bin/python --version)"
echo "[setup] cron_pipeline.sh와 run_tests.sh가 .venv 자동 인식."
