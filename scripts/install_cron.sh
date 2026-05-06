#!/bin/bash
# PTPoll cron 등록/제거 — macOS user crontab.
# 사용법:
#   ./scripts/install_cron.sh install    # 매일 05:00 KST 등록
#   ./scripts/install_cron.sh uninstall  # 제거
#   ./scripts/install_cron.sh status     # 현재 상태 확인
set -euo pipefail

PROJECT_DIR="${PTPOLL_DIR:-/Users/up_main/Desktop/T_Antigravity/PTPoll}"
SCRIPT="$PROJECT_DIR/scripts/cron_pipeline.sh"
# stdout/stderr를 cron-stdout.log에 redirect — 스크립트 자체 에러 진단용
ENTRY="0 5 * * * $SCRIPT >> $PROJECT_DIR/logs/cron-stdout.log 2>&1"
MARKER="# PTPoll daily pipeline"

cmd="${1:-status}"

case "$cmd" in
  install)
    if [[ ! -x "$SCRIPT" ]]; then
      chmod +x "$SCRIPT"
      echo "[+] chmod +x $SCRIPT"
    fi
    if crontab -l 2>/dev/null | grep -q "$SCRIPT"; then
      echo "[=] already installed"
      crontab -l | grep -E "$SCRIPT|$MARKER"
      exit 0
    fi
    (crontab -l 2>/dev/null; echo "$MARKER"; echo "$ENTRY") | crontab -
    echo "[+] installed: $ENTRY"
    crontab -l | tail -5
    ;;
  uninstall)
    if ! crontab -l 2>/dev/null | grep -q "$SCRIPT"; then
      echo "[=] not installed"
      exit 0
    fi
    crontab -l 2>/dev/null | grep -v "$SCRIPT" | grep -v "$MARKER" | crontab -
    echo "[-] removed"
    ;;
  status)
    if crontab -l 2>/dev/null | grep -q "$SCRIPT"; then
      echo "[+] installed"
      crontab -l | grep -B1 "$SCRIPT"
    else
      echo "[-] not installed"
    fi
    ;;
  *)
    echo "usage: $0 {install|uninstall|status}" >&2
    exit 2
    ;;
esac
