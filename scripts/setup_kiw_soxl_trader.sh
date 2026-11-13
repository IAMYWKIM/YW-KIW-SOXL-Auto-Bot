#!/bin/bash
# ============================================================
# kiw_soxl_trader 초기 설치 스크립트 (신규 서버/Google Cloud VM용)
# 위치: ~/setup_kiw_soxl_trader.sh
#
# 사용법:
#   1) kiw_soxl_trader_vX.Y.Z_full.zip 과 이 스크립트를 홈 디렉토리(~)에 업로드
#      (다른 키움 봇과 파일 이름이 겹치지 않도록 zip 그대로 올립니다 — 풀지 마세요)
#   2) bash ~/setup_kiw_soxl_trader.sh 실행
#
# 설정 파일 2종 (kiw_scalp_trader 와 동일한 분리 원칙):
#   kiw_soxl.env(.env) - 비밀값(API키/토큰/계좌). 변경 시 재시작 필요.
#   config.json        - 운영 파라미터(수수료/한도/보고/알림). 수정 즉시 반영.
#
# 구조 (다른 봇과 코드·상태·텔레그램 봇을 공유하지 않는 완전 독립 프로그램):
#   ~/kiw_soxl_trader/        - 키움 포팅판(연결·기록·보고), 서비스: kiwoombot_soxl
#   ~/kiw_soxl_trader/engine/ - 원본(pipios) 매매 엔진 소스 — 받은 그대로 보관
#   ~/kiw_soxl_trader/run/    - 실행 폴더(real/mock/dry 별 원장)
#   kiw_soxl_venv/, data/, logs/ - 독립 가상환경/기록/로그
# ============================================================
set -e

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'

TARGET=~/kiw_soxl_trader
SERVICE=kiwoombot_soxl
VENV="$TARGET/kiw_soxl_venv"
USER_NAME=$(whoami)

CORE_FILES="main.py config.py logger.py kiwoom_us_client.py toss_bridge.py engine_hooks.py
            engine_sync.py store.py trade_context.py scheduler.py reports.py export.py
            emailer.py notify.py telegram_bot.py settings_menu.py diag.py version_history.py"

echo ""
echo -e "${CYAN}============================================================"
echo "  kiw_soxl_trader 초기 설치 스크립트"
echo "  (키움증권 해외주식 SOXL 스나이퍼, 신규 서버용)"
echo -e "============================================================${NC}"
echo ""

# ── 1. 설치 zip 찾기 ───────────────────────────────────────────
ZIP=$(ls -t ~/kiw_soxl_trader_v*_full.zip 2>/dev/null | head -n 1)
if [ -z "$ZIP" ] && [ ! -f "$TARGET/main.py" ]; then
    echo -e "${RED}❌ 홈(~)에 kiw_soxl_trader_v*_full.zip 이 없습니다. 먼저 업로드하세요.${NC}"
    exit 1
fi

# ── 2. 디렉토리 생성 + 압축 풀기 ───────────────────────────────
echo "📁 디렉토리 생성 중..."
mkdir -p "$TARGET"/{data,logs,docs,engine,run}
if [ -n "$ZIP" ]; then
    command -v unzip > /dev/null || { echo "📦 unzip 설치 중..."; sudo apt-get install -y -q unzip; }
    echo "📦 $(basename "$ZIP") 푸는 중..."
    TMP=$(mktemp -d)
    unzip -q -o "$ZIP" -d "$TMP"
    # 기존 .env / config.json 은 덮어쓰지 않음
    rm -f "$TMP/.env" "$TMP/config.json"
    cp -r "$TMP"/. "$TARGET"/
    rm -rf "$TMP"
    mkdir -p ~/kiw_soxl_installed && mv "$ZIP" ~/kiw_soxl_installed/
    echo "  ✅ 소스 배치 완료 (zip 은 ~/kiw_soxl_installed/ 로 옮김)"
fi

# ── 3. 필수 파일 확인 + 문법 검증 ──────────────────────────────
echo ""
echo "🔍 필수 파일 확인 중..."
cd "$TARGET"
missing=0
for f in $CORE_FILES; do
    if [ ! -f "$TARGET/$f" ]; then echo -e "  ${RED}❌ $f 없음${NC}"; missing=1; else echo "  ✅ $f"; fi
done
[ $missing -eq 1 ] && { echo -e "${RED}❌ 필수 파일 누락 — 설치 중단${NC}"; exit 1; }
for f in $CORE_FILES; do
    python3 -m py_compile "$f" || { echo -e "${RED}❌ 문법 오류: $f — 설치 중단${NC}"; exit 1; }
