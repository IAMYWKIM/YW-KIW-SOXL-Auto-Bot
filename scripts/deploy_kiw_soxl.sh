#!/bin/bash
# ============================================================
# kiw_soxl_trader 배포 스크립트 v1.0
# 위치: ~/deploy_kiw_soxl.sh   /   명령: deploy_kiw_soxl
#
#   1) 홈(~)의 kiw_soxl_trader_v*_update.zip (또는 _full.zip) 을 ~/kiw_soxl_trader 에 반영
#      (.env · config.json · data · logs · run 은 건드리지 않음.
#       update zip 에는 engine/ 이 없으므로 원본 엔진도 그대로 유지)
#   2) .env 값 채움 여부 점검
#   3) config.json 신규 키 병합 (서버에서 바꾼 값 보존)
#   4) Python 문법 검증 + 원본 엔진 호환성 점검
#   5) systemd 서비스(kiwoombot_soxl) 재시작 + 상태 확인
# zip 이 없으면 2)~5) 만 수행합니다 (단순 재시작).
# ============================================================
TARGET=~/kiw_soxl_trader
SERVICE=kiwoombot_soxl
VENV="$TARGET/kiw_soxl_venv"
PYTHON="$VENV/bin/python3"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'

CORE_FILES="main.py config.py logger.py kiwoom_us_client.py toss_bridge.py engine_hooks.py
            engine_sync.py store.py trade_context.py scheduler.py reports.py export.py
            emailer.py notify.py telegram_bot.py settings_menu.py diag.py version_history.py"

