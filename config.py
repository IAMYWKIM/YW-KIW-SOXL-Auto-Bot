# -*- coding: utf-8 -*-
"""
kiw_soxl_trader - 설정 모듈 (키움증권 해외주식 SOXL 스나이퍼 봇)

kiw_scalp_trader 와 동일한 2파일 분리 원칙:
  - .env (또는 kiw_soxl.env) : 비밀값(API키/토큰/계좌번호) + 배포 단계 설정. 변경 시 재시작 필요.
  - config.json              : 운영 파라미터(수수료/한도/보고/이메일/알림). 저장 즉시 반영(핫리로드).

※ 매매 전략 파라미터(8% 정량, 7층 그리드, 익절 % 등)는 engine/ 폴더의 원본(pipios) 소스가
  그대로 결정합니다. 이 파일은 "키움 연결 · 기록 · 보고 · 안전장치" 만 다룹니다.
"""
import json
import os
import threading
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ENGINE_DIR = BASE_DIR / "engine"
DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"
REPORT_DIR = DATA_DIR / "reports"
for _d in (ENGINE_DIR, DATA_DIR, LOG_DIR, REPORT_DIR):
    _d.mkdir(parents=True, exist_ok=True)

ENV_CANDIDATES = [BASE_DIR / ".env", BASE_DIR / "kiw_soxl.env"]


def _load_env_file(path: Path) -> None:
    """python-dotenv 없이도 동작하는 최소 .env 로더 (기존 환경변수는 덮어쓰지 않음)"""
    try:
        for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[7:].strip()
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip()
            if v[:1] in ("'", '"') and v[-1:] == v[:1]:
                v = v[1:-1]
            elif " #" in v:
                v = v.split(" #", 1)[0].strip()
            if k and k not in os.environ:
                os.environ[k] = v
    except Exception:
        pass


for _env in ENV_CANDIDATES:
    if _env.exists():
        _load_env_file(_env)
        break


