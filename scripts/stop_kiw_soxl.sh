#!/bin/bash
# ============================================================
# kiw_soxl_trader 중지 스크립트 v1.0
# 실행: bash stop_kiw_soxl.sh [옵션]   /   명령: stop_kiw_soxl
#   옵션 없음    : 정상 중지 (서버 재부팅 시에는 다시 자동 시작)
#   --permanent  : 영구 중지 (재부팅해도 자동 시작 안 함)
#   --status     : 현재 상태만 확인
#
# ⚠️ 봇을 멈춰도 이미 키움에 나가 있는 미체결 주문과 보유 주식은 그대로 남습니다.
#    필요하면 영웅문에서 직접 취소/매도하세요. (봇은 다시 켜질 때 계좌와 지층을 스스로 맞춥니다)
# ============================================================
SERVICE="kiwoombot_soxl"
TARGET=~/kiw_soxl_trader

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'

show_status() {
    echo ""
    echo -e "${CYAN}========================================"
    echo "  현재 상태: $(date '+%Y-%m-%d %H:%M:%S %Z') / $(TZ=America/New_York date '+%H:%M ET')"
    echo -e "========================================${NC}"
    if systemctl is-active "$SERVICE" > /dev/null 2>&1; then
        echo -e "  상태 : ${RED}✅ 실행 중${NC}"
        echo    "  PID  : $(systemctl show -p MainPID --value "$SERVICE" 2>/dev/null)"
        echo    "  시작 : $(systemctl show -p ActiveEnterTimestamp --value "$SERVICE" 2>/dev/null)"
    else
        echo -e "  상태 : ${GREEN}⛔ 중지됨 ($(systemctl is-active "$SERVICE" 2>/dev/null))${NC}"
    fi
    MAIN_PID=$(systemctl show -p MainPID --value "$SERVICE" 2>/dev/null)
    ZOMBIE=$(pgrep -f "$TARGET/main.py" | grep -v "^${MAIN_PID}$" || true)
    [ -n "$ZOMBIE" ] && echo -e "  ${YELLOW}⚠️  서비스 밖에서 도는 프로세스: $ZOMBIE${NC}"
    if systemctl is-enabled "$SERVICE" > /dev/null 2>&1; then echo "  부팅 시 자동 시작: 켜짐"; else echo "  부팅 시 자동 시작: 꺼짐"; fi
    if [ -f "$TARGET/.env" ]; then
        echo "  모드 : $(grep -E '^TRADE_MODE=' "$TARGET/.env" | tail -n1) / $(grep -E '^DRY_RUN=' "$TARGET/.env" | tail -n1)"
    fi
    echo ""
    echo "  최근 로그:"
    tail -n 8 "$TARGET/logs/kiw_soxl_trader.log" 2>/dev/null | sed 's/^/    /' || echo "    (로그 없음)"
    echo ""
}

if [ "$1" == "--status" ]; then show_status; exit 0; fi

echo ""
echo -e "${YELLOW}⚠️  키움증권 해외주식 SOXL 스나이퍼 봇(kiwoombot_soxl)을 중지합니다.${NC}"
echo -e "${YELLOW}   키움에 나가 있는 미체결 주문과 보유 주식은 그대로 남습니다.${NC}"
echo -ne "  계속하려면 ${RED}y${NC} 를 입력하세요 (y/N): "
read -r CONFIRM
if [[ "$CONFIRM" != "y" && "$CONFIRM" != "Y" ]]; then echo -e "${GREEN}✅ 취소되었습니다.${NC}"; exit 0; fi

sudo systemctl stop "$SERVICE"
sleep 3
LEFT=$(pgrep -f "$TARGET/main.py" || true)
if [ -n "$LEFT" ]; then
    echo -e "${YELLOW}⚠️  남은 프로세스 종료: $LEFT${NC}"
    kill $LEFT 2>/dev/null; sleep 2; kill -9 $LEFT 2>/dev/null || true
fi
if [ "$1" == "--permanent" ]; then
    sudo systemctl disable "$SERVICE" > /dev/null 2>&1
    echo -e "${GREEN}⛔ 영구 중지 완료 (재부팅해도 자동 시작하지 않습니다). 다시 켜기: sudo systemctl enable $SERVICE && deploy_kiw_soxl${NC}"
else
    echo -e "${GREEN}⛔ 중지 완료 (서버를 재부팅하면 다시 시작됩니다). 다시 켜기: deploy_kiw_soxl${NC}"
fi
show_status
