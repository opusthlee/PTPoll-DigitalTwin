#!/bin/bash
# 모든 단위·통합 테스트 실행. CI/cron 후 health-check 용도로도 가능.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${PTPOLL_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
cd "$PROJECT_DIR"

if [[ -n "${PTPOLL_PYTHON:-}" ]]; then
  PYTHON="$PTPOLL_PYTHON"
elif [[ -x "$PROJECT_DIR/.venv/bin/python" ]]; then
  PYTHON="$PROJECT_DIR/.venv/bin/python"
else
  PYTHON="python3"
fi

echo "[test] using $PYTHON"
"$PYTHON" -m unittest discover -s tests "$@" 2>&1 | tail -10