def _get(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def _get_bool(key: str, default: bool = False) -> bool:
    return _get(key, str(default)).lower() in ("1", "true", "yes", "y", "on")


# ── 키움증권 REST API (kiw_scalp_trader 와 같은 변수명) ─────────────
TRADE_MODE = (_get("TRADE_MODE") or _get("KIWOOM_MODE", "MOCK")).upper()
KIWOOM_IS_MOCK = TRADE_MODE == "MOCK"
if KIWOOM_IS_MOCK:
    KIWOOM_APP_KEY = _get("KIWOOM_APP_KEY_MOCK")
    KIWOOM_SECRET_KEY = _get("KIWOOM_SECRET_KEY_MOCK")
    KIWOOM_ACCOUNT_NO = _get("KIWOOM_ACCOUNT_NO_MOCK")
    KIWOOM_API_BASE = _get("KIWOOM_MOCK_API_BASE", "https://mockapi.kiwoom.com")
else:
    KIWOOM_APP_KEY = _get("KIWOOM_APP_KEY")
    KIWOOM_SECRET_KEY = _get("KIWOOM_SECRET_KEY")
    KIWOOM_ACCOUNT_NO = _get("KIWOOM_ACCOUNT_NO")
    KIWOOM_API_BASE = _get("KIWOOM_API_BASE", "https://api.kiwoom.com")

# ── 안전장치 ───────────────────────────────────────────────────────
# true: 키움에 실제 주문을 보내지 않고 봇 내부 가상 계좌로 체결을 흉내냅니다 (시세·차트는 실제 조회)
DRY_RUN = _get_bool("DRY_RUN", True)

# ── 텔레그램 / 이메일 ──────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = _get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = _get("TELEGRAM_CHAT_ID")
TELEGRAM_ENABLED = _get_bool("TELEGRAM_ENABLED", True)
SMTP_HOST = _get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(_get("SMTP_PORT", "465") or 465)
SMTP_USER = _get("SMTP_USER")
SMTP_PASSWORD = _get("SMTP_PASSWORD")
EMAIL_TO = [x.strip() for x in _get("EMAIL_TO").split(",") if x.strip()]
EMAIL_ENABLED_ENV = _get_bool("EMAIL_ENABLED", False)

BOT_LABEL = _get("BOT_LABEL", "키움 해외주식 SOXL 스나이퍼 봇 #1 (KIW_SOXL)")
LOG_LEVEL = _get("LOG_LEVEL", "INFO")
LOG_PREFIX = "[SOXL]"
SERVICE_NAME = "kiwoombot_soxl"

# ── config.json (핫리로드) ─────────────────────────────────────────
CONFIG_JSON = BASE_DIR / "config.json"

DEFAULTS = {
    "_README": "값 저장 즉시 반영됩니다. 전략 수치는 engine/ 원본 소스가 결정하며 여기서는 키움 연결·기록·보고·안전장치만 다룹니다.",
    "SYMBOLS": {
        "_README": "종목별 키움 거래소구분(stex_tp). NA=AMEX, ND=NASDAQ, NY=NYSE(키움은 NYSE Arca 상장 ETF 인 SOXL·SGOV 를 NY 로 분류). AUTO 는 자동 탐색합니다.",
        "SOXL": "NY", "SGOV": "NY", "QQQ": "ND",
    },
    "FEE": {
        "_README": "편도 수수료율(소수). 0.0025 = 0.25%. 본인 계좌의 실제 미국주식 수수료율로 반드시 맞추세요 (엔진의 순익·익절 계산에 쓰입니다). 엔진 허용 범위는 0 초과 0.01 이하.",
        "US_COMMISSION_RATE": 0.0025,
        "SELL_EXTRA_FEE_RATE": 0.0,
    },
    "KIWOOM": {
        "_README": "CHART_TZ: 분봉 체결시간의 시간대(AUTO/ET/KST). CANDLE_TIME_IS_END: 분봉 시각이 봉의 끝 시각이면 true. QUERY_MIN_INTERVAL: 조회 TR 최소 간격(초). ORDER_MIN_INTERVAL: 주문 TR 최소 간격(초).",
        "CHART_TZ": "AUTO",
        "CANDLE_TIME_IS_END": False,
        "QUERY_MIN_INTERVAL": 0.22,
        "ORDER_MIN_INTERVAL": 0.12,
        "TIMEOUT_SEC": 8,
        "PRICE_CACHE_SEC": 0.35,
        "REF_PRICE_CACHE_SEC": 5.0,
        "HOLDINGS_CACHE_SEC": 0.8,
        "ORDERS_CACHE_SEC": 0.35,
        "DEPOSIT_CACHE_SEC": 1.5,
        "CANDLE_1M_CACHE_SEC": 4.0,
        "CANDLE_1D_CACHE_SEC": 300,
        "CANDLE_MAX_PAGES": 4,
        "BUYING_POWER_SOURCE": "FC_ORD_ALOWA",
    },
    "SAFETY": {
        "_README": "MAX_BOT_BUYING_POWER_USD: 봇에게 보여줄 주문가능 달러 상한(0=제한 없음). 원본 엔진은 계좌의 남는 달러를 예비시드로 자동 수혈하므로, 같은 계좌에 다른 돈이 있으면 상한을 꼭 정하세요. MAX_ORDER_USD: 1회 주문금액 상한(0=제한 없음, 초과 주문은 거부). ALLOW_ENGINE_GIT_UPDATE 는 항상 false 권장(원본의 깃 자동 업데이트 차단).",
        "MAX_BOT_BUYING_POWER_USD": 0,
        "MAX_ORDER_USD": 0,
        "ALLOW_ENGINE_GIT_UPDATE": False,
        "DRY_RUN_START_CASH_USD": 10000,
    },
    "REPORT": {
        "_README": "시각은 한국시간(KST). 미국 정규장 마감 후 일일 보고를 보냅니다. 주간=토요일, 월간=매월 1일.",
        "DAILY_ENABLED": True, "DAILY_TIME_KST": "07:10",
        "WEEKLY_ENABLED": True, "WEEKLY_TIME_KST": "07:20",
        "MONTHLY_ENABLED": True, "MONTHLY_TIME_KST": "07:30",
        "ATTACH_EXCEL_TELEGRAM": True,
        "SNAPSHOT_INTERVAL_MIN": 10,
    },
    "EMAIL": {
        "_README": "자동발송 스위치. 계정은 .env 의 SMTP_* / EMAIL_TO.",
        "ENABLED": None, "DAILY": True, "WEEKLY": True, "MONTHLY": True, "ATTACH_EXCEL": True,
    },
    "ALERT": {
        "_README": "봇 자체 알림. FILL=체결 요약(원본 엔진 알림과 별개의 한 줄 기록), API_ERROR_COOLDOWN_MIN=같은 API 오류 알림 간격, TRANSIENT_REPEAT=키움 서버 일시 오류는 10분 안에 이 횟수 이상 반복될 때만 알림.",
        "FILL": False, "API_ERROR": True, "API_ERROR_COOLDOWN_MIN": 30, "TRANSIENT_REPEAT": 3,
        "STALE_CANDLE_MIN": 5,
    },
}

_lock = threading.RLock()
_cache = {"mtime": None, "data": None}


def _deep_merge_defaults(cur: dict, defaults: dict) -> list:
    added = []
    for k, v in defaults.items():
        if k not in cur:
            cur[k] = json.loads(json.dumps(v))
            added.append(k)
        elif isinstance(v, dict) and isinstance(cur[k], dict):
            added += [f"{k}.{x}" for x in _deep_merge_defaults(cur[k], v)]
    return added


def ensure_config_json() -> list:
    """config.json 이 없으면 기본값으로 만들고, 있으면 새 키만 보충합니다 (기존 값 보존)."""
    with _lock:
        cur = {}
        if CONFIG_JSON.exists():
            try:
                cur = json.loads(CONFIG_JSON.read_text(encoding="utf-8"))
            except Exception:
                cur = {}
        added = _deep_merge_defaults(cur, DEFAULTS)
        if cur.get("EMAIL", {}).get("ENABLED") is None:
            cur["EMAIL"]["ENABLED"] = bool(EMAIL_ENABLED_ENV)
            added.append("EMAIL.ENABLED")
        if added or not CONFIG_JSON.exists():
            _write(cur)
        return added


def _write(data: dict) -> None:
    tmp = CONFIG_JSON.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, CONFIG_JSON)
    _cache["mtime"] = None