done
N_ENGINE=$(ls "$TARGET"/engine/*.py 2>/dev/null | wc -l)
if [ "$N_ENGINE" -lt 15 ]; then
    echo -e "  ${YELLOW}⚠️  engine/ 에 원본 소스가 ${N_ENGINE}개뿐입니다 — 설치 후 sync_kiw_soxl 로 원본 zip 을 반영하세요${NC}"
else
    echo "  ✅ engine/ 원본 소스 ${N_ENGINE}개"
fi

# ── 4. 환경변수 파일 준비 ──────────────────────────────────────
echo ""
echo "📝 환경변수 파일 준비..."
if [ -f "$TARGET/.env" ] || [ -f "$TARGET/kiw_soxl.env" ]; then
    echo "  ℹ️  환경변수 파일이 이미 있습니다 — 그대로 사용합니다"
else
    cp "$TARGET/kiw_soxl.env.example" "$TARGET/.env"
    chmod 600 "$TARGET/.env"
    echo "  ✅ kiw_soxl.env.example → .env 복사 완료"
    echo -e "  ${YELLOW}⚠️  아래 항목을 반드시 실제 값으로 채워주세요:${NC}"
    echo "     TRADE_MODE, KIWOOM_APP_KEY(_MOCK), KIWOOM_SECRET_KEY(_MOCK),"
    echo "     KIWOOM_ACCOUNT_NO(_MOCK), TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID"
    echo "     (⚠️ 텔레그램 봇은 다른 키움 봇과 다른 새 봇으로 발급)"
fi

# ── 5. venv 생성 + requirements 설치 ───────────────────────────
echo ""
if [ ! -d "$VENV" ]; then
    echo "🐍 Python 가상환경 생성 중... (pandas 설치로 2~4분 소요)"
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install --upgrade pip -q
    "$VENV/bin/pip" install -r "$TARGET/requirements.txt" -q
    echo "  ✅ requirements.txt 설치 완료"
else
    echo "🐍 kiw_soxl_venv 이미 존재 — 건너뜀"
fi

# ── 6. config.json 생성 + 원본 엔진 호환성 점검 ────────────────
echo ""
echo "📝 config.json 준비 + 원본 엔진 점검..."
"$VENV/bin/python3" -c "
import config
added = config.ensure_config_json()
print('  ✅ config.json 준비 완료' + (f' (새 키 {len(added)}개)' if added else ''))
"
"$VENV/bin/python3" engine_sync.py check || echo -e "  ${YELLOW}⚠️  원본 엔진 점검에서 문제가 나왔습니다 — 위 내용을 확인하세요${NC}"

# ── 7. systemd 서비스 ──────────────────────────────────────────
echo ""
SERVICE_FILE=/etc/systemd/system/${SERVICE}.service
if [ ! -f "$SERVICE_FILE" ]; then
    echo "⚙️  systemd 서비스 파일 생성 ($SERVICE)..."
    sudo tee "$SERVICE_FILE" > /dev/null << EOT
[Unit]
Description=키움증권 해외주식 SOXL 스나이퍼 자동매매 봇 (kiw_soxl_trader)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER_NAME
WorkingDirectory=$TARGET
ExecStart=$VENV/bin/python3 -B $TARGET/main.py
Restart=on-failure
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOT
    sudo systemctl daemon-reload
    sudo systemctl enable "$SERVICE"
    echo "  ✅ $SERVICE.service 생성 및 부팅 자동시작 활성화"
else
    echo "⚙️  $SERVICE.service 이미 존재 — 건너뜀"
fi

# ── 8. deploy / stop / sync 스크립트 등록 ──────────────────────
echo ""
echo "📜 배포/중지/원본동기화 스크립트 등록 중..."
for s in deploy_kiw_soxl.sh stop_kiw_soxl.sh sync_kiw_soxl.sh email_setup_kiw_soxl.sh; do
    [ -f "$TARGET/scripts/$s" ] && cp "$TARGET/scripts/$s" ~/"$s"
    if [ -f ~/"$s" ]; then
        chmod +x ~/"$s"
        name="${s%.sh}"
        sudo ln -sf ~/"$s" /usr/local/bin/"$name" 2>/dev/null || true
        grep -q "alias $name=" ~/.bashrc 2>/dev/null || echo "alias $name='bash ~/$s'" >> ~/.bashrc
        echo "  ✅ $name"
    else
        echo -e "  ${YELLOW}⚠️  ~/$s 없음${NC}"
    fi
done

# ── 9. 완료 안내 ───────────────────────────────────────────────
echo ""
echo -e "${CYAN}============================================================"
echo "  ✅ 설치 완료"
echo -e "============================================================${NC}"
cat << 'EOT'

📋 다음 단계:

1️⃣  .env 값 채우기 (가장 중요 — 비밀값):
    nano ~/kiw_soxl_trader/.env
    → TRADE_MODE=MOCK, DRY_RUN=true 로 시작
    → KIWOOM_APP_KEY_MOCK / KIWOOM_SECRET_KEY_MOCK / KIWOOM_ACCOUNT_NO_MOCK
    → TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID (kiw_soxl 전용 새 봇)

2️⃣  키움 응답 실측 점검 (실거래 전 필수):
    cd ~/kiw_soxl_trader && kiw_soxl_venv/bin/python3 diag.py
    → 끝에 나오는 "권장 설정"을 config.json 에 반영
    → 프리장 시간(한국 18:00 이후, 서머타임 17:00 이후)에 한 번 더 실행

3️⃣  config.json 확인 (수수료율·주문가능 상한):
    nano ~/kiw_soxl_trader/config.json    ← 저장 즉시 반영
    → FEE.US_COMMISSION_RATE 를 본인 계좌의 실제 수수료율로

4️⃣  기동:            deploy_kiw_soxl
5️⃣  상태 확인:       stop_kiw_soxl --status
                      tail -f ~/kiw_soxl_trader/logs/kiw_soxl_trader.log

────────────────────────────────────────────────────────
앞으로의 워크플로우:
  키움 포팅판 새 버전   → kiw_soxl_trader_vX_update.zip 을 홈에 올리고  deploy_kiw_soxl
  원본(pipios) 새 소스  → pipiosToss_bot-main.zip 을 홈에 올리고        sync_kiw_soxl
  중지                  → stop_kiw_soxl   (영구 중지: --permanent)
────────────────────────────────────────────────────────
EOT
