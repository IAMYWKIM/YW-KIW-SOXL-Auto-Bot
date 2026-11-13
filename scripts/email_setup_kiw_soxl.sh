#!/bin/bash
# ============================================================
# kiw_soxl_trader 성과보고 이메일 설정 스크립트 v1.0
# 위치: ~/email_setup_kiw_soxl.sh   /   명령: email_setup_kiw_soxl [받는주소]
#
#   1) 이 봇 .env 에 메일 계정이 없으면, 다른 키움 봇(kiw_scalp_trader 등) .env 의
#      SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASSWORD 를 복사 (없으면 직접 입력)
#   2) EMAIL_TO = 인자로 준 주소 (없으면 SMTP_USER 본인 계정), EMAIL_ENABLED=true
#   3) config.json 의 EMAIL.ENABLED / DAILY / WEEKLY / MONTHLY 를 켬
#      (config.json 은 첫 기동 때 '꺼짐'으로 저장되므로 .env 만 고쳐서는 켜지지 않음)
#   4) 테스트 메일 발송 → 성공하면 서비스 재시작 여부 확인
# ============================================================
TARGET=~/kiw_soxl_trader
SERVICE=kiwoombot_soxl
PYTHON="$TARGET/kiw_soxl_venv/bin/python3"
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'

echo -e "${CYAN}============================================================"
echo "  📧 kiw_soxl_trader 성과보고 이메일 설정"
echo -e "============================================================${NC}"

[ -d "$TARGET" ] || { echo -e "${RED}❌ $TARGET 가 없습니다.${NC}"; exit 1; }
[ -x "$PYTHON" ] || PYTHON=python3

ENV_FILE=""
for f in "$TARGET/.env" "$TARGET/kiw_soxl.env"; do [ -f "$f" ] && { ENV_FILE="$f"; break; }; done
[ -n "$ENV_FILE" ] || { echo -e "${RED}❌ .env 파일이 없습니다 ($TARGET/.env)${NC}"; exit 1; }
cp "$ENV_FILE" "$ENV_FILE.bak_email_$(date +%Y%m%d_%H%M%S)"
echo -e "  설정 파일: $ENV_FILE  (백업 생성)"

getv() { grep -E "^$2=" "$1" 2>/dev/null | tail -1 | cut -d= -f2- | sed 's/^["'\'']//;s/["'\'']$//'; }
setv() {   # setv 파일 키 값
    local f="$1" k="$2" v="$3"
    if grep -qE "^$k=" "$f"; then
        "$PYTHON" - "$f" "$k" "$v" <<'PY'
import sys, re
f, k, v = sys.argv[1:4]
s = open(f, encoding="utf-8").read()
s = re.sub(rf"(?m)^{re.escape(k)}=.*$", lambda m: f"{k}={v}", s)
open(f, "w", encoding="utf-8").write(s)
PY
    else
        echo "$k=$v" >> "$f"
    fi
}
is_blank() { [ -z "$1" ] || [[ "$1" == *"보내는계정"* ]] || [[ "$1" == *"앱비밀번호"* ]]; }

USER_V=$(getv "$ENV_FILE" SMTP_USER); PASS_V=$(getv "$ENV_FILE" SMTP_PASSWORD)