def load() -> dict:
    with _lock:
        try:
            mt = CONFIG_JSON.stat().st_mtime
        except FileNotFoundError:
            ensure_config_json()
            mt = CONFIG_JSON.stat().st_mtime
        if _cache["data"] is None or _cache["mtime"] != mt:
            try:
                data = json.loads(CONFIG_JSON.read_text(encoding="utf-8"))
                _deep_merge_defaults(data, DEFAULTS)
                _cache.update(mtime=mt, data=data)
            except Exception:
                if _cache["data"] is None:
                    _cache["data"] = json.loads(json.dumps(DEFAULTS))
        return _cache["data"]


def get(dot_path: str, default=None):
    node = load()
    for part in dot_path.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def set_value(dot_path: str, value) -> None:
    with _lock:
        data = json.loads(json.dumps(load()))
        node = data
        parts = dot_path.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
        _write(data)


def parse_value(dot_path: str, text: str):
    """/set 입력 문자열을 기존 값의 자료형에 맞춰 변환"""
    cur = get(dot_path)
    t = str(text).strip()
    if isinstance(cur, bool) or t.lower() in ("true", "false", "on", "off"):
        return t.lower() in ("true", "on", "1", "yes")
    if isinstance(cur, int) and not isinstance(cur, bool):
        return int(float(t.replace(",", "")))
    if isinstance(cur, float):
        return float(t.replace(",", ""))
    if isinstance(cur, str) and ":" not in t and t.replace(".", "", 1).isdigit() and "TIME" in dot_path:
        t = t.replace(".", ":")
    return t


def runtime_config_summary() -> str:
    c = load()
    return "\n".join([
        f"모드: {'모의(MOCK)' if KIWOOM_IS_MOCK else '실전(REAL)'} · DRY_RUN={'켜짐' if DRY_RUN else '꺼짐'}",
        f"수수료(편도): {c['FEE']['US_COMMISSION_RATE'] * 100:.3f}%",
        f"주문가능 상한: {c['SAFETY']['MAX_BOT_BUYING_POWER_USD'] or '없음'} · 1회 주문 상한: {c['SAFETY']['MAX_ORDER_USD'] or '없음'}",
        f"일일보고 {c['REPORT']['DAILY_TIME_KST']} KST · 이메일 {'켜짐' if c['EMAIL']['ENABLED'] else '꺼짐'}",
    ])