echo ""
echo -e "${CYAN}============================================================"
echo "  🚀 kiw_soxl_trader 배포 스크립트 (kiwoombot_soxl)"
echo -e "============================================================${NC}"
echo ""
echo -e "${YELLOW}┌─────────────────────────────────────────────────────┐${NC}"
echo -e "${YELLOW}│  ⚠️   키움증권 해외주식 SOXL 스나이퍼 봇             │${NC}"
echo -e "${YELLOW}│      (kiwoombot_soxl)을 배포하시겠습니까?            │${NC}"
echo -e "${YELLOW}│      서비스가 재시작되며 감시/매매가 잠시 멈춥니다.  │${NC}"
echo -e "${YELLOW}└─────────────────────────────────────────────────────┘${NC}"
ET_NOW=$(TZ=America/New_York date '+%u %H%M')
ET_DOW=${ET_NOW% *}; ET_HM=${ET_NOW#* }
if [ "$ET_DOW" -le 5 ] && [ "$((10#$ET_HM))" -ge 400 ] && [ "$((10#$ET_HM))" -le 1600 ]; then
    echo -e "${RED}  ⏰ 지금은 미국 장 시간(04:00~16:00 ET)입니다. 미체결 주문이 있을 수 있습니다.${NC}"
    echo -e "${RED}     되도록 장 마감 후(한국 아침 06시 이후)에 배포하세요.${NC}"
fi
echo -ne "  계속하려면 ${RED}y${NC} 를 입력하세요 (y/N): "
read -r CONFIRM
if [[ "$CONFIRM" != "y" && "$CONFIRM" != "Y" ]]; then
    echo -e "${GREEN}✅ 취소되었습니다. 아무것도 변경되지 않았습니다.${NC}"; echo ""; exit 0
fi
echo ""

if [ ! -d "$TARGET" ]; then
    echo -e "${RED}❌ $TARGET 가 없습니다. 먼저 setup_kiw_soxl_trader.sh 를 실행하세요.${NC}"; exit 1
fi

# ── 홈의 zip 반영 ────────────────────────────────────────────
ZIP=$(ls -t ~/kiw_soxl_trader_v*_update.zip ~/kiw_soxl_trader_v*_full.zip 2>/dev/null | head -n 1)
if [ -n "$ZIP" ]; then
    echo "📦 $(basename "$ZIP") 반영 중..."
    TMP=$(mktemp -d)
    unzip -q -o "$ZIP" -d "$TMP" || { echo -e "${RED}❌ zip 풀기 실패 — 배포 중단${NC}"; exit 1; }
    rm -f "$TMP/.env" "$TMP/config.json"
    if [ -d "$TMP/engine" ] && ls "$TARGET"/engine/*.py > /dev/null 2>&1; then
        echo -e "  ${YELLOW}ℹ️  zip 안의 engine/ 은 반영하지 않습니다 (원본 엔진은 sync_kiw_soxl 로만 바꿉니다)${NC}"
        rm -rf "$TMP/engine"
    fi
    BK="$TARGET/data/source_backups/$(date +%Y%m%d_%H%M%S)"
    mkdir -p "$BK" && cp "$TARGET"/*.py "$BK"/ 2>/dev/null
    cp -r "$TMP"/. "$TARGET"/
    rm -rf "$TMP"
    mkdir -p ~/kiw_soxl_installed && mv "$ZIP" ~/kiw_soxl_installed/
    for s in deploy_kiw_soxl.sh stop_kiw_soxl.sh sync_kiw_soxl.sh email_setup_kiw_soxl.sh; do
        [ -f "$TARGET/scripts/$s" ] && [ "$s" != "deploy_kiw_soxl.sh" ] && cp "$TARGET/scripts/$s" ~/"$s" && chmod +x ~/"$s"
    done
    [ -f "$TARGET/scripts/deploy_kiw_soxl.sh" ] && cp "$TARGET/scripts/deploy_kiw_soxl.sh" ~/deploy_kiw_soxl.sh.new \
        && chmod +x ~/deploy_kiw_soxl.sh.new && mv ~/deploy_kiw_soxl.sh.new ~/deploy_kiw_soxl.sh
    ls -dt "$TARGET"/data/source_backups/*/ 2>/dev/null | tail -n +8 | xargs -r rm -rf
    echo "  ✅ 반영 완료 (이전 소스 보관: $BK)"
else
    echo "ℹ️  홈에 kiw_soxl_trader_v*.zip 이 없습니다 — 소스 변경 없이 점검·재시작만 합니다"
fi

# ── 환경변수 파일 점검 ───────────────────────────────────────
echo ""
echo "🔍 환경변수 파일 점검..."
if [ -f "$TARGET/.env" ]; then ENV_FILE="$TARGET/.env"
elif [ -f "$TARGET/kiw_soxl.env" ]; then ENV_FILE="$TARGET/kiw_soxl.env"
else
    echo -e "${RED}❌ $TARGET/.env 또는 kiw_soxl.env 가 없습니다. 배포 중단.${NC}"
    echo "   cp kiw_soxl.env.example .env 후 값을 채워주세요."; exit 1
fi
MODE=$(grep -E "^TRADE_MODE=" "$ENV_FILE" | tail -n 1 | cut -d= -f2 | tr -d '"' | tr 'a-z' 'A-Z')
if [ "$MODE" = "REAL" ]; then CHECK="^(KIWOOM_APP_KEY|KIWOOM_SECRET_KEY|TELEGRAM_BOT_TOKEN|TELEGRAM_CHAT_ID)="
else CHECK="^(KIWOOM_APP_KEY_MOCK|KIWOOM_SECRET_KEY_MOCK|TELEGRAM_BOT_TOKEN|TELEGRAM_CHAT_ID)="; fi
if grep -E "$CHECK" "$ENV_FILE" | grep -q "여기에_"; then
    echo -e "${RED}❌ $(basename "$ENV_FILE") 에 아직 채우지 않은 항목이 있습니다. 배포 중단.${NC}"
    grep -E "$CHECK" "$ENV_FILE" | grep "여기에_" | sed 's/=.*/=(비어 있음)/; s/^/     /'
    exit 1
fi
echo -e "${GREEN}  ✅ 환경변수 값 채움 확인 (TRADE_MODE=${MODE:-MOCK})${NC}"
if grep -qiE "^DRY_RUN=(true|1|yes|on)" "$ENV_FILE"; then
    echo -e "${YELLOW}  ℹ️  DRY_RUN=true — 키움에 실제 주문을 내지 않는 가상 체결 모드입니다 (원장: run/dry).${NC}"
elif [ "$MODE" = "REAL" ]; then
    echo -e "${RED}  🔴 실전투자 + DRY_RUN=false — 실제 주문이 나갑니다.${NC}"
fi

# ── 캐시 삭제 / venv ─────────────────────────────────────────
find "$TARGET" -path "$VENV" -prune -o -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
if [ ! -x "$PYTHON" ] || ! "$PYTHON" -c "import sys" > /dev/null 2>&1; then
    echo -e "${YELLOW}⚠️  가상환경 재생성 중... (2~4분)${NC}"
    rm -rf "$VENV"; python3 -m venv "$VENV"; "$VENV/bin/pip" install -q --upgrade pip
fi
echo "🔄 패키지 동기화 중 (requirements.txt 기준)..."
"$VENV/bin/pip" install -q -r "$TARGET/requirements.txt" || { echo -e "${RED}❌ 패키지 설치 실패 — 배포 중단${NC}"; exit 1; }
echo "  ✅ 패키지 동기화 완료"

# ── config.json 신규 키 병합 + 문법 검증 + 엔진 점검 ─────────
cd "$TARGET"
echo ""
echo "🔍 config.json 점검..."
"$PYTHON" -c "
import config
added = config.ensure_config_json()
print('  ✅ config.json 신규 키 %d개 병합: %s' % (len(added), ', '.join(added)) if added else '  ℹ️  config.json 새로 추가할 키 없음 (기존 값 유지)')
" || { echo -e "${RED}❌ config.json 오류 — 배포 중단${NC}"; exit 1; }

echo "🔍 Python 문법 검증 중..."
error=0
for f in $CORE_FILES; do
    [ -f "$f" ] || { echo -e "  ${RED}❌ $f 없음${NC}"; error=1; continue; }
    "$PYTHON" -m py_compile "$f" || { echo "  ❌ $f"; error=1; }
done
[ $error -eq 1 ] && { echo -e "${RED}❌ 문법 오류 또는 파일 누락 — 배포 중단${NC}"; exit 1; }
echo "  ✅ 키움 포팅판 $(echo $CORE_FILES | wc -w)개 파일 통과"

echo "🔍 원본 엔진 호환성 점검..."
"$PYTHON" engine_sync.py check || { echo -e "${RED}❌ 원본 엔진 점검 실패 — 배포 중단 (sync_kiw_soxl rollback 검토)${NC}"; exit 1; }

mkdir -p "$TARGET/logs" "$TARGET/data"

# ── 서비스 재시작 ────────────────────────────────────────────
echo ""
echo "🔄 $SERVICE 재시작 중..."
sudo systemctl restart "$SERVICE"
sleep 8
if sudo systemctl is-active --quiet "$SERVICE"; then
    echo -e "${GREEN}✅ $SERVICE 정상 기동 확인${NC}"
else
    echo -e "${RED}❌ $SERVICE 기동 실패! 아래 에러를 확인하세요:${NC}"
    sudo journalctl -u "$SERVICE" -n 30 --no-pager
    echo -e "${YELLOW}💡 흔한 원인: .env 항목 누락/오타 · 패키지 미설치(위 pip 단계) · engine/ 원본 누락${NC}"
fi
echo ""
echo "📋 서비스 상태:"
sudo systemctl status "$SERVICE" --no-pager -l | head -n 10
echo ""
echo "📋 최근 로그 (kiw_soxl_trader.log):"
tail -n 20 "$TARGET/logs/kiw_soxl_trader.log" 2>/dev/null || echo "  로그 파일 없음 (첫 실행 후 생성됨)"
echo ""
echo "📋 현재 설정 요약:"
"$PYTHON" -c "import config, version_history as v; print('  v' + v.CURRENT_VERSION); print(' ', config.runtime_config_summary().replace(chr(10), chr(10)+'  ')); print('  엔진 원본:', v.engine_fingerprint())" 2>/dev/null
echo ""