# ── 1) 계정: 비어 있으면 다른 키움 봇에서 복사 ─────────────────
if is_blank "$USER_V" || is_blank "$PASS_V"; then
    SRC=""
    for f in ~/kiw_scalp_trader/.env ~/kiw_scalp_trader/kiw_scalp.env ~/kiw_*_trader/.env ~/kiw_*_trader/*.env; do
        [ -f "$f" ] || continue
        [[ "$f" == "$TARGET"* ]] && continue
        u=$(getv "$f" SMTP_USER); p=$(getv "$f" SMTP_PASSWORD)
        if ! is_blank "$u" && ! is_blank "$p"; then SRC="$f"; break; fi
    done
    if [ -n "$SRC" ]; then
        echo -e "  ${GREEN}✅ 메일 계정을 $SRC 에서 복사합니다${NC}"
        for k in SMTP_HOST SMTP_PORT SMTP_USER SMTP_PASSWORD; do
            v=$(getv "$SRC" $k); [ -n "$v" ] && setv "$ENV_FILE" $k "$v"
        done
    else
        echo -e "  ${YELLOW}다른 봇에서 메일 계정을 찾지 못했습니다. 직접 입력하세요.${NC}"
        echo "  (Gmail: Google 계정 → 보안 → 2단계 인증 → '앱 비밀번호' 16자리. 일반 비밀번호는 안 됩니다)"
        read -r -p "  보내는 Gmail 주소: " U
        read -r -s -p "  앱 비밀번호 16자리 (화면에 안 보임): " P; echo
        P="${P// /}"
        [ -n "$U" ] && [ -n "$P" ] || { echo -e "${RED}❌ 입력이 비어 있어 중단합니다.${NC}"; exit 1; }
        setv "$ENV_FILE" SMTP_HOST smtp.gmail.com
        setv "$ENV_FILE" SMTP_PORT 465
        setv "$ENV_FILE" SMTP_USER "$U"
        setv "$ENV_FILE" SMTP_PASSWORD "$P"
    fi
else
    echo -e "  ${GREEN}✅ 이 봇 .env 에 메일 계정이 이미 있습니다 ($USER_V)${NC}"
fi

# ── 2) 받는 사람 / 자동발송 ─────────────────────────────────
TO="$1"; [ -n "$TO" ] || TO=$(getv "$ENV_FILE" SMTP_USER)
setv "$ENV_FILE" EMAIL_TO "$TO"
setv "$ENV_FILE" EMAIL_ENABLED true
echo -e "  받는 사람: ${GREEN}$TO${NC}"

# ── 3) config.json 스위치 켜기 + 4) 테스트 메일 ────────────────
cd "$TARGET" || exit 1
"$PYTHON" - <<'PY'
import config, emailer
config.ensure_config_json()
for k in ("ENABLED", "DAILY", "WEEKLY", "MONTHLY", "ATTACH_EXCEL"):
    config.set_value(f"EMAIL.{k}", True)
print("  ✅ config.json: 이메일 자동발송 켜짐 (일간·주간·월간·엑셀 첨부)")
ok, msg = emailer.send("[SOXL 스나이퍼] 이메일 설정 테스트",
                       "<b>📧 이메일 설정 완료</b>\n앞으로 일간·주간·월간 성과보고가 텔레그램과 같은 시각에 이 주소로도 발송됩니다.")
print(("  ✅ 테스트 메일 " if ok else "  ❌ 테스트 메일 ") + msg)
raise SystemExit(0 if ok else 2)
PY
RC=$?
if [ $RC -ne 0 ]; then
    echo -e "${RED}  메일 발송에 실패했습니다. 자주 있는 원인:${NC}"
    echo "   - 앱 비밀번호가 아닌 일반 비밀번호를 넣음 (Gmail 은 2단계 인증 + 앱 비밀번호 필수)"
    echo "   - 앱 비밀번호를 지웠거나 다시 만들어서 예전 값이 무효가 됨"
    echo "   - SMTP_HOST/SMTP_PORT 오타 (Gmail: smtp.gmail.com / 465)"
    echo "  $ENV_FILE 을 고친 뒤 이 스크립트를 다시 실행하세요."
    exit 1
fi

echo ""
echo -e "${YELLOW}  .env 변경은 서비스 재시작 후 적용됩니다.${NC}"
ET_NOW=$(TZ=America/New_York date '+%u %H%M'); ET_DOW=${ET_NOW% *}; ET_HM=${ET_NOW#* }
if [ "$ET_DOW" -le 5 ] && [ "$((10#$ET_HM))" -ge 400 ] && [ "$((10#$ET_HM))" -le 1600 ]; then
    echo -e "${RED}  ⏰ 지금은 미국 장 시간(04:00~16:00 ET)입니다. 장 마감 후 재시작을 권합니다.${NC}"
fi
read -r -p "  지금 $SERVICE 를 재시작할까요? (y/N): " A
if [[ "$A" =~ ^[Yy]$ ]]; then
    sudo systemctl restart $SERVICE && sleep 3 && systemctl is-active --quiet $SERVICE \
        && echo -e "  ${GREEN}✅ 재시작 완료${NC}" || echo -e "  ${RED}❌ 재시작 실패: journalctl -u $SERVICE -n 50${NC}"
else
    echo "  나중에 deploy_kiw_soxl (또는 sudo systemctl restart $SERVICE) 로 재시작하세요."
fi
echo ""
echo "  확인: 텔레그램 📋 키움 메뉴 → 📧 이메일보고 화면에서 '자동발송 ✅' 와 받는 주소를 확인하세요."
