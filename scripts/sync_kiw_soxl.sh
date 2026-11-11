#!/bin/bash
# ============================================================
# kiw_soxl_trader 원본 엔진 동기화 스크립트 v1.0
# 위치: ~/sync_kiw_soxl.sh   /   명령: sync_kiw_soxl
#
# 원본(pipios 토스 스나이퍼) 소스가 바뀌었을 때:
#   1) 새 원본 zip (예: pipiosToss_bot-main.zip) 을 홈(~)에 올리고
#   2) sync_kiw_soxl            ← 홈에서 가장 최근 pipios*.zip 을 자동으로 찾음
#      sync_kiw_soxl <zip경로>   ← 직접 지정
#      sync_kiw_soxl check      ← 지금 engine/ 점검만
#      sync_kiw_soxl rollback   ← 직전 원본으로 되돌리기
#
# 하는 일: 바뀐 파일만 engine/ 에 반영(이전 것은 data/engine_backups/ 보관) →
#          키움 블록 호환성 점검 → 문제 있으면 자동으로 되돌림 → 통과하면 재시작 여부 질문
# 원장(ledger)은 run/<모드>/ 에 따로 있어 영향받지 않습니다.
# ============================================================
TARGET=~/kiw_soxl_trader
SERVICE=kiwoombot_soxl
PYTHON="$TARGET/kiw_soxl_venv/bin/python3"
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'

cd "$TARGET" || { echo -e "${RED}❌ $TARGET 가 없습니다.${NC}"; exit 1; }
[ -x "$PYTHON" ] || PYTHON=python3

if [ "$1" == "check" ]; then "$PYTHON" engine_sync.py check; exit $?; fi
if [ "$1" == "rollback" ]; then
    "$PYTHON" engine_sync.py rollback || exit 1
    echo -e "${YELLOW}되돌린 원본으로 돌리려면: deploy_kiw_soxl${NC}"; exit 0
fi

SRC="$1"
[ -z "$SRC" ] && SRC=$(ls -t ~/pipios*.zip ~/*Toss_bot*.zip 2>/dev/null | head -n 1)
if [ -z "$SRC" ] || [ ! -e "$SRC" ]; then
    echo -e "${RED}❌ 새 원본 zip 을 찾지 못했습니다. 홈(~)에 pipiosToss_bot-main.zip 을 올리거나 경로를 지정하세요.${NC}"
    echo "   예) sync_kiw_soxl ~/pipiosToss_bot-main.zip"; exit 1
fi
echo -e "${CYAN}📥 새 원본: $SRC${NC}"

"$PYTHON" engine_sync.py diff "$SRC"
RC=$?
echo ""
if [ $RC -ne 0 ]; then
    echo -e "${RED}❌ 이 원본은 지금의 키움 블록과 맞지 않습니다 (위 ❌ 항목). engine/ 은 바꾸지 않았습니다.${NC}"
    echo "   → 위 내용을 Claude 에게 보여 주고 toss_bridge.py 보강본(kiw_soxl_trader update zip)을 받아 deploy_kiw_soxl 한 뒤 다시 실행하세요."
    exit 2
fi
echo -ne "  위 변경을 engine/ 에 반영할까요? (y/N): "
read -r CONFIRM
[[ "$CONFIRM" != "y" && "$CONFIRM" != "Y" ]] && { echo -e "${GREEN}✅ 취소 — 아무것도 바꾸지 않았습니다.${NC}"; exit 0; }

"$PYTHON" engine_sync.py apply "$SRC" || { echo -e "${RED}❌ 반영 실패 (이전 원본 유지)${NC}"; exit 2; }
mkdir -p ~/kiw_soxl_installed && [ -f "$SRC" ] && mv "$SRC" ~/kiw_soxl_installed/"$(date +%Y%m%d_%H%M)_$(basename "$SRC")"

echo ""
if systemctl is-active "$SERVICE" > /dev/null 2>&1; then
    ET_HM=$(TZ=America/New_York date '+%H%M'); ET_DOW=$(TZ=America/New_York date '+%u')
    if [ "$ET_DOW" -le 5 ] && [ "$((10#$ET_HM))" -ge 400 ] && [ "$((10#$ET_HM))" -le 1600 ]; then
        echo -e "${YELLOW}⏰ 지금은 미국 장 시간입니다. 장 마감 후 재시작을 권합니다.${NC}"
    fi
    echo -ne "  지금 봇을 재시작해 새 원본으로 돌릴까요? (y/N): "
    read -r R
    if [[ "$R" == "y" || "$R" == "Y" ]]; then
        sudo systemctl restart "$SERVICE"; sleep 8
        sudo systemctl is-active --quiet "$SERVICE" && echo -e "${GREEN}✅ 재시작 완료${NC}" \
            || { echo -e "${RED}❌ 기동 실패 — sync_kiw_soxl rollback 후 deploy_kiw_soxl${NC}"; sudo journalctl -u "$SERVICE" -n 20 --no-pager; }
        tail -n 12 "$TARGET/logs/kiw_soxl_trader.log" 2>/dev/null
    else
        echo "ℹ️  나중에 deploy_kiw_soxl 또는 sudo systemctl restart $SERVICE 로 재시작하면 반영됩니다."
    fi
else
    echo "ℹ️  봇이 꺼져 있습니다. deploy_kiw_soxl 로 켜면 새 원본으로 돕니다."
fi
